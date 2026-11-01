"""Real model initialisation and inference with the configured (known-good) settings."""
import time

import pytest

from backend.config import load_settings
from backend.engine import Engine
from backend.gpu import detect_gpu


@pytest.fixture(scope="module")
def engine():
    e = Engine(load_settings())
    e.load()
    yield e
    e.close()


@pytest.mark.model
def test_model_initialises(engine):
    assert engine.status.state == "ready", engine.status.error
    assert engine.status.model["layers"] == 28
    assert engine.status.load_seconds < 120


@pytest.mark.model
@pytest.mark.gpu
def test_full_gpu_offload_with_safe_kqv(engine):
    assert engine.status.n_gpu_layers == -1, engine.status.gpu_plan
    assert engine.status.offload_kqv is False
    assert engine.status.gpu["used_mb_after_load"] - engine.status.gpu["used_mb"] > 3000


@pytest.mark.model
def test_deterministic_answer(engine):
    out = engine.generate([{"role": "user", "content": "What is the capital of France? One word."}],
                          max_tokens=10, temperature=0)
    assert "paris" in out["content"].lower()
    assert out["completion_tokens"] > 0 and out["tokens_per_second"] > 0


@pytest.mark.model
def test_streaming_inference(engine):
    events = list(engine.stream([{"role": "user", "content": "Count from 1 to 5, separated by commas."}],
                                max_tokens=30, temperature=0))
    text = "".join(e.get("delta", "") for e in events)
    assert "1" in text and "5" in text
    done = events[-1]
    assert done["done"] and done["completion_tokens"] > 3
    assert done["time_to_first_token"] is not None


@pytest.mark.model
def test_multi_turn_context(engine):
    out = engine.generate([
        {"role": "user", "content": "My name is Gurkirat. Remember it."},
        {"role": "assistant", "content": "Got it, Gurkirat."},
        {"role": "user", "content": "What is my name? Reply with only the name."},
    ], max_tokens=10, temperature=0)
    assert "gurkirat" in out["content"].lower()


@pytest.mark.model
def test_corrupt_model_load_error(tmp_path):
    """A file with a valid header but no tensors must fail cleanly (no crash)."""
    from conftest import write_gguf
    s = load_settings(model_path=str(write_gguf(tmp_path / "broken.gguf")), n_gpu_layers=0)
    e = Engine(s)
    e.load()
    assert e.status.state == "error"
    assert e.status.load_attempts and "ok" not in e.status.load_attempts[-1]
