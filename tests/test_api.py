import json

import httpx
import pytest

from backend.app import create_app
from backend.engine import Engine


def make_app(settings, loader, load=True):
    engine = Engine(settings, loader)
    app = create_app(settings, engine=engine, load_model=False)
    if load:
        engine.load()
    return app, engine


@pytest.fixture
async def client(tmp_settings, fake_loader):
    app, _ = make_app(tmp_settings, fake_loader)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as c:
            yield c


def parse_sse(text):
    return [json.loads(line[5:]) for line in text.split("\n\n") if line.startswith("data:")]


async def test_health(client):
    r = await client.get("/api/health")
    assert r.status_code == 200 and r.json() == {"status": "ok", "model_state": "ready"}


async def test_status_reports_model_and_offline(client):
    s = (await client.get("/api/status")).json()
    assert s["state"] == "ready"
    assert s["model"]["architecture"] == "llama"
    assert s["offline_enforced"] is True
    assert s["settings"]["offload_kqv"] is False


async def test_chat_non_streaming_and_persistence(client):
    r = await client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}], "stream": False})
    assert r.status_code == 200
    body = r.json()
    assert body["content"] == "Hello from the fake model."
    cid = r.headers["X-Conversation-Id"]
    assert body["conversation_id"] == cid
    hist = (await client.get(f"/api/conversations/{cid}")).json()["messages"]
    assert [(m["role"], m["content"]) for m in hist] == [("user", "hi"), ("assistant", "Hello from the fake model.")]


async def test_chat_streaming(client):
    r = await client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(r.text)
    assert "".join(e.get("delta", "") for e in events) == "Hello from the fake model."
    assert events[-1]["done"] is True
    assert events[-1]["conversation_id"] == r.headers["X-Conversation-Id"]


@pytest.mark.parametrize("payload", [
    {},
    {"messages": []},
    {"messages": [{"role": "user", "content": ""}]},
    {"messages": [{"role": "user", "content": "   "}]},
    {"messages": [{"role": "robot", "content": "hi"}]},
    {"messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "x"}]},
    {"messages": [{"role": "user", "content": "hi"}], "temperature": 5},
    {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 0},
    {"messages": [{"role": "user", "content": "hi"}], "top_p": 0},
    {"messages": [{"role": "user", "content": "hi"}], "conversation_id": "../../etc"},
    {"messages": [{"role": "user", "content": "x" * 32001}]},
])
async def test_invalid_input_rejected(client, payload):
    r = await client.post("/api/chat", json=payload)
    assert r.status_code == 422


async def test_malformed_json_rejected(client):
    r = await client.post("/api/chat", content=b"{not json", headers={"content-type": "application/json"})
    assert r.status_code == 422


async def test_prompt_too_long_is_400(client):
    r = await client.post("/api/chat", json={"messages": [{"role": "user", "content": "w " * 2000}]})
    assert r.status_code == 400 and "too long" in r.json()["detail"]


async def test_invalid_conversation_id_path(client):
    assert (await client.get("/api/conversations/not-hex")).status_code == 400


async def test_frontend_served(client):
    r = await client.get("/")
    assert r.status_code == 200 and "<h1" in r.text and 'lang="en"' in r.text


async def test_model_load_error_returns_503(tmp_settings, fake_loader):
    tmp_settings.model_path = "C:/missing/model.gguf"
    app, _ = make_app(tmp_settings, fake_loader)
    async with app.router.lifespan_context(app):
        t = httpx.ASGITransport(app=app, client=("127.0.0.1", 1))
        async with httpx.AsyncClient(transport=t, base_url="http://127.0.0.1") as c:
            assert (await c.get("/api/status")).json()["state"] == "error"
            r = await c.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})
            assert r.status_code == 503 and "not found" in r.json()["detail"]
            r = await c.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}], "stream": False})
            assert r.status_code == 503


async def test_model_not_loaded_yet_returns_503(tmp_settings, fake_loader):
    app, _ = make_app(tmp_settings, fake_loader, load=False)
    async with app.router.lifespan_context(app):
        t = httpx.ASGITransport(app=app, client=("127.0.0.1", 1))
        async with httpx.AsyncClient(transport=t, base_url="http://127.0.0.1") as c:
            r = await c.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})
            assert r.status_code == 503


async def test_remote_client_rejected(tmp_settings, fake_loader):
    app, _ = make_app(tmp_settings, fake_loader)
    async with app.router.lifespan_context(app):
        t = httpx.ASGITransport(app=app, client=("192.168.1.50", 1))
        async with httpx.AsyncClient(transport=t, base_url="http://127.0.0.1") as c:
            assert (await c.get("/api/health")).status_code == 403


async def test_foreign_host_header_rejected(client):
    r = await client.get("/api/health", headers={"host": "attacker.example:8000"})
    assert r.status_code == 403
    assert (await client.get("/api/health", headers={"host": "localhost:8000"})).status_code == 200
