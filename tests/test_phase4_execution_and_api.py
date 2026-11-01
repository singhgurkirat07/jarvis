"""Phase 4 tests: Task Execution, Permissions, Recovery, Notifications, Chat Integration & FastAPI Endpoints."""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest

from backend.config import Settings
from backend.db import (
    Database,
    DuplicateTaskExecutionError,
    InvalidTaskTransitionError,
)
from backend.permissions import PermissionLevel, PermissionManager
from backend.tasks import TaskEngine, TaskScheduler
from backend.tools import (
    ToolExecutionContext,
    ToolRegistry,
    ToolSpec,
    ToolTimeoutError,
    build_default_tool_registry,
)


class DeterministicClock:
    def __init__(self, initial: datetime | None = None):
        self.current = initial or datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **kwargs: float) -> datetime:
        self.current = self.current + timedelta(**kwargs)
        return self.current


class FailingEngineSpy:
    """Spy LLM engine that fails if called, proving reminders & tool tasks do not invoke the model unless requested."""

    def __init__(self):
        self.ready = True
        self.calls = 0

    def generate(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        raise AssertionError("LLM engine should not be invoked for reminder triggering!")


@pytest.fixture()
def phase4_env(tmp_path: Path):
    db_path = tmp_path / "jarvis_phase4_exec.db"
    ws_path = tmp_path / "workspace"
    ws_path.mkdir(parents=True, exist_ok=True)
    (ws_path / "notes.txt").write_text("Alpha\nBeta\nGamma\n", encoding="utf-8")

    db = Database(db_path)
    clock = DeterministicClock()
    settings = Settings(
        db_path=str(db_path),
        workspace_dir=str(ws_path),
        offline_mode=True,
        scheduler_enabled=False,
        max_concurrent_tasks=2,
        task_lease_seconds=60,
        missed_execution_threshold_seconds=120,
        approval_ttl_seconds=300,
    )
    pm = PermissionManager(db)
    registry = build_default_tool_registry(pm)
    spy_engine = FailingEngineSpy()

    search_provider_holder: dict[str, Any] = {"provider": None}

    def ctx_factory(conv_id: str | None = None) -> ToolExecutionContext:
        return ToolExecutionContext(
            settings=settings,
            workspace_root=ws_path,
            db=db,
            search_provider=search_provider_holder["provider"],
            conversation_id=conv_id,
        )

    task_engine = TaskEngine(
        db=db,
        settings_provider=lambda: settings,
        tool_registry=registry,
        permission_manager=pm,
        tool_context_factory=ctx_factory,
        engine=spy_engine,
        clock=clock,
        base_backoff_seconds=5.0,
    )
    scheduler = TaskScheduler(task_engine, lambda: settings)
    yield {
        "tmp_path": tmp_path,
        "db_path": db_path,
        "ws_path": ws_path,
        "db": db,
        "clock": clock,
        "settings": settings,
        "registry": registry,
        "pm": pm,
        "spy_engine": spy_engine,
        "task_engine": task_engine,
        "scheduler": scheduler,
        "ctx_factory": ctx_factory,
        "search_provider_holder": search_provider_holder,
    }
    scheduler.stop()
    db.close()


# ==============================================================================
# 1. Execution, Timeout, Retry, Checkpoint, Pause/Resume/Cancel & Recovery Tests
# ==============================================================================
def test_reminder_triggers_without_model_inference_and_supports_snooze_and_complete(phase4_env):
    te: TaskEngine = phase4_env["task_engine"]
    sched: TaskScheduler = phase4_env["scheduler"]
    clock: DeterministicClock = phase4_env["clock"]
    spy: FailingEngineSpy = phase4_env["spy_engine"]
    db: Database = phase4_env["db"]

    due_at = clock() + timedelta(minutes=10)
    rem = te.create_task(
        title="Study DSA graphs",
        description="Revise shortest path algorithms",
        task_type="reminder",
        scheduled_at=due_at,
        timezone_name="Asia/Kolkata",
    )
    assert rem["state"] == "Scheduled"

    # Advance clock past due_at and tick scheduler
    clock.advance(minutes=11)
    tick_res = sched.tick()
    assert tick_res["executed_count"] == 1
    assert spy.calls == 0  # Zero LLM calls!

    updated = te.get_task(rem["id"])
    assert updated["state"] == "Completed"
    assert len(updated["executions"]) == 1
    assert updated["executions"][0]["outcome"] == "completed"
    assert updated["executions"][0]["resource_usage"]["used_model"] is False

    notifs = db.list_notifications(task_id=rem["id"])
    assert len(notifs) == 1
    assert notifs[0]["kind"] == "reminder_due"

    # Snooze completed reminder for 15 minutes
    snoozed = te.snooze_reminder(rem["id"], minutes=15)
    assert snoozed["state"] == "Scheduled"
    assert snoozed["next_run_at"] > clock().isoformat()

    # Complete/dismiss directly
    completed = te.complete_or_dismiss_reminder(rem["id"])
    assert completed["state"] == "Completed"
    assert completed["next_run_at"] is None


def test_local_task_execution_with_phase3_tools(phase4_env):
    te: TaskEngine = phase4_env["task_engine"]
    clock: DeterministicClock = phase4_env["clock"]

    task = te.create_task(
        title="Calculate and read workspace file",
        task_type="local_task",
        action={
            "steps": [
                {"tool_name": "calculate", "arguments": {"expression": "12 * 12 + 6"}},
                {"tool_name": "workspace_read_file", "arguments": {"path": "notes.txt"}},
            ]
        },
        scheduled_at=clock(),
    )
    res = te.execute_task(task["id"])
    assert res["task"]["state"] == "Completed"
    assert res["execution"]["outcome"] == "completed"
    assert len(res["execution"]["tool_calls"]) == 2
    assert "150" in res["execution"]["output_summary"]
    assert "workspace/notes.txt" in res["execution"]["output_refs"]


def test_task_retry_backoff_and_exhaustion_to_failed(phase4_env):
    te: TaskEngine = phase4_env["task_engine"]
    clock: DeterministicClock = phase4_env["clock"]
    db: Database = phase4_env["db"]

    # Reading a non-existent file with max_retries=2 will retry twice with backoff and then fail
    task = te.create_task(
        title="Read missing report",
        task_type="local_task",
        action={"tool_name": "workspace_read_file", "arguments": {"path": "missing_file.txt"}},
        max_retries=2,
        scheduled_at=clock(),
    )

    # Attempt 1 -> Scheduled for retry 1 in 5s
    r1 = te.execute_task(task["id"])
    assert r1["task"]["state"] == "Scheduled"
    assert r1["task"]["retry_count"] == 1
    assert r1["execution"]["outcome"] == "failed"

    # Attempt 2 -> Scheduled for retry 2 in 10s
    clock.advance(seconds=6)
    r2 = te.execute_task(task["id"])
    assert r2["task"]["state"] == "Scheduled"
    assert r2["task"]["retry_count"] == 2

    # Attempt 3 (exhausts max_retries=2) -> Failed
    clock.advance(seconds=11)
    r3 = te.execute_task(task["id"])
    assert r3["task"]["state"] == "Failed"
    assert r3["execution"]["outcome"] == "failed"

    execs = db.list_task_executions(task_id=task["id"])
    assert len(execs) == 3
    notifs = db.list_notifications(task_id=task["id"])
    assert any(n["kind"] == "task_failed" for n in notifs)


def test_task_timeout_handling(phase4_env):
    te: TaskEngine = phase4_env["task_engine"]
    reg: ToolRegistry = phase4_env["registry"]
    clock: DeterministicClock = phase4_env["clock"]

    def slow_tool(args: dict[str, Any], ctx: ToolExecutionContext) -> dict[str, Any]:
        for _ in range(20):
            if ctx.cancel_event and ctx.cancel_event.is_set():
                break
            time.sleep(0.05)
        return {"ok": True}

    reg.register(
        ToolSpec(
            name="slow_test_tool",
            description="Slow test tool for timeout verification.",
            parameters_schema={"type": "object", "properties": {}, "required": []},
            validator=lambda a, _c: dict(a),
            executor=slow_tool,
            permission_level=PermissionLevel.SAFE_READ,
            timeout_s=5.0,
        )
    )

    task = te.create_task(
        title="Slow bounded task",
        task_type="local_task",
        action={"tool_name": "slow_test_tool", "arguments": {}},
        timeout_seconds=0.15,
        max_retries=0,
        scheduled_at=clock(),
    )
    res = te.execute_task(task["id"])
    assert res["task"]["state"] == "Failed"
    assert res["execution"]["outcome"] == "timed_out"


def test_checkpoint_recovery_skips_already_completed_steps(phase4_env):
    te: TaskEngine = phase4_env["task_engine"]
    reg: ToolRegistry = phase4_env["registry"]
    clock: DeterministicClock = phase4_env["clock"]

    step_counts = {"step0": 0, "step1": 0}

    def step0_handler(args: dict[str, Any], ctx: ToolExecutionContext) -> dict[str, Any]:
        step_counts["step0"] += 1
        return {"step0_done": True}

    def step1_flaky_handler(args: dict[str, Any], ctx: ToolExecutionContext) -> dict[str, Any]:
        step_counts["step1"] += 1
        if step_counts["step1"] == 1:
            raise RuntimeError("Transient error on step 1")
        return {"step1_done": True}

    reg.register(
        ToolSpec(
            name="test_step0",
            description="Step 0 tool",
            parameters_schema={"type": "object", "properties": {}, "required": []},
            validator=lambda a, _c: dict(a),
            executor=step0_handler,
            permission_level=PermissionLevel.SAFE_READ,
            timeout_s=5.0,
        )
    )
    reg.register(
        ToolSpec(
            name="test_step1_flaky",
            description="Step 1 flaky tool",
            parameters_schema={"type": "object", "properties": {}, "required": []},
            validator=lambda a, _c: dict(a),
            executor=step1_flaky_handler,
            permission_level=PermissionLevel.SAFE_READ,
            timeout_s=5.0,
        )
    )

    task = te.create_task(
        title="Two-step checkpointed task",
        task_type="local_task",
        action={
            "steps": [
                {"tool_name": "test_step0", "arguments": {}},
                {"tool_name": "test_step1_flaky", "arguments": {}},
            ]
        },
        max_retries=2,
        scheduled_at=clock(),
    )

    # First run: step 0 succeeds and checkpoints; step 1 fails -> Scheduled for retry
    r1 = te.execute_task(task["id"])
    assert r1["task"]["state"] == "Scheduled"
    assert r1["task"]["checkpoint"]["completed_steps"] == 1
    assert step_counts == {"step0": 1, "step1": 1}

    # Second run: resumes from checkpoint (completed_steps == 1), so step0 is NOT re-run!
    clock.advance(seconds=10)
    r2 = te.execute_task(task["id"])
    assert r2["task"]["state"] == "Completed"
    assert r2["task"]["checkpoint"] is None
    assert step_counts == {"step0": 1, "step1": 2}


def test_pause_resume_and_cancel_confirmation_for_running_task(phase4_env):
    te: TaskEngine = phase4_env["task_engine"]
    db: Database = phase4_env["db"]
    clock: DeterministicClock = phase4_env["clock"]

    t = te.create_task(
        title="Scheduled analysis",
        task_type="local_task",
        action={"tool_name": "calculate", "arguments": {"expression": "10 + 20"}},
        scheduled_at=clock() + timedelta(hours=1),
    )
    assert t["state"] == "Scheduled"

    paused = te.pause_task(t["id"], reason="Hold briefly")
    assert paused["state"] == "Paused"

    resumed = te.resume_task(t["id"])
    assert resumed["state"] == "Scheduled"

    # Simulate a task currently in 'Running' state and verify cancel requires confirm=True
    db.acquire_task_lease(
        t["id"],
        lease_owner="worker-test",
        lease_expires_at=(clock() + timedelta(minutes=5)).isoformat(),
        now_iso=clock().isoformat(),
    )
    with pytest.raises(ValueError, match="confirm=true"):
        te.cancel_task(t["id"], confirm=False)

    cancelled = te.cancel_task(t["id"], confirm=True, reason="User confirmed stop")
    assert cancelled["state"] == "Cancelled"


def test_duplicate_execution_prevention_and_crash_recovery_mutating_guard(phase4_env):
    te: TaskEngine = phase4_env["task_engine"]
    db: Database = phase4_env["db"]
    clock: DeterministicClock = phase4_env["clock"]

    # 1. Read-only task interrupted in 'Running' state recovers safely to 'Scheduled'
    ro_task = te.create_task(
        title="Read-only interrupted task",
        task_type="local_task",
        action={"tool_name": "workspace_list_files", "arguments": {}},
        scheduled_at=clock(),
    )
    db.acquire_task_lease(
        ro_task["id"],
        lease_owner="worker-1",
        lease_expires_at=(clock() + timedelta(minutes=1)).isoformat(),
        now_iso=clock().isoformat(),
    )
    # Concurrent lease acquisition while active must fail with DuplicateTaskExecutionError
    with pytest.raises(DuplicateTaskExecutionError):
        db.acquire_task_lease(
            ro_task["id"],
            lease_owner="worker-2",
            lease_expires_at=(clock() + timedelta(minutes=2)).isoformat(),
            now_iso=clock().isoformat(),
        )

    # 2. Mutating task interrupted in 'Running' state before checkpointing must NOT blindly retry
    mut_task = te.create_task(
        title="Mutating interrupted task",
        task_type="local_task",
        action={"tool_name": "workspace_write_file", "arguments": {"path": "out.txt", "content": "data"}},
        scheduled_at=clock(),
    )
    te.approve_task(mut_task["id"])
    db.acquire_task_lease(
        mut_task["id"],
        lease_owner="worker-1",
        lease_expires_at=(clock() + timedelta(minutes=1)).isoformat(),
        now_iso=clock().isoformat(),
    )

    # Run startup crash recovery
    recovered = te.recover_tasks(startup=True)
    rec_by_id = {r["id"]: r for r in recovered}
    assert rec_by_id[ro_task["id"]]["state"] == "Scheduled"
    assert rec_by_id[mut_task["id"]]["state"] == "WaitingForUser"
    assert "Automatic retry was blocked" in rec_by_id[mut_task["id"]]["last_error"]


# ==============================================================================
# 2. Permission Revalidation & Offline Research Task Tests
# ==============================================================================
def test_mutating_task_approval_rejection_expiration_and_recurring_reapproval(phase4_env):
    te: TaskEngine = phase4_env["task_engine"]
    clock: DeterministicClock = phase4_env["clock"]
    ws_path: Path = phase4_env["ws_path"]

    # A. Rejecting a Level 2 task transitions it to Cancelled
    t_reject = te.create_task(
        title="Create rejected file",
        task_type="local_task",
        action={"tool_name": "workspace_create_file", "arguments": {"path": "rejected.txt", "content": "no"}},
        scheduled_at=clock(),
    )
    assert t_reject["state"] == "AwaitingApproval"
    rej = te.reject_task_approval(t_reject["id"], reason="Do not create this file")
    assert rej["state"] == "Cancelled"
    assert not (ws_path / "rejected.txt").exists()

    # B. Expired approval at execution time blocks execution and issues a fresh approval request
    t_exp = te.create_task(
        title="Write file after long delay",
        task_type="local_task",
        action={"tool_name": "workspace_create_file", "arguments": {"path": "delayed.txt", "content": "hello"}},
        scheduled_at=clock() + timedelta(hours=2),
    )
    first_approval_id = t_exp["approval_id"]
    te.approve_task(t_exp["id"])
    assert te.get_task(t_exp["id"])["state"] == "Scheduled"

    # Advance clock past the approval TTL (3600s) and attempt execution
    clock.advance(hours=2)
    res_exp = te.execute_task(t_exp["id"])
    assert res_exp["task"]["state"] == "AwaitingApproval"
    assert res_exp["execution"]["outcome"] == "awaiting_approval"
    assert res_exp["task"]["approval_id"] != first_approval_id
    assert not (ws_path / "delayed.txt").exists()

    # Approve the fresh approval request and execute immediately -> succeeds!
    te.approve_task(t_exp["id"])
    res_ok = te.execute_task(t_exp["id"])
    assert res_ok["task"]["state"] == "Completed"
    assert (ws_path / "delayed.txt").read_text(encoding="utf-8") == "hello"

    # C. Recurring mutating task requires fresh approval for its next occurrence
    t_rec = te.create_task(
        title="Daily log write",
        task_type="local_task",
        action={
            "tool_name": "workspace_write_file",
            "arguments": {"path": "daily.log", "content": "entry"},
        },
        recurrence={"type": "daily", "interval": 1},
        scheduled_at=clock(),
    )
    assert t_rec["state"] == "AwaitingApproval"
    te.approve_task(t_rec["id"])
    r_rec = te.execute_task(t_rec["id"])
    assert r_rec["execution"]["outcome"] == "completed"
    # Must transition to AwaitingApproval (not Scheduled) for the next daily run!
    assert r_rec["task"]["state"] == "AwaitingApproval"
    assert r_rec["task"]["approval_id"] != t_rec["approval_id"]


def test_research_task_pauses_in_offline_mode_and_runs_when_online(phase4_env):
    from backend import offline

    te: TaskEngine = phase4_env["task_engine"]
    settings: Settings = phase4_env["settings"]
    clock: DeterministicClock = phase4_env["clock"]
    db: Database = phase4_env["db"]

    settings.offline_mode = True
    rt = te.create_task(
        title="Research Python 3.12 release notes",
        task_type="research_task",
        action={"query": "Python 3.12 release notes"},
        scheduled_at=clock(),
    )
    assert rt["state"] == "Scheduled"

    # Execute while offline_mode=True -> Paused with paused_offline notification; offline_mode stays True
    res_off = te.execute_task(rt["id"])
    assert res_off["task"]["state"] == "Paused"
    assert res_off["execution"]["outcome"] == "paused_offline"
    assert settings.offline_mode is True
    notifs = db.list_notifications(task_id=rt["id"])
    assert any(n["kind"] == "paused_offline" for n in notifs)

    class FakeSearchProvider:
        name = "fake_search"

        def search(self, query: str, max_results: int = 5) -> list[dict[str, Any]]:
            return [
                {
                    "rank": 1,
                    "title": "Python 3.12 Docs",
                    "url": "https://docs.python.org/3.12/",
                    "snippet": "Release notes",
                    "retrieved_at": clock().isoformat(),
                }
            ]

    phase4_env["search_provider_holder"]["provider"] = FakeSearchProvider()
    was_enforced = offline.is_enabled()
    settings.offline_mode = False
    settings.enforce_offline = False
    offline.disable()
    try:
        te.resume_task(rt["id"])
        res_on = te.execute_task(rt["id"])
        assert res_on["task"]["state"] == "Completed"
        assert res_on["execution"]["outcome"] == "completed"
        assert "https://docs.python.org/3.12/" in res_on["execution"]["output_refs"]
    finally:
        settings.offline_mode = True
        settings.enforce_offline = True
        if was_enforced:
            offline.enable()


# ==============================================================================
# 3. Persistent Notifications & Deduplication Tests
# ==============================================================================
def test_notifications_persistence_deduplication_read_and_dismiss(tmp_path: Path):
    db_path = tmp_path / "notif_test.db"
    db1 = Database(db_path)
    n1 = db1.create_notification(
        kind="reminder_due",
        title="Reminder: Drink water",
        message="Stay hydrated",
        dedup_key="rem:water:1",
    )
    # Duplicate dedup_key returns the same notification without inserting a duplicate
    n_dup = db1.create_notification(
        kind="reminder_due",
        title="Reminder: Drink water",
        message="Stay hydrated",
        dedup_key="rem:water:1",
    )
    assert n_dup["id"] == n1["id"]
    n2 = db1.create_notification(
        kind="task_completed",
        title="Task finished",
        message="Done",
        dedup_key="task:done:2",
    )
    assert db1.count_unread_notifications() == 2
    db1.close()

    # Reopen DB to verify persistence across restarts
    db2 = Database(db_path)
    all_notifs = db2.list_notifications()
    assert len(all_notifs) == 2
    assert db2.count_unread_notifications() == 2

    read_n1 = db2.mark_notification_read(n1["id"])
    assert read_n1["read_at"] is not None
    assert db2.count_unread_notifications() == 1

    db2.mark_all_notifications_read()
    assert db2.count_unread_notifications() == 0

    db2.dismiss_notification(n2["id"])
    visible = db2.list_notifications(include_dismissed=False)
    assert len(visible) == 1
    assert visible[0]["id"] == n1["id"]
    db2.close()


# ==============================================================================
# 4. Chat Integration & FastAPI API Endpoint Tests
# ==============================================================================
async def test_chat_task_commands_and_rest_api_endpoints(tmp_path: Path, monkeypatch):
    from backend.app import create_app
    from backend.engine import Engine

    db_path = tmp_path / "api_phase4.db"
    ws_path = tmp_path / "workspace"
    ws_path.mkdir(parents=True, exist_ok=True)

    test_settings = Settings(
        db_path=str(db_path),
        workspace_dir=str(ws_path),
        offline_mode=True,
        scheduler_enabled=False,
    )
    fake_engine = Engine(test_settings)
    fake_engine.status.state = "ready"
    monkeypatch.setattr(fake_engine, "close", lambda: None)

    def fake_generate(messages: list[dict[str, str]], **kwargs: Any) -> dict[str, Any]:
        return {
            "content": "Good luck with your assignment tomorrow!",
            "prompt_tokens": 20,
            "completion_tokens": 10,
            "total_tokens": 30,
            "duration_ms": 12.5,
            "tokens_per_second": 80.0,
        }

    monkeypatch.setattr(fake_engine, "generate", fake_generate)
    app = create_app(settings=test_settings, engine=fake_engine, load_model=False)

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
            # 1. Status endpoint reports Phase 4 task & notification fields
            st = (await client.get("/api/status")).json()
            assert st["state"] == "ready"
            assert "active_tasks_count" in st
            assert "unread_notifications_count" in st

            # 2. Chat: "Remind me to study DSA at 7 PM."
            r_rem1 = await client.post(
                "/api/chat",
                json={"message": "Remind me to study DSA at 7 PM.", "stream": False},
            )
            assert r_rem1.status_code == 200
            d_rem1 = r_rem1.json()
            assert "reminder scheduled" in d_rem1["content"].lower()
            assert "study dsa" in d_rem1["content"].lower()

            # 3. Chat: "Create a reminder every Sunday at 10 AM to review my progress."
            r_rem2 = await client.post(
                "/api/chat",
                json={
                    "message": "Create a reminder every Sunday at 10 AM to review my progress.",
                    "stream": False,
                },
            )
            assert r_rem2.status_code == 200
            assert "weekly" in r_rem2.json()["content"].lower()

            # 4. Create a research task via REST API so we can pause/resume it via chat
            r_create_rt = await client.post(
                "/api/tasks",
                json={
                    "title": "AI safety research task",
                    "task_type": "research_task",
                    "action": {"query": "AI alignment benchmarks"},
                    "scheduled_at": (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat(),
                },
            )
            assert r_create_rt.status_code in (200, 201)
            rt_id = r_create_rt.json()["task"]["id"]

            # 5. Chat: "Show my pending tasks."
            r_list = await client.post(
                "/api/chat",
                json={"message": "Show my pending tasks.", "stream": False},
            )
            assert r_list.status_code == 200
            assert "study DSA" in r_list.json()["content"]
            assert "AI safety research task" in r_list.json()["content"]

            # 6. Chat: "Pause the research task."
            r_pause = await client.post(
                "/api/chat",
                json={"message": "Pause the research task.", "stream": False},
            )
            assert r_pause.status_code == 200
            assert "Task Paused" in r_pause.json()["content"]
            assert (await client.get(f"/api/tasks/{rt_id}")).json()["task"]["state"] == "Paused"

            # 7. Chat: "Resume my paused task."
            r_resume = await client.post(
                "/api/chat",
                json={"message": "Resume my paused task.", "stream": False},
            )
            assert r_resume.status_code == 200
            assert "Task Resumed" in r_resume.json()["content"]
            assert (await client.get(f"/api/tasks/{rt_id}")).json()["task"]["state"] == "Scheduled"

            # 8. Chat: "Cancel the reminder I created yesterday."
            r_cancel = await client.post(
                "/api/chat",
                json={"message": "Cancel the reminder I created yesterday.", "stream": False},
            )
            assert r_cancel.status_code == 200
            assert "Task Cancelled" in r_cancel.json()["content"]

            # 9. Ambiguous schedule in chat asks for confirmation in user's timezone without creating a task
            tasks_before = len((await client.get("/api/tasks")).json()["tasks"])
            r_ambig = await client.post(
                "/api/chat",
                json={"message": "Remind me to call Alex", "stream": False},
            )
            assert r_ambig.status_code == 200
            assert "Schedule Confirmation Needed" in r_ambig.json()["content"]
            assert len((await client.get("/api/tasks")).json()["tasks"]) == tasks_before

            # 10. Ordinary chat statement ("I have an assignment due tomorrow.") does NOT invent a task
            r_ordinary = await client.post(
                "/api/chat",
                json={"message": "I have an assignment due tomorrow.", "stream": False},
            )
            assert r_ordinary.status_code == 200
            assert len((await client.get("/api/tasks")).json()["tasks"]) == tasks_before

            # 11. Exercise REST endpoints: snooze, complete, run, scheduler/tick, notifications
            rem_list = (await client.get("/api/tasks?task_type=reminder")).json()["tasks"]
            rem_id = rem_list[0]["id"]
            r_snooze = await client.post(f"/api/tasks/{rem_id}/snooze", json={"minutes": 15})
            assert r_snooze.status_code == 200
            assert r_snooze.json()["task"]["state"] == "Scheduled"

            r_run = await client.post(f"/api/tasks/{rem_id}/run")
            assert r_run.status_code == 200
            assert r_run.json()["execution"]["outcome"] == "completed"

            r_tick = await client.post("/api/scheduler/tick")
            assert r_tick.status_code == 200
            assert "evaluated_at" in r_tick.json()

            notifs_resp = (await client.get("/api/notifications")).json()
            assert notifs_resp["unread_count"] >= 1
            nid = notifs_resp["notifications"][0]["id"]
            assert (await client.post(f"/api/notifications/{nid}/read")).status_code == 200
            assert (await client.post("/api/notifications/read-all")).status_code == 200
            assert (await client.post(f"/api/notifications/{nid}/dismiss")).status_code == 200
