"""Phase 4 Persistent Task Engine, Scheduler, Recurrence Calculator, Recovery & Chat Task Parser."""
from __future__ import annotations

import calendar
import json
import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import Settings
from .db import (
    ACTIVE_TASK_STATES,
    TASK_STATES,
    TASK_TYPES,
    TERMINAL_TASK_STATES,
    Database,
    DuplicateTaskError,
    DuplicateTaskExecutionError,
    InvalidTaskTransitionError,
    NotFoundError,
    validate_task_transition,
)
from .permissions import (
    ApprovalExpiredError,
    ApprovalMismatchError,
    ApprovalRejectedError,
    ApprovalRequiredError,
    OfflineModeError,
    PermissionDeniedError,
    PermissionLevel,
    PermissionManager,
)
from .tools import (
    ToolCancelledError,
    ToolError,
    ToolExecutionContext,
    ToolExecutionError,
    ToolRegistry,
    ToolTimeoutError,
    ToolValidationError,
    UnknownToolError,
    resolve_timezone_info,
    strip_tool_call_tags,
)

log = logging.getLogger("jarvis.tasks")

VALID_RECURRENCE_TYPES = ("none", "daily", "weekly", "monthly")
WEEKDAY_MAP = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}
WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def get_system_timezone_name() -> str:
    """Return an IANA or UTC-offset timezone name representing the local system clock."""
    local_now = datetime.now().astimezone()
    tz = local_now.tzinfo
    if isinstance(tz, ZoneInfo):
        return tz.key
    tz_key = getattr(tz, "key", None)
    if tz_key:
        return str(tz_key)
    offset = local_now.utcoffset() or timedelta(0)
    total_minutes = int(offset.total_seconds() // 60)
    if total_minutes == 330:
        return "Asia/Kolkata"
    if total_minutes == 0:
        return "UTC"
    sign = "+" if total_minutes >= 0 else "-"
    abs_min = abs(total_minutes)
    hh, mm = divmod(abs_min, 60)
    return f"UTC{sign}{hh:02d}:{mm:02d}"


def resolve_tz(tz_name: str | None) -> tuple[tzinfo, str]:
    """Resolve an IANA timezone name or UTC±HH:MM string into a tzinfo and canonical label."""
    if not tz_name or not str(tz_name).strip():
        sys_name = get_system_timezone_name()
        return resolve_tz(sys_name)
    cleaned = str(tz_name).strip()
    m = re.fullmatch(r"UTC([+-])(\d{2}):(\d{2})", cleaned, re.IGNORECASE)
    if m:
        sign = 1 if m.group(1) == "+" else -1
        hh, mm = int(m.group(2)), int(m.group(3))
        delta = timedelta(minutes=sign * (hh * 60 + mm))
        return timezone(delta, name=f"UTC{m.group(1)}{hh:02d}:{mm:02d}"), f"UTC{m.group(1)}{hh:02d}:{mm:02d}"
    try:
        return resolve_timezone_info(cleaned)
    except ToolValidationError as e:
        raise ValueError(str(e)) from e


def to_utc_iso(dt: datetime) -> str:
    """Convert a timezone-aware datetime to a canonical UTC ISO-8601 string with microseconds."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds")


def to_local_iso(dt_or_iso: datetime | str | None, tz_name: str | None = None) -> str | None:
    if dt_or_iso is None:
        return None
    dt = parse_iso_datetime(dt_or_iso, default_tz=tz_name or "UTC")
    tz_obj, _ = resolve_tz(tz_name)
    return dt.astimezone(tz_obj).isoformat(timespec="seconds")


def parse_iso_datetime(value: str | datetime, default_tz: str | None = None) -> datetime:
    """Parse an ISO-8601 string or datetime into a timezone-aware datetime."""
    tz_obj, _ = resolve_tz(default_tz)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=tz_obj)
        return value
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Datetime string must not be empty")
    raw = value.strip()
    if raw.endswith("Z") or raw.endswith("z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as e:
        raise ValueError(f"Invalid ISO-8601 datetime {value!r}: {e}") from e
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz_obj)
    return parsed


def validate_recurrence(recurrence: dict[str, Any] | None) -> dict[str, Any]:
    """Validate and normalize a recurrence specification."""
    if not recurrence:
        return {"type": "none"}
    if not isinstance(recurrence, dict):
        raise ValueError("recurrence must be a JSON object")
    rtype = str(recurrence.get("type", "none") or "none").strip().lower()
    if rtype not in VALID_RECURRENCE_TYPES:
        raise ValueError(f"Unsupported recurrence type {rtype!r}. Must be one of {VALID_RECURRENCE_TYPES}")
    if rtype == "none":
        return {"type": "none"}

    interval = recurrence.get("interval", 1)
    if isinstance(interval, bool) or not isinstance(interval, int) or not (1 <= interval <= 365):
        raise ValueError("recurrence.interval must be an integer between 1 and 365")

    normalized: dict[str, Any] = {"type": rtype, "interval": interval}
    if rtype == "weekly" and "weekday" in recurrence and recurrence["weekday"] is not None:
        wd = recurrence["weekday"]
        if isinstance(wd, str) and wd.strip().lower() in WEEKDAY_MAP:
            wd = WEEKDAY_MAP[wd.strip().lower()]
        if isinstance(wd, bool) or not isinstance(wd, int) or not (0 <= wd <= 6):
            raise ValueError("recurrence.weekday must be 0 (Monday) through 6 (Sunday)")
        normalized["weekday"] = wd
    if rtype == "monthly" and "day_of_month" in recurrence and recurrence["day_of_month"] is not None:
        dom = recurrence["day_of_month"]
        if isinstance(dom, bool) or not isinstance(dom, int) or not (1 <= dom <= 31):
            raise ValueError("recurrence.day_of_month must be an integer between 1 and 31")
        normalized["day_of_month"] = dom
    return normalized


def _step_one_occurrence(local_dt: datetime, recurrence: dict[str, Any], tz_obj: tzinfo) -> datetime:
    rtype = recurrence.get("type", "none")
    interval = int(recurrence.get("interval", 1))
    if rtype == "daily":
        target_date = local_dt.date() + timedelta(days=interval)
        return datetime(
            target_date.year, target_date.month, target_date.day,
            local_dt.hour, local_dt.minute, local_dt.second, local_dt.microsecond,
            tzinfo=tz_obj,
        )
    if rtype == "weekly":
        target_wd = recurrence.get("weekday")
        if target_wd is not None and local_dt.weekday() != int(target_wd):
            days_ahead = (int(target_wd) - local_dt.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7 * interval
            target_date = local_dt.date() + timedelta(days=days_ahead)
        else:
            target_date = local_dt.date() + timedelta(days=7 * interval)
        return datetime(
            target_date.year, target_date.month, target_date.day,
            local_dt.hour, local_dt.minute, local_dt.second, local_dt.microsecond,
            tzinfo=tz_obj,
        )
    if rtype == "monthly":
        total_months = (local_dt.year * 12 + (local_dt.month - 1)) + interval
        new_year, new_month0 = divmod(total_months, 12)
        new_month = new_month0 + 1
        desired_day = int(recurrence.get("day_of_month") or local_dt.day)
        max_day = calendar.monthrange(new_year, new_month)[1]
        clamped_day = min(desired_day, max_day)
        return datetime(
            new_year, new_month, clamped_day,
            local_dt.hour, local_dt.minute, local_dt.second, local_dt.microsecond,
            tzinfo=tz_obj,
        )
    raise ValueError(f"Cannot compute next occurrence for recurrence type {rtype!r}")


def compute_next_occurrence(
    reference_run: datetime | str,
    recurrence: dict[str, Any],
    tz_name: str = "UTC",
    *,
    after: datetime | str | None = None,
) -> tuple[datetime | None, int]:
    """Compute the next occurrence strictly after `after` (or `reference_run`), coalescing missed runs.

    Returns `(next_datetime_utc, coalesced_missed_count)`.
    """
    rec = validate_recurrence(recurrence)
    if rec["type"] == "none":
        return None, 0

    tz_obj, _ = resolve_tz(tz_name)
    ref_dt = parse_iso_datetime(reference_run, default_tz=tz_name).astimezone(tz_obj)
    after_dt = (
        parse_iso_datetime(after, default_tz=tz_name).astimezone(tz_obj)
        if after is not None
        else ref_dt
    )

    # Preserve desired day_of_month across short months (e.g. Jan 31 -> Feb 28 -> Mar 31)
    if rec["type"] == "monthly" and "day_of_month" not in rec:
        rec = {**rec, "day_of_month": ref_dt.day}

    candidate = _step_one_occurrence(ref_dt, rec, tz_obj)
    missed = 0
    max_hops = 10_000
    while candidate <= after_dt and missed < max_hops:
        missed += 1
        candidate = _step_one_occurrence(candidate, rec, tz_obj)

    return candidate.astimezone(timezone.utc), missed


def _extract_action_steps(action: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Normalize a task's `action` dictionary into a list of tool-call step dicts."""
    if not action or not isinstance(action, dict):
        return []
    if isinstance(action.get("steps"), list):
        out = []
        for s in action["steps"]:
            if isinstance(s, dict) and s.get("tool_name"):
                out.append({
                    "tool_name": str(s["tool_name"]).strip(),
                    "arguments": dict(s.get("arguments") or {}),
                })
        return out
    if action.get("tool_name"):
        return [{
            "tool_name": str(action["tool_name"]).strip(),
            "arguments": dict(action.get("arguments") or {}),
        }]
    return []


class TaskEngine:
    """Centralized persistent task engine integrated with SQLite, ToolRegistry, and PermissionManager."""

    def __init__(
        self,
        db: Database,
        settings_provider: Callable[[], Settings],
        tool_registry: ToolRegistry,
        permission_manager: PermissionManager,
        *,
        tool_context_factory: Callable[[str | None], ToolExecutionContext],
        engine: Any = None,
        clock: Callable[[], datetime] | None = None,
        base_backoff_seconds: float = 2.0,
    ):
        self.db = db
        self.settings_provider = settings_provider
        self.registry = tool_registry
        self.permission_manager = permission_manager
        self.tool_context_factory = tool_context_factory
        self.engine = engine
        self._clock: Callable[[], datetime] = clock or (lambda: datetime.now(timezone.utc))
        self.base_backoff_seconds = base_backoff_seconds

        self._lock = threading.RLock()
        self._active_cancels: dict[str, threading.Event] = {}
        self._pause_requests: set[str] = set()
        self._semaphore = threading.Semaphore(max(1, int(self.settings_provider().max_concurrent_tasks)))

    def now(self) -> datetime:
        dt = self._clock()
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def now_iso(self) -> str:
        return to_utc_iso(self.now())

    def set_clock(self, clock: Callable[[], datetime]) -> None:
        self._clock = clock

    # ---- Task Creation & Validation ------------------------------------------
    def create_task(
        self,
        *,
        title: str,
        description: str = "",
        task_type: str = "reminder",
        priority: int = 3,
        scheduled_at: str | datetime | None = None,
        deadline_at: str | datetime | None = None,
        timezone_name: str | None = None,
        recurrence: dict[str, Any] | None = None,
        action: dict[str, Any] | None = None,
        max_retries: int = 2,
        timeout_seconds: float = 30.0,
        approval_id: str | None = None,
        conversation_id: str | None = None,
        initial_state: str | None = None,
        metadata: dict[str, Any] | None = None,
        allow_duplicate: bool = False,
    ) -> dict[str, Any]:
        _, resolved_tz_name = resolve_tz(timezone_name)
        rec = validate_recurrence(recurrence)

        now_dt = self.now()
        now_str = to_utc_iso(now_dt)

        sched_utc_iso: str | None = None
        if scheduled_at is not None and str(scheduled_at).strip() != "":
            sched_dt = parse_iso_datetime(scheduled_at, default_tz=resolved_tz_name)
            sched_utc_iso = to_utc_iso(sched_dt)
        elif task_type == "reminder":
            # Default a reminder without an explicit schedule only if initial_state == 'Draft'
            if initial_state != "Draft":
                raise ValueError("A reminder requires a scheduled execution time ('scheduled_at')")

        deadline_utc_iso: str | None = None
        if deadline_at is not None and str(deadline_at).strip() != "":
            deadline_dt = parse_iso_datetime(deadline_at, default_tz=resolved_tz_name)
            deadline_utc_iso = to_utc_iso(deadline_dt)

        # Validate associated tool steps against ToolRegistry
        act = dict(action or {})
        steps = _extract_action_steps(act)
        ctx = self.tool_context_factory(conversation_id)

        requires_approval = False
        first_mutating_step: tuple[Any, dict[str, Any]] | None = None
        has_network_step = False

        validated_steps: list[dict[str, Any]] = []
        for step in steps:
            spec, val_args = self.registry.validate_call(step["tool_name"], step["arguments"], ctx)
            validated_steps.append({"tool_name": spec.name, "arguments": val_args})
            if spec.permission_level == PermissionLevel.MUTATING_LOCAL:
                requires_approval = True
                if first_mutating_step is None:
                    first_mutating_step = (spec, val_args)
            elif spec.permission_level == PermissionLevel.EXTERNAL_NETWORK:
                has_network_step = True

        if validated_steps:
            if "steps" in act:
                act["steps"] = validated_steps
            else:
                act["tool_name"] = validated_steps[0]["tool_name"]
                act["arguments"] = validated_steps[0]["arguments"]

        if task_type == "research_task" and not validated_steps and not act.get("query") and not act.get("url"):
            raise ValueError("A research_task must specify a web_search / fetch_webpage tool action, 'query', or 'url'")
        if task_type == "research_task" and not validated_steps:
            if act.get("url"):
                spec, val_args = self.registry.validate_call("fetch_webpage", {"url": act["url"]}, ctx)
            else:
                spec, val_args = self.registry.validate_call("web_search", {"query": act["query"]}, ctx)
            act["tool_name"] = spec.name
            act["arguments"] = val_args
            has_network_step = True

        if has_network_step and task_type == "local_task":
            task_type = "research_task"

        # Determine initial state and approval linkage
        linked_approval_id = approval_id
        pending_approval_obj: dict[str, Any] | None = None

        if requires_approval:
            spec, val_args = first_mutating_step  # type: ignore[misc]
            has_valid_approval = False
            if linked_approval_id:
                try:
                    rec_app = self.permission_manager.check_and_refresh_Pending(
                        linked_approval_id, now=now_dt
                    )
                    if rec_app["status"] == "approved":
                        has_valid_approval = True
                except Exception:
                    has_valid_approval = False

            if not has_valid_approval:
                explanation = self.registry.describe_action(spec, val_args)
                settings = self.settings_provider()
                pending_approval_obj = self.permission_manager.create_approval_request(
                    tool_name=spec.name,
                    permission_level=spec.permission_level,
                    validated_args=val_args,
                    explanation=f"[Task: {title.strip()}] {explanation}",
                    conversation_id=conversation_id,
                    ttl_seconds=float(max(settings.approval_ttl_seconds, 3600)),
                    now=now_dt,
                )
                linked_approval_id = pending_approval_obj["id"]
                eff_state = "AwaitingApproval"
            else:
                eff_state = initial_state or ("Scheduled" if sched_utc_iso else "Queued")
        else:
            if initial_state:
                eff_state = initial_state
            elif sched_utc_iso:
                eff_state = "Scheduled"
            else:
                eff_state = "Queued"

        eff_next_run = sched_utc_iso if eff_state in ("Scheduled", "AwaitingApproval") else (sched_utc_iso or now_str)

        created = self.db.create_task(
            title=title,
            description=description,
            task_type=task_type,
            priority=priority,
            state=eff_state,
            timezone_name=resolved_tz_name,
            scheduled_at=sched_utc_iso,
            next_run_at=eff_next_run,
            deadline_at=deadline_utc_iso,
            recurrence=rec,
            max_retries=max_retries,
            timeout_seconds=timeout_seconds,
            action=act,
            requires_approval=requires_approval,
            approval_id=linked_approval_id,
            conversation_id=conversation_id,
            metadata=metadata,
            created_at=now_str,
            allow_duplicate=allow_duplicate,
        )

        if pending_approval_obj is not None:
            self.db.create_notification(
                kind="approval_required",
                title=f"Approval required: {created['title']}",
                message=f"Task '{created['title']}' requires your approval before it can run: {pending_approval_obj['explanation']}",
                severity="warning",
                task_id=created["id"],
                dedup_key=f"task_approval:{created['id']}:{pending_approval_obj['id']}",
                metadata={"approval_id": pending_approval_obj["id"]},
                created_at=now_str,
            )

        return self._enrich_task(created)

    def _enrich_task(self, task: dict[str, Any] | None) -> dict[str, Any] | None:
        if task is None:
            return None
        tz_name = task.get("timezone") or "UTC"
        out = dict(task)
        out["scheduled_at_local"] = to_local_iso(task.get("scheduled_at"), tz_name)
        out["next_run_at_local"] = to_local_iso(task.get("next_run_at"), tz_name)
        out["last_run_at_local"] = to_local_iso(task.get("last_run_at"), tz_name)
        out["deadline_at_local"] = to_local_iso(task.get("deadline_at"), tz_name)
        if task.get("approval_id"):
            out["approval"] = self.db.get_tool_approval(task["approval_id"])
        return out

    def get_task(self, task_id: str, include_executions: bool = True) -> dict[str, Any]:
        t = self.db.get_task(task_id, include_executions=include_executions)
        if t is None:
            raise NotFoundError(f"Task not found: {task_id}")
        return self._enrich_task(t)  # type: ignore[return-value]

    def list_tasks(
        self,
        *,
        state: str | list[str] | None = None,
        filter_group: str | None = None,
        task_type: str | None = None,
        conversation_id: str | None = None,
        include_executions: bool = False,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        eff_state = state
        if filter_group and not eff_state:
            fg = filter_group.strip().lower()
            if fg == "pending":
                eff_state = ["Draft", "AwaitingApproval", "Scheduled", "Queued", "WaitingForUser"]
            elif fg == "scheduled":
                eff_state = ["Scheduled", "Queued"]
            elif fg == "running":
                eff_state = ["Running"]
            elif fg == "completed":
                eff_state = ["Completed"]
            elif fg == "failed":
                eff_state = ["Failed"]
            elif fg == "paused":
                eff_state = ["Paused", "WaitingForUser", "AwaitingApproval"]
        rows = self.db.list_tasks(
            state=eff_state,
            task_type=task_type,
            conversation_id=conversation_id,
            include_executions=include_executions,
            limit=limit,
        )
        return [self._enrich_task(r) for r in rows if r is not None]  # type: ignore[misc]

    # ---- Lifecycle Controls: Pause, Resume, Cancel, Approve, Complete, Snooze
    def pause_task(self, task_id: str, *, reason: str | None = None) -> dict[str, Any]:
        now_str = self.now_iso()
        with self._lock:
            task = self.db.get_task(task_id)
            if task is None:
                raise NotFoundError(f"Task not found: {task_id}")
            if task["state"] == "Running":
                self._pause_requests.add(task_id)
                cancel_ev = self._active_cancels.get(task_id)
                if cancel_ev:
                    cancel_ev.set()
            meta = dict(task.get("metadata") or {})
            if reason:
                meta["pause_reason"] = reason
            updated = self.db.transition_task_state(
                task_id,
                "Paused",
                now_iso=now_str,
                updates={
                    "lease_owner": None,
                    "lease_expires_at": None,
                    "metadata": meta,
                    **({"last_error": reason} if reason else {}),
                },
            )
            return self._enrich_task(updated)  # type: ignore[return-value]

    def resume_task(self, task_id: str) -> dict[str, Any]:
        now_dt = self.now()
        now_str = to_utc_iso(now_dt)
        with self._lock:
            self._pause_requests.discard(task_id)
            task = self.db.get_task(task_id)
            if task is None:
                raise NotFoundError(f"Task not found: {task_id}")
            if task["state"] not in ("Paused", "WaitingForUser", "Failed"):
                raise InvalidTaskTransitionError(
                    f"Cannot resume task in state {task['state']!r} (must be Paused, WaitingForUser, or Failed)"
                )

            # If task requires approval, check if it has an approved approval_id
            if task["requires_approval"]:
                app_id = task.get("approval_id")
                approved_ok = False
                if app_id:
                    try:
                        app = self.permission_manager.check_and_refresh_Pending(app_id, now=now_dt)
                        approved_ok = app["status"] == "approved"
                    except Exception:
                        approved_ok = False
                if not approved_ok:
                    updated = self.db.transition_task_state(
                        task_id,
                        "AwaitingApproval",
                        now_iso=now_str,
                    )
                    return self._enrich_task(updated)  # type: ignore[return-value]

            target_state = "Scheduled" if task.get("next_run_at") else "Queued"
            meta = dict(task.get("metadata") or {})
            meta.pop("pause_reason", None)
            updates: dict[str, Any] = {
                "last_error": None,
                "metadata": meta,
            }
            if task["state"] == "Failed":
                updates["retry_count"] = 0
            if not task.get("next_run_at"):
                updates["next_run_at"] = now_str
            updated = self.db.transition_task_state(
                task_id,
                target_state,
                now_iso=now_str,
                updates=updates,
            )
            return self._enrich_task(updated)  # type: ignore[return-value]

    def cancel_task(self, task_id: str, *, confirm: bool = True, reason: str | None = None) -> dict[str, Any]:
        now_str = self.now_iso()
        with self._lock:
            task = self.db.get_task(task_id)
            if task is None:
                raise NotFoundError(f"Task not found: {task_id}")
            if task["state"] == "Running" and not confirm:
                raise ValueError(
                    "Cancelling a currently running task requires explicit confirmation (confirm=true)"
                )
            cancel_ev = self._active_cancels.get(task_id)
            if cancel_ev:
                cancel_ev.set()
            updated = self.db.transition_task_state(
                task_id,
                "Cancelled",
                now_iso=now_str,
                updates={
                    "lease_owner": None,
                    "lease_expires_at": None,
                    "next_run_at": None,
                    **({"last_error": reason} if reason else {}),
                },
            )
            return self._enrich_task(updated)  # type: ignore[return-value]

    def complete_or_dismiss_reminder(self, task_id: str) -> dict[str, Any]:
        now_str = self.now_iso()
        with self._lock:
            task = self.db.get_task(task_id)
            if task is None:
                raise NotFoundError(f"Task not found: {task_id}")
            # Mark any unread notifications for this task as dismissed
            for n in self.db.list_notifications(task_id=task_id, include_dismissed=False):
                self.db.dismiss_notification(n["id"], now_iso=now_str)
            if task["state"] == "Completed":
                return self._enrich_task(task)  # type: ignore[return-value]
            updated = self.db.transition_task_state(
                task_id,
                "Completed",
                now_iso=now_str,
                updates={"next_run_at": None, "lease_owner": None, "lease_expires_at": None},
            )
            return self._enrich_task(updated)  # type: ignore[return-value]

    def snooze_reminder(
        self,
        task_id: str,
        *,
        minutes: int = 10,
        until: str | datetime | None = None,
    ) -> dict[str, Any]:
        if minutes < 1 or minutes > 10080:
            raise ValueError("Snooze minutes must be between 1 and 10080 (7 days)")
        now_dt = self.now()
        now_str = to_utc_iso(now_dt)
        with self._lock:
            task = self.db.get_task(task_id)
            if task is None:
                raise NotFoundError(f"Task not found: {task_id}")
            if task["state"] == "Cancelled":
                raise InvalidTaskTransitionError("Cannot snooze a Cancelled task")
            if until is not None:
                snooze_dt = parse_iso_datetime(until, default_tz=task["timezone"])
            else:
                snooze_dt = now_dt + timedelta(minutes=int(minutes))
            snooze_iso = to_utc_iso(snooze_dt)

            # Dismiss existing due notifications for this reminder so the snoozed run will notify fresh
            for n in self.db.list_notifications(task_id=task_id, include_dismissed=False):
                if n["kind"] == "reminder_due":
                    self.db.dismiss_notification(n["id"], now_iso=now_str)

            # Allow rescheduling a Completed one-time reminder when snoozed by updating via SQLite
            with self.db._lock, self.db._conn:
                self.db._conn.execute(
                    """
                    UPDATE tasks
                    SET state = 'Scheduled',
                        next_run_at = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (snooze_iso, now_str, task_id),
                )
            return self.get_task(task_id)

    def approve_task(self, task_id: str) -> dict[str, Any]:
        """Approve the pending Level 2 tool approval attached to a task and transition it to Scheduled/Queued."""
        now_dt = self.now()
        now_str = to_utc_iso(now_dt)
        with self._lock:
            task = self.db.get_task(task_id)
            if task is None:
                raise NotFoundError(f"Task not found: {task_id}")
            app_id = task.get("approval_id")
            if not app_id:
                raise ValueError(f"Task {task_id} does not have an associated approval request")
            self.permission_manager.approve(app_id, now=now_dt)
            target_state = "Scheduled" if task.get("next_run_at") else "Queued"
            updated = self.db.transition_task_state(
                task_id,
                target_state,
                now_iso=now_str,
                updates={"last_error": None},
                allow_same=True,
            )
            return self._enrich_task(updated)  # type: ignore[return-value]

    def reject_task_approval(self, task_id: str, *, reason: str | None = None) -> dict[str, Any]:
        now_dt = self.now()
        now_str = to_utc_iso(now_dt)
        with self._lock:
            task = self.db.get_task(task_id)
            if task is None:
                raise NotFoundError(f"Task not found: {task_id}")
            app_id = task.get("approval_id")
            if app_id:
                try:
                    self.permission_manager.reject(app_id, reason=reason, now=now_dt)
                except Exception:
                    pass
            updated = self.db.transition_task_state(
                task_id,
                "Cancelled",
                now_iso=now_str,
                updates={
                    "next_run_at": None,
                    "last_error": reason or "Rejected by user",
                },
                allow_same=True,
            )
            return self._enrich_task(updated)  # type: ignore[return-value]

    # ---- Crash & Stale Lease Recovery ----------------------------------------
    def _task_has_uncheckpointed_mutating_step(self, task: dict[str, Any]) -> bool:
        steps = _extract_action_steps(task.get("action"))
        if not steps:
            return False
        cp = task.get("checkpoint") or {}
        completed_steps = int(cp.get("completed_steps", 0))
        remaining = steps[completed_steps:]
        for step in remaining:
            try:
                spec = self.registry.get(step["tool_name"])
                if spec.permission_level == PermissionLevel.MUTATING_LOCAL:
                    return True
            except UnknownToolError:
                continue
        return False

    def recover_tasks(self, *, startup: bool = False, now: datetime | None = None) -> list[dict[str, Any]]:
        """Recover tasks left in 'Running' state after a crash/restart or stale lease expiration."""
        now_dt = now or self.now()
        now_str = to_utc_iso(now_dt)
        running_tasks = self.db.list_tasks(state="Running", limit=200)
        recovered: list[dict[str, Any]] = []

        for task in running_tasks:
            lease_exp = task.get("lease_expires_at")
            if not startup and lease_exp and lease_exp > now_str:
                continue

            tid = task["id"]
            # Mark any dangling 'running' execution records as 'recovered_interrupted'
            for ex in self.db.list_task_executions(task_id=tid, limit=10):
                if ex["outcome"] == "running" and ex["finished_at"] is None:
                    self.db.finish_task_execution(
                        ex["id"],
                        finished_at=now_str,
                        outcome="recovered_interrupted",
                        error_details="Task execution was interrupted by application restart or lease expiration.",
                        output_summary="Interrupted execution detected during recovery.",
                    )

            # Never blindly retry non-idempotent mutating operations whose outcome is uncertain
            if self._task_has_uncheckpointed_mutating_step(task):
                msg = (
                    "Task was interrupted during a mutating local file operation. "
                    "Automatic retry was blocked to prevent duplicate writes; please inspect and resume or cancel."
                )
                updated = self.db.transition_task_state(
                    tid,
                    "WaitingForUser",
                    now_iso=now_str,
                    updates={
                        "lease_owner": None,
                        "lease_expires_at": None,
                        "last_error": msg,
                    },
                )
                self.db.create_notification(
                    kind="recovery_notice",
                    title=f"Action needed after restart: {task['title']}",
                    message=msg,
                    severity="warning",
                    task_id=tid,
                    dedup_key=f"recovery:{tid}:{task['retry_count']}",
                    created_at=now_str,
                )
                recovered.append(self._enrich_task(updated))  # type: ignore[arg-type]
                continue

            new_retry = int(task["retry_count"]) + 1
            max_retries = int(task["max_retries"])
            if new_retry <= max_retries:
                msg = (
                    f"Recovered interrupted task (retry {new_retry}/{max_retries}). "
                    "Rescheduled safely from last checkpoint."
                )
                updated = self.db.transition_task_state(
                    tid,
                    "Scheduled",
                    now_iso=now_str,
                    updates={
                        "lease_owner": None,
                        "lease_expires_at": None,
                        "retry_count": new_retry,
                        "next_run_at": now_str,
                        "last_error": msg,
                    },
                )
                self.db.create_notification(
                    kind="recovery_notice",
                    title=f"Task recovered: {task['title']}",
                    message=msg,
                    severity="info",
                    task_id=tid,
                    dedup_key=f"recovery:{tid}:{new_retry}",
                    created_at=now_str,
                )
                recovered.append(self._enrich_task(updated))  # type: ignore[arg-type]
            else:
                msg = (
                    f"Task could not recover after interruption because maximum retries "
                    f"({max_retries}) were exhausted."
                )
                updated = self.db.transition_task_state(
                    tid,
                    "Failed",
                    now_iso=now_str,
                    updates={
                        "lease_owner": None,
                        "lease_expires_at": None,
                        "last_error": msg,
                        "next_run_at": None,
                    },
                )
                self.db.create_notification(
                    kind="recovery_notice",
                    title=f"Task failed after restart: {task['title']}",
                    message=msg,
                    severity="error",
                    task_id=tid,
                    dedup_key=f"recovery_failed:{tid}",
                    created_at=now_str,
                )
                recovered.append(self._enrich_task(updated))  # type: ignore[arg-type]

        return recovered

    # ---- Overdue Detection ---------------------------------------------------
    def check_overdue_tasks(self, *, now: datetime | None = None) -> list[dict[str, Any]]:
        now_dt = now or self.now()
        now_str = to_utc_iso(now_dt)
        active_tasks = self.db.list_tasks(state=list(ACTIVE_TASK_STATES), limit=200)
        overdue_found: list[dict[str, Any]] = []

        for task in active_tasks:
            deadline = task.get("deadline_at")
            if not deadline or deadline > now_str:
                continue
            meta = dict(task.get("metadata") or {})
            if not meta.get("overdue"):
                meta["overdue"] = True
                meta["overdue_detected_at"] = now_str
                task = self.db.update_task(task["id"], metadata=meta, now_iso=now_str)
            self.db.create_notification(
                kind="task_overdue",
                title=f"Overdue task: {task['title']}",
                message=f"Task '{task['title']}' passed its deadline ({to_local_iso(deadline, task['timezone'])}).",
                severity="warning",
                task_id=task["id"],
                dedup_key=f"overdue:{task['id']}:{deadline}",
                created_at=now_str,
            )
            overdue_found.append(self._enrich_task(task))  # type: ignore[arg-type]
        return overdue_found

    # ---- Execution Engine ----------------------------------------------------
    def execute_task(self, task_id: str, *, now: datetime | None = None) -> dict[str, Any]:
        """Execute a due or queued task with lease locking, permission revalidation, and checkpointing."""
        now_dt = now or self.now()
        now_str = to_utc_iso(now_dt)
        settings = self.settings_provider()

        task_before = self.db.get_task(task_id)
        if task_before is None:
            raise NotFoundError(f"Task not found: {task_id}")

        # Acquire concurrency slot (non-blocking check first to avoid deadlocks)
        if not self._semaphore.acquire(timeout=max(5.0, float(task_before["timeout_seconds"]))):
            raise RuntimeError("Maximum concurrent tasks limit reached; try again shortly")

        try:
            lease_owner = f"worker-{uuid.uuid4().hex[:12]}"
            lease_duration = max(float(settings.task_lease_seconds), float(task_before["timeout_seconds"]) + 30.0)
            lease_exp_iso = to_utc_iso(now_dt + timedelta(seconds=lease_duration))

            # Atomic SQLite lease acquisition (prevents duplicate execution)
            task = self.db.acquire_task_lease(
                task_id,
                lease_owner=lease_owner,
                lease_expires_at=lease_exp_iso,
                now_iso=now_str,
            )

            cancel_ev = threading.Event()
            with self._lock:
                self._active_cancels[task_id] = cancel_ev
                self._pause_requests.discard(task_id)

            exec_rec = self.db.create_task_execution(
                task_id=task_id,
                started_at=now_str,
                retry_number=task["retry_count"],
                outcome="running",
            )
            t0 = time.perf_counter()

            try:
                return self._run_leased_task(task, exec_rec, cancel_ev, now_dt=now_dt, t0=t0)
            finally:
                with self._lock:
                    self._active_cancels.pop(task_id, None)
        finally:
            self._semaphore.release()

    def _run_leased_task(
        self,
        task: dict[str, Any],
        exec_rec: dict[str, Any],
        cancel_ev: threading.Event,
        *,
        now_dt: datetime,
        t0: float,
    ) -> dict[str, Any]:
        tid = task["id"]
        eid = exec_rec["id"]
        now_str = to_utc_iso(now_dt)
        settings = self.settings_provider()
        steps = _extract_action_steps(task.get("action"))

        # Check missed execution delay
        scheduled_run_ref = task.get("next_run_at") or task.get("scheduled_at") or now_str
        sched_dt = parse_iso_datetime(scheduled_run_ref, default_tz=task["timezone"])
        delay_seconds = max(0.0, (now_dt - sched_dt).total_seconds())
        was_missed = delay_seconds > float(settings.missed_execution_threshold_seconds)

        # ---- 1. Reminders & Follow-up Reminders (No Model Inference!) --------
        if task["task_type"] in ("reminder", "followup") and not steps:
            duration_ms = round((time.perf_counter() - t0) * 1000, 2)
            rec = task.get("recurrence") or {"type": "none"}
            is_recurring = rec.get("type", "none") != "none"

            next_run_iso: str | None = None
            coalesced_missed = 0
            if is_recurring:
                next_dt, coalesced_missed = compute_next_occurrence(
                    sched_dt, rec, task["timezone"], after=now_dt
                )
                next_run_iso = to_utc_iso(next_dt) if next_dt else None

            missed_note = ""
            if was_missed:
                missed_note = f" (Recovered missed schedule from {to_local_iso(scheduled_run_ref, task['timezone'])})"
                if coalesced_missed > 0:
                    missed_note += f" [coalesced {coalesced_missed} missed occurrence(s)]"

            summary = f"Reminder triggered: {task['title']}{missed_note}"
            finished_exec = self.db.finish_task_execution(
                eid,
                finished_at=to_utc_iso(self.now()),
                outcome="completed",
                tool_calls=[],
                output_summary=summary,
                output_refs=[],
                resource_usage={
                    "duration_ms": duration_ms,
                    "used_model": False,
                    "was_missed": was_missed,
                    "coalesced_missed_occurrences": coalesced_missed,
                },
            )

            notif_msg = task["description"] or f"Reminder: {task['title']}"
            if missed_note:
                notif_msg = f"{notif_msg}{missed_note}"
            self.db.create_notification(
                kind="reminder_due",
                title=f"Reminder: {task['title']}",
                message=notif_msg,
                severity="info",
                task_id=tid,
                execution_id=eid,
                dedup_key=f"reminder_due:{tid}:{scheduled_run_ref}",
                metadata={
                    "scheduled_at": scheduled_run_ref,
                    "timezone": task["timezone"],
                    "was_missed": was_missed,
                },
                created_at=now_str,
            )

            meta = dict(task.get("metadata") or {})
            meta["last_triggered_at"] = now_str
            meta["was_missed_last_run"] = was_missed
            if coalesced_missed:
                meta["coalesced_missed_occurrences"] = coalesced_missed

            target_state = "Scheduled" if (is_recurring and next_run_iso) else "Completed"
            updated_task = self.db.transition_task_state(
                tid,
                target_state,
                now_iso=now_str,
                updates={
                    "last_run_at": now_str,
                    "next_run_at": next_run_iso,
                    "retry_count": 0,
                    "last_error": None,
                    "lease_owner": None,
                    "lease_expires_at": None,
                    "metadata": meta,
                },
            )
            return {
                "task": self._enrich_task(updated_task),
                "execution": finished_exec,
            }

        # ---- 2. Check Offline Mode for Research Tasks ------------------------
        has_network_tool = task["task_type"] == "research_task" or any(
            self._is_network_tool(s["tool_name"]) for s in steps
        )
        if has_network_tool and settings.offline_mode:
            err_msg = (
                "Task paused because Offline Mode is active at execution time. "
                "External web research requires Online Mode; Jarvis will never silently enable Online Mode."
            )
            duration_ms = round((time.perf_counter() - t0) * 1000, 2)
            finished_exec = self.db.finish_task_execution(
                eid,
                finished_at=to_utc_iso(self.now()),
                outcome="paused_offline",
                tool_calls=[],
                error_details=err_msg,
                output_summary="Paused: Offline Mode is active.",
                resource_usage={"duration_ms": duration_ms, "used_model": False},
            )
            meta = dict(task.get("metadata") or {})
            meta["pause_reason"] = "offline_mode"
            updated_task = self.db.transition_task_state(
                tid,
                "Paused",
                now_iso=now_str,
                updates={
                    "last_error": err_msg,
                    "lease_owner": None,
                    "lease_expires_at": None,
                    "metadata": meta,
                },
            )
            self.db.create_notification(
                kind="paused_offline",
                title=f"Task paused (Offline Mode): {task['title']}",
                message=err_msg,
                severity="warning",
                task_id=tid,
                execution_id=eid,
                dedup_key=f"paused_offline:{tid}:{scheduled_run_ref}",
                created_at=now_str,
            )
            return {
                "task": self._enrich_task(updated_task),
                "execution": finished_exec,
            }

        # ---- 3. Execute Tool Steps with Checkpointing & Permission Revalidation
        checkpoint = dict(task.get("checkpoint") or {})
        completed_steps = int(checkpoint.get("completed_steps", 0))
        step_results: list[dict[str, Any]] = list(checkpoint.get("step_results") or [])
        output_refs: list[str] = list(checkpoint.get("output_refs") or [])

        ctx = self.tool_context_factory(task.get("conversation_id"))
        ctx.cancel_event = cancel_ev
        current_step_is_mutating = False

        try:
            for idx in range(completed_steps, len(steps)):
                if cancel_ev.is_set():
                    if tid in self._pause_requests:
                        raise _TaskPausedSignal("Task paused by user")
                    raise ToolCancelledError("Task cancelled by user")

                step = steps[idx]
                tool_name = step["tool_name"]
                tool_args = step["arguments"]
                spec = self.registry.get(tool_name)
                current_step_is_mutating = spec.permission_level == PermissionLevel.MUTATING_LOCAL

                # Revalidate approval at execution time for Level 2 mutating tools
                approval_id_to_use: str | None = None
                if current_step_is_mutating:
                    approval_id_to_use = task.get("approval_id")
                    if not approval_id_to_use:
                        explanation = self.registry.describe_action(spec, tool_args)
                        new_app = self.permission_manager.create_approval_request(
                            tool_name=spec.name,
                            permission_level=spec.permission_level,
                            validated_args=tool_args,
                            explanation=f"[Task: {task['title']}] {explanation}",
                            conversation_id=task.get("conversation_id"),
                            ttl_seconds=float(max(settings.approval_ttl_seconds, 3600)),
                            now=self.now(),
                        )
                        raise ApprovalRequiredError(new_app)
                    self.permission_manager.check_and_refresh_Pending(approval_id_to_use, now=self.now())

                step_res = self.registry.execute_tool(
                    tool_name,
                    tool_args,
                    ctx,
                    approval_id=approval_id_to_use,
                    user_initiated_network=True,
                    timeout_override=float(task["timeout_seconds"]),
                )

                if cancel_ev.is_set():
                    if tid in self._pause_requests:
                        raise _TaskPausedSignal("Task paused by user")
                    raise ToolCancelledError("Task cancelled by user")

                step_record = {
                    "step_index": idx,
                    "execution_id": step_res["execution_id"],
                    "tool_name": step_res["tool_name"],
                    "permission_level": step_res["permission_level"],
                    "arguments": Database._redact_args_for_log(step_res["arguments"]),
                    "duration_ms": step_res["duration_ms"],
                    "result": step_res["result"],
                }
                step_results.append(step_record)
                if isinstance(step_res["result"], dict):
                    if step_res["result"].get("path"):
                        output_refs.append(f"workspace/{step_res['result']['path']}")
                    if step_res["result"].get("url"):
                        output_refs.append(str(step_res["result"]["url"]))
                    if isinstance(step_res["result"].get("citations"), list):
                        for c in step_res["result"]["citations"]:
                            if isinstance(c, dict) and c.get("url"):
                                output_refs.append(str(c["url"]))

                # Persist checkpoint after each completed step
                checkpoint = {
                    "completed_steps": idx + 1,
                    "total_steps": len(steps),
                    "step_results": step_results,
                    "output_refs": output_refs,
                    "updated_at": self.now_iso(),
                }
                self.db.save_task_checkpoint(tid, checkpoint, now_iso=self.now_iso())

        except _TaskPausedSignal as e:
            duration_ms = round((time.perf_counter() - t0) * 1000, 2)
            finished_exec = self.db.finish_task_execution(
                eid,
                finished_at=self.now_iso(),
                outcome="paused",
                tool_calls=step_results,
                error_details=str(e),
                output_summary=f"Task paused at step {len(step_results)}/{len(steps)}.",
                output_refs=output_refs,
                resource_usage={"duration_ms": duration_ms, "used_model": False},
            )
            cur = self.db.get_task(tid)
            if cur and cur["state"] != "Paused":
                self.db.transition_task_state(
                    tid,
                    "Paused",
                    now_iso=self.now_iso(),
                    updates={"lease_owner": None, "lease_expires_at": None},
                )
            return {"task": self.get_task(tid), "execution": finished_exec}

        except ToolCancelledError as e:
            duration_ms = round((time.perf_counter() - t0) * 1000, 2)
            finished_exec = self.db.finish_task_execution(
                eid,
                finished_at=self.now_iso(),
                outcome="cancelled",
                tool_calls=step_results,
                error_details=str(e),
                output_summary="Task execution was cancelled.",
                output_refs=output_refs,
                resource_usage={"duration_ms": duration_ms, "used_model": False},
            )
            cur = self.db.get_task(tid)
            if cur and cur["state"] != "Cancelled":
                self.db.transition_task_state(
                    tid,
                    "Cancelled",
                    now_iso=self.now_iso(),
                    updates={"lease_owner": None, "lease_expires_at": None, "next_run_at": None},
                )
            return {"task": self.get_task(tid), "execution": finished_exec}

        except (ApprovalRequiredError, ApprovalExpiredError, ApprovalMismatchError) as e:
            duration_ms = round((time.perf_counter() - t0) * 1000, 2)
            new_app = getattr(e, "approval", None)
            if new_app is None:
                # Approval expired or mismatched at execution time -> issue a fresh approval request
                step = steps[len(step_results)]
                spec = self.registry.get(step["tool_name"])
                explanation = self.registry.describe_action(spec, step["arguments"])
                new_app = self.permission_manager.create_approval_request(
                    tool_name=spec.name,
                    permission_level=spec.permission_level,
                    validated_args=step["arguments"],
                    explanation=f"[Task: {task['title']}] {explanation}",
                    conversation_id=task.get("conversation_id"),
                    ttl_seconds=float(max(settings.approval_ttl_seconds, 3600)),
                    now=self.now(),
                )
            err_text = str(e)
            finished_exec = self.db.finish_task_execution(
                eid,
                finished_at=self.now_iso(),
                outcome="awaiting_approval",
                tool_calls=step_results,
                error_details=err_text,
                output_summary=f"Blocked awaiting user approval: {new_app['explanation']}",
                output_refs=output_refs,
                resource_usage={"duration_ms": duration_ms, "used_model": False},
            )
            updated_task = self.db.transition_task_state(
                tid,
                "AwaitingApproval",
                now_iso=self.now_iso(),
                updates={
                    "requires_approval": True,
                    "approval_id": new_app["id"],
                    "last_error": err_text,
                    "lease_owner": None,
                    "lease_expires_at": None,
                },
            )
            self.db.create_notification(
                kind="approval_required",
                title=f"Approval required: {task['title']}",
                message=f"Task '{task['title']}' needs approval to execute `{new_app['tool_name']}`: {new_app['explanation']}",
                severity="warning",
                task_id=tid,
                execution_id=eid,
                dedup_key=f"task_approval:{tid}:{new_app['id']}",
                metadata={"approval_id": new_app["id"]},
                created_at=self.now_iso(),
            )
            return {"task": self._enrich_task(updated_task), "execution": finished_exec}

        except ApprovalRejectedError as e:
            duration_ms = round((time.perf_counter() - t0) * 1000, 2)
            finished_exec = self.db.finish_task_execution(
                eid,
                finished_at=self.now_iso(),
                outcome="failed",
                tool_calls=step_results,
                error_details=str(e),
                output_summary=f"Task rejected: {e}",
                output_refs=output_refs,
                resource_usage={"duration_ms": duration_ms, "used_model": False},
            )
            updated_task = self.db.transition_task_state(
                tid,
                "Failed",
                now_iso=self.now_iso(),
                updates={
                    "last_error": str(e),
                    "next_run_at": None,
                    "lease_owner": None,
                    "lease_expires_at": None,
                },
            )
            self.db.create_notification(
                kind="task_failed",
                title=f"Task rejected: {task['title']}",
                message=str(e),
                severity="error",
                task_id=tid,
                execution_id=eid,
                dedup_key=f"task_failed:{tid}:{eid}",
                created_at=self.now_iso(),
            )
            return {"task": self._enrich_task(updated_task), "execution": finished_exec}

        except OfflineModeError as e:
            duration_ms = round((time.perf_counter() - t0) * 1000, 2)
            finished_exec = self.db.finish_task_execution(
                eid,
                finished_at=self.now_iso(),
                outcome="paused_offline",
                tool_calls=step_results,
                error_details=str(e),
                output_summary="Paused because Offline Mode is active.",
                output_refs=output_refs,
                resource_usage={"duration_ms": duration_ms, "used_model": False},
            )
            updated_task = self.db.transition_task_state(
                tid,
                "Paused",
                now_iso=self.now_iso(),
                updates={
                    "last_error": str(e),
                    "lease_owner": None,
                    "lease_expires_at": None,
                },
            )
            self.db.create_notification(
                kind="paused_offline",
                title=f"Task paused (Offline Mode): {task['title']}",
                message=str(e),
                severity="warning",
                task_id=tid,
                execution_id=eid,
                dedup_key=f"paused_offline:{tid}:{eid}",
                created_at=self.now_iso(),
            )
            return {"task": self._enrich_task(updated_task), "execution": finished_exec}

        except Exception as e:
            duration_ms = round((time.perf_counter() - t0) * 1000, 2)
            outcome_label = "timed_out" if isinstance(e, ToolTimeoutError) else "failed"
            err_str = str(e)

            # Non-idempotent mutating operations must NEVER be blindly retried when outcome is uncertain
            can_retry = (not current_step_is_mutating) and (int(task["retry_count"]) < int(task["max_retries"]))
            if can_retry:
                new_retry = int(task["retry_count"]) + 1
                backoff_s = float(self.base_backoff_seconds) * (2 ** (new_retry - 1))
                next_retry_dt = self.now() + timedelta(seconds=backoff_s)
                next_retry_iso = to_utc_iso(next_retry_dt)
                summary = (
                    f"Attempt failed ({err_str}); scheduled retry {new_retry}/{task['max_retries']} "
                    f"in {backoff_s:.1f}s."
                )
                finished_exec = self.db.finish_task_execution(
                    eid,
                    finished_at=self.now_iso(),
                    outcome=outcome_label,
                    tool_calls=step_results,
                    error_details=err_str,
                    output_summary=summary,
                    output_refs=output_refs,
                    resource_usage={"duration_ms": duration_ms, "used_model": False},
                )
                updated_task = self.db.transition_task_state(
                    tid,
                    "Scheduled",
                    now_iso=self.now_iso(),
                    updates={
                        "retry_count": new_retry,
                        "next_run_at": next_retry_iso,
                        "last_error": err_str,
                        "lease_owner": None,
                        "lease_expires_at": None,
                    },
                )
                return {"task": self._enrich_task(updated_task), "execution": finished_exec}

            # Exhausted retries or non-idempotent failure -> Failed
            finished_exec = self.db.finish_task_execution(
                eid,
                finished_at=self.now_iso(),
                outcome=outcome_label,
                tool_calls=step_results,
                error_details=err_str,
                output_summary=f"Task failed: {err_str}",
                output_refs=output_refs,
                resource_usage={"duration_ms": duration_ms, "used_model": False},
            )
            updated_task = self.db.transition_task_state(
                tid,
                "Failed",
                now_iso=self.now_iso(),
                updates={
                    "last_error": err_str,
                    "next_run_at": None,
                    "lease_owner": None,
                    "lease_expires_at": None,
                },
            )
            self.db.create_notification(
                kind="task_failed",
                title=f"Task failed: {task['title']}",
                message=f"Task '{task['title']}' failed: {err_str}",
                severity="error",
                task_id=tid,
                execution_id=eid,
                dedup_key=f"task_failed:{tid}:{eid}",
                created_at=self.now_iso(),
            )
            return {"task": self._enrich_task(updated_task), "execution": finished_exec}

        # ---- 4. All Tool Steps Succeeded -------------------------------------
        used_model = False
        act = task.get("action") or {}
        summary_text = self._build_concise_summary(task, step_results)

        if act.get("synthesize_with_model") and self.engine is not None and getattr(self.engine, "ready", False):
            try:
                prompt_text = act.get("prompt") or f"Summarize the result of task '{task['title']}' concisely."
                tool_json = json.dumps([s["result"] for s in step_results], ensure_ascii=False)[:3000]
                gen = self.engine.generate(
                    [
                        {"role": "system", "content": "Provide a concise factual summary of the completed task output."},
                        {"role": "user", "content": f"Task: {prompt_text}\nTool results:\n{tool_json}"},
                    ],
                    max_tokens=200,
                    temperature=0.2,
                )
                clean_gen = strip_tool_call_tags(gen.get("content", "")).strip()
                if clean_gen:
                    summary_text = clean_gen
                    used_model = True
            except Exception as e:
                log.warning("optional model synthesis in task skipped", extra={"task_id": tid, "error": str(e)})

        duration_ms = round((time.perf_counter() - t0) * 1000, 2)
        finished_exec = self.db.finish_task_execution(
            eid,
            finished_at=self.now_iso(),
            outcome="completed",
            tool_calls=step_results,
            error_details=None,
            output_summary=summary_text,
            output_refs=output_refs,
            resource_usage={
                "duration_ms": duration_ms,
                "used_model": used_model,
                "steps_executed": len(step_results),
            },
        )

        rec = task.get("recurrence") or {"type": "none"}
        is_recurring = rec.get("type", "none") != "none"
        next_run_iso = None
        if is_recurring:
            next_dt, _ = compute_next_occurrence(sched_dt, rec, task["timezone"], after=self.now())
            next_run_iso = to_utc_iso(next_dt) if next_dt else None

        # Scheduled execution does NOT grant permanent approval for recurring mutating tasks
        if is_recurring and next_run_iso:
            if task["requires_approval"]:
                first_mut = next(
                    (s for s in steps if self.registry.get(s["tool_name"]).permission_level == PermissionLevel.MUTATING_LOCAL),
                    steps[0],
                )
                spec = self.registry.get(first_mut["tool_name"])
                new_app = self.permission_manager.create_approval_request(
                    tool_name=spec.name,
                    permission_level=spec.permission_level,
                    validated_args=first_mut["arguments"],
                    explanation=f"[Recurring Task: {task['title']}] {self.registry.describe_action(spec, first_mut['arguments'])}",
                    conversation_id=task.get("conversation_id"),
                    ttl_seconds=float(max(settings.approval_ttl_seconds, 3600)),
                    now=self.now(),
                )
                target_state = "AwaitingApproval"
                new_approval_id = new_app["id"]
            else:
                target_state = "Scheduled"
                new_approval_id = None
        else:
            target_state = "Completed"
            new_approval_id = task.get("approval_id")

        updated_task = self.db.transition_task_state(
            tid,
            target_state,
            now_iso=self.now_iso(),
            updates={
                "last_run_at": self.now_iso(),
                "next_run_at": next_run_iso,
                "retry_count": 0,
                "last_error": None,
                "checkpoint": None,
                "approval_id": new_approval_id,
                "lease_owner": None,
                "lease_expires_at": None,
            },
        )

        self.db.create_notification(
            kind="task_completed",
            title=f"Task completed: {task['title']}",
            message=summary_text,
            severity="success",
            task_id=tid,
            execution_id=eid,
            dedup_key=f"task_completed:{tid}:{eid}",
            created_at=self.now_iso(),
        )
        return {"task": self._enrich_task(updated_task), "execution": finished_exec}

    def _is_network_tool(self, tool_name: str) -> bool:
        try:
            spec = self.registry.get(tool_name)
            return spec.permission_level == PermissionLevel.EXTERNAL_NETWORK
        except UnknownToolError:
            return False

    @staticmethod
    def _build_concise_summary(task: dict[str, Any], step_results: list[dict[str, Any]]) -> str:
        if not step_results:
            return f"Completed task '{task['title']}'."
        parts: list[str] = []
        for s in step_results:
            tname = s["tool_name"]
            res = s.get("result")
            if tname == "calculate" and isinstance(res, dict):
                parts.append(f"calculate({res.get('expression')}) = {res.get('formatted')}")
            elif tname == "workspace_list_files" and isinstance(res, dict):
                parts.append(f"Listed {res.get('count', 0)} file(s) in workspace/{res.get('directory', '.')}")
            elif tname == "workspace_read_file" and isinstance(res, dict):
                parts.append(f"Read workspace/{res.get('path')} ({res.get('size_bytes', 0)} bytes)")
            elif tname in ("workspace_create_file", "workspace_write_file") and isinstance(res, dict):
                parts.append(f"{res.get('action', 'wrote').capitalize()} workspace/{res.get('path')} ({res.get('size_bytes', 0)} bytes)")
            elif tname in ("fetch_webpage", "web_search") and isinstance(res, dict):
                title = res.get("title") or res.get("query") or tname
                parts.append(f"{tname}: {title}")
            else:
                parts.append(f"{tname}: {json.dumps(res, ensure_ascii=False)[:180]}")
        return "; ".join(parts)


class _TaskPausedSignal(RuntimeError):
    """Internal signal raised when a running task is paused by the user."""


class TaskScheduler:
    """Persistent background scheduler with startup recovery, overdue checks, and missed-run coalescing."""

    def __init__(self, task_engine: TaskEngine, settings_provider: Callable[[], Settings]):
        self.task_engine = task_engine
        self.settings_provider = settings_provider
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._tick_lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and not self._stop_event.is_set()

    def start(self) -> None:
        if self.running:
            return
        self._stop_event.clear()
        # Run startup recovery immediately before starting polling loop
        try:
            self.task_engine.recover_tasks(startup=True)
        except Exception as e:
            log.error("scheduler startup recovery failed", extra={"error": str(e)})

        if not self.settings_provider().scheduler_enabled:
            return

        self._thread = threading.Thread(
            target=self._run_loop,
            name="jarvis-task-scheduler",
            daemon=True,
        )
        self._thread.start()
        log.info("task scheduler started")

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=timeout)
        self._thread = None
        log.info("task scheduler stopped")

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            settings = self.settings_provider()
            poll_s = max(0.05, float(settings.scheduler_poll_seconds))
            if settings.scheduler_enabled:
                try:
                    self.tick()
                except Exception as e:
                    log.error("scheduler tick error", extra={"error": str(e)})
            if self._stop_event.wait(timeout=poll_s):
                break

    def tick(self, *, now: datetime | None = None, max_due_per_tick: int = 10) -> dict[str, Any]:
        """Run one deterministic scheduler evaluation cycle."""
        with self._tick_lock:
            now_dt = now or self.task_engine.now()
            now_str = to_utc_iso(now_dt)

            recovered = self.task_engine.recover_tasks(startup=False, now=now_dt)
            overdue = self.task_engine.check_overdue_tasks(now=now_dt)

            candidates = self.task_engine.db.list_tasks(
                state=["Scheduled", "Queued"],
                limit=100,
            )
            due_tasks = [
                t for t in candidates
                if t["state"] == "Queued" or (t.get("next_run_at") and t["next_run_at"] <= now_str)
            ][:max_due_per_tick]

            executed: list[dict[str, Any]] = []
            for t in due_tasks:
                try:
                    res = self.task_engine.execute_task(t["id"], now=now_dt)
                    executed.append(res)
                except DuplicateTaskExecutionError:
                    continue
                except Exception as e:
                    log.error("failed executing due task", extra={"task_id": t["id"], "error": str(e)})

            return {
                "evaluated_at": now_str,
                "recovered_count": len(recovered),
                "overdue_count": len(overdue),
                "executed_count": len(executed),
                "executed": executed,
            }


# ---- Natural-Language Chat Task Command Parser -------------------------------
@dataclass
class ParsedChatTaskCommand:
    intent: str  # "create_reminder", "create_task", "ambiguous_schedule", "list_tasks", "pause_task", "resume_task", "cancel_task"
    title: str = ""
    task_type: str = "reminder"
    scheduled_at: datetime | None = None
    recurrence: dict[str, Any] | None = None
    timezone_name: str = "UTC"
    action: dict[str, Any] | None = None
    target_query: str = ""
    filter_group: str = "pending"
    ambiguity_reason: str = ""


_TIME_12H_RE = re.compile(
    r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)\b",
    re.IGNORECASE,
)
_TIME_24H_RE = re.compile(
    r"\bat\s+([01]?\d|2[0-3]):([0-5]\d)\b",
    re.IGNORECASE,
)
_IN_REL_RE = re.compile(
    r"\bin\s+(\d+)\s*(minute|minutes|min|mins|hour|hours|hr|hrs|day|days)\b",
    re.IGNORECASE,
)
_ISO_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})(?:[T\s]+(\d{1,2}):(\d{2}))?\b")


def _parse_time_of_day(text: str) -> tuple[int, int] | None:
    m12 = _TIME_12H_RE.search(text)
    if m12:
        hh = int(m12.group(1))
        mm = int(m12.group(2) or 0)
        ampm = m12.group(3).lower().replace(".", "")
        if not (1 <= hh <= 12) or not (0 <= mm <= 59):
            return None
        if ampm == "pm" and hh != 12:
            hh += 12
        elif ampm == "am" and hh == 12:
            hh = 0
        return hh, mm
    m24 = _TIME_24H_RE.search(text)
    if m24:
        return int(m24.group(1)), int(m24.group(2))
    return None


def _clean_reminder_title(raw: str) -> str:
    s = raw.strip()
    # Strip trailing time/recurrence clauses that were already parsed
    s = re.sub(
        r"\s+\b(?:every\s+(?:day|daily|week|month|monday|tuesday|wednesday|thursday|friday|saturday|sunday)(?:\s+at\s+[^\s,.]+(?:\s*(?:am|pm))?)?)\b.*$",
        "",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(
        r"\s+\b(?:tomorrow|today|tonight)(?:\s+(?:morning|afternoon|evening))?(?:\s+at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)?)?$",
        "",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(
        r"\s+\bat\s+\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)?\b.*$",
        "",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(
        r"\s+\bin\s+\d+\s*(?:minute|minutes|min|mins|hour|hours|hr|hrs|day|days)\b.*$",
        "",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(r"\s+\bon\s+\d{4}-\d{2}-\d{2}\b.*$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^(?:to|about|that)\s+", "", s, flags=re.IGNORECASE)
    return s.strip(" .!?,;:") or raw.strip(" .!?,;:")


def parse_chat_task_command(
    user_text: str,
    *,
    now: datetime | None = None,
    timezone_name: str | None = None,
) -> ParsedChatTaskCommand | None:
    """Parse explicit task/reminder management commands from natural chat.

    Returns `None` for ordinary conversation so Jarvis never invents tasks or reminders.
    """
    if not user_text or not isinstance(user_text, str):
        return None
    text = user_text.strip()
    lower = text.lower()

    # Ignore questions about how reminders/tasks work
    if lower.startswith(("how do ", "what is ", "explain ", "why ", "tell me about ")):
        return None

    tz_obj, tz_label = resolve_tz(timezone_name)
    now_local = (now or datetime.now(timezone.utc)).astimezone(tz_obj)

    # 1. Show / list tasks
    m_list = re.match(
        r"^(?:please\s+)?(?:show|list|view|display|what\s+are)\s+(?:me\s+)?(?:my\s+|all\s+|the\s+)?(?:(pending|scheduled|running|completed|failed|paused|active|all)\s+)?(tasks|reminders)\b",
        lower,
    )
    if m_list:
        fg = (m_list.group(1) or "all").lower()
        if fg == "active":
            fg = "pending"
        return ParsedChatTaskCommand(
            intent="list_tasks",
            filter_group=fg,
            timezone_name=tz_label,
        )

    # 2. Pause a task
    m_pause = re.match(
        r"^(?:please\s+)?pause\s+(?:my\s+|the\s+)?(.+?)\s*$",
        text,
        re.IGNORECASE,
    )
    if m_pause and ("task" in lower or "reminder" in lower or len(m_pause.group(1)) >= 4):
        target = re.sub(r"\b(?:task|reminder)\b", "", m_pause.group(1), flags=re.IGNORECASE).strip(" .!?'\"")
        return ParsedChatTaskCommand(
            intent="pause_task",
            target_query=target or m_pause.group(1).strip(" .!'\""),
            timezone_name=tz_label,
        )

    # 3. Resume a task
    m_resume = re.match(
        r"^(?:please\s+)?resume\s+(?:my\s+|the\s+)?(.+?)\s*$",
        text,
        re.IGNORECASE,
    )
    if m_resume and ("task" in lower or "reminder" in lower or "paused" in lower):
        target = re.sub(r"\b(?:paused|my|the|task|reminder)\b", "", m_resume.group(1), flags=re.IGNORECASE).strip(" .!?'\"")
        return ParsedChatTaskCommand(
            intent="resume_task",
            target_query=target,
            timezone_name=tz_label,
        )

    # 4. Cancel a task / reminder
    m_cancel = re.match(
        r"^(?:please\s+)?cancel\s+(?:my\s+|the\s+)?(.+?)\s*$",
        text,
        re.IGNORECASE,
    )
    if m_cancel and ("task" in lower or "reminder" in lower):
        raw_target = m_cancel.group(1).strip(" .!?'\"")
        target = re.sub(
            r"\b(?:the\s+)?(?:reminder|task)(?:\s+i\s+created\s+(?:yesterday|today|earlier))?(?:\s+to\s+|\s+for\s+|\s+about\s+)?",
            "",
            raw_target,
            flags=re.IGNORECASE,
        ).strip(" .!?'\"")
        return ParsedChatTaskCommand(
            intent="cancel_task",
            target_query=target,
            timezone_name=tz_label,
        )

    # 5. Create a reminder / recurring reminder / follow-up
    m_rem = re.match(
        r"^(?:please\s+)?(?:remind\s+me|create\s+a\s+reminder|set\s+a\s+reminder|schedule\s+a\s+reminder|follow\s+up\s+with\s+me)\s+(.+)$",
        text,
        re.IGNORECASE,
    )
    if m_rem:
        body = m_rem.group(1).strip()
        is_followup = lower.startswith("follow up")
        ttype = "followup" if is_followup else "reminder"

        # Parse recurrence if present
        rec: dict[str, Any] = {"type": "none"}
        target_weekday: int | None = None
        m_every_wd = re.search(
            r"\bevery\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
            body,
            re.IGNORECASE,
        )
        if m_every_wd:
            target_weekday = WEEKDAY_MAP[m_every_wd.group(1).lower()]
            rec = {"type": "weekly", "interval": 1, "weekday": target_weekday}
        elif re.search(r"\b(?:every\s+day|daily)\b", body, re.IGNORECASE):
            rec = {"type": "daily", "interval": 1}
        elif re.search(r"\b(?:every\s+week|weekly)\b", body, re.IGNORECASE):
            rec = {"type": "weekly", "interval": 1}
        elif re.search(r"\b(?:every\s+month|monthly)\b", body, re.IGNORECASE):
            rec = {"type": "monthly", "interval": 1}

        # Parse time/date
        rel_match = _IN_REL_RE.search(body)
        iso_match = _ISO_DATE_RE.search(body)
        tod = _parse_time_of_day(body)
        has_tomorrow = bool(re.search(r"\btomorrow\b", body, re.IGNORECASE))
        has_today = bool(re.search(r"\b(?:today|tonight)\b", body, re.IGNORECASE))

        sched_local: datetime | None = None
        if rel_match:
            amt = int(rel_match.group(1))
            unit = rel_match.group(2).lower()
            if unit.startswith("min"):
                sched_local = now_local + timedelta(minutes=amt)
            elif unit.startswith(("hour", "hr")):
                sched_local = now_local + timedelta(hours=amt)
            else:
                sched_local = now_local + timedelta(days=amt)
        elif iso_match:
            d_part = datetime.strptime(iso_match.group(1), "%Y-%m-%d").date()
            if iso_match.group(2) is not None and iso_match.group(3) is not None:
                hh, mm = int(iso_match.group(2)), int(iso_match.group(3))
            elif tod is not None:
                hh, mm = tod
            else:
                # Date without time -> ambiguous!
                return ParsedChatTaskCommand(
                    intent="ambiguous_schedule",
                    title=_clean_reminder_title(body),
                    task_type=ttype,
                    timezone_name=tz_label,
                    ambiguity_reason=(
                        f"You specified the date ({iso_match.group(1)}) without a time of day. "
                        f"Please specify the exact time in your timezone ({tz_label}), for example 'at 9:00 AM'."
                    ),
                )
            sched_local = datetime(d_part.year, d_part.month, d_part.day, hh, mm, 0, tzinfo=tz_obj)
        elif target_weekday is not None:
            if tod is None:
                return ParsedChatTaskCommand(
                    intent="ambiguous_schedule",
                    title=_clean_reminder_title(body),
                    task_type=ttype,
                    recurrence=rec,
                    timezone_name=tz_label,
                    ambiguity_reason=(
                        f"You requested a weekly reminder every {WEEKDAY_NAMES[target_weekday]}, "
                        f"but did not specify a time of day. What time in {tz_label} should it trigger?"
                    ),
                )
            hh, mm = tod
            days_ahead = (target_weekday - now_local.weekday()) % 7
            cand_date = now_local.date() + timedelta(days=days_ahead)
            cand_dt = datetime(cand_date.year, cand_date.month, cand_date.day, hh, mm, 0, tzinfo=tz_obj)
            if cand_dt <= now_local:
                cand_date = cand_date + timedelta(days=7)
                cand_dt = datetime(cand_date.year, cand_date.month, cand_date.day, hh, mm, 0, tzinfo=tz_obj)
            sched_local = cand_dt
        elif has_tomorrow:
            if tod is None:
                # Check if "tomorrow morning" / "tomorrow evening" without clock time -> ask for confirmation
                return ParsedChatTaskCommand(
                    intent="ambiguous_schedule",
                    title=_clean_reminder_title(body),
                    task_type=ttype,
                    timezone_name=tz_label,
                    ambiguity_reason=(
                        f"You mentioned 'tomorrow' without a specific clock time. "
                        f"Please confirm the time in {tz_label} (for example, 'tomorrow at 9:00 AM')."
                    ),
                )
            hh, mm = tod
            tom = now_local.date() + timedelta(days=1)
            sched_local = datetime(tom.year, tom.month, tom.day, hh, mm, 0, tzinfo=tz_obj)
        elif tod is not None:
            hh, mm = tod
            cand = datetime(
                now_local.year, now_local.month, now_local.day, hh, mm, 0, tzinfo=tz_obj
            )
            if cand <= now_local and not has_today:
                nxt_day = now_local.date() + timedelta(days=1)
                cand = datetime(nxt_day.year, nxt_day.month, nxt_day.day, hh, mm, 0, tzinfo=tz_obj)
            sched_local = cand
        else:
            return ParsedChatTaskCommand(
                intent="ambiguous_schedule",
                title=_clean_reminder_title(body),
                task_type=ttype,
                recurrence=rec,
                timezone_name=tz_label,
                ambiguity_reason=(
                    f"No specific date or time was provided for the reminder. "
                    f"Please specify when in {tz_label} you would like to be reminded (e.g., 'at 7:00 PM' or 'tomorrow at 9:00 AM')."
                ),
            )

        clean_title = _clean_reminder_title(body)
        return ParsedChatTaskCommand(
            intent="create_reminder",
            title=clean_title,
            task_type=ttype,
            scheduled_at=sched_local,
            recurrence=rec,
            timezone_name=tz_label,
        )

    return None
