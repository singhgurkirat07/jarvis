"""Phase 2 API & end-to-end integration tests: conversations, switching, search, memories, settings, restart."""
from __future__ import annotations

import json

import httpx
import pytest

from backend import offline
from backend.app import create_app
from backend.engine import Engine
from conftest import FakeLlama, write_gguf


def make_app(settings, loader, load=True):
    engine = Engine(settings, loader)
    app = create_app(settings, engine=engine, load_model=False)
    if load:
        engine.load()
    return app, engine


@pytest.fixture
async def client_and_llm(tmp_settings, fake_loader, fake_llm):
    app, engine = make_app(tmp_settings, fake_loader)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as c:
            yield c, fake_llm, fake_loader


async def test_conversation_lifecycle_and_switching(client_and_llm):
    client, fake_llm, _ = client_and_llm

    # 1. Create two independent conversations
    r1 = await client.post("/api/conversations", json={"title": "Rust CLI"})
    assert r1.status_code == 201
    cid1 = r1.json()["id"]

    r2 = await client.post("/api/conversations", json={"title": "Gardening Notes"})
    assert r2.status_code == 201
    cid2 = r2.json()["id"]

    # 2. Send a message in Conversation 1
    fake_llm.reply = "Use clap for Rust argument parsing."
    res1 = await client.post(
        "/api/chat",
        json={"conversation_id": cid1, "message": "Which crate parses CLI args in Rust?", "stream": False},
    )
    assert res1.status_code == 200

    # 3. Switch to Conversation 2 and send a message
    fake_llm.reply = "Water tomatoes deeply at the base."
    res2 = await client.post(
        "/api/chat",
        json={"conversation_id": cid2, "message": "How should I water tomato plants?", "stream": False},
    )
    assert res2.status_code == 200
    # Verify Conversation 2 prompt did NOT include Conversation 1's messages
    sent_conv2 = fake_llm.calls[-1]["messages"]
    assert all("Rust" not in m["content"] for m in sent_conv2)

    # 4. Switch back to Conversation 1 and send a follow-up (only passing the new user message)
    fake_llm.reply = "Clap supports derive macros."
    res1_followup = await client.post(
        "/api/chat",
        json={"conversation_id": cid1, "message": "Does it support derive macros?", "stream": False},
    )
    assert res1_followup.status_code == 200
    sent_conv1 = fake_llm.calls[-1]["messages"]
    # The backend must automatically load Conversation 1's prior turns from SQLite
    contents = [m["content"] for m in sent_conv1]
    assert "Which crate parses CLI args in Rust?" in contents
    assert "Use clap for Rust argument parsing." in contents
    assert "Does it support derive macros?" == contents[-1]
    assert all("tomato" not in c.lower() for c in contents)


async def test_rename_archive_restore_and_delete_confirmation(client_and_llm):
    client, _, _ = client_and_llm
    conv = (await client.post("/api/conversations", json={"title": "Initial Title"})).json()
    cid = conv["id"]

    # Rename
    r = await client.patch(f"/api/conversations/{cid}", json={"title": "Updated Title"})
    assert r.status_code == 200
    assert r.json()["conversation"]["title"] == "Updated Title"

    # Archive
    r_arch = await client.post(f"/api/conversations/{cid}/archive")
    assert r_arch.status_code == 200 and r_arch.json()["conversation"]["archived"] is True

    active_list = (await client.get("/api/conversations")).json()["conversations"]
    assert all(c["id"] != cid for c in active_list)
    archived_list = (await client.get("/api/conversations?archived_only=true")).json()["conversations"]
    assert [c["id"] for c in archived_list] == [cid]

    # Restore
    r_rest = await client.post(f"/api/conversations/{cid}/restore")
    assert r_rest.status_code == 200 and r_rest.json()["conversation"]["archived"] is False

    # Delete WITHOUT confirm=true must be rejected (400)
    r_del_no_confirm = await client.delete(f"/api/conversations/{cid}")
    assert r_del_no_confirm.status_code == 400
    assert "confirmation" in r_del_no_confirm.json()["detail"].lower()

    # Delete WITH confirm=true succeeds
    r_del = await client.delete(f"/api/conversations/{cid}?confirm=true")
    assert r_del.status_code == 200 and r_del.json()["deleted"] is True
    assert (await client.get(f"/api/conversations/{cid}")).status_code == 404


async def test_search_conversations_endpoint(client_and_llm):
    client, _, _ = client_and_llm
    c1 = (await client.post("/api/conversations", json={"title": "FastAPI Websockets"})).json()["id"]
    await client.post(
        "/api/chat",
        json={"conversation_id": c1, "message": "How do SSE streams work in Starlette?", "stream": False},
    )

    r_title = await client.get("/api/conversations/search?q=Websockets")
    assert r_title.status_code == 200
    assert [c["id"] for c in r_title.json()["conversations"]] == [c1]

    r_msg = await client.get("/api/search?q=Starlette")
    assert r_msg.status_code == 200
    assert [c["id"] for c in r_msg.json()["conversations"]] == [c1]


async def test_duplicate_client_msg_id_rejected_with_409(client_and_llm):
    client, _, _ = client_and_llm
    r1 = await client.post(
        "/api/chat",
        json={"message": "Hello once", "client_msg_id": "unique-turn-1", "stream": False},
    )
    assert r1.status_code == 200
    cid = r1.json()["conversation_id"]

    r2 = await client.post(
        "/api/chat",
        json={"conversation_id": cid, "message": "Hello duplicate", "client_msg_id": "unique-turn-1", "stream": False},
    )
    assert r2.status_code == 409


async def test_explicit_memory_saving_retrieval_crud_and_disable(client_and_llm):
    client, fake_llm, _ = client_and_llm

    # 1. Explicit "Remember that..." command in chat saves a memory automatically
    r_save = await client.post(
        "/api/chat",
        json={"message": "Remember that I prefer Python 3.12 and pytest for testing.", "stream": False},
    )
    assert r_save.status_code == 200
    saved_mem = r_save.json()["saved_memory"]
    assert saved_mem is not None
    assert "Python 3.12" in saved_mem["content"]
    mid = saved_mem["id"]

    # 2. List & Search memories via /api/memories
    mem_list = (await client.get("/api/memories?q=pytest")).json()["memories"]
    assert len(mem_list) == 1 and mem_list[0]["id"] == mid

    # 3. Edit memory
    r_edit = await client.patch(
        f"/api/memories/{mid}",
        json={"content": "I prefer Python 3.12, FastAPI, and pytest", "importance": 5},
    )
    assert r_edit.status_code == 200
    assert r_edit.json()["memory"]["importance"] == 5

    # 4. Ask a question in a brand-new conversation -> memory is retrieved and reported
    r_ask = await client.post(
        "/api/chat",
        json={"message": "What Python testing tool do I prefer?", "stream": False},
    )
    assert r_ask.status_code == 200
    retrieved = r_ask.json()["retrieved_memories"]
    assert any(m["id"] == mid for m in retrieved)
    assert "FastAPI, and pytest" in fake_llm.calls[-1]["messages"][0]["content"]

    # 5. Disable memory retrieval in settings -> memory is no longer retrieved
    await client.patch("/api/settings", json={"memory_enabled": False})
    r_disabled = await client.post(
        "/api/chat",
        json={"message": "What Python testing tool do I prefer?", "stream": False},
    )
    assert r_disabled.status_code == 200
    assert r_disabled.json()["retrieved_memories"] == []
    assert "FastAPI, and pytest" not in fake_llm.calls[-1]["messages"][0]["content"]

    # 6. Delete memory
    r_del = await client.delete(f"/api/memories/{mid}")
    assert r_del.status_code == 200 and r_del.json()["deleted"] is True
    assert (await client.get(f"/api/memories/{mid}")).status_code == 404


async def test_settings_validation_saved_vs_active_and_model_reload_confirmation(client_and_llm, tmp_path):
    client, _, fake_loader = client_and_llm
    initial_loads = len(fake_loader.calls)

    # 1. Hot-reloadable settings apply immediately without reloading the model
    r_hot = await client.put(
        "/api/settings",
        json={"temperature": 0.25, "theme": "midnight", "stream_responses": False},
    )
    assert r_hot.status_code == 200
    body = r_hot.json()
    assert body["active"]["temperature"] == 0.25
    assert body["active"]["theme"] == "midnight"
    assert body["saved"]["temperature"] == 0.25
    assert body["reload_required"] is False
    assert len(fake_loader.calls) == initial_loads  # model was NOT reloaded

    # 2. Invalid settings are rejected (422)
    assert (await client.put("/api/settings", json={"temperature": 9.9})).status_code == 422
    assert (await client.put("/api/settings", json={"theme": "neon-pink"})).status_code == 422
    assert (await client.put("/api/settings", json={"offload_kqv": True, "flash_attn": False})).status_code == 422
    assert (await client.put("/api/settings", json={"model_path": str(tmp_path / "missing.gguf"), "save_only": True})).status_code == 422

    # 3. Changing n_ctx (a model-reload setting) WITHOUT confirmation returns 409 Conflict
    r_conflict = await client.put("/api/settings", json={"n_ctx": 1024})
    assert r_conflict.status_code == 409
    assert len(fake_loader.calls) == initial_loads

    # 4. Saving n_ctx with save_only=True saves to SQLite without reloading active engine
    r_save_only = await client.put("/api/settings", json={"n_ctx": 1024, "save_only": True})
    assert r_save_only.status_code == 200
    st = r_save_only.json()
    assert st["saved"]["n_ctx"] == 1024
    assert st["active"]["n_ctx"] == 512
    assert st["reload_required"] is True
    assert "n_ctx" in st["pending_reload_fields"]
    assert len(fake_loader.calls) == initial_loads

    # 5. Explicit confirmation via /api/settings/reload-model reloads the model
    assert (await client.post("/api/settings/reload-model", json={"confirm": False})).status_code == 409
    r_reload = await client.post("/api/settings/reload-model", json={"confirm": True})
    assert r_reload.status_code == 200
    st_after = r_reload.json()
    assert st_after["active"]["n_ctx"] == 1024
    assert st_after["reload_required"] is False
    assert len(fake_loader.calls) == initial_loads + 1


async def test_application_restart_persistence_and_offline(tmp_settings, fake_loader):
    """Verify conversations, messages, memories, and settings survive a full application restart offline."""
    offline.enable()

    baseline = tmp_settings.copy()

    # Session 1: create conversation, send turn, save memory, update setting
    app1, _ = make_app(tmp_settings.copy(), fake_loader)
    async with app1.router.lifespan_context(app1):
        t1 = httpx.ASGITransport(app=app1, client=("127.0.0.1", 50001))
        async with httpx.AsyncClient(transport=t1, base_url="http://127.0.0.1:8000") as c1:
            chat_res = (
                await c1.post(
                    "/api/chat",
                    json={"message": "Remember that my server port is 8000", "stream": False},
                )
            ).json()
            cid = chat_res["conversation_id"]
            mid = chat_res["saved_memory"]["id"]
            await c1.patch(f"/api/conversations/{cid}", json={"title": "Persisted Session Chat"})
            await c1.put("/api/settings", json={"temperature": 0.35, "theme": "light"})

    # Session 2: fresh Settings & fresh App instance pointing at the same SQLite database file
    fresh_settings = baseline.copy()
    assert fresh_settings.temperature == 0.7  # default before DB load
    app2, _ = make_app(fresh_settings, fake_loader)
    async with app2.router.lifespan_context(app2):
        t2 = httpx.ASGITransport(app=app2, client=("127.0.0.1", 50002))
        async with httpx.AsyncClient(transport=t2, base_url="http://127.0.0.1:8000") as c2:
            # Verify settings persisted and loaded into startup
            st = (await c2.get("/api/settings")).json()
            assert st["active"]["temperature"] == 0.35
            assert st["active"]["theme"] == "light"

            # Verify conversation and messages persisted
            conv = (await c2.get(f"/api/conversations/{cid}")).json()
            assert conv["conversation"]["title"] == "Persisted Session Chat"
            assert len(conv["messages"]) == 2

            # Verify memory persisted
            mem = (await c2.get(f"/api/memories/{mid}")).json()["memory"]
            assert "server port is 8000" in mem["content"]

    assert offline.blocked_attempts == []
