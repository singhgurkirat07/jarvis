"""SQLite persistence layer: schema migrations, conversations, messages, settings, and memories."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_CONVERSATION_TITLE = "New conversation"

TASK_STATES: tuple[str, ...] = (
    "Draft",
    "AwaitingApproval",
    "Scheduled",
    "Queued",
    "Running",
    "WaitingForUser",
    "Paused",
    "Completed",
    "Failed",
    "Cancelled",
)

ACTIVE_TASK_STATES: tuple[str, ...] = (
    "Draft",
    "AwaitingApproval",
    "Scheduled",
    "Queued",
    "Running",
    "WaitingForUser",
    "Paused",
)

TERMINAL_TASK_STATES: tuple[str, ...] = ("Completed", "Failed", "Cancelled")

TASK_TYPES: tuple[str, ...] = ("reminder", "local_task", "research_task", "followup")

VALID_TASK_TRANSITIONS: dict[str, set[str]] = {
    "Draft": {"AwaitingApproval", "Scheduled", "Queued", "Paused", "Cancelled"},
    "AwaitingApproval": {"Scheduled", "Queued", "Running", "WaitingForUser", "Paused", "Failed", "Cancelled"},
    "Scheduled": {"Queued", "Running", "AwaitingApproval", "WaitingForUser", "Paused", "Completed", "Failed", "Cancelled"},
    "Queued": {"Running", "Scheduled", "AwaitingApproval", "WaitingForUser", "Paused", "Completed", "Failed", "Cancelled"},
    "Running": {"Completed", "Failed", "Scheduled", "Queued", "AwaitingApproval", "WaitingForUser", "Paused", "Cancelled"},
    "WaitingForUser": {"Scheduled", "Queued", "Running", "AwaitingApproval", "Paused", "Completed", "Failed", "Cancelled"},
    "Paused": {"Scheduled", "Queued", "AwaitingApproval", "WaitingForUser", "Cancelled"},
    "Completed": set(),
    "Failed": {"Scheduled", "Queued", "Cancelled"},
    "Cancelled": set(),
}


class DatabaseError(RuntimeError):
    """Base error for database failures."""


class NotFoundError(KeyError):
    """Requested record does not exist."""


class DuplicateMessageError(ValueError):
    """A message with the same (conversation_id, client_msg_id) already exists."""


class DuplicateTaskError(ValueError):
    """An active task with the same deduplication signature already exists."""


class InvalidTaskTransitionError(ValueError):
    """Requested task state transition is not permitted by the state machine."""


class DuplicateTaskExecutionError(RuntimeError):
    """Task is already leased / running and cannot be executed concurrently."""


def validate_task_transition(current_state: str, new_state: str, *, allow_same: bool = False) -> None:
    if new_state not in TASK_STATES:
        raise InvalidTaskTransitionError(f"Unknown task state: {new_state!r}")
    if current_state not in TASK_STATES:
        raise InvalidTaskTransitionError(f"Unknown current task state: {current_state!r}")
    if current_state == new_state:
        if allow_same:
            return
        raise InvalidTaskTransitionError(f"Task is already in state {new_state!r}")
    allowed = VALID_TASK_TRANSITIONS.get(current_state, set())
    if new_state not in allowed:
        raise InvalidTaskTransitionError(
            f"Invalid task state transition: {current_state} -> {new_state}"
        )


def _now() -> str:
    """Return a timezone-aware UTC ISO-8601 timestamp with microsecond precision."""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def derive_title(text: str, max_len: int = 60) -> str:
    """Create a concise conversation title from the first user message."""
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if not cleaned:
        return DEFAULT_CONVERSATION_TITLE
    if len(cleaned) <= max_len:
        return cleaned
    cut = cleaned[: max_len - 1].rsplit(" ", 1)[0]
    return (cut if len(cut) >= max_len // 2 else cleaned[: max_len - 1]) + "…"


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _migration_001(conn: sqlite3.Connection) -> None:
    """Baseline Phase 1 messages table."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('system', 'user', 'assistant')),
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            completion_tokens INTEGER,
            tokens_per_second REAL,
            seconds REAL
        );
        CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, id);
        """
    )


def _migration_002(conn: sqlite3.Connection) -> None:
    """Phase 2 schema: conversations, upgraded messages with FK + deduplication, user_settings, memories."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            archived INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0, 1))
        );
        CREATE INDEX IF NOT EXISTS idx_conversations_updated
            ON conversations(archived, updated_at DESC);

        CREATE TABLE IF NOT EXISTS user_settings (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS memories (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'general',
            importance INTEGER NOT NULL DEFAULT 3 CHECK (importance BETWEEN 1 AND 5),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            source_conversation_id TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_memories_updated
            ON memories(category, importance DESC, updated_at DESC);
        """
    )

    # Backfill conversation records for any pre-existing Phase 1 messages so no data is orphaned.
    existing_cids = conn.execute(
        """
        SELECT DISTINCT m.conversation_id
        FROM messages m
        LEFT JOIN conversations c ON c.id = m.conversation_id
        WHERE c.id IS NULL
        """
    ).fetchall()
    now = _now()
    for (cid,) in existing_cids:
        bounds = conn.execute(
            "SELECT MIN(created_at), MAX(created_at) FROM messages WHERE conversation_id = ?",
            (cid,),
        ).fetchone()
        first_user = conn.execute(
            "SELECT content FROM messages WHERE conversation_id = ? AND role = 'user' ORDER BY id ASC LIMIT 1",
            (cid,),
        ).fetchone()
        first_any = first_user or conn.execute(
            "SELECT content FROM messages WHERE conversation_id = ? ORDER BY id ASC LIMIT 1",
            (cid,),
        ).fetchone()
        title = derive_title(first_any[0]) if first_any else DEFAULT_CONVERSATION_TITLE
        created_at = (bounds[0] if bounds and bounds[0] else now)
        updated_at = (bounds[1] if bounds and bounds[1] else created_at)
        conn.execute(
            "INSERT OR IGNORE INTO conversations (id, title, created_at, updated_at, archived) VALUES (?, ?, ?, ?, 0)",
            (cid, title, created_at, updated_at),
        )

    # Check whether messages already has the Phase 2 columns and foreign key.
    cols = {row[1] for row in conn.execute("PRAGMA table_info(messages)").fetchall()}
    fks = conn.execute("PRAGMA foreign_key_list(messages)").fetchall()
    if "client_msg_id" not in cols or "metadata_json" not in cols or not fks:
        conn.executescript(
            """
            CREATE TABLE messages_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                role TEXT NOT NULL CHECK (role IN ('system', 'user', 'assistant')),
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                client_msg_id TEXT,
                completion_tokens INTEGER,
                prompt_tokens INTEGER,
                tokens_per_second REAL,
                seconds REAL,
                finish_reason TEXT,
                metadata_json TEXT,
                UNIQUE(conversation_id, client_msg_id)
            );
            INSERT INTO messages_v2 (
                id, conversation_id, role, content, created_at,
                completion_tokens, tokens_per_second, seconds
            )
            SELECT
                id, conversation_id, role, content, created_at,
                completion_tokens, tokens_per_second, seconds
            FROM messages
            ORDER BY id ASC;
            DROP TABLE messages;
            ALTER TABLE messages_v2 RENAME TO messages;
            CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, id ASC);
            """
        )


def _ensure_phase3_tables(conn: sqlite3.Connection) -> None:
    """Ensure Phase 3 tool_executions and tool_approvals tables and indexes exist idempotently."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS tool_executions (
            id TEXT PRIMARY KEY,
            conversation_id TEXT,
            tool_name TEXT NOT NULL,
            permission_level TEXT NOT NULL,
            arguments_json TEXT NOT NULL,
            status TEXT NOT NULL,
            result_summary TEXT,
            error TEXT,
            duration_ms REAL,
            approval_id TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_tool_executions_created
            ON tool_executions(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_tool_executions_conv
            ON tool_executions(conversation_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS tool_approvals (
            id TEXT PRIMARY KEY,
            conversation_id TEXT,
            tool_name TEXT NOT NULL,
            permission_level TEXT NOT NULL,
            arguments_json TEXT NOT NULL,
            arguments_hash TEXT NOT NULL,
            explanation TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected', 'expired', 'executed')),
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            decided_at TEXT,
            executed_at TEXT,
            rejection_reason TEXT,
            pending_turn_json TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_tool_approvals_status
            ON tool_approvals(status, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_tool_approvals_conv
            ON tool_approvals(conversation_id, created_at DESC);
        """
    )


def _ensure_phase4_tables(conn: sqlite3.Connection) -> None:
    """Phase 4 schema: persistent tasks, task_executions, and notifications tables."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            task_type TEXT NOT NULL CHECK (task_type IN ('reminder', 'local_task', 'research_task', 'followup')),
            priority INTEGER NOT NULL DEFAULT 3 CHECK (priority BETWEEN 1 AND 5),
            state TEXT NOT NULL CHECK (state IN (
                'Draft', 'AwaitingApproval', 'Scheduled', 'Queued', 'Running',
                'WaitingForUser', 'Paused', 'Completed', 'Failed', 'Cancelled'
            )),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            timezone TEXT NOT NULL DEFAULT 'UTC',
            scheduled_at TEXT,
            next_run_at TEXT,
            last_run_at TEXT,
            deadline_at TEXT,
            recurrence_json TEXT NOT NULL DEFAULT '{"type": "none"}',
            retry_count INTEGER NOT NULL DEFAULT 0,
            max_retries INTEGER NOT NULL DEFAULT 2,
            timeout_seconds REAL NOT NULL DEFAULT 30.0,
            action_json TEXT NOT NULL DEFAULT '{}',
            requires_approval INTEGER NOT NULL DEFAULT 0 CHECK (requires_approval IN (0, 1)),
            approval_id TEXT,
            last_error TEXT,
            checkpoint_json TEXT,
            lease_owner TEXT,
            lease_expires_at TEXT,
            dedup_key TEXT,
            conversation_id TEXT,
            metadata_json TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_tasks_state_next_run
            ON tasks(state, next_run_at ASC);
        CREATE INDEX IF NOT EXISTS idx_tasks_dedup
            ON tasks(dedup_key, state);
        CREATE INDEX IF NOT EXISTS idx_tasks_updated
            ON tasks(updated_at DESC);

        CREATE TABLE IF NOT EXISTS task_executions (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            outcome TEXT NOT NULL,
            retry_number INTEGER NOT NULL DEFAULT 0,
            tool_calls_json TEXT NOT NULL DEFAULT '[]',
            error_details TEXT,
            output_summary TEXT,
            output_refs_json TEXT NOT NULL DEFAULT '[]',
            resource_usage_json TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_task_executions_task
            ON task_executions(task_id, started_at DESC);
        CREATE INDEX IF NOT EXISTS idx_task_executions_started
            ON task_executions(started_at DESC);

        CREATE TABLE IF NOT EXISTS notifications (
            id TEXT PRIMARY KEY,
            task_id TEXT REFERENCES tasks(id) ON DELETE SET NULL,
            execution_id TEXT,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            severity TEXT NOT NULL DEFAULT 'info',
            read INTEGER NOT NULL DEFAULT 0 CHECK (read IN (0, 1)),
            dismissed INTEGER NOT NULL DEFAULT 0 CHECK (dismissed IN (0, 1)),
            dedup_key TEXT UNIQUE,
            created_at TEXT NOT NULL,
            read_at TEXT,
            dismissed_at TEXT,
            metadata_json TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_notifications_active
            ON notifications(dismissed, read, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_notifications_task
            ON notifications(task_id, created_at DESC);
        """
    )


MIGRATIONS: list[tuple[int, str, object]] = [
    (1, "001_initial_messages", _migration_001),
    (2, "002_phase2_conversations_settings_memories", _migration_002),
    (3, "003_phase3_tools_and_approvals", _ensure_phase3_tables),
    (4, "004_phase4_tasks_executions_notifications", _ensure_phase4_tables),
]


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        try:
            self._conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=5.0)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._migrate()
            self._conn.execute("PRAGMA foreign_keys=ON")
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to initialize SQLite database at {self.path}: {e}") from e

    # ---- schema migrations -----------------------------------------------
    def _migrate(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys=OFF")
            try:
                with self._conn:
                    self._conn.execute(
                        """
                        CREATE TABLE IF NOT EXISTS schema_migrations (
                            version INTEGER PRIMARY KEY,
                            name TEXT NOT NULL,
                            applied_at TEXT NOT NULL
                        )
                        """
                    )
                applied = {
                    row["version"]
                    for row in self._conn.execute("SELECT version FROM schema_migrations").fetchall()
                }
                for version, name, fn in MIGRATIONS:
                    if version in applied:
                        continue
                    with self._conn:
                        fn(self._conn)
                        self._conn.execute(
                            "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                            (version, name, _now()),
                        )
                with self._conn:
                    _ensure_phase3_tables(self._conn)
                    _ensure_phase4_tables(self._conn)
            finally:
                self._conn.execute("PRAGMA foreign_keys=ON")

    def get_migrations(self, include_all: bool = False) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT version, name, applied_at FROM schema_migrations ORDER BY version ASC"
            ).fetchall()
        all_rows = [dict(r) for r in rows]
        if include_all:
            return all_rows
        return [r for r in all_rows if r["version"] <= 2]

    def get_schema_migrations(self) -> list[dict]:
        """Return all applied migrations across Phases 1-4."""
        return self.get_migrations(include_all=True)

    @staticmethod
    def new_conversation_id() -> str:
        return uuid.uuid4().hex

    @staticmethod
    def new_memory_id() -> str:
        return uuid.uuid4().hex

    # ---- conversations ---------------------------------------------------
    def create_conversation(self, title: str | None = None, conversation_id: str | None = None) -> dict:
        cid = conversation_id or self.new_conversation_id()
        clean_title = (title or "").strip() or DEFAULT_CONVERSATION_TITLE
        now = _now()
        try:
            with self._lock, self._conn:
                self._conn.execute(
                    "INSERT INTO conversations (id, title, created_at, updated_at, archived) VALUES (?, ?, ?, ?, 0)",
                    (cid, clean_title, now, now),
                )
        except sqlite3.IntegrityError as e:
            raise DatabaseError(f"Conversation {cid} already exists") from e
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to create conversation: {e}") from e
        return {
            "id": cid,
            "title": clean_title,
            "created_at": now,
            "updated_at": now,
            "archived": False,
            "message_count": 0,
            "last_message_preview": None,
        }

    def _ensure_conversation_locked(self, conversation_id: str, initial_user_text: str | None = None) -> None:
        row = self._conn.execute(
            "SELECT id, title FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        now = _now()
        if row is None:
            title = derive_title(initial_user_text) if initial_user_text else DEFAULT_CONVERSATION_TITLE
            self._conn.execute(
                "INSERT INTO conversations (id, title, created_at, updated_at, archived) VALUES (?, ?, ?, ?, 0)",
                (conversation_id, title, now, now),
            )
        else:
            if initial_user_text and row["title"] == DEFAULT_CONVERSATION_TITLE:
                self._conn.execute(
                    "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
                    (derive_title(initial_user_text), now, conversation_id),
                )
            else:
                self._conn.execute(
                    "UPDATE conversations SET updated_at = ? WHERE id = ?",
                    (now, conversation_id),
                )

    def get_conversation_meta(self, conversation_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT
                    c.id, c.title, c.created_at, c.updated_at, c.archived,
                    COUNT(m.id) AS message_count,
                    (SELECT substr(m2.content, 1, 120) FROM messages m2
                     WHERE m2.conversation_id = c.id ORDER BY m2.id DESC LIMIT 1) AS last_message_preview
                FROM conversations c
                LEFT JOIN messages m ON m.conversation_id = c.id
                WHERE c.id = ?
                GROUP BY c.id
                """,
                (conversation_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "title": row["title"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "archived": bool(row["archived"]),
            "message_count": row["message_count"],
            "last_message_preview": row["last_message_preview"],
        }

    def list_conversations(self, include_archived: bool = False,
                           archived_only: bool = False, limit: int = 100) -> list[dict]:
        where = ""
        params: list[object] = []
        if archived_only:
            where = "WHERE c.archived = 1"
        elif not include_archived:
            where = "WHERE c.archived = 0"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT
                    c.id, c.title, c.created_at, c.updated_at, c.archived,
                    COUNT(m.id) AS message_count,
                    (SELECT substr(m2.content, 1, 120) FROM messages m2
                     WHERE m2.conversation_id = c.id ORDER BY m2.id DESC LIMIT 1) AS last_message_preview
                FROM conversations c
                LEFT JOIN messages m ON m.conversation_id = c.id
                {where}
                GROUP BY c.id
                ORDER BY c.updated_at DESC, c.created_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [
            {
                "id": r["id"],
                "title": r["title"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
                "archived": bool(r["archived"]),
                "message_count": r["message_count"],
                "last_message_preview": r["last_message_preview"],
            }
            for r in rows
        ]

    def update_conversation(self, conversation_id: str, title: str | None = None,
                            archived: bool | None = None) -> dict:
        with self._lock:
            existing = self.get_conversation_meta(conversation_id)
            if existing is None:
                raise NotFoundError(f"Conversation not found: {conversation_id}")
            new_title = existing["title"] if title is None else title.strip()
            if not new_title:
                raise ValueError("Conversation title must not be blank")
            new_archived = int(existing["archived"] if archived is None else archived)
            now = _now()
            with self._conn:
                self._conn.execute(
                    "UPDATE conversations SET title = ?, archived = ?, updated_at = ? WHERE id = ?",
                    (new_title, new_archived, now, conversation_id),
                )
            return self.get_conversation_meta(conversation_id)

    def delete_conversation(self, conversation_id: str) -> bool:
        with self._lock:
            existing = self.get_conversation_meta(conversation_id)
            if existing is None:
                raise NotFoundError(f"Conversation not found: {conversation_id}")
            with self._conn:
                # Clear source_conversation_id reference without deleting any memories
                self._conn.execute(
                    "UPDATE memories SET source_conversation_id = NULL WHERE source_conversation_id = ?",
                    (conversation_id,),
                )
                self._conn.execute(
                    "UPDATE tool_executions SET conversation_id = NULL WHERE conversation_id = ?",
                    (conversation_id,),
                )
                self._conn.execute(
                    "DELETE FROM tool_approvals WHERE conversation_id = ?",
                    (conversation_id,),
                )
                self._conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        return True

    def search_conversations(self, query: str, include_archived: bool = False,
                             limit: int = 50) -> list[dict]:
        q = (query or "").strip()
        if not q:
            return self.list_conversations(include_archived=include_archived, limit=limit)
        pattern = f"%{_escape_like(q)}%"
        arch_clause = "" if include_archived else "AND c.archived = 0"
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT
                    c.id, c.title, c.created_at, c.updated_at, c.archived,
                    COUNT(m.id) AS message_count,
                    CASE WHEN c.title LIKE ? ESCAPE '\\' THEN 'title' ELSE 'message' END AS match_type,
                    COALESCE(
                        (SELECT substr(m2.content, 1, 160) FROM messages m2
                         WHERE m2.conversation_id = c.id AND m2.content LIKE ? ESCAPE '\\'
                         ORDER BY m2.id DESC LIMIT 1),
                        (SELECT substr(m3.content, 1, 160) FROM messages m3
                         WHERE m3.conversation_id = c.id
                         ORDER BY m3.id DESC LIMIT 1)
                    ) AS snippet
                FROM conversations c
                LEFT JOIN messages m ON m.conversation_id = c.id
                WHERE (
                    c.title LIKE ? ESCAPE '\\'
                    OR EXISTS (
                        SELECT 1 FROM messages mx
                        WHERE mx.conversation_id = c.id AND mx.content LIKE ? ESCAPE '\\'
                    )
                )
                {arch_clause}
                GROUP BY c.id
                ORDER BY
                    CASE WHEN c.title LIKE ? ESCAPE '\\' THEN 0 ELSE 1 END ASC,
                    c.updated_at DESC
                LIMIT ?
                """,
                (pattern, pattern, pattern, pattern, pattern, limit),
            ).fetchall()
        return [
            {
                "id": r["id"],
                "title": r["title"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
                "archived": bool(r["archived"]),
                "message_count": r["message_count"],
                "match_type": r["match_type"],
                "snippet": r["snippet"],
            }
            for r in rows
        ]

    # ---- messages --------------------------------------------------------
    def _insert_message_locked(self, conversation_id: str, role: str, content: str,
                               stats: dict | None = None, client_msg_id: str | None = None,
                               metadata: dict | None = None) -> dict:
        stats = stats or {}
        meta_obj = dict(metadata) if metadata else {}
        for extra_k in (
            "decode_tokens_per_second",
            "time_to_first_token",
            "retrieved_memories",
            "saved_memory",
            "tool_events",
            "pending_approval",
            "citations",
        ):
            if extra_k in stats and stats[extra_k] is not None and extra_k not in meta_obj:
                meta_obj[extra_k] = stats[extra_k]
        meta_json = json.dumps(meta_obj, ensure_ascii=False) if meta_obj else None
        now = _now()
        try:
            cur = self._conn.execute(
                """
                INSERT INTO messages (
                    conversation_id, role, content, created_at, client_msg_id,
                    completion_tokens, prompt_tokens, tokens_per_second, seconds,
                    finish_reason, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conversation_id,
                    role,
                    content,
                    now,
                    client_msg_id,
                    stats.get("completion_tokens"),
                    stats.get("prompt_tokens"),
                    stats.get("tokens_per_second"),
                    stats.get("seconds"),
                    stats.get("finish_reason"),
                    meta_json,
                ),
            )
        except sqlite3.IntegrityError as e:
            if client_msg_id and "UNIQUE" in str(e).upper():
                raise DuplicateMessageError(
                    f"Duplicate message client_msg_id={client_msg_id!r} for conversation {conversation_id}"
                ) from e
            raise DatabaseError(f"Failed to insert message: {e}") from e
        return {
            "id": cur.lastrowid,
            "conversation_id": conversation_id,
            "role": role,
            "content": content,
            "created_at": now,
            "client_msg_id": client_msg_id,
            "completion_tokens": stats.get("completion_tokens"),
            "prompt_tokens": stats.get("prompt_tokens"),
            "tokens_per_second": stats.get("tokens_per_second"),
            "seconds": stats.get("seconds"),
            "finish_reason": stats.get("finish_reason"),
            "metadata": meta_obj,
        }

    def add_message(self, conversation_id: str, role: str, content: str,
                    stats: dict | None = None, client_msg_id: str | None = None,
                    metadata: dict | None = None) -> dict:
        try:
            with self._lock, self._conn:
                self._ensure_conversation_locked(
                    conversation_id, initial_user_text=content if role == "user" else None
                )
                return self._insert_message_locked(
                    conversation_id, role, content, stats=stats,
                    client_msg_id=client_msg_id, metadata=metadata,
                )
        except (DuplicateMessageError, ValueError):
            raise
        except sqlite3.Error as e:
            raise DatabaseError(f"Database error while adding message: {e}") from e

    def add_turn(self, conversation_id: str, user_content: str, assistant_content: str,
                 stats: dict | None = None, client_msg_id: str | None = None,
                 metadata: dict | None = None) -> tuple[dict, dict]:
        """Atomically persist a user message and its assistant response in one transaction."""
        try:
            with self._lock, self._conn:
                self._ensure_conversation_locked(conversation_id, initial_user_text=user_content)
                u_msg = self._insert_message_locked(
                    conversation_id, "user", user_content, client_msg_id=client_msg_id
                )
                a_msg = self._insert_message_locked(
                    conversation_id, "assistant", assistant_content, stats=stats, metadata=metadata
                )
                return u_msg, a_msg
        except (DuplicateMessageError, ValueError):
            raise
        except sqlite3.Error as e:
            raise DatabaseError(f"Database error while saving conversation turn: {e}") from e

    def get_conversation(self, conversation_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, role, content, created_at, client_msg_id,
                       completion_tokens, prompt_tokens, tokens_per_second,
                       seconds, finish_reason, metadata_json
                FROM messages
                WHERE conversation_id = ?
                ORDER BY id ASC
                """,
                (conversation_id,),
            ).fetchall()
        result = []
        for r in rows:
            meta = json.loads(r["metadata_json"]) if r["metadata_json"] else {}
            result.append({
                "id": r["id"],
                "role": r["role"],
                "content": r["content"],
                "created_at": r["created_at"],
                "client_msg_id": r["client_msg_id"],
                "completion_tokens": r["completion_tokens"],
                "prompt_tokens": r["prompt_tokens"],
                "tokens_per_second": r["tokens_per_second"],
                "seconds": r["seconds"],
                "finish_reason": r["finish_reason"],
                "metadata": meta,
            })
        return result

    # ---- user settings ---------------------------------------------------
    def get_user_settings(self) -> dict[str, object]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT key, value_json FROM user_settings ORDER BY key ASC"
            ).fetchall()
        return {r["key"]: json.loads(r["value_json"]) for r in rows}

    def set_user_settings(self, updates: dict[str, object]) -> dict[str, object]:
        now = _now()
        try:
            with self._lock, self._conn:
                for k, v in updates.items():
                    self._conn.execute(
                        """
                        INSERT INTO user_settings (key, value_json, updated_at)
                        VALUES (?, ?, ?)
                        ON CONFLICT(key) DO UPDATE SET
                            value_json = excluded.value_json,
                            updated_at = excluded.updated_at
                        """,
                        (k, json.dumps(v, ensure_ascii=False), now),
                    )
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to persist settings: {e}") from e
        return self.get_user_settings()

    # ---- explicit long-term memories -------------------------------------
    def create_memory(self, content: str, category: str = "general",
                      importance: int = 3, source_conversation_id: str | None = None) -> dict:
        clean_content = re.sub(r"\s+", " ", (content or "").strip())
        if not clean_content:
            raise ValueError("Memory content must not be blank")
        clean_category = re.sub(r"[^a-z0-9_-]", "", (category or "general").strip().lower()) or "general"
        imp = int(importance)
        if not (1 <= imp <= 5):
            raise ValueError("Memory importance must be between 1 and 5")

        now = _now()
        with self._lock, self._conn:
            # Avoid inserting duplicate memories if identical content already exists
            existing = self._conn.execute(
                "SELECT id, created_at FROM memories WHERE lower(content) = lower(?)",
                (clean_content,),
            ).fetchone()
            if existing is not None:
                mid = existing["id"]
                self._conn.execute(
                    "UPDATE memories SET content = ?, category = ?, importance = ?, updated_at = ? WHERE id = ?",
                    (clean_content, clean_category, imp, now, mid),
                )
                return self.get_memory(mid)

            mid = self.new_memory_id()
            self._conn.execute(
                """
                INSERT INTO memories (
                    id, content, category, importance, created_at, updated_at, source_conversation_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (mid, clean_content, clean_category, imp, now, now, source_conversation_id),
            )
            return self.get_memory(mid)

    def get_memory(self, memory_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT id, content, category, importance, created_at, updated_at, source_conversation_id
                FROM memories WHERE id = ?
                """,
                (memory_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def list_memories(self, category: str | None = None, query: str | None = None,
                      limit: int = 200) -> list[dict]:
        clauses = []
        params: list[object] = []
        if category:
            clauses.append("category = ?")
            params.append(category.strip().lower())
        if query and query.strip():
            pat = f"%{_escape_like(query.strip())}%"
            clauses.append("(content LIKE ? ESCAPE '\\' OR category LIKE ? ESCAPE '\\')")
            params.extend([pat, pat])
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT id, content, category, importance, created_at, updated_at, source_conversation_id
                FROM memories
                {where}
                ORDER BY importance DESC, updated_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    def update_memory(self, memory_id: str, content: str | None = None,
                      category: str | None = None, importance: int | None = None) -> dict:
        with self._lock:
            existing = self.get_memory(memory_id)
            if existing is None:
                raise NotFoundError(f"Memory not found: {memory_id}")
            new_content = existing["content"] if content is None else re.sub(r"\s+", " ", content.strip())
            if not new_content:
                raise ValueError("Memory content must not be blank")
            new_cat = (
                existing["category"]
                if category is None
                else (re.sub(r"[^a-z0-9_-]", "", category.strip().lower()) or "general")
            )
            new_imp = existing["importance"] if importance is None else int(importance)
            if not (1 <= new_imp <= 5):
                raise ValueError("Memory importance must be between 1 and 5")
            now = _now()
            with self._conn:
                self._conn.execute(
                    "UPDATE memories SET content = ?, category = ?, importance = ?, updated_at = ? WHERE id = ?",
                    (new_content, new_cat, new_imp, now, memory_id),
                )
            return self.get_memory(memory_id)

    def delete_memory(self, memory_id: str) -> bool:
        with self._lock:
            existing = self.get_memory(memory_id)
            if existing is None:
                raise NotFoundError(f"Memory not found: {memory_id}")
            with self._conn:
                self._conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        return True

    # ---- tool executions & approvals (Phase 3) ---------------------------
    @staticmethod
    def _redact_args_for_log(arguments: dict | None) -> dict:
        """Redact or truncate bulky file contents so audit logs remain compact and safe."""
        if not isinstance(arguments, dict):
            return {}
        out: dict = {}
        for k, v in arguments.items():
            if k in ("content", "text", "body") and isinstance(v, str) and len(v) > 200:
                out[k] = f"{v[:200]}… [+{len(v) - 200} chars]"
            else:
                out[k] = v
        return out

    def record_tool_execution(
        self,
        *,
        tool_name: str,
        permission_level: str,
        arguments: dict | None,
        status: str,
        conversation_id: str | None = None,
        result_summary: str | None = None,
        error: str | None = None,
        duration_ms: float | None = None,
        approval_id: str | None = None,
    ) -> dict:
        eid = uuid.uuid4().hex
        now = _now()
        safe_args = self._redact_args_for_log(arguments)
        args_json = json.dumps(safe_args, ensure_ascii=False, sort_keys=True)
        summary = (result_summary[:1000] + "…") if result_summary and len(result_summary) > 1000 else result_summary
        try:
            with self._lock, self._conn:
                self._conn.execute(
                    """
                    INSERT INTO tool_executions (
                        id, conversation_id, tool_name, permission_level, arguments_json,
                        status, result_summary, error, duration_ms, approval_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        eid,
                        conversation_id,
                        tool_name,
                        permission_level,
                        args_json,
                        status,
                        summary,
                        error,
                        round(duration_ms, 2) if duration_ms is not None else None,
                        approval_id,
                        now,
                    ),
                )
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to record tool execution: {e}") from e
        return {
            "id": eid,
            "conversation_id": conversation_id,
            "tool_name": tool_name,
            "permission_level": permission_level,
            "arguments": safe_args,
            "status": status,
            "result_summary": summary,
            "error": error,
            "duration_ms": round(duration_ms, 2) if duration_ms is not None else None,
            "approval_id": approval_id,
            "created_at": now,
        }

    def list_tool_executions(self, conversation_id: str | None = None, limit: int = 100) -> list[dict]:
        where = "WHERE conversation_id = ?" if conversation_id else ""
        params: list[object] = [conversation_id, limit] if conversation_id else [limit]
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT id, conversation_id, tool_name, permission_level, arguments_json,
                       status, result_summary, error, duration_ms, approval_id, created_at
                FROM tool_executions
                {where}
                ORDER BY created_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        out = []
        for r in rows:
            out.append({
                "id": r["id"],
                "conversation_id": r["conversation_id"],
                "tool_name": r["tool_name"],
                "permission_level": r["permission_level"],
                "arguments": json.loads(r["arguments_json"]) if r["arguments_json"] else {},
                "status": r["status"],
                "result_summary": r["result_summary"],
                "error": r["error"],
                "duration_ms": r["duration_ms"],
                "approval_id": r["approval_id"],
                "created_at": r["created_at"],
            })
        return out

    def create_tool_approval(
        self,
        *,
        tool_name: str,
        permission_level: str,
        arguments: dict,
        arguments_hash: str,
        explanation: str,
        expires_at: str,
        conversation_id: str | None = None,
        pending_turn: dict | None = None,
    ) -> dict:
        aid = uuid.uuid4().hex
        now = _now()
        args_json = json.dumps(arguments, ensure_ascii=False, sort_keys=True)
        turn_json = json.dumps(pending_turn, ensure_ascii=False) if pending_turn else None
        try:
            with self._lock, self._conn:
                self._conn.execute(
                    """
                    INSERT INTO tool_approvals (
                        id, conversation_id, tool_name, permission_level, arguments_json,
                        arguments_hash, explanation, status, created_at, expires_at,
                        decided_at, executed_at, rejection_reason, pending_turn_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, NULL, NULL, NULL, ?)
                    """,
                    (
                        aid,
                        conversation_id,
                        tool_name,
                        permission_level,
                        args_json,
                        arguments_hash,
                        explanation,
                        now,
                        expires_at,
                        turn_json,
                    ),
                )
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to create tool approval: {e}") from e
        return self.get_tool_approval(aid)

    def _row_to_approval(self, r: sqlite3.Row, include_pending_turn: bool = False) -> dict:
        d = {
            "id": r["id"],
            "conversation_id": r["conversation_id"],
            "tool_name": r["tool_name"],
            "permission_level": r["permission_level"],
            "arguments": json.loads(r["arguments_json"]) if r["arguments_json"] else {},
            "arguments_hash": r["arguments_hash"],
            "explanation": r["explanation"],
            "status": r["status"],
            "created_at": r["created_at"],
            "expires_at": r["expires_at"],
            "decided_at": r["decided_at"],
            "executed_at": r["executed_at"],
            "rejection_reason": r["rejection_reason"],
        }
        if include_pending_turn:
            d["pending_turn"] = json.loads(r["pending_turn_json"]) if r["pending_turn_json"] else None
        return d

    def get_tool_approval(self, approval_id: str, include_pending_turn: bool = False) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT id, conversation_id, tool_name, permission_level, arguments_json,
                       arguments_hash, explanation, status, created_at, expires_at,
                       decided_at, executed_at, rejection_reason, pending_turn_json
                FROM tool_approvals
                WHERE id = ?
                """,
                (approval_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_approval(row, include_pending_turn=include_pending_turn)

    def list_tool_approvals(
        self,
        conversation_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        clauses = []
        params: list[object] = []
        if conversation_id:
            clauses.append("conversation_id = ?")
            params.append(conversation_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT id, conversation_id, tool_name, permission_level, arguments_json,
                       arguments_hash, explanation, status, created_at, expires_at,
                       decided_at, executed_at, rejection_reason, pending_turn_json
                FROM tool_approvals
                {where}
                ORDER BY created_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [self._row_to_approval(r) for r in rows]

    def update_tool_approval_status(
        self,
        approval_id: str,
        status: str,
        *,
        decided_at: str | None = None,
        executed_at: str | None = None,
        rejection_reason: str | None = None,
    ) -> dict:
        with self._lock:
            existing = self.get_tool_approval(approval_id, include_pending_turn=True)
            if existing is None:
                raise NotFoundError(f"Tool approval not found: {approval_id}")
            new_decided = decided_at if decided_at is not None else existing["decided_at"]
            new_executed = executed_at if executed_at is not None else existing["executed_at"]
            new_reason = rejection_reason if rejection_reason is not None else existing["rejection_reason"]
            with self._conn:
                self._conn.execute(
                    """
                    UPDATE tool_approvals
                    SET status = ?, decided_at = ?, executed_at = ?, rejection_reason = ?
                    WHERE id = ?
                    """,
                    (status, new_decided, new_executed, new_reason, approval_id),
                )
            return self.get_tool_approval(approval_id, include_pending_turn=True)

    # ---- tasks, task_executions & notifications (Phase 4) ----------------
    @staticmethod
    def new_task_id() -> str:
        return uuid.uuid4().hex

    @staticmethod
    def compute_task_dedup_key(
        title: str,
        task_type: str,
        scheduled_at: str | None,
        action: dict | None,
        recurrence: dict | None,
    ) -> str:
        norm_title = re.sub(r"\s+", " ", (title or "").strip()).lower()
        canonical_action = json.dumps(action or {}, sort_keys=True, ensure_ascii=False)
        canonical_rec = json.dumps(recurrence or {"type": "none"}, sort_keys=True, ensure_ascii=False)
        payload = f"{norm_title}|{task_type}|{scheduled_at or ''}|{canonical_action}|{canonical_rec}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _row_to_task(self, r: sqlite3.Row, include_executions: bool = False) -> dict:
        d = {
            "id": r["id"],
            "title": r["title"],
            "description": r["description"],
            "task_type": r["task_type"],
            "priority": int(r["priority"]),
            "state": r["state"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
            "timezone": r["timezone"],
            "scheduled_at": r["scheduled_at"],
            "next_run_at": r["next_run_at"],
            "last_run_at": r["last_run_at"],
            "deadline_at": r["deadline_at"],
            "recurrence": json.loads(r["recurrence_json"]) if r["recurrence_json"] else {"type": "none"},
            "retry_count": int(r["retry_count"]),
            "max_retries": int(r["max_retries"]),
            "timeout_seconds": float(r["timeout_seconds"]),
            "action": json.loads(r["action_json"]) if r["action_json"] else {},
            "requires_approval": bool(r["requires_approval"]),
            "approval_id": r["approval_id"],
            "last_error": r["last_error"],
            "checkpoint": json.loads(r["checkpoint_json"]) if r["checkpoint_json"] else None,
            "lease_owner": r["lease_owner"],
            "lease_expires_at": r["lease_expires_at"],
            "dedup_key": r["dedup_key"],
            "conversation_id": r["conversation_id"],
            "metadata": json.loads(r["metadata_json"]) if r["metadata_json"] else {},
        }
        if include_executions:
            d["executions"] = self.list_task_executions(task_id=r["id"], limit=50)
        return d

    def create_task(
        self,
        *,
        title: str,
        description: str = "",
        task_type: str = "reminder",
        priority: int = 3,
        state: str = "Scheduled",
        timezone_name: str = "UTC",
        scheduled_at: str | None = None,
        next_run_at: str | None = None,
        deadline_at: str | None = None,
        recurrence: dict | None = None,
        max_retries: int = 2,
        timeout_seconds: float = 30.0,
        action: dict | None = None,
        requires_approval: bool = False,
        approval_id: str | None = None,
        checkpoint: dict | None = None,
        conversation_id: str | None = None,
        metadata: dict | None = None,
        created_at: str | None = None,
        task_id: str | None = None,
        allow_duplicate: bool = False,
    ) -> dict:
        clean_title = re.sub(r"\s+", " ", (title or "").strip())
        if not clean_title:
            raise ValueError("Task title must not be blank")
        if len(clean_title) > 300:
            raise ValueError("Task title must not exceed 300 characters")
        if task_type not in TASK_TYPES:
            raise ValueError(f"Unsupported task_type: {task_type!r}. Must be one of {TASK_TYPES}")
        if state not in TASK_STATES:
            raise InvalidTaskTransitionError(f"Unknown initial task state: {state!r}")
        p_int = int(priority)
        if not (1 <= p_int <= 5):
            raise ValueError("Task priority must be between 1 and 5")
        if max_retries < 0 or max_retries > 20:
            raise ValueError("max_retries must be between 0 and 20")
        if timeout_seconds <= 0 or timeout_seconds > 3600:
            raise ValueError("timeout_seconds must be between 0 and 3600")

        tid = task_id or self.new_task_id()
        now = created_at or _now()
        rec_dict = recurrence or {"type": "none"}
        act_dict = action or {}
        dedup = self.compute_task_dedup_key(clean_title, task_type, scheduled_at, act_dict, rec_dict)
        eff_next_run = next_run_at if next_run_at is not None else scheduled_at

        try:
            with self._lock, self._conn:
                if not allow_duplicate and state in ACTIVE_TASK_STATES:
                    dup = self._conn.execute(
                        """
                        SELECT id FROM tasks
                        WHERE dedup_key = ?
                          AND state NOT IN ('Completed', 'Failed', 'Cancelled')
                        LIMIT 1
                        """,
                        (dedup,),
                    ).fetchone()
                    if dup is not None:
                        raise DuplicateTaskError(
                            f"Duplicate active task already exists (id={dup['id']})"
                        )
                self._conn.execute(
                    """
                    INSERT INTO tasks (
                        id, title, description, task_type, priority, state,
                        created_at, updated_at, timezone, scheduled_at, next_run_at,
                        last_run_at, deadline_at, recurrence_json, retry_count, max_retries,
                        timeout_seconds, action_json, requires_approval, approval_id,
                        last_error, checkpoint_json, lease_owner, lease_expires_at,
                        dedup_key, conversation_id, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, 0, ?, ?, ?, ?, ?, NULL, ?, NULL, NULL, ?, ?, ?)
                    """,
                    (
                        tid,
                        clean_title,
                        (description or "").strip(),
                        task_type,
                        p_int,
                        state,
                        now,
                        now,
                        timezone_name or "UTC",
                        scheduled_at,
                        eff_next_run,
                        deadline_at,
                        json.dumps(rec_dict, ensure_ascii=False, sort_keys=True),
                        int(max_retries),
                        float(timeout_seconds),
                        json.dumps(act_dict, ensure_ascii=False, sort_keys=True),
                        1 if requires_approval else 0,
                        approval_id,
                        json.dumps(checkpoint, ensure_ascii=False) if checkpoint is not None else None,
                        dedup,
                        conversation_id,
                        json.dumps(metadata or {}, ensure_ascii=False),
                    ),
                )
        except (DuplicateTaskError, InvalidTaskTransitionError, ValueError):
            raise
        except sqlite3.IntegrityError as e:
            raise DatabaseError(f"Task creation integrity error: {e}") from e
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to create task: {e}") from e
        return self.get_task(tid, include_executions=True)

    def get_task(self, task_id: str, include_executions: bool = False) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                return None
            return self._row_to_task(row, include_executions=include_executions)

    def list_tasks(
        self,
        *,
        state: str | list[str] | tuple[str, ...] | None = None,
        task_type: str | None = None,
        conversation_id: str | None = None,
        include_executions: bool = False,
        limit: int = 200,
    ) -> list[dict]:
        clauses: list[str] = []
        params: list[object] = []
        if state:
            if isinstance(state, (list, tuple, set)):
                states_list = list(state)
                placeholders = ",".join("?" for _ in states_list)
                clauses.append(f"state IN ({placeholders})")
                params.extend(states_list)
            else:
                clauses.append("state = ?")
                params.append(state)
        if task_type:
            clauses.append("task_type = ?")
            params.append(task_type)
        if conversation_id:
            clauses.append("conversation_id = ?")
            params.append(conversation_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT * FROM tasks
                {where}
                ORDER BY
                    CASE state
                        WHEN 'Running' THEN 1
                        WHEN 'AwaitingApproval' THEN 2
                        WHEN 'WaitingForUser' THEN 3
                        WHEN 'Queued' THEN 4
                        WHEN 'Scheduled' THEN 5
                        WHEN 'Paused' THEN 6
                        WHEN 'Draft' THEN 7
                        WHEN 'Failed' THEN 8
                        WHEN 'Completed' THEN 9
                        WHEN 'Cancelled' THEN 10
                        ELSE 11
                    END ASC,
                    COALESCE(next_run_at, updated_at) ASC,
                    priority DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
            return [self._row_to_task(r, include_executions=include_executions) for r in rows]

    def transition_task_state(
        self,
        task_id: str,
        new_state: str,
        *,
        now_iso: str | None = None,
        updates: dict | None = None,
        allow_same: bool = False,
    ) -> dict:
        with self._lock:
            existing = self.get_task(task_id)
            if existing is None:
                raise NotFoundError(f"Task not found: {task_id}")
            validate_task_transition(existing["state"], new_state, allow_same=allow_same)
            merged_updates = dict(updates or {})
            merged_updates["state"] = new_state
            return self._apply_task_updates_locked(task_id, existing, merged_updates, now_iso=now_iso)

    def update_task(
        self,
        task_id: str,
        *,
        now_iso: str | None = None,
        allow_same_state: bool = True,
        **updates,
    ) -> dict:
        with self._lock:
            existing = self.get_task(task_id)
            if existing is None:
                raise NotFoundError(f"Task not found: {task_id}")
            if "state" in updates and updates["state"] is not None:
                validate_task_transition(
                    existing["state"], updates["state"], allow_same=allow_same_state
                )
            return self._apply_task_updates_locked(task_id, existing, updates, now_iso=now_iso)

    def _apply_task_updates_locked(
        self,
        task_id: str,
        existing: dict,
        updates: dict,
        *,
        now_iso: str | None = None,
    ) -> dict:
        now = now_iso or _now()
        cols_sql: list[str] = ["updated_at = ?"]
        vals: list[object] = [now]

        direct_fields = {
            "title": "title",
            "description": "description",
            "task_type": "task_type",
            "priority": "priority",
            "state": "state",
            "timezone": "timezone",
            "scheduled_at": "scheduled_at",
            "next_run_at": "next_run_at",
            "last_run_at": "last_run_at",
            "deadline_at": "deadline_at",
            "retry_count": "retry_count",
            "max_retries": "max_retries",
            "timeout_seconds": "timeout_seconds",
            "approval_id": "approval_id",
            "last_error": "last_error",
            "lease_owner": "lease_owner",
            "lease_expires_at": "lease_expires_at",
        }
        for key, col in direct_fields.items():
            if key in updates:
                val = updates[key]
                if key == "title":
                    val = re.sub(r"\s+", " ", (val or "").strip())
                    if not val:
                        raise ValueError("Task title must not be blank")
                elif key == "priority" and val is not None:
                    val = int(val)
                    if not (1 <= val <= 5):
                        raise ValueError("Task priority must be between 1 and 5")
                cols_sql.append(f"{col} = ?")
                vals.append(val)

        if "requires_approval" in updates:
            cols_sql.append("requires_approval = ?")
            vals.append(1 if updates["requires_approval"] else 0)
        if "recurrence" in updates:
            cols_sql.append("recurrence_json = ?")
            vals.append(
                json.dumps(updates["recurrence"] or {"type": "none"}, ensure_ascii=False, sort_keys=True)
            )
        if "action" in updates:
            cols_sql.append("action_json = ?")
            vals.append(json.dumps(updates["action"] or {}, ensure_ascii=False, sort_keys=True))
        if "checkpoint" in updates:
            cp = updates["checkpoint"]
            cols_sql.append("checkpoint_json = ?")
            vals.append(json.dumps(cp, ensure_ascii=False) if cp is not None else None)
        if "metadata" in updates:
            cols_sql.append("metadata_json = ?")
            vals.append(json.dumps(updates["metadata"] or {}, ensure_ascii=False))

        vals.append(task_id)
        with self._conn:
            self._conn.execute(
                f"UPDATE tasks SET {', '.join(cols_sql)} WHERE id = ?",
                vals,
            )
        return self.get_task(task_id, include_executions=True)

    def acquire_task_lease(
        self,
        task_id: str,
        *,
        lease_owner: str,
        lease_expires_at: str,
        now_iso: str | None = None,
    ) -> dict:
        """Atomically transition a Scheduled/Queued task to Running and acquire an execution lease."""
        now = now_iso or _now()
        with self._lock:
            existing = self.get_task(task_id)
            if existing is None:
                raise NotFoundError(f"Task not found: {task_id}")
            if existing["state"] == "Running":
                exp = existing.get("lease_expires_at")
                if exp and exp > now:
                    raise DuplicateTaskExecutionError(
                        f"Task {task_id} is already running under active lease {existing.get('lease_owner')}"
                    )
            validate_task_transition(existing["state"], "Running", allow_same=(existing["state"] == "Running"))
            with self._conn:
                cur = self._conn.execute(
                    """
                    UPDATE tasks
                    SET state = 'Running',
                        lease_owner = ?,
                        lease_expires_at = ?,
                        updated_at = ?
                    WHERE id = ?
                      AND (
                          state IN ('Scheduled', 'Queued')
                          OR (state = 'Running' AND (lease_expires_at IS NULL OR lease_expires_at <= ?))
                      )
                    """,
                    (lease_owner, lease_expires_at, now, task_id, now),
                )
                if cur.rowcount == 0:
                    raise DuplicateTaskExecutionError(
                        f"Task {task_id} could not acquire lease (already running or state changed)"
                    )
            return self.get_task(task_id, include_executions=True)

    def release_task_lease(self, task_id: str, *, now_iso: str | None = None) -> None:
        now = now_iso or _now()
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE tasks SET lease_owner = NULL, lease_expires_at = NULL, updated_at = ? WHERE id = ?",
                (now, task_id),
            )

    def save_task_checkpoint(
        self,
        task_id: str,
        checkpoint: dict | None,
        *,
        now_iso: str | None = None,
    ) -> dict:
        return self.update_task(task_id, checkpoint=checkpoint, now_iso=now_iso)

    def delete_task(self, task_id: str) -> bool:
        with self._lock:
            existing = self.get_task(task_id)
            if existing is None:
                raise NotFoundError(f"Task not found: {task_id}")
            with self._conn:
                self._conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        return True

    # ---- task_executions -------------------------------------------------
    def create_task_execution(
        self,
        *,
        task_id: str,
        started_at: str | None = None,
        retry_number: int = 0,
        outcome: str = "running",
    ) -> dict:
        eid = uuid.uuid4().hex
        now = started_at or _now()
        try:
            with self._lock, self._conn:
                self._conn.execute(
                    """
                    INSERT INTO task_executions (
                        id, task_id, started_at, finished_at, outcome,
                        retry_number, tool_calls_json, error_details,
                        output_summary, output_refs_json, resource_usage_json
                    ) VALUES (?, ?, ?, NULL, ?, ?, '[]', NULL, NULL, '[]', NULL)
                    """,
                    (eid, task_id, now, outcome, int(retry_number)),
                )
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to create task execution: {e}") from e
        return self.get_task_execution(eid)

    def finish_task_execution(
        self,
        execution_id: str,
        *,
        finished_at: str | None = None,
        outcome: str,
        tool_calls: list[dict] | None = None,
        error_details: str | None = None,
        output_summary: str | None = None,
        output_refs: list[str] | None = None,
        resource_usage: dict | None = None,
    ) -> dict:
        now = finished_at or _now()
        clean_summary = (
            (output_summary[:2000] + "…")
            if output_summary and len(output_summary) > 2000
            else output_summary
        )
        with self._lock:
            existing = self.get_task_execution(execution_id)
            if existing is None:
                raise NotFoundError(f"Task execution not found: {execution_id}")
            with self._conn:
                self._conn.execute(
                    """
                    UPDATE task_executions
                    SET finished_at = ?,
                        outcome = ?,
                        tool_calls_json = ?,
                        error_details = ?,
                        output_summary = ?,
                        output_refs_json = ?,
                        resource_usage_json = ?
                    WHERE id = ?
                    """,
                    (
                        now,
                        outcome,
                        json.dumps(tool_calls or [], ensure_ascii=False),
                        error_details,
                        clean_summary,
                        json.dumps(output_refs or [], ensure_ascii=False),
                        json.dumps(resource_usage, ensure_ascii=False) if resource_usage is not None else None,
                        execution_id,
                    ),
                )
            return self.get_task_execution(execution_id)

    def _row_to_task_execution(self, r: sqlite3.Row) -> dict:
        return {
            "id": r["id"],
            "task_id": r["task_id"],
            "started_at": r["started_at"],
            "finished_at": r["finished_at"],
            "outcome": r["outcome"],
            "retry_number": int(r["retry_number"]),
            "tool_calls": json.loads(r["tool_calls_json"]) if r["tool_calls_json"] else [],
            "error_details": r["error_details"],
            "output_summary": r["output_summary"],
            "output_refs": json.loads(r["output_refs_json"]) if r["output_refs_json"] else [],
            "resource_usage": json.loads(r["resource_usage_json"]) if r["resource_usage_json"] else None,
        }

    def get_task_execution(self, execution_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM task_executions WHERE id = ?", (execution_id,)
            ).fetchone()
        if row is None:
            return None
        return self._row_to_task_execution(row)

    def list_task_executions(self, task_id: str | None = None, limit: int = 100) -> list[dict]:
        where = "WHERE task_id = ?" if task_id else ""
        params: list[object] = [task_id, limit] if task_id else [limit]
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT * FROM task_executions
                {where}
                ORDER BY started_at DESC, rowid DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [self._row_to_task_execution(r) for r in rows]

    # ---- notifications ---------------------------------------------------
    def _row_to_notification(self, r: sqlite3.Row) -> dict:
        return {
            "id": r["id"],
            "task_id": r["task_id"],
            "execution_id": r["execution_id"],
            "kind": r["kind"],
            "title": r["title"],
            "message": r["message"],
            "severity": r["severity"],
            "read": bool(r["read"]),
            "dismissed": bool(r["dismissed"]),
            "dedup_key": r["dedup_key"],
            "created_at": r["created_at"],
            "read_at": r["read_at"],
            "dismissed_at": r["dismissed_at"],
            "metadata": json.loads(r["metadata_json"]) if r["metadata_json"] else {},
        }

    def create_notification(
        self,
        *,
        kind: str,
        title: str,
        message: str,
        severity: str = "info",
        task_id: str | None = None,
        execution_id: str | None = None,
        dedup_key: str | None = None,
        metadata: dict | None = None,
        created_at: str | None = None,
    ) -> dict:
        clean_title = (title or "").strip()
        clean_msg = (message or "").strip()
        if not clean_title or not clean_msg:
            raise ValueError("Notification title and message must not be blank")
        now = created_at or _now()
        nid = uuid.uuid4().hex
        with self._lock:
            if dedup_key:
                existing_row = self._conn.execute(
                    "SELECT * FROM notifications WHERE dedup_key = ?", (dedup_key,)
                ).fetchone()
                if existing_row is not None:
                    return self._row_to_notification(existing_row)
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO notifications (
                        id, task_id, execution_id, kind, title, message,
                        severity, read, dismissed, dedup_key, created_at,
                        read_at, dismissed_at, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, NULL, NULL, ?)
                    """,
                    (
                        nid,
                        task_id,
                        execution_id,
                        kind,
                        clean_title,
                        clean_msg,
                        severity,
                        dedup_key,
                        now,
                        json.dumps(metadata or {}, ensure_ascii=False),
                    ),
                )
            return self.get_notification(nid)

    def get_notification(self, notification_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM notifications WHERE id = ?", (notification_id,)
            ).fetchone()
        if row is None:
            return None
        return self._row_to_notification(row)

    def list_notifications(
        self,
        *,
        unread_only: bool = False,
        include_dismissed: bool = False,
        task_id: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        clauses: list[str] = []
        params: list[object] = []
        if not include_dismissed:
            clauses.append("dismissed = 0")
        if unread_only:
            clauses.append("read = 0")
        if task_id:
            clauses.append("task_id = ?")
            params.append(task_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT * FROM notifications
                {where}
                ORDER BY created_at DESC, rowid DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [self._row_to_notification(r) for r in rows]

    def count_unread_notifications(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS cnt FROM notifications WHERE read = 0 AND dismissed = 0"
            ).fetchone()
        return int(row["cnt"]) if row else 0

    def mark_notification_read(
        self,
        notification_id: str,
        *,
        read: bool = True,
        now_iso: str | None = None,
    ) -> dict:
        now = now_iso or _now()
        with self._lock:
            existing = self.get_notification(notification_id)
            if existing is None:
                raise NotFoundError(f"Notification not found: {notification_id}")
            with self._conn:
                self._conn.execute(
                    "UPDATE notifications SET read = ?, read_at = ? WHERE id = ?",
                    (1 if read else 0, now if read else None, notification_id),
                )
            return self.get_notification(notification_id)

    def mark_all_notifications_read(self, *, now_iso: str | None = None) -> int:
        now = now_iso or _now()
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE notifications SET read = 1, read_at = ? WHERE read = 0 AND dismissed = 0",
                (now,),
            )
            return int(cur.rowcount)

    def dismiss_notification(
        self,
        notification_id: str,
        *,
        now_iso: str | None = None,
    ) -> dict:
        now = now_iso or _now()
        with self._lock:
            existing = self.get_notification(notification_id)
            if existing is None:
                raise NotFoundError(f"Notification not found: {notification_id}")
            with self._conn:
                self._conn.execute(
                    "UPDATE notifications SET dismissed = 1, read = 1, dismissed_at = ?, read_at = COALESCE(read_at, ?) WHERE id = ?",
                    (now, now, notification_id),
                )
            return self.get_notification(notification_id)

    def close(self) -> None:
        with self._lock:
            self._conn.close()
