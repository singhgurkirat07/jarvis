import threading
import time

import pytest

from backend.engine import Engine, EngineBusy, EngineNotReady, PromptTooLong
from conftest import FakeLlama


def test_load_ready(tmp_settings, fake_loader):
    e = Engine(tmp_settings, fake_loader)
    e.load()
    assert e.status.state == "ready"
    assert e.status.model["architecture"] == "llama"
    assert e.status.load_seconds is not None
    assert fake_loader.calls == [e.status.n_gpu_layers]


def test_load_missing_model_sets_error(tmp_settings, fake_loader):
    tmp_settings.model_path = "C:/nope/missing.gguf"
    e = Engine(tmp_settings, fake_loader)
    e.load()
    assert e.status.state == "error"
    assert "not found" in e.status.error
    assert fake_loader.calls == []  # never tried to load an invalid file


def test_oom_fallback_reduces_gpu_layers(tmp_settings):
    tmp_settings.n_gpu_layers = 4
    tried = []

    def loader(s, n):
        tried.append(n)
        if n > 0:
            raise ValueError("Failed to load model (simulated CUDA out of memory)")
        return FakeLlama()
    e = Engine(tmp_settings, loader)
    e.load()
    assert tried == [4, 2, 0]
    assert e.status.state == "ready" and e.status.n_gpu_layers == 0
    assert "fallback" in e.status.gpu_plan


def test_all_load_attempts_fail(tmp_settings):
    def loader(s, n):
        raise ValueError("boom")
    e = Engine(tmp_settings, loader)
    e.load()
    assert e.status.state == "error" and "boom" in e.status.error


def test_generate_and_system_prompt(tmp_settings, fake_loader, fake_llm):
    e = Engine(tmp_settings, fake_loader)
    e.load()
    out = e.generate([{"role": "user", "content": "hi"}], max_tokens=3, temperature=0)
    assert out["content"] == "Hello from the"
    assert out["completion_tokens"] == 3
    sent = fake_llm.calls[-1]
    assert sent["messages"][0] == {"role": "system", "content": tmp_settings.system_prompt}
    assert sent["temperature"] == 0 and sent["max_tokens"] == 3


def test_stream_events(tmp_settings, fake_loader):
    e = Engine(tmp_settings, fake_loader)
    e.load()
    events = list(e.stream([{"role": "user", "content": "hi"}]))
    text = "".join(ev.get("delta", "") for ev in events)
    assert text == "Hello from the fake model."
    assert events[-1]["done"] and events[-1]["completion_tokens"] == 5
    assert events[-1]["finish_reason"] == "stop"


def test_not_ready_raises(tmp_settings, fake_loader):
    e = Engine(tmp_settings, fake_loader)
    with pytest.raises(EngineNotReady):
        e.generate([{"role": "user", "content": "hi"}])
    with pytest.raises(EngineNotReady):
        next(e.stream([{"role": "user", "content": "hi"}]))


def test_busy_when_another_generation_runs(tmp_settings):
    tmp_settings.queue_timeout_s = 0.05
    e = Engine(tmp_settings, lambda s, n: FakeLlama(delay=0.4))
    e.load()
    t = threading.Thread(target=e.generate, args=([{"role": "user", "content": "a"}],))
    t.start()
    time.sleep(0.05)
    with pytest.raises(EngineBusy):  # queue_timeout_s=0.05 < remaining generation time
        e.generate([{"role": "user", "content": "b"}])
    t.join()


def test_prompt_too_long(tmp_settings, fake_loader):
    e = Engine(tmp_settings, fake_loader)
    e.load()
    with pytest.raises(PromptTooLong):
        e.generate([{"role": "user", "content": "word " * 1000}])
    # the lock must have been released
    assert e.generate([{"role": "user", "content": "ok"}])["content"]


def test_old_turns_trimmed_to_fit_context(tmp_settings, fake_loader, fake_llm):
    e = Engine(tmp_settings, fake_loader)
    e.load()
    history = []
    for i in range(20):
        history += [{"role": "user", "content": f"q{i} " + "x " * 30},
                    {"role": "assistant", "content": "y " * 30}]
    history.append({"role": "user", "content": "latest"})
    e.generate(history)
    sent = fake_llm.calls[-1]["messages"]
    assert sent[0]["role"] == "system" and sent[-1]["content"] == "latest"
    assert len(sent) < len(history)


def test_close_releases_model(tmp_settings, fake_loader, fake_llm):
    e = Engine(tmp_settings, fake_loader)
    e.load()
    e.close()
    assert e.status.state == "closed" and fake_llm.closed
    with pytest.raises(EngineNotReady):
        e.generate([{"role": "user", "content": "hi"}])


def test_close_stops_running_stream(tmp_settings):
    llm = FakeLlama(reply=" ".join(["w"] * 200), delay=0.01)
    tmp_settings.max_tokens = 200
    e = Engine(tmp_settings, lambda s, n: llm)
    e.load()
    gen = e.stream([{"role": "user", "content": "hi"}])
    next(gen)
    closer = threading.Thread(target=e.close)
    closer.start()
    events = list(gen)
    closer.join(5)
    assert not closer.is_alive()
    assert events[-1]["finish_reason"] == "shutdown"
    assert llm.closed


def test_abandoned_stream_releases_lock(tmp_settings, fake_loader):
    e = Engine(tmp_settings, fake_loader)
    e.load()
    gen = e.stream([{"role": "user", "content": "hi"}])
    next(gen)
    gen.close()  # client disconnected
    assert e.generate([{"role": "user", "content": "again"}])["content"]
