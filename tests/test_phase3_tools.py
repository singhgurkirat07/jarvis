"""Phase 3 tests for ToolRegistry, Safe Calculator, Date/Time, System Info, and Workspace File Sandbox."""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from backend.config import Settings
from backend.db import Database
from backend.permissions import PermissionLevel, PermissionManager
from backend.tools import (
    MAX_WORKSPACE_FILE_BYTES,
    ToolCancelledError,
    ToolExecutionContext,
    ToolExecutionError,
    ToolRegistry,
    ToolSpec,
    ToolTimeoutError,
    ToolValidationError,
    UnknownToolError,
    WorkspaceSecurityError,
    build_default_tool_registry,
    evaluate_math_expression,
    resolve_workspace_path,
)


@pytest.fixture
def tool_env(tmp_path):
    db = Database(tmp_path / "tools.db")
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        db_path=str(tmp_path / "tools.db"),
        workspace_dir=str(ws),
        offline_mode=True,
        tools_enabled=True,
        max_tool_calls_per_turn=3,
    )
    pm = PermissionManager(db)
    reg = build_default_tool_registry(pm)
    ctx = ToolExecutionContext(
        settings=settings,
        workspace_root=ws,
        db=db,
        engine_status_provider=lambda: {
            "state": "ready",
            "model": {
                "name": "Qwen2.5-7B-Instruct",
                "architecture": "qwen2",
                "size_mb": 4400,
                "layers": 28,
                "path": "C:/models/qwen2.5-7b-instruct.gguf",
            },
            "n_gpu_layers": 28,
            "gpu_plan": "all layers on GPU",
            "load_seconds": 1.5,
            "gpu": {"available": True, "name": "NVIDIA GeForce RTX 5050 Laptop GPU", "total_mb": 8192},
        },
    )
    try:
        yield db, pm, reg, ctx, ws
    finally:
        db.close()


# ---- 1. Tool Registry tests -------------------------------------------------
def test_tool_registry_registration_and_duplicate_rejection(tool_env):
    _, pm, _, _, _ = tool_env
    reg = ToolRegistry(pm)
    spec = ToolSpec(
        name="custom_echo",
        description="Echo a message",
        parameters_schema={
            "type": "object",
            "properties": {"msg": {"type": "string"}},
            "required": ["msg"],
        },
        validator=lambda a, _c: {"msg": str(a["msg"])},
        executor=lambda a, _c: {"echo": a["msg"]},
        permission_level=PermissionLevel.SAFE_READ,
    )
    reg.register(spec)
    assert reg.get("custom_echo").name == "custom_echo"

    with pytest.raises(ValueError, match="Duplicate tool registration"):
        reg.register(spec)

    with pytest.raises(ValueError, match="Invalid tool name"):
        reg.register(
            ToolSpec(
                name="Bad-Tool-Name!",
                description="Invalid",
                parameters_schema={},
                validator=lambda a, _c: a,
                executor=lambda a, _c: a,
                permission_level=PermissionLevel.SAFE_READ,
            )
        )


def test_unknown_tool_and_invalid_arguments_rejected(tool_env):
    db, _, reg, ctx, _ = tool_env

    with pytest.raises(UnknownToolError, match="not registered"):
        reg.execute_tool("invented_shell_tool", {"cmd": "whoami"}, ctx)

    # Unknown parameter key rejected
    with pytest.raises(ToolValidationError, match="Unknown argument"):
        reg.execute_tool("calculate", {"expression": "1+1", "bypass_permissions": True}, ctx)

    # Missing required argument rejected
    with pytest.raises(ToolValidationError, match="Missing required argument"):
        reg.execute_tool("calculate", {}, ctx)

    # Non-object arguments rejected
    with pytest.raises(ToolValidationError, match="must be a JSON object"):
        reg.execute_tool("calculate", ["1+1"], ctx)

    execs = db.list_tool_executions()
    statuses = [e["status"] for e in execs]
    assert "rejected_unknown_tool" in statuses
    assert "validation_failed" in statuses


def test_tool_timeout_and_cancellation_enforced(tool_env):
    _, pm, _, ctx, _ = tool_env
    reg = ToolRegistry(pm)

    def slow_exec(_args, _ctx):
        time.sleep(0.5)
        return {"ok": True}

    reg.register(
        ToolSpec(
            name="slow_tool",
            description="Sleeps past timeout",
            parameters_schema={"type": "object", "properties": {}, "required": []},
            validator=lambda _a, _c: {},
            executor=slow_exec,
            permission_level=PermissionLevel.SAFE_READ,
            timeout_s=0.08,
        )
    )

    with pytest.raises(ToolTimeoutError, match="timed out"):
        reg.execute_tool("slow_tool", {}, ctx)

    # Pre-cancelled execution raises ToolCancelledError immediately
    cancel_ev = threading.Event()
    cancel_ev.set()
    ctx.cancel_event = cancel_ev
    with pytest.raises(ToolCancelledError, match="cancelled"):
        reg.execute_tool("slow_tool", {}, ctx)


# ---- 2. Safe Calculator tests -----------------------------------------------
@pytest.mark.parametrize(
    "expr, expected",
    [
        ("2 + 3 * 4", 14),
        ("(2 + 3) * 4", 20),
        ("100 / 4", 25),
        ("17 // 5", 3),
        ("17 % 5", 2),
        ("2 ** 10", 1024),
        ("-5 + +12", 7),
        ("sqrt(1024) + abs(-18)", 50),
        ("round(pi, 4)", 3.1416),
        ("sin(0) + cos(0)", 1),
        ("log10(1000) + floor(4.9) + ceil(4.1)", 12),
        ("factorial(6)", 720),
        ("min(10, 4, 8) + max(1, 9, 3)", 13),
        ("pow(3, 4)", 81),
    ],
)
def test_calculator_valid_expressions(tool_env, expr, expected):
    _, _, reg, ctx, _ = tool_env
    res = reg.execute_tool("calculate", {"expression": expr}, ctx)
    assert res["status"] == "success"
    assert res["result"]["result"] == pytest.approx(expected)


def test_calculator_division_by_zero_and_domain_errors(tool_env):
    _, _, reg, ctx, _ = tool_env
    with pytest.raises(ToolExecutionError, match="Division by zero"):
        reg.execute_tool("calculate", {"expression": "42 / (5 - 5)"}, ctx)

    with pytest.raises(ToolExecutionError, match="Modulo by zero"):
        reg.execute_tool("calculate", {"expression": "10 % 0"}, ctx)

    with pytest.raises(ToolExecutionError, match="domain error"):
        reg.execute_tool("calculate", {"expression": "sqrt(-9)"}, ctx)


@pytest.mark.parametrize(
    "malicious_expr",
    [
        "__import__('os').system('calc')",
        "open('secret.txt').read()",
        "(1).__class__.__base__.__subclasses__()",
        "lambda x: x + 1",
        "[x for x in range(10)]",
        "{'a': 1}",
        "'hello' + 'world'",
        "True + True",
        "10 ** 10000",
        "9 ** 9 ** 9",
        "factorial(500)",
        "unknown_var + 1",
        "math.sqrt(4)",
    ],
)
def test_calculator_rejects_arbitrary_code_and_dos(malicious_expr):
    with pytest.raises((ToolValidationError, ToolExecutionError)):
        evaluate_math_expression(malicious_expr)


# ---- 3. Date, Time & System Info tools --------------------------------------
def test_date_time_and_timezone_tools(tool_env):
    _, _, reg, ctx, _ = tool_env

    d_res = reg.execute_tool("get_current_date", {}, ctx)["result"]
    assert len(d_res["date"]) == 10 and d_res["date"].count("-") == 2
    assert d_res["weekday"]

    t_res = reg.execute_tool("get_current_time", {"timezone": "UTC"}, ctx)["result"]
    assert t_res["timezone"] == "UTC"
    assert t_res["utc_offset"] == "+00:00"

    tz_res = reg.execute_tool("get_current_timezone", {}, ctx)["result"]
    assert "timezone" in tz_res and "utc_offset" in tz_res

    ny_res = reg.execute_tool("get_datetime_in_timezone", {"timezone": "America/New_York"}, ctx)["result"]
    assert ny_res["timezone"] == "America/New_York"
    assert ny_res["utc_offset"] in ("-04:00", "-05:00")

    ist_res = reg.execute_tool("get_datetime_in_timezone", {"timezone": "IST"}, ctx)["result"]
    assert ist_res["timezone"] == "Asia/Kolkata"
    assert ist_res["utc_offset"] == "+05:30"

    with pytest.raises(ToolValidationError, match="Unsupported timezone"):
        reg.execute_tool("get_datetime_in_timezone", {"timezone": "Invalid/NoSuchZone"}, ctx)


def test_system_info_tool_reports_status_without_secrets(tool_env):
    _, _, reg, ctx, _ = tool_env
    res = reg.execute_tool("get_system_info", {}, ctx)["result"]
    assert res["model_state"] == "ready"
    assert res["model"]["name"] == "Qwen2.5-7B-Instruct"
    assert res["gpu"]["available"] is True
    assert res["configuration"]["offline_mode"] is True
    assert "environ" not in str(res).lower()


# ---- 4. Workspace File Sandbox Security tests -------------------------------
def test_workspace_list_and_read_safe_files(tool_env):
    _, _, reg, ctx, ws = tool_env
    sample = ws / "notes.md"
    sample.write_text("# Project Notes\nLocal inference is fast.", encoding="utf-8")

    listed = reg.execute_tool("workspace_list_files", {}, ctx)["result"]
    assert listed["count"] == 1
    assert listed["files"][0]["path"] == "notes.md"

    read_res = reg.execute_tool("workspace_read_file", {"path": "notes.md"}, ctx)["result"]
    assert "Local inference is fast." in read_res["content"]
    assert read_res["size_bytes"] == sample.stat().st_size


@pytest.mark.parametrize(
    "bad_path",
    [
        "../secret.txt",
        "../../backend/app.py",
        "subdir/../../outside.txt",
        "..\\..\\Windows\\win.ini",
        "C:\\Windows\\System32\\drivers\\etc\\hosts",
        "D:/data/file.txt",
        "/etc/passwd",
        "\\Windows\\System32\\cmd.exe",
        "\\\\server\\share\\file.txt",
        ".env",
        ".env.local",
        "subdir/.env.production",
        "my_secret_token.txt",
        "aws_credentials.json",
        "user_passwords.csv",
        "id_rsa",
        "id_ed25519.pub",
        "server.pem",
        "private.key",
        "jarvis.db",
        "model.gguf",
        "payload.exe",
        "script.ps1",
        "batch.bat",
        "binary.bin",
    ],
)
def test_workspace_blocks_traversal_absolute_paths_secrets_and_bad_extensions(tool_env, bad_path):
    _, _, reg, ctx, ws = tool_env
    with pytest.raises(WorkspaceSecurityError):
        resolve_workspace_path(ws, bad_path, allow_directory=False)

    with pytest.raises(WorkspaceSecurityError):
        reg.execute_tool("workspace_read_file", {"path": bad_path}, ctx)


def test_workspace_blocks_symlink_escape(tool_env, tmp_path):
    import subprocess

    _, _, reg, ctx, ws = tool_env
    outside_dir = tmp_path / "outside_dir"
    outside_dir.mkdir(parents=True, exist_ok=True)
    outside_file = outside_dir / "outside_secret.txt"
    outside_file.write_text("TOP SECRET OUTSIDE WORKSPACE", encoding="utf-8")

    link_path = ws / "escaped_link.txt"
    created_symlink = False
    try:
        link_path.symlink_to(outside_file)
        created_symlink = True
    except OSError:
        # On Windows without SeCreateSymbolicLinkPrivilege, test NTFS junction (/J) escape instead
        junction_dir = ws / "escaped_junction"
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction_dir), str(outside_dir)],
            check=True,
            capture_output=True,
        )

    if created_symlink:
        with pytest.raises(WorkspaceSecurityError, match="Symbolic links|escapes"):
            reg.execute_tool("workspace_read_file", {"path": "escaped_link.txt"}, ctx)
    else:
        with pytest.raises(WorkspaceSecurityError, match="Symbolic links|escapes"):
            reg.execute_tool("workspace_read_file", {"path": "escaped_junction/outside_secret.txt"}, ctx)


def test_workspace_enforces_file_size_limits(tool_env):
    _, _, reg, ctx, ws = tool_env
    oversized = "A" * (MAX_WORKSPACE_FILE_BYTES + 1024)

    with pytest.raises(ToolValidationError, match="exceeds maximum limit"):
        reg.validate_call("workspace_create_file", {"path": "huge.txt", "content": oversized}, ctx)

    big_file = ws / "existing_huge.txt"
    big_file.write_text(oversized, encoding="utf-8")
    with pytest.raises(ToolExecutionError, match="exceeds maximum readable size"):
        reg.execute_tool("workspace_read_file", {"path": "existing_huge.txt"}, ctx)
