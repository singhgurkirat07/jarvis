"""Graceful shutdown: in-process uvicorn server (fake model) and a real server subprocess (real model)."""
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request

import pytest
import uvicorn

from backend.app import create_app
from backend.engine import Engine
from backend.gpu import detect_gpu
from conftest import ROOT, FakeLlama


def wait_for(url, predicate, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                data = json.loads(r.read())
                if predicate(data):
                    return data
        except OSError:
            pass
        time.sleep(0.5)
    raise TimeoutError(url)


def test_inprocess_server_shutdown_frees_model(tmp_settings):
    llm = FakeLlama()
    engine = Engine(tmp_settings, lambda s, n: llm)
    app = create_app(tmp_settings, engine=engine)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8771, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    wait_for("http://127.0.0.1:8771/api/health", lambda d: d["model_state"] == "ready", 20)

    server.should_exit = True
    t.join(15)
    assert not t.is_alive(), "server did not stop"
    assert engine.status.state == "closed"
    assert llm.closed, "model was not released on shutdown"


@pytest.mark.model
def test_real_server_ctrl_break_graceful_shutdown(tmp_path):
    env = {**os.environ, "JARVIS_LOG_DIR": str(tmp_path / "logs"), "JARVIS_DB_PATH": str(tmp_path / "j.db"),
           "PYTHONUNBUFFERED": "1"}
    p = subprocess.Popen([sys.executable, "-m", "backend", "--port", "8772"], cwd=ROOT, env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    try:
        status = wait_for("http://127.0.0.1:8772/api/status", lambda d: d["state"] in ("ready", "error"), 120)
        assert status["state"] == "ready", status.get("error")
        vram_loaded = detect_gpu().used_mb

        p.send_signal(signal.CTRL_BREAK_EVENT)
        out, _ = p.communicate(timeout=60)
    finally:
        if p.poll() is None:
            p.kill()
    assert p.returncode == 0, out[-2000:]
    assert "shutting down" in out and "shutdown complete" in out, out[-2000:]
    log = (tmp_path / "logs" / "jarvis.log").read_text(encoding="utf-8")
    assert '"msg": "engine closed"' in log
    if status["n_gpu_layers"] != 0 and detect_gpu().available:
        time.sleep(1)
        assert detect_gpu().used_mb < vram_loaded - 2000, "VRAM was not released"
