"""Offline guarantees: the network guard works, and real inference succeeds with it on."""
import socket

import pytest

from backend import offline
from backend.config import load_settings
from backend.engine import Engine


def test_guard_blocks_external_connections():
    offline.enable()
    s = socket.socket()
    try:
        with pytest.raises(offline.OfflineViolation):
            s.connect(("93.184.216.34", 80))     # blocked before any packet is sent
        with pytest.raises(offline.OfflineViolation):
            s.connect(("example.com", 443))       # hostnames are not provably local
    finally:
        s.close()
    assert len(offline.blocked_attempts) == 2


def test_guard_blocks_http_libraries():
    import urllib.request
    offline.enable()
    with pytest.raises(Exception) as exc:
        urllib.request.urlopen("http://example.com", timeout=3)
    assert "offline" in str(exc.value).lower()


def test_guard_allows_loopback():
    offline.enable()
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    c = socket.socket()
    try:
        c.connect(srv.getsockname())  # must not raise
    finally:
        c.close()
        srv.close()


def test_guard_sets_offline_env(monkeypatch):
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    offline.enable()
    import os
    assert os.environ["HF_HUB_OFFLINE"] == "1"


def test_no_cloud_or_ollama_code_paths():
    """The backend must not reference cloud LLM SDKs or Ollama."""
    from conftest import ROOT
    banned = ("import openai", "from openai", "ollama", "anthropic", "api.openai.com", "from_pretrained")
    for f in (ROOT / "backend").glob("*.py"):
        text = f.read_text(encoding="utf-8").lower()
        for b in banned:
            assert b not in text, f"{f.name} contains {b!r}"


@pytest.mark.model
def test_real_inference_with_network_blocked():
    offline.enable()
    s = load_settings(n_ctx=1024)
    e = Engine(s)
    e.load()
    try:
        assert e.status.state == "ready", e.status.error
        out = e.generate([{"role": "user", "content": "What is 2+2? Reply with just the number."}],
                         max_tokens=8, temperature=0)
        assert "4" in out["content"]
        assert offline.blocked_attempts == [], "inference attempted network access"
    finally:
        e.close()
