"""Tool permission levels, cryptographic argument binding, and user approval lifecycle."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from . import offline
from .config import Settings
from .db import Database


class PermissionLevel(str, Enum):
    SAFE_READ = "level_1_safe_read"
    MUTATING_LOCAL = "level_2_mutating_local"
    EXTERNAL_NETWORK = "level_3_external_network"


PERMISSION_LABELS: dict[str, str] = {
    PermissionLevel.SAFE_READ.value: "Level 1 — Safe Read-Only Local",
    PermissionLevel.MUTATING_LOCAL.value: "Level 2 — Mutating Local (Approval Required)",
    PermissionLevel.EXTERNAL_NETWORK.value: "Level 3 — External Network (Online Mode Required)",
}


class PermissionDeniedError(RuntimeError):
    """Base error raised when a tool call is not permitted to execute."""


class ApprovalRequiredError(PermissionDeniedError):
    """Raised when a tool call requires explicit user approval before execution."""

    def __init__(self, approval: dict[str, Any]):
        self.approval = approval
        super().__init__(
            f"User approval required for {approval['tool_name']}: {approval['explanation']}"
        )


class ApprovalExpiredError(PermissionDeniedError):
    """Raised when a user approval has passed its expiration timestamp."""


class ApprovalRejectedError(PermissionDeniedError):
    """Raised when attempting to execute a rejected tool approval."""


class ApprovalMismatchError(PermissionDeniedError):
    """Raised when an approval ID is reused for a different tool, file, or argument set."""


class OfflineModeError(PermissionDeniedError):
    """Raised when an external network tool is invoked while Offline Mode is active."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds")


def canonical_arguments_json(tool_name: str, validated_args: dict[str, Any]) -> str:
    """Produce a deterministic canonical JSON representation binding tool_name and arguments."""
    payload = {
        "tool": str(tool_name),
        "arguments": validated_args,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_arguments_hash(tool_name: str, validated_args: dict[str, Any]) -> str:
    """Return the SHA-256 hex digest of the canonical (tool_name, validated_args) payload."""
    canonical = canonical_arguments_json(tool_name, validated_args)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def is_approval_expired(expires_at_iso: str, now: datetime | None = None) -> bool:
    """Return True if expires_at_iso is in the past relative to now (UTC)."""
    now = now or _utc_now()
    try:
        exp = datetime.fromisoformat(expires_at_iso)
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return now >= exp
    except ValueError:
        return True


class PermissionManager:
    """Independently enforces tool permissions, approval bindings, and offline mode."""

    def __init__(self, db: Database):
        self.db = db

    def create_approval_request(
        self,
        *,
        tool_name: str,
        permission_level: PermissionLevel | str,
        validated_args: dict[str, Any],
        explanation: str,
        conversation_id: str | None = None,
        ttl_seconds: float = 300.0,
        pending_turn: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        lvl = permission_level.value if isinstance(permission_level, PermissionLevel) else str(permission_level)
        now_dt = now or _utc_now()
        expires_at = _iso_utc(now_dt + timedelta(seconds=float(ttl_seconds)))
        args_hash = compute_arguments_hash(tool_name, validated_args)
        return self.db.create_tool_approval(
            tool_name=tool_name,
            permission_level=lvl,
            arguments=validated_args,
            arguments_hash=args_hash,
            explanation=explanation,
            expires_at=expires_at,
            conversation_id=conversation_id,
            pending_turn=pending_turn,
        )

    def check_and_refresh_Pending(self, approval_id: str, *, now: datetime | None = None) -> dict[str, Any]:
        """Fetch an approval record and automatically transition it to 'expired' if past TTL."""
        now_dt = now or _utc_now()
        record = self.db.get_tool_approval(approval_id, include_pending_turn=True)
        if record is None:
            raise PermissionDeniedError(f"Approval request not found: {approval_id}")
        if record["status"] in ("pending", "approved") and is_approval_expired(record["expires_at"], now=now_dt):
            record = self.db.update_tool_approval_status(
                approval_id,
                "expired",
                decided_at=record["decided_at"] or _iso_utc(now_dt),
                rejection_reason="Approval expired before execution",
            )
        return record

    def approve(self, approval_id: str, *, now: datetime | None = None) -> dict[str, Any]:
        """Mark a pending approval as approved by the user (ready for immediate single-use execution)."""
        now_dt = now or _utc_now()
        record = self.check_and_refresh_Pending(approval_id, now=now_dt)
        status = record["status"]
        if status == "expired":
            raise ApprovalExpiredError(f"Approval {approval_id} has expired and cannot be approved")
        if status == "rejected":
            raise ApprovalRejectedError(f"Approval {approval_id} was already rejected")
        if status == "executed":
            raise ApprovalMismatchError(f"Approval {approval_id} has already been executed")
        if status != "pending":
            raise PermissionDeniedError(f"Approval {approval_id} is in state {status!r}")
        return self.db.update_tool_approval_status(
            approval_id,
            "approved",
            decided_at=_iso_utc(now_dt),
        )

    def reject(self, approval_id: str, reason: str | None = None, *, now: datetime | None = None) -> dict[str, Any]:
        """Reject a pending approval and record the rejection in the tool execution audit log."""
        now_dt = now or _utc_now()
        record = self.check_and_refresh_Pending(approval_id, now=now_dt)
        status = record["status"]
        if status == "expired":
            raise ApprovalExpiredError(f"Approval {approval_id} has already expired")
        if status == "executed":
            raise ApprovalMismatchError(f"Approval {approval_id} was already executed")
        if status == "rejected":
            return record
        clean_reason = (reason or "Rejected by user").strip()[:300]
        updated = self.db.update_tool_approval_status(
            approval_id,
            "rejected",
            decided_at=_iso_utc(now_dt),
            rejection_reason=clean_reason,
        )
        self.db.record_tool_execution(
            tool_name=record["tool_name"],
            permission_level=record["permission_level"],
            arguments=record["arguments"],
            status="rejected",
            conversation_id=record["conversation_id"],
            error=clean_reason,
            approval_id=approval_id,
        )
        return updated

    def verify_and_consume_for_execution(
        self,
        *,
        settings: Settings,
        tool_name: str,
        permission_level: PermissionLevel | str,
        validated_args: dict[str, Any],
        approval_id: str | None = None,
        user_initiated_network: bool = False,
    ) -> dict[str, Any] | None:
        """Re-verify permissions at execution time right before running a tool.

        For Level 2 (or approval-bound calls), verifies the approval is 'approved',
        non-expired, and cryptographically bound to (tool_name, validated_args), then
        atomically marks it 'executed' so it can never be reused.
        """
        if not settings.tools_enabled:
            raise PermissionDeniedError("Tool calling is disabled in settings.")

        lvl = permission_level.value if isinstance(permission_level, PermissionLevel) else str(permission_level)

        if lvl == PermissionLevel.SAFE_READ.value:
            return None

        if lvl == PermissionLevel.EXTERNAL_NETWORK.value:
            if settings.offline_mode or not settings.enforce_offline is False or offline.is_enabled():
                raise OfflineModeError(
                    "External web access is blocked because Offline Mode is active. "
                    "Enable Online Mode in Settings before using web search or web page fetch tools."
                )
            if not user_initiated_network and not approval_id:
                raise PermissionDeniedError(
                    "External network tool requires an explicit user request or approval."
                )
            if approval_id:
                return self._consume_bound_approval(approval_id, tool_name, validated_args)
            return None

        if lvl == PermissionLevel.MUTATING_LOCAL.value:
            if not approval_id:
                raise PermissionDeniedError(
                    f"Tool '{tool_name}' is a Level 2 mutating tool and requires explicit user approval."
                )
            return self._consume_bound_approval(approval_id, tool_name, validated_args)

        raise PermissionDeniedError(f"Unknown permission level: {lvl!r}")

    def _consume_bound_approval(
        self,
        approval_id: str,
        tool_name: str,
        validated_args: dict[str, Any],
    ) -> dict[str, Any]:
        record = self.check_and_refresh_Pending(approval_id)
        status = record["status"]
        if status == "expired":
            raise ApprovalExpiredError(f"Approval {approval_id} has expired")
        if status == "rejected":
            raise ApprovalRejectedError(f"Approval {approval_id} was rejected by the user")
        if status == "executed":
            raise ApprovalMismatchError(
                f"Approval {approval_id} has already been consumed and cannot be reused"
            )
        if status != "approved":
            raise PermissionDeniedError(
                f"Approval {approval_id} has not been approved by the user (status={status!r})"
            )

        expected_hash = compute_arguments_hash(tool_name, validated_args)
        if (
            record["tool_name"] != tool_name
            or record["arguments_hash"] != expected_hash
            or record["arguments"] != validated_args
        ):
            raise ApprovalMismatchError(
                f"Approval {approval_id} is bound to {record['tool_name']} with different arguments "
                f"and cannot be reused for {tool_name}."
            )

        return self.db.update_tool_approval_status(
            approval_id,
            "executed",
            executed_at=_iso_utc(_utc_now()),
        )
