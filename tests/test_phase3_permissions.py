"""Phase 3 tests for PermissionManager, Approval lifecycle, Cryptographic Binding, and Chat Tool Loop."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from backend.app import create_app
from backend.engine import Engine
from backend.permissions import (
    ApprovalExpiredError,
    ApprovalMismatchError,
    ApprovalRejectedError,
    ApprovalRequiredError,
    PermissionDeniedError,
)


class ScriptedLlama:
    """Deterministic LLM stub that returns a scripted sequence of replies across calls."""

    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.calls: list[dict] = []
        self.closed = False

    def tokenize(self, data: bytes, add_bos=False, special=False):
        return data.split()

    def close(self):
        self.closed = True

    def _next_reply(self) -> str:
        if len(self.replies) > 1:
            return self.replies.pop(0)
        return self.replies[0] if self.replies else "Done."

    def create_chat_completion(self, messages, stream=False, max_tokens=128, **kw):
        self.calls.append({"messages": messages, "max_tokens": max_tokens, **kw})
        reply = self._next_reply()
        if not stream:
            return {
                "choices": [{"message": {"role": "assistant", "content": reply}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 20, "completion_tokens": max(1, len(reply.split()))},
            }

        def gen():
            tokens = reply.split(" ")
            for i, tok in enumerate(tokens):
                yield {"choices": [{"delta": {"content": (" " if i else "") + tok}, "finish_reason": None}]}
            yield {"choices": [{"delta": {}, "finish_reason": "stop"}]}

        return gen()


@pytest.fixture
async def phase3_client(tmp_settings, tmp_path):
    ws_dir = tmp_path / "workspace"
    ws_dir.mkdir(parents=True, exist_ok=True)
    tmp_settings.workspace_dir = str(ws_dir)
    tmp_settings.n_ctx = 2048
    tmp_settings.max_tokens = 128

    llm = ScriptedLlama(["Ready."])
    engine = Engine(tmp_settings, loader=lambda _s, _n: llm)
    engine.load()
    app = create_app(tmp_settings, engine=engine, load_model=False)

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
            yield client, llm, ws_dir, app


async def test_level1_tools_execute_automatically_in_chat_and_direct_api(phase3_client):
    client, llm, _, _ = phase3_client

    # 1. Direct execution of Level 1 calculator tool succeeds immediately without approval
    r_direct = await client.post(
        "/api/tools/execute",
        json={"tool_name": "calculate", "arguments": {"expression": "(15 * 24) + sqrt(144)"}},
    )
    assert r_direct.status_code == 200
    assert r_direct.json()["result"]["result"] == 372

    # 2. Model-driven Level 1 tool call in /api/chat executes automatically and returns final answer
    llm.replies = [
        '<tool_call>{"name": "calculate", "arguments": {"expression": "256 * 16"}}</tool_call>',
        "Using the calculator tool, 256 multiplied by 16 is 4096.",
    ]
    r_chat = await client.post(
        "/api/chat",
        json={"message": "What is 256 * 16?", "stream": False},
    )
    assert r_chat.status_code == 200
    body = r_chat.json()
    assert "4096" in body["content"]
    assert len(body["tool_events"]) == 1
    assert body["tool_events"][0]["tool_name"] == "calculate"
    assert body["tool_events"][0]["status"] == "success"
    assert body["tool_events"][0]["result"]["result"] == 4096
    assert body["pending_approval"] is None


async def test_level2_file_create_requires_approval_and_executes_only_when_approved(phase3_client):
    client, llm, ws_dir, _ = phase3_client

    # Model proposes creating a new file in workspace/
    llm.replies = [
        '<tool_call>{"name": "workspace_create_file", "arguments": {"path": "roadmap.md", "content": "# Phase 3 Roadmap\\nAll local tools active."}}</tool_call>',
    ]
    r_chat = await client.post(
        "/api/chat",
        json={"message": "Create a file roadmap.md in the workspace.", "stream": False},
    )
    assert r_chat.status_code == 200
    data = r_chat.json()

    # File MUST NOT exist before user approval
    target_file = ws_dir / "roadmap.md"
    assert not target_file.exists()

    approval = data["pending_approval"]
    assert approval is not None
    assert approval["status"] == "pending"
    assert approval["tool_name"] == "workspace_create_file"
    assert approval["arguments"]["path"] == "roadmap.md"
    approval_id = approval["id"]

    # Approve via API -> executes bound file creation and updates conversation
    llm.replies = ["I have created `workspace/roadmap.md` as approved."]
    r_approve = await client.post(f"/api/approvals/{approval_id}/approve")
    assert r_approve.status_code == 200
    app_body = r_approve.json()
    assert app_body["approval"]["status"] == "executed"
    assert app_body["execution"]["status"] == "success"

    # File now exists with exact approved content
    assert target_file.is_file()
    assert target_file.read_text(encoding="utf-8") == "# Phase 3 Roadmap\nAll local tools active."


async def test_level2_rejected_tool_call_never_executes(phase3_client):
    client, llm, ws_dir, _ = phase3_client

    llm.replies = [
        '<tool_call>{"name": "workspace_create_file", "arguments": {"path": "rejected_notes.txt", "content": "Should never be written"}}</tool_call>',
    ]
    r_chat = await client.post(
        "/api/chat",
        json={"message": "Create rejected_notes.txt in workspace.", "stream": False},
    )
    approval_id = r_chat.json()["pending_approval"]["id"]
    target_file = ws_dir / "rejected_notes.txt"
    assert not target_file.exists()

    # Reject the approval
    r_reject = await client.post(
        f"/api/approvals/{approval_id}/reject",
        json={"reason": "Do not create this file"},
    )
    assert r_reject.status_code == 200
    assert r_reject.json()["approval"]["status"] == "rejected"
    assert not target_file.exists()

    # Attempting to approve an already-rejected approval must fail with 403
    r_approve_after = await client.post(f"/api/approvals/{approval_id}/approve")
    assert r_approve_after.status_code == 403
    assert not target_file.exists()


async def test_approval_cannot_be_reused_for_different_file_or_replayed(phase3_client):
    client, _, ws_dir, app = phase3_client
    tool_svc = app.state.tool_service

    # Request creation of file_a.txt -> returns HTTP 202 with pending_approval
    r_req = await client.post(
        "/api/tools/execute",
        json={
            "tool_name": "workspace_create_file",
            "arguments": {"path": "file_a.txt", "content": "Approved content A"},
        },
    )
    assert r_req.status_code == 202
    approval_id = r_req.json()["pending_approval"]["id"]

    # Mark approved via PermissionManager so we can test parameter tampering at execution time
    tool_svc.permission_manager.approve(approval_id)

    # 1. Attempting to use approval_id for a DIFFERENT file path must be rejected (403)
    r_tamper_path = await client.post(
        "/api/tools/execute",
        json={
            "tool_name": "workspace_create_file",
            "arguments": {"path": "file_b.txt", "content": "Approved content A"},
            "approval_id": approval_id,
        },
    )
    assert r_tamper_path.status_code == 403
    assert not (ws_dir / "file_b.txt").exists()

    # 2. Attempting to use approval_id for MODIFIED content must be rejected (403)
    r_tamper_content = await client.post(
        "/api/tools/execute",
        json={
            "tool_name": "workspace_create_file",
            "arguments": {"path": "file_a.txt", "content": "Tampered malicious content"},
            "approval_id": approval_id,
        },
    )
    assert r_tamper_content.status_code == 403
    assert not (ws_dir / "file_a.txt").exists()

    # 3. Executing with the exact approved arguments succeeds once
    r_valid = await client.post(
        "/api/tools/execute",
        json={
            "tool_name": "workspace_create_file",
            "arguments": {"path": "file_a.txt", "content": "Approved content A"},
            "approval_id": approval_id,
        },
    )
    assert r_valid.status_code == 200
    assert (ws_dir / "file_a.txt").read_text(encoding="utf-8") == "Approved content A"

    # 4. Replaying the same approval_id a second time must be rejected (403)
    r_replay = await client.post(
        "/api/tools/execute",
        json={
            "tool_name": "workspace_write_file",
            "arguments": {"path": "file_a.txt", "content": "Approved content A", "mode": "overwrite"},
            "approval_id": approval_id,
        },
    )
    assert r_replay.status_code == 403


async def test_expired_approval_is_rejected_with_410(phase3_client):
    client, _, ws_dir, app = phase3_client
    app.state.settings_service.active_settings.approval_ttl_seconds = -5

    r_req = await client.post(
        "/api/tools/execute",
        json={
            "tool_name": "workspace_create_file",
            "arguments": {"path": "expired.txt", "content": "Too late"},
        },
    )
    assert r_req.status_code == 202
    approval_id = r_req.json()["pending_approval"]["id"]

    r_approve = await client.post(f"/api/approvals/{approval_id}/approve")
    assert r_approve.status_code == 410
    assert not (ws_dir / "expired.txt").exists()


async def test_quoted_tool_call_in_user_message_is_never_executed(phase3_client):
    client, llm, ws_dir, _ = phase3_client

    # Even if the model echoes back the <tool_call> that the user quoted in their prompt,
    # the backend must refuse to execute it.
    quoted_call = '<tool_call>{"name": "workspace_create_file", "arguments": {"path": "quoted_hack.txt", "content": "should not run"}}</tool_call>'
    llm.replies = [
        quoted_call,
        "That syntax represents a JSON tool call to workspace_create_file.",
    ]

    r = await client.post(
        "/api/chat",
        json={
            "message": f"What does this syntax mean: {quoted_call} ?",
            "stream": False,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["tool_events"] == []
    assert body["pending_approval"] is None
    assert not (ws_dir / "quoted_hack.txt").exists()


async def test_tool_execution_loop_enforces_hard_call_limit_and_duplicate_guard(phase3_client):
    client, llm, _, app = phase3_client
    app.state.settings_service.active_settings.max_tool_calls_per_turn = 2

    # Case 1: Model repeatedly tries to call the exact same tool with identical arguments
    llm.replies = [
        '<tool_call>{"name": "calculate", "arguments": {"expression": "10 + 10"}}</tool_call>',
        '<tool_call>{"name": "calculate", "arguments": {"expression": "10 + 10"}}</tool_call>',
        "10 + 10 is 20.",
    ]
    r_dup = await client.post("/api/chat", json={"message": "Compute 10+10", "stream": False})
    assert r_dup.status_code == 200
    dup_events = r_dup.json()["tool_events"]
    assert dup_events[0]["status"] == "success"
    assert dup_events[1]["status"] == "duplicate_blocked"

    # Case 2: Model tries to chain more distinct tool calls than max_tool_calls_per_turn (2)
    llm.replies = [
        '<tool_call>{"name": "calculate", "arguments": {"expression": "1 + 1"}}</tool_call>',
        '<tool_call>{"name": "calculate", "arguments": {"expression": "2 + 2"}}</tool_call>',
        '<tool_call>{"name": "calculate", "arguments": {"expression": "3 + 3"}}</tool_call>',
        "Final bounded response.",
    ]
    r_bound = await client.post("/api/chat", json={"message": "Run many calculations", "stream": False})
    assert r_bound.status_code == 200
    bound_events = r_bound.json()["tool_events"]
    # At most max_tool_calls_per_turn (2) tools were executed
    executed_count = sum(1 for e in bound_events if e["status"] == "success")
    assert executed_count == 2
