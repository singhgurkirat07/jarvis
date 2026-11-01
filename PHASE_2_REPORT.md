# JARVIS — Phase 2 Completion Report

**Date:** 2026-10-31  
**Phase:** Phase 2 — Persistent Chat History, Settings & Memory  
**Status:** Complete (awaiting user approval before any Git commit or Phase 3 work)

---

## 1. Pre-Change Audit Summary

Before writing any Phase 2 code:
1. **Workspace & Git Root Verified:** `C:\Users\Gurkirat Singh\Desktop\jarvis` (Git repository root: `C:/Users/Gurkirat Singh/Desktop/jarvis`).
2. **Phase 1 Report & Codebase Inspected:** Reviewed `PHASE_1_REPORT.md`, `backend/`, `frontend/`, `config/settings.json`, and `tests/`.
3. **Phase 1 Regression Suite Executed Prior to Changes:** All **72 Phase 1 tests passed** (`28.85 s`).
4. **Existing SQLite Database Inspected (`data/jarvis.db`):** Found 6 existing messages across 3 Phase 1 conversations (`847c270bd7034b808f57af41ddfe0833`, `572f14e2ac754c06b686b09b4fc38ff6`, `020958beccfb414987fe4a2043bb0e3d`) stored in the Phase 1 `messages` table. All 3 conversations and all 6 messages were migrated non-destructively into the Phase 2 schema.

---

## 2. Files Created and Modified

### Created Files
1. `backend/context.py` — Deterministic token-budget `ContextManager` between SQLite history, retrieved long-term memories, system prompt, and `llama.cpp`.
2. `backend/memory.py` — Explicit remember-command parser (`extract_explicit_memory`), sensitive-secret filter, deterministic keyword + importance memory ranker (`retrieve_memories`), and safe contextual prompt formatter.
3. `backend/services.py` — Dedicated service layer (`SettingsService`, `MemoryService`, `ConversationService`, `ChatService`) with 32-char hex ID validation, model-reload confirmation enforcement, and turn orchestration.
4. `tests/test_phase2_db.py` — 7 database unit/integration tests (initialization, Phase 1 → Phase 2 migration, conversation CRUD & message ordering, duplicate message prevention, transaction rollback on interrupted write, conversation delete preserving memories, and search).
5. `tests/test_phase2_context_memory.py` — 18 unit/integration tests for explicit memory extraction, non-memory/question/secret rejection, memory retrieval & disabled mode, context window trimming, and SQLite data preservation on long conversations.
6. `tests/test_phase2_api.py` — 7 end-to-end API tests covering conversation creation/switching, rename/archive/restore/delete confirmation, search, duplicate `client_msg_id` rejection, explicit memory lifecycle, settings validation + model-reload confirmation, and full application restart persistence offline.
7. `PHASE_2_REPORT.md` — This completion report.

### Modified Files
1. `config/settings.json` — Added default Phase 2 application settings (`theme`, `stream_responses`, `conversation_history_enabled`, `memory_enabled`, `default_new_conversation_behavior`) while preserving Phase 1 safe CUDA defaults (`n_gpu_layers: "auto"`, `offload_kqv: false`, `flash_attn: false`).
2. `backend/config.py` — Extended `Settings` dataclass, added `validate_settings_values()` (including range checks, GGUF path validation, and RTX 5050 `offload_kqv` safety guard), and defined `MODEL_RELOAD_FIELDS` vs `USER_EDITABLE_FIELDS`.
3. `backend/db.py` — Upgraded to full versioned SQLite persistence layer with `schema_migrations`, `conversations`, `messages` (foreign key + deduplication + metadata), `user_settings`, and `memories`.
4. `backend/engine.py` — Added `count_tokens()` helper for `ContextManager`, `reload(new_settings)` for explicitly confirmed model reloads, and per-call `repeat_penalty` support.
5. `backend/app.py` — Wired service layer and added validated FastAPI endpoints for conversations, search, settings, model reload, and memories while preserving full backward compatibility with all Phase 1 routes.
6. `frontend/index.html` — Extended interface with collapsible conversation sidebar, search input, Active/Archived tabs, Memory status indicator, Rename/Delete confirmation dialogs, Long-Term Memory modal, and Settings modal.
7. `frontend/styles.css` — Added responsive layout rules for desktop (`1280px`), tablet (`768px`), and mobile (`375px`), plus `dark`, `midnight`, and `light` themes.
8. `frontend/app.js` — Implemented conversation switching, live search, rename/archive/restore/delete confirmation, memory CRUD & retrieval pills, and settings management with explicit model-reload confirmation.
9. `README.md` — Updated documentation with Phase 2 architecture, endpoints, and test suite instructions.

---

## 3. SQLite Database Schema & Migration Strategy

### 3.1 Database Schema (`data/jarvis.db`)

Configured with `PRAGMA journal_mode=WAL`, `PRAGMA synchronous=NORMAL`, `PRAGMA foreign_keys=ON`, and `PRAGMA busy_timeout=5000`:

```sql
CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0, 1))
);
CREATE INDEX idx_conversations_updated ON conversations(archived, updated_at DESC);

CREATE TABLE messages (
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
CREATE INDEX idx_messages_conversation ON messages(conversation_id, id ASC);

CREATE TABLE user_settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE memories (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'general',
    importance INTEGER NOT NULL DEFAULT 3 CHECK (importance BETWEEN 1 AND 5),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    source_conversation_id TEXT
);
CREATE INDEX idx_memories_updated ON memories(category, importance DESC, updated_at DESC);
```

### 3.2 Non-Destructive Migration Strategy
- `Database._migrate()` tracks applied migrations in `schema_migrations`.
- **Migration 1 (`001_initial_messages`):** Ensures the baseline `messages` table exists.
- **Migration 2 (`002_phase2_conversations_settings_memories`):**
  1. Creates `conversations`, `user_settings`, and `memories`.
  2. Queries any pre-existing `conversation_id` values in `messages` that do not yet have a parent row in `conversations`, and backfills `conversations` using the first user message to derive the title (`derive_title()`) and `MIN(created_at)` / `MAX(created_at)` for timestamps.
  3. Rebuilds `messages` inside a transaction to attach `REFERENCES conversations(id) ON DELETE CASCADE`, `UNIQUE(conversation_id, client_msg_id)`, and metadata columns (`client_msg_id`, `prompt_tokens`, `finish_reason`, `metadata_json`) while copying 100% of existing message rows and IDs in order.

---

## 4. API Endpoints

| Method | Path | Purpose | Status Codes |
|---|---|---|---|
| `GET` | `/api/health` | Liveness and `model_state` | `200` |
| `GET` | `/api/status` | Engine status, GPU info, offline guard, active settings, and `reload_required` flag | `200` |
| `POST` | `/api/chat` | Chat completion (SSE stream or JSON) with SQLite history, context management, and memory retrieval | `200`, `400`, `409`, `422`, `429`, `503` |
| `POST` | `/api/conversations` | Create a new conversation | `201`, `422` |
| `GET` | `/api/conversations` | List active or archived conversations (`include_archived`, `archived_only`, `limit`) | `200` |
| `GET` | `/api/conversations/search` (and `/api/search`) | Search conversation titles and message contents (`?q=...`) | `200` |
| `GET` | `/api/conversations/{id}` | Retrieve conversation metadata and ordered message history | `200`, `400`, `404` |
| `PATCH` / `PUT` | `/api/conversations/{id}` | Rename conversation and/or toggle archive state | `200`, `400`, `404`, `422` |
| `POST` | `/api/conversations/{id}/archive` | Archive a conversation | `200`, `400`, `404` |
| `POST` | `/api/conversations/{id}/restore` | Restore an archived conversation | `200`, `400`, `404` |
| `DELETE` | `/api/conversations/{id}?confirm=true` | Delete a conversation and its messages (`400` if `confirm` is not `true`; preserves memories) | `200`, `400`, `404` |
| `GET` | `/api/settings` | Read `saved` settings, `active` runtime settings, `reload_required`, and `pending_reload_fields` | `200` |
| `PUT` / `PATCH` | `/api/settings` | Validate and persist settings (`409` if changing model-reload fields without `confirm_model_reload` or `save_only`) | `200`, `409`, `422` |
| `POST` | `/api/settings/reload-model` | Reload the model using saved settings (`{"confirm": true}` required) | `200`, `409`, `422` |
| `GET` | `/api/memories` | List or search saved long-term memories (`?category=...&q=...`) | `200` |
| `POST` | `/api/memories` | Create a long-term memory (`content`, `category`, `importance`) | `201`, `422` |
| `GET` | `/api/memories/{id}` | Retrieve a single memory by ID | `200`, `400`, `404` |
| `PATCH` / `PUT` | `/api/memories/{id}` | Edit a memory's content, category, or importance | `200`, `400`, `404`, `422` |
| `DELETE` | `/api/memories/{id}` | Delete a memory | `200`, `400`, `404` |

---

## 5. Architecture of Phase 2 Features

### 5.1 Conversation Management (`backend/services.py::ConversationService`)
- Conversations are created either explicitly (`POST /api/conversations`) or automatically on the first message of a new chat.
- When the first user message is saved into a `"New conversation"`, its title is automatically derived from the message (`derive_title()`).
- User and assistant turns are persisted atomically in a single SQLite transaction (`Database.add_turn()`), with optional `client_msg_id` deduplication (`UNIQUE(conversation_id, client_msg_id)`).
- Switching conversations in the UI (or passing `conversation_id` to `/api/chat`) loads that conversation's independent message history from SQLite.
- Deleting a conversation requires `confirm=true` (enforced both by a confirmation modal in the UI and by HTTP `400` in the API) and cascades to `messages` while leaving `memories` untouched.

### 5.2 Deterministic Context Management (`backend/context.py::ContextManager`)
- Reserves token budget for `max_tokens` (reply reservation), the base `system_prompt`, retrieved long-term memories, and the latest user message (`budget = n_ctx - max_tokens`).
- Uses `Engine.count_tokens()` (backed by `llama.cpp`'s tokenizer when loaded) plus per-message chat template overhead (`8` tokens/message).
- Walks backward from the most recent persisted message (`max_history_messages = 40`), selecting contiguous recent turns that fit within `budget`.
- If the prompt is tight, lowest-priority retrieved memories are trimmed before failing; if the single new user message + system prompt exceeds `budget`, raises `PromptTooLong` (`HTTP 400`).
- **Omitted historical messages are never deleted or modified in SQLite**; the response metadata reports `context_messages_used` and `context_messages_omitted`.

### 5.3 Explicit Long-Term Memory (`backend/memory.py` & `MemoryService`)
- **Explicit Saving Only:** `extract_explicit_memory()` only matches explicit user directives (`"Remember that ..."`, `"Jarvis, please remember ..."`, `"Save to memory: ..."`, `"Always remember my ..."`). Ordinary statements, questions (`"Do you remember ...?"`), and assistant responses are never converted into memories.
- **Sensitive Data Protection:** `_SENSITIVE_PATTERNS` blocks passwords, API keys, tokens, SSNs, and payment card numbers from being stored.
- **Deterministic Local Retrieval:** `retrieve_memories()` ranks saved memories by stemmed keyword overlap with the user query plus `importance` (`1–5`), with a top-importance fallback (`importance >= 3`, up to 3 items) so explicit user preferences remain available across conversations without embeddings or external services.
- **Prompt Safety:** `format_memories_for_system_prompt()` wraps retrieved memories in a `[Saved User Memories — Contextual Information Only]` block instructing the model never to treat them as instructions that override system rules.
- **Transparency:** Every `/api/chat` response returns `retrieved_memories` and `saved_memory`, which are persisted in `messages.metadata_json` and rendered as badges beneath the assistant message.

### 5.4 Settings Management (`backend/services.py::SettingsService`)
- Persists user-configured settings in SQLite `user_settings` and reloads them automatically on startup.
- Separates **hot-applied settings** (`temperature`, `top_p`, `max_tokens`, `repeat_penalty`, `system_prompt`, `theme`, `stream_responses`, `conversation_history_enabled`, `memory_enabled`, `default_new_conversation_behavior`) from **model-reload settings** (`model_path`, `n_gpu_layers`, `n_ctx`, `offload_kqv`, `flash_attn`).
- Changing a model-reload setting without `confirm_model_reload=true` or `save_only=true` returns `HTTP 409 Conflict` so the model is never reloaded silently.
- Preserves the verified RTX 5050 CUDA configuration (`offload_kqv: false`) and rejects unsafe `offload_kqv=true, flash_attn=false` combinations.

---

## 6. Automated Test Results (Phase 2 + Phase 1 Regression)

Command executed: `py -3.12 -m pytest -v`

- **Total Tests Collected & Executed:** `104`
- **Passed:** `104`
- **Failed:** `0`
- **Skipped:** `0`
- **Total Duration:** `29.56 s`

| Test Suite | File | Passed | Failed | Skipped |
|---|---|---:|---:|---:|
| Phase 1 — API & Security | `tests/test_api.py` | 23 | 0 | 0 |
| Phase 1 — Inference Engine & Lifecycle | `tests/test_engine.py` | 13 | 0 | 0 |
| Phase 1 — GPU Detection & Layer Planner | `tests/test_gpu.py` | 8 | 0 | 0 |
| Phase 1 — Real Model & GPU Inference | `tests/test_inference_real.py` | 6 | 0 | 0 |
| Phase 1 — Model Discovery & GGUF Validation | `tests/test_model_file.py` | 14 | 0 | 0 |
| Phase 1 — Offline Socket Enforcement | `tests/test_offline.py` | 6 | 0 | 0 |
| Phase 1 — Graceful Shutdown & VRAM Release | `tests/test_shutdown.py` | 2 | 0 | 0 |
| **Phase 2 — SQLite Schema, Migrations, Dedup, Search & Errors** | `tests/test_phase2_db.py` | **7** | **0** | **0** |
| **Phase 2 — Context Manager & Explicit Memory Logic** | `tests/test_phase2_context_memory.py` | **18** | **0** | **0** |
| **Phase 2 — API, Switching, Settings Reload & Restart Persistence** | `tests/test_phase2_api.py` | **7** | **0** | **0** |
| **Total** | **10 test modules** | **104** | **0** | **0** |

---

## 7. Working Live Demonstration (All 6 Required Steps)

Executed against the live FastAPI server (`py -3.12 -m backend`) with `qwen2.5-7b-instruct.gguf` on the NVIDIA RTX 5050 Laptop GPU and `data/jarvis.db`:

1. **Creating a Conversation (`POST /api/conversations`):**
   - Created conversation `da15045af08947a58ddfe0aa385efc6f` with title `"Phase 2 Architecture Demo"` at `2026-10-31T17:55:46.713129+00:00`.
   - All 3 pre-existing Phase 1 conversations in `data/jarvis.db` were also automatically migrated and preserved.
2. **Sending Messages & Saving an Explicit Memory (`POST /api/chat`):**
   - Sent Turn 1: `"Remember that my primary project codename is Aurora-7."`
     - Automatically extracted and saved explicit memory `a1ee272cb78846ad8724861ca2fdfcdb` (`content: "my primary project codename is Aurora-7"`, `category: "project"`, `importance: 3`).
     - Assistant reply (`20.0 tok/s`): *"Understood. Your primary project codename is Aurora-7. How can I assist you with this project?"*
   - Sent Turn 2: `"We are testing SQLite WAL persistence in this conversation. Confirm in one short sentence."`
     - Assistant reply (`24.88 tok/s`, `context_messages_used: 2`): *"SQLite Write-Ahead Log (WAL) persistence is confirmed for this conversation."*
3. **Changing Settings (`PUT /api/settings`):**
   - Updated `temperature` to `0.3` and `theme` to `"midnight"`. Confirmed both `saved` and `active` states updated immediately without reloading the model.
4. **Closing and Reopening Jarvis:**
   - Sent `CTRL_BREAK_EVENT` to Server Process 1; verified clean lifespan shutdown (`shutdown complete`, exit code `0`).
   - Started a fresh Server Process 2 (`py -3.12 -m backend`).
5. **Recovering Previous Conversation & Confirming Persisted Settings (`GET /api/conversations/{id}` & `GET /api/settings`):**
   - Recovered conversation `da15045af08947a58ddfe0aa385efc6f` (`"Phase 2 Architecture Demo"`) with all 4 messages in exact order.
   - Confirmed `GET /api/settings` after restart returned `saved.temperature = 0.3`, `active.temperature = 0.3`, `saved.theme = "midnight"`, `active.theme = "midnight"`.
6. **Retrieving the Explicitly Saved Memory in a New Conversation (`POST /api/chat`):**
   - Created new conversation `8a2551756930480f8791f4db115ac0ed` (`"Memory Recall Verification"`).
   - Sent message: `"What is my primary project codename? Reply in one short sentence."`
   - `retrieved_memories` returned `[{"id": "a1ee272cb78846ad8724861ca2fdfcdb", "content": "my primary project codename is Aurora-7", "category": "project", "importance": 3}]`.
   - Assistant reply: **"Your primary project codename is Aurora-7."**

---

## 8. Remaining Limitations

1. **Keyword & Importance Memory Retrieval:** As required for Phase 2, memory retrieval uses deterministic keyword/stem matching and importance ranking in SQLite rather than vector embeddings or cloud semantic search.
2. **Deterministic Sliding Context Window:** Long conversations use token-aware recent-turn selection (`ContextManager`) without background LLM summarization; all omitted messages remain safely stored in SQLite.
3. **Blackwell (`sm_120`) Standard KQV Kernel:** As documented in Phase 1, `offload_kqv` remains `false` by default (enforced by settings validation unless `flash_attn` is also enabled).
