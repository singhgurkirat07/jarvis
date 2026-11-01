"""Model file discovery and validation, including a minimal GGUF header reader."""
from __future__ import annotations

import os
import struct
from dataclasses import dataclass
from pathlib import Path

GGUF_MAGIC = b"GGUF"
MIN_MODEL_BYTES = 1 * 1024 * 1024  # anything smaller cannot be a usable LLM

# GGUF value type id -> struct format (fixed-size scalars)
_SCALARS = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}
_STRING, _ARRAY = 8, 9


class ModelValidationError(Exception):
    """The model file is missing, unreadable or not a valid GGUF file."""


@dataclass
class ModelInfo:
    path: str
    size_bytes: int
    gguf_version: int
    tensor_count: int
    architecture: str | None
    block_count: int | None
    name: str | None


def _read(f, fmt):
    size = struct.calcsize(fmt)
    buf = f.read(size)
    if len(buf) != size:
        raise ModelValidationError("GGUF header is truncated")
    return struct.unpack(fmt, buf)[0]


def _read_str(f) -> str:
    n = _read(f, "<Q")
    if n > 1 << 24:
        raise ModelValidationError("GGUF header contains an implausible string length")
    return f.read(n).decode("utf-8", errors="replace")


def _skip_value(f, vtype):
    if vtype in _SCALARS:
        f.seek(struct.calcsize(_SCALARS[vtype]), os.SEEK_CUR)
    elif vtype == _STRING:
        f.seek(_read(f, "<Q"), os.SEEK_CUR)
    elif vtype == _ARRAY:
        itype, n = _read(f, "<I"), _read(f, "<Q")
        if itype in _SCALARS:
            f.seek(struct.calcsize(_SCALARS[itype]) * n, os.SEEK_CUR)
        else:
            for _ in range(n):
                _skip_value(f, itype)
    else:
        raise ModelValidationError(f"Unknown GGUF value type {vtype}")


def validate_model(path: str | Path) -> ModelInfo:
    """Check the path exists, is a readable GGUF v2/v3 file, and read basic metadata."""
    if not path:
        raise ModelValidationError("No model path configured (set model_path in config/settings.json)")
    p = Path(path)
    if not p.exists():
        raise ModelValidationError(f"Model file not found: {p}")
    if not p.is_file():
        raise ModelValidationError(f"Model path is not a file: {p}")
    if p.suffix.lower() != ".gguf":
        raise ModelValidationError(f"Model file must have a .gguf extension: {p}")
    size = p.stat().st_size
    if size < MIN_MODEL_BYTES:
        raise ModelValidationError(f"Model file is too small to be valid ({size} bytes): {p}")
    try:
        f = open(p, "rb")
    except OSError as e:
        raise ModelValidationError(f"Model file is not readable: {p} ({e})") from e

    meta: dict[str, object] = {}
    wanted = {"general.architecture", "general.name"}
    with f:
        if f.read(4) != GGUF_MAGIC:
            raise ModelValidationError(f"Not a GGUF file (bad magic bytes): {p}")
        version = _read(f, "<I")
        if version not in (2, 3):
            raise ModelValidationError(f"Unsupported GGUF version {version}: {p}")
        tensor_count, kv_count = _read(f, "<Q"), _read(f, "<Q")
        for _ in range(kv_count):
            key, vtype = _read_str(f), _read(f, "<I")
            arch = meta.get("general.architecture")
            if (key in wanted or (arch and key == f"{arch}.block_count")) and vtype == _STRING:
                meta[key] = _read_str(f)
            elif (arch and key == f"{arch}.block_count") and vtype in _SCALARS:
                meta[key] = _read(f, _SCALARS[vtype])
            else:
                _skip_value(f, vtype)
            if arch and f"{arch}.block_count" in meta and "general.name" in meta:
                break

    arch = meta.get("general.architecture")
    return ModelInfo(
        path=str(p), size_bytes=size, gguf_version=version, tensor_count=tensor_count,
        architecture=arch, block_count=meta.get(f"{arch}.block_count") if arch else None,
        name=meta.get("general.name"),
    )
