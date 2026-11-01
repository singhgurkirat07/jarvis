import os
import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.config import Settings, load_settings  # noqa: E402
from backend.gpu import detect_gpu  # noqa: E402

REAL_SETTINGS = load_settings()
REAL_MODEL = Path(REAL_SETTINGS.model_path)
HAS_MODEL = REAL_MODEL.is_file()
HAS_GPU = detect_gpu().available


def pytest_collection_modifyitems(config, items):
    skip_model = pytest.mark.skip(reason=f"model file not found: {REAL_MODEL}")
    skip_gpu = pytest.mark.skip(reason="no NVIDIA GPU visible via NVML")
    for item in items:
        if "model" in item.keywords and not HAS_MODEL:
            item.add_marker(skip_model)
        if "gpu" in item.keywords and not HAS_GPU:
            item.add_marker(skip_gpu)


def write_gguf(path: Path, version=3, arch="llama", blocks=4, pad_to=1024 * 1024, magic=b"GGUF"):
    """Write a minimal synthetic GGUF header (no tensors), padded so size checks pass."""
    def s(x: str) -> bytes:
        b = x.encode()
        return struct.pack("<Q", len(b)) + b

    kvs = [
        s("general.architecture") + struct.pack("<I", 8) + s(arch),
        s("general.name") + struct.pack("<I", 8) + s("Test Model"),
        s("tokenizer.ggml.tokens") + struct.pack("<I", 9) + struct.pack("<IQ", 8, 2) + s("a") + s("b"),
        s(f"{arch}.block_count") + struct.pack("<I", 4) + struct.pack("<I", blocks),
    ]
    data = magic + struct.pack("<I", version) + struct.pack("<QQ", 0, len(kvs)) + b"".join(kvs)
    path.write_bytes(data + b"\0" * max(0, pad_to - len(data)))
    return path


class FakeLlama:
    """Stands in for llama_cpp.Llama with deterministic output."""

    def __init__(self, reply="Hello from the fake model.", delay=0.0):
        self.reply, self.delay, self.closed, self.calls = reply, delay, False, []

    def tokenize(self, data: bytes, add_bos=False, special=False):
        return data.split()

    def close(self):
        self.closed = True

    def create_chat_completion(self, messages, stream=False, max_tokens=16, **kw):
        import time
        self.calls.append({"messages": messages, "max_tokens": max_tokens, **kw})
        words = self.reply.split(" ")[:max_tokens]
        if not stream:
            time.sleep(self.delay)
            return {"choices": [{"message": {"role": "assistant", "content": " ".join(words)},
                                 "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": len(words)}}

        def gen():
            for i, w in enumerate(words):
                time.sleep(self.delay)
                yield {"choices": [{"delta": {"content": (" " if i else "") + w}, "finish_reason": None}]}
            yield {"choices": [{"delta": {}, "finish_reason": "stop"}]}
        return gen()


@pytest.fixture
def tmp_settings(tmp_path):
    model = write_gguf(tmp_path / "fake.gguf")
    return Settings(model_path=str(model), db_path=str(tmp_path / "jarvis.db"),
                    log_dir=str(tmp_path / "logs"), n_ctx=512, max_tokens=32,
                    queue_timeout_s=0.5, vram_reserve_mb=1024)


@pytest.fixture
def fake_llm():
    return FakeLlama()


@pytest.fixture
def fake_loader(fake_llm):
    calls = []

    def loader(settings, n_gpu_layers):
        calls.append(n_gpu_layers)
        return fake_llm
    loader.calls = calls
    return loader


@pytest.fixture(autouse=True)
def _restore_offline():
    from backend import offline
    yield
    offline.disable()
    offline.blocked_attempts.clear()
