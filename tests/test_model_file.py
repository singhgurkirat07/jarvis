import os

import pytest

from backend.config import load_settings
from backend.model_file import ModelValidationError, validate_model
from conftest import HAS_MODEL, REAL_MODEL, write_gguf


# ---- discovery --------------------------------------------------------------
@pytest.mark.model
def test_configured_model_is_discovered():
    s = load_settings()
    assert os.path.isfile(s.model_path)
    assert os.access(s.model_path, os.R_OK)


@pytest.mark.model
def test_real_model_metadata():
    info = validate_model(REAL_MODEL)
    assert info.gguf_version == 3
    assert info.architecture == "qwen2"
    assert info.block_count == 28
    assert info.name == "Qwen2.5 7B Instruct"
    assert info.size_bytes > 4 * 2**30


# ---- validation -------------------------------------------------------------
def test_synthetic_gguf_is_valid(tmp_path):
    info = validate_model(write_gguf(tmp_path / "m.gguf", arch="llama", blocks=7))
    assert (info.architecture, info.block_count, info.name) == ("llama", 7, "Test Model")


@pytest.mark.parametrize("path, message", [
    ("", "No model path"),
    ("C:/definitely/not/here/model.gguf", "not found"),
])
def test_missing_or_empty_path(path, message):
    with pytest.raises(ModelValidationError, match=message):
        validate_model(path)


def test_directory_rejected(tmp_path):
    d = tmp_path / "dir.gguf"
    d.mkdir()
    with pytest.raises(ModelValidationError, match="not a file"):
        validate_model(d)


def test_wrong_extension(tmp_path):
    with pytest.raises(ModelValidationError, match=".gguf extension"):
        validate_model(write_gguf(tmp_path / "m.bin"))


def test_too_small(tmp_path):
    with pytest.raises(ModelValidationError, match="too small"):
        validate_model(write_gguf(tmp_path / "m.gguf", pad_to=0))


def test_bad_magic(tmp_path):
    with pytest.raises(ModelValidationError, match="bad magic"):
        validate_model(write_gguf(tmp_path / "m.gguf", magic=b"NOPE"))


def test_unsupported_version(tmp_path):
    with pytest.raises(ModelValidationError, match="Unsupported GGUF version 1"):
        validate_model(write_gguf(tmp_path / "m.gguf", version=1))


def test_truncated_header(tmp_path):
    p = tmp_path / "m.gguf"
    p.write_bytes(b"GGUF" + (3).to_bytes(4, "little") + b"\xff" * 16 + b"\0" * (2 * 2**20))
    with pytest.raises(ModelValidationError):
        validate_model(p)


# ---- configuration -----------------------------------------------------------
def test_env_overrides(monkeypatch):
    monkeypatch.setenv("JARVIS_N_GPU_LAYERS", "12")
    monkeypatch.setenv("JARVIS_OFFLOAD_KQV", "true")
    monkeypatch.setenv("JARVIS_TEMPERATURE", "0.2")
    s = load_settings()
    assert (s.n_gpu_layers, s.offload_kqv, s.temperature) == (12, True, 0.2)


def test_defaults_are_safe():
    s = load_settings()
    assert s.host == "127.0.0.1"
    assert s.allow_remote is False
    assert s.offload_kqv is False
    assert s.enforce_offline is True


def test_unknown_config_key_rejected(tmp_path):
    p = tmp_path / "c.json"
    p.write_text('{"modle_path": "x"}')
    with pytest.raises(ValueError, match="Unknown keys"):
        load_settings(p)
