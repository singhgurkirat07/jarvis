"""Phase 4 tests: Task SQLite schema, migrations, state machine, scheduling, recurrence, timezones, missed/overdue."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from backend.config import Settings
from backend.db import (
    Database,
    DuplicateTaskError,
    DuplicateTaskExecutionError,
    InvalidTaskTransitionError,
    NotFoundError,
    validate_task_transition,
)
from backend.permissions import PermissionManager
from backend.tasks import (
    TaskEngine,
    TaskScheduler,
    compute_next_occurrence,
    parse_iso_datetime,
    to_utc_iso,
    validate_recurrence,
)
from backend.tools import ToolExecutionContext, build_default_tool_registry


class DeterministicClock:
    def __init__(self, initial: datetime):
        self.current = initial if initial.tzinfo else initial.replace(tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **kwargs) -> datetime:
        self.current = self.current + timedelta(**kwargs)
        return self.current

    def set(self, dt: datetime) -> datetime:
        self.current = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        return self.current


def _make_engine(tmp_path, clock: DeterministicClock | None = None):
    db = Database(tmp_path / "jarvis_p4.db")
    ws = tmp_path / "workspace"
    ws.mkdir(exist_ok=True)
    settings = Settings(
        db_path=str(tmp_path / "jarvis_p4.db"),
        workspace_dir=str(ws),
        offline_mode=True,
        tools_enabled=True,
        scheduler_enabled=False,
    )
    pm = PermissionManager(db)
    reg = build_default_tool_registry(pm)
    clk = clock or DeterministicClock(datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc))

    def ctx_factory(cid: str | None = None) -> ToolExecutionContext:
        return ToolExecutionContext(
            settings=settings,
            workspace_root=ws,
            db=db,
            conversation_id=cid,
        )

    te = TaskEngine(
        db=db,
        settings_provider=lambda: settings,
        tool_registry=reg,
        permission_manager=pm,
        tool_context_factory=ctx_factory,
        clock=clk,
        base_backoff_seconds=10.0,
    )
    sched = TaskScheduler(te, lambda: settings)
    return db, settings, te, sched, clk


# ---- 1. Database Schema Migrations & CRUD -----------------------------------
def test_phase4_schema_migrations_recorded(tmp_path):
    db = Database(tmp_path / "mig.db")
    try:
        all_migs = db.get_schema_migrations()
        assert [m["version"] for m in all_migs] == [1, 2, 3, 4]
        assert all_migs[3]["name"] == "004_phase4_tasks_executions_notifications"
        # Phase 2 backwards-compatible helper still returns [1, 2]
        assert [m["version"] for m in db.get_migrations()] == [1, 2]
    finally:
        db.close()


def test_task_crud_and_execution_history_persistence(tmp_path):
    db, _, te, _, clk = _make_engine(tmp_path)
    try:
        sched_time = clk() + timedelta(hours=2)
        created = te.create_task(
            title="Study Dynamic Programming",
            description="Review knapsack and LCS problems",
            task_type="reminder",
            priority=4,
            scheduled_at=sched_time,
            deadline_at=sched_time + timedelta(hours=4),
            timezone_name="Asia/Kolkata",
        )
        tid = created["id"]
        assert created["title"] == "Study Dynamic Programming"
        assert created["task_type"] == "reminder"
        assert created["priority"] == 4
        assert created["state"] == "Scheduled"
        assert created["timezone"] == "Asia/Kolkata"
        assert created["retry_count"] == 0

        fetched = te.get_task(tid)
        assert fetched["id"] == tid
        assert fetched["description"] == "Review knapsack and LCS problems"

        # Update task priority and description
        updated = db.update_task(tid, priority=5, description="Updated description")
        assert updated["priority"] == 5
        assert updated["description"] == "Updated description"

        # Execution history persistence
        ex = db.create_task_execution(task_id=tid, retry_number=0, outcome="running")
        fin = db.finish_task_execution(
            ex["id"],
            outcome="completed",
            tool_calls=[{"tool_name": "calculate", "result": 42}],
            output_summary="Calculated answer 42",
            output_refs=["workspace/notes.md"],
            resource_usage={"duration_ms": 4.2, "used_model": False},
        )
        assert fin["outcome"] == "completed"
        assert fin["output_summary"] == "Calculated answer 42"
        assert fin["output_refs"] == ["workspace/notes.md"]
        assert fin["resource_usage"]["used_model"] is False

        history = db.list_task_executions(task_id=tid)
        assert len(history) == 1
        assert history[0]["id"] == ex["id"]

        # Delete task cascades to task_executions
        assert db.delete_task(tid) is True
        with pytest.raises(NotFoundError):
            te.get_task(tid)
        assert db.list_task_executions(task_id=tid) == []
    finally:
        db.close()


def test_task_state_machine_validation_and_invalid_transitions_rejected(tmp_path):
    db, _, te, _, clk = _make_engine(tmp_path)
    try:
        t = te.create_task(
            title="State Machine Test",
            task_type="reminder",
            scheduled_at=clk() + timedelta(minutes=30),
        )
        tid = t["id"]
        assert t["state"] == "Scheduled"

        # Scheduled -> Paused -> Scheduled -> Running -> Completed is valid
        t = db.transition_task_state(tid, "Paused")
        assert t["state"] == "Paused"
        t = db.transition_task_state(tid, "Scheduled")
        assert t["state"] == "Scheduled"
        t = db.transition_task_state(tid, "Running")
        assert t["state"] == "Running"
        t = db.transition_task_state(tid, "Completed")
        assert t["state"] == "Completed"

        # Completed is terminal: cannot transition to Running, Paused, or Cancelled
        for invalid_next in ("Running", "Paused", "Cancelled", "Queued"):
            with pytest.raises(InvalidTaskTransitionError):
                db.transition_task_state(tid, invalid_next)

        # Cancelled is also terminal
        t2 = te.create_task(
            title="Cancel Terminal Test",
            task_type="reminder",
            scheduled_at=clk() + timedelta(hours=1),
        )
        te.cancel_task(t2["id"])
        for invalid_next in ("Scheduled", "Running", "Completed", "Paused"):
            with pytest.raises(InvalidTaskTransitionError):
                db.transition_task_state(t2["id"], invalid_next)

        # Draft cannot jump directly to Completed
        with pytest.raises(InvalidTaskTransitionError):
            validate_task_transition("Draft", "Completed")
    finally:
        db.close()


def test_duplicate_active_task_creation_prevented(tmp_path):
    db, _, te, _, clk = _make_engine(tmp_path)
    try:
        when = clk() + timedelta(hours=1)
        t1 = te.create_task(
            title="Pay electricity bill",
            task_type="reminder",
            scheduled_at=when,
        )
        assert t1["state"] == "Scheduled"

        # Exact same active task should be rejected as a duplicate
        with pytest.raises(DuplicateTaskError):
            te.create_task(
                title="  Pay   electricity bill ",
                task_type="reminder",
                scheduled_at=when,
            )

        # Once the first task is Cancelled, creating it again is permitted
        te.cancel_task(t1["id"])
        t2 = te.create_task(
            title="Pay electricity bill",
            task_type="reminder",
            scheduled_at=when,
        )
        assert t2["id"] != t1["id"]
        assert t2["state"] == "Scheduled"
    finally:
        db.close()


# ---- 2. Scheduling, Recurrence, Timezones, Missed & Overdue -----------------
def test_one_time_and_daily_weekly_monthly_recurrence_calculations():
    tz = "Asia/Kolkata"
    kolkata = ZoneInfo(tz)
    start_local = datetime(2026, 1, 31, 9, 30, 0, tzinfo=kolkata)

    # 1. One-time
    nxt, missed = compute_next_occurrence(start_local, {"type": "none"}, tz)
    assert nxt is None
    assert missed == 0

    # 2. Daily recurrence preserves 09:30 Asia/Kolkata
    nxt_daily, missed = compute_next_occurrence(start_local, {"type": "daily", "interval": 1}, tz)
    assert missed == 0
    assert nxt_daily is not None
    assert nxt_daily.astimezone(kolkata) == datetime(2026, 2, 1, 9, 30, 0, tzinfo=kolkata)

    # 3. Weekly recurrence (+7 days)
    nxt_weekly, missed = compute_next_occurrence(start_local, {"type": "weekly", "interval": 1}, tz)
    assert missed == 0
    assert nxt_weekly is not None
    assert nxt_weekly.astimezone(kolkata) == datetime(2026, 2, 7, 9, 30, 0, tzinfo=kolkata)

    # 4. Monthly recurrence clamps Jan 31 -> Feb 28 (non-leap year 2026) at 09:30 Asia/Kolkata
    nxt_monthly, missed = compute_next_occurrence(start_local, {"type": "monthly", "interval": 1}, tz)
    assert missed == 0
    assert nxt_monthly is not None
    assert nxt_monthly.astimezone(kolkata) == datetime(2026, 2, 28, 9, 30, 0, tzinfo=kolkata)

    # Invalid recurrence rejected
    with pytest.raises(ValueError):
        validate_recurrence({"type": "hourly"})


def test_missed_recurring_executions_coalesce_without_backlog_flood(tmp_path):
    """If Jarvis was closed for 5 days, a daily reminder must fire ONCE and schedule the next future run."""
    clk = DeterministicClock(datetime(2026, 11, 1, 8, 0, 0, tzinfo=timezone.utc))
    db, _, te, sched, _ = _make_engine(tmp_path, clock=clk)
    try:
        due_first = datetime(2026, 11, 1, 9, 0, 0, tzinfo=timezone.utc)
        task = te.create_task(
            title="Daily morning standup reminder",
            task_type="reminder",
            scheduled_at=due_first,
            timezone_name="UTC",
            recurrence={"type": "daily", "interval": 1},
        )
        tid = task["id"]

        # Advance clock by 5 days + 2 hours (simulating PC off for 5 days)
        clk.set(datetime(2026, 11, 6, 11, 0, 0, tzinfo=timezone.utc))

        tick_res = sched.tick()
        assert tick_res["executed_count"] == 1

        updated = te.get_task(tid)
        assert updated["state"] == "Scheduled"
        # Next run must be strictly in the future: 2026-11-07 09:00:00+00:00 (NOT 2026-11-02!)
        next_dt = parse_iso_datetime(updated["next_run_at"])
        assert next_dt == datetime(2026, 11, 7, 9, 0, 0, tzinfo=timezone.utc)
        assert updated["metadata"]["was_missed_last_run"] is True
        assert updated["metadata"]["coalesced_missed_occurrences"] == 5

        # Running another tick 1 minute later must execute 0 tasks (no backlog flood!)
        clk.advance(minutes=1)
        tick_res_2 = sched.tick()
        assert tick_res_2["executed_count"] == 0
        assert len(db.list_task_executions(task_id=tid)) == 1
    finally:
        db.close()


def test_overdue_detection_and_notification_deduplication(tmp_path):
    clk = DeterministicClock(datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc))
    db, _, te, sched, _ = _make_engine(tmp_path, clock=clk)
    try:
        t = te.create_task(
            title="Submit assignment report",
            task_type="reminder",
            scheduled_at=clk() + timedelta(hours=5),
            deadline_at=clk() + timedelta(hours=1),
        )
        # Advance past deadline_at (1.5 hours) but before scheduled_at
        clk.advance(minutes=90)
        res1 = sched.tick()
        assert res1["overdue_count"] == 1

        updated = te.get_task(t["id"])
        assert updated["metadata"].get("overdue") is True

        notifs = db.list_notifications(task_id=t["id"])
        assert len(notifs) == 1
        assert notifs[0]["kind"] == "task_overdue"

        # Subsequent ticks do not create duplicate overdue notifications
        clk.advance(minutes=10)
        sched.tick()
        assert len(db.list_notifications(task_id=t["id"])) == 1
    finally:
        db.close()


def test_scheduler_restart_persistence_and_recovery(tmp_path):
    clk = DeterministicClock(datetime(2026, 11, 1, 12, 0, 0, tzinfo=timezone.utc))
    db1, _, te1, _, _ = _make_engine(tmp_path, clock=clk)
    due = clk() + timedelta(minutes=30)
    created = te1.create_task(
        title="Persist across restart reminder",
        task_type="reminder",
        scheduled_at=due,
        timezone_name="Asia/Kolkata",
    )
    tid = created["id"]
    db1.close()

    # Reopen a fresh Database + TaskEngine + TaskScheduler on the same SQLite file
    db2, _, te2, sched2, _ = _make_engine(tmp_path, clock=clk)
    try:
        recovered = te2.get_task(tid)
        assert recovered["title"] == "Persist across restart reminder"
        assert recovered["state"] == "Scheduled"
        assert recovered["timezone"] == "Asia/Kolkata"

        # Advance clock past due time and tick scheduler
        clk.advance(minutes=35)
        tick_out = sched2.tick()
        assert tick_out["executed_count"] == 1

        after = te2.get_task(tid)
        assert after["state"] == "Completed"
        notifs = db2.list_notifications(task_id=tid)
        assert len(notifs) == 1
        assert notifs[0]["kind"] == "reminder_due"
    finally:
        db2.close()
