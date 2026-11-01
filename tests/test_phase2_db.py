"""Phase 2 SQLite database tests: initialization, Phase 1->2 migration, CRUD, ordering, dedup, errors."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from backend.db import (
    Database,
    DatabaseError,
    DuplicateMessageError,
    NotFoundError,
)


def test_db_initialization_and_migrations_recorded(tmp_path):
    db_file = tmp_path / "init.db"
    db = Database(db_file)
    try:
        migrations = db.get_migrations()
        assert [m["version"] for m in migrations] == [1, 2]
        assert migrations[1]["name"] == "002_phase2_conversations_settings_memories"
    finally:
        db.close()


def test_phase1_database_migrates_without_losing_data(tmp_path):
    """Simulate a Phase 1 SQLite database (only the original messages table) and upgrade it."""
    db_file = tmp_path / "phase1.db"
    conn = sqlite3.connect(str(db_file))
    conn.executescript(
        """
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('system', 'user', 'assistant')),
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            completion_tokens INTEGER,
            tokens_per_second REAL,
            seconds REAL
        );
        INSERT INTO messages (conversation_id, role, content, created_at, completion_tokens, tokens_per_second, seconds)
        VALUES
            ('847c270bd7034b808f57af41ddfe0833', 'user', 'What is 2+2?', '2026-10-31T17:05:50+00:00', NULL, NULL, NULL),
            ('847c270bd7034b808f57af41ddfe0833', 'assistant', '2+2 is 4.', '2026-10-31T17:05:51+00:00', 7, 9.74, 0.718);
        """
    )
    conn.close()

    # Open with Phase 2 Database class -> runs migrations automatically
    db = Database(db_file)
    try:
        convs = db.list_conversations()
        assert len(convs) == 1
        assert convs[0]["id"] == "847c270bd7034b808f57af41ddfe0833"
        assert convs[0]["title"] == "What is 2+2?"
        assert convs[0]["message_count"] == 2

        msgs = db.get_conversation("847c270bd7034b808f57af41ddfe0833")
        assert [(m["role"], m["content"]) for m in msgs] == [
            ("user", "What is 2+2?"),
            ("assistant", "2+2 is 4."),
        ]
        assert msgs[1]["completion_tokens"] == 7
        assert msgs[1]["tokens_per_second"] == pytest.approx(9.74)
    finally:
        db.close()


def test_conversation_crud_and_message_ordering(tmp_path):
    db = Database(tmp_path / "crud.db")
    try:
        c1 = db.create_conversation(title="Project Alpha")
        cid = c1["id"]
        assert c1["title"] == "Project Alpha"
        assert "+00:00" in c1["created_at"]  # timezone-aware UTC

        for i in range(1, 6):
            db.add_turn(cid, f"User question {i}", f"Assistant answer {i}", stats={"completion_tokens": i})

        msgs = db.get_conversation(cid)
        assert len(msgs) == 10
        expected_contents = []
        for i in range(1, 6):
            expected_contents.extend([f"User question {i}", f"Assistant answer {i}"])
        assert [m["content"] for m in msgs] == expected_contents
        assert [m["id"] for m in msgs] == sorted(m["id"] for m in msgs)

        # Rename
        renamed = db.update_conversation(cid, title="Project Alpha Renamed")
        assert renamed["title"] == "Project Alpha Renamed"

        # Archive & Restore
        archived = db.update_conversation(cid, archived=True)
        assert archived["archived"] is True
        assert db.list_conversations(include_archived=False) == []
        assert len(db.list_conversations(archived_only=True)) == 1

        restored = db.update_conversation(cid, archived=False)
        assert restored["archived"] is False
        assert len(db.list_conversations(include_archived=False)) == 1
    finally:
        db.close()


def test_duplicate_message_prevention(tmp_path):
    db = Database(tmp_path / "dedup.db")
    try:
        cid = db.new_conversation_id()
        db.add_message(cid, "user", "First send", client_msg_id="msg-001")
        with pytest.raises(DuplicateMessageError):
            db.add_message(cid, "user", "Retry send", client_msg_id="msg-001")

        # Same client_msg_id in a DIFFERENT conversation is allowed
        other_cid = db.new_conversation_id()
        db.add_message(other_cid, "user", "Other conv send", client_msg_id="msg-001")
        assert len(db.get_conversation(cid)) == 1
        assert len(db.get_conversation(other_cid)) == 1
    finally:
        db.close()


def test_interrupted_turn_write_rolls_back(tmp_path):
    """If an error occurs mid-transaction, neither message of the turn should be committed."""
    db = Database(tmp_path / "tx.db")
    try:
        cid = db.new_conversation_id()
        # Passing an invalid role inside a raw transaction must roll back the entire block
        with pytest.raises(sqlite3.IntegrityError):
            with db._lock, db._conn:
                db._ensure_conversation_locked(cid, "Valid start")
                db._insert_message_locked(cid, "user", "Valid user message")
                db._conn.execute(
                    "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, 'invalid_role', 'bad', 'now')",
                    (cid,),
                )
        assert db.get_conversation(cid) == []
    finally:
        db.close()


def test_delete_conversation_cascades_messages_but_preserves_memories(tmp_path):
    db = Database(tmp_path / "del.db")
    try:
        conv = db.create_conversation(title="Temp Chat")
        cid = conv["id"]
        db.add_turn(cid, "Remember that I use Neovim", "Noted.")
        mem = db.create_memory("I use Neovim", category="preference", importance=4, source_conversation_id=cid)

        db.delete_conversation(cid)
        assert db.get_conversation_meta(cid) is None
        assert db.get_conversation(cid) == []

        # Memory must remain intact even after source conversation is deleted
        kept_mem = db.get_memory(mem["id"])
        assert kept_mem is not None
        assert kept_mem["content"] == "I use Neovim"
        assert kept_mem["source_conversation_id"] is None
    finally:
        db.close()


def test_search_conversations_titles_and_messages(tmp_path):
    db = Database(tmp_path / "search.db")
    try:
        c1 = db.create_conversation(title="SQLite Migration Guide")
        db.add_turn(c1["id"], "How do foreign keys work?", "Use PRAGMA foreign_keys=ON.")

        c2 = db.create_conversation(title="Weekend Cooking")
        db.add_turn(c2["id"], "Recipe for sourdough_bread 100% hydration", "Mix flour and water.")

        by_title = db.search_conversations("Migration")
        assert [c["id"] for c in by_title] == [c1["id"]]
        assert by_title[0]["match_type"] == "title"

        by_msg = db.search_conversations("sourdough_bread 100%")
        assert [c["id"] for c in by_msg] == [c2["id"]]
        assert by_msg[0]["match_type"] == "message"
    finally:
        db.close()


def test_database_error_handling_on_invalid_operations(tmp_path):
    db = Database(tmp_path / "err.db")
    try:
        with pytest.raises(NotFoundError):
            db.update_conversation("a" * 32, title="Missing")
        with pytest.raises(NotFoundError):
            db.delete_conversation("a" * 32)
        with pytest.raises(NotFoundError):
            db.update_memory("b" * 32, content="Missing")
        with pytest.raises(NotFoundError):
            db.delete_memory("b" * 32)
        with pytest.raises(ValueError):
            db.create_memory("   ")
        with pytest.raises(ValueError):
            db.create_memory("Valid fact", importance=9)
    finally:
        db.close()
