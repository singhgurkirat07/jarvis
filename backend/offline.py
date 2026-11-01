"""Offline enforcement: refuse any non-loopback network connection from this process.

This guards Python-level sockets (urllib, httpx, requests, huggingface_hub, ...), so no
library can silently download a model or call a cloud API. Native code inside llama.dll
does not use Python sockets; llama.cpp itself performs no network access when loading a
local GGUF file via model_path.
"""
from __future__ import annotations

import ipaddress
import os
import socket

_original_connect = socket.socket.connect
_original_connect_ex = socket.socket.connect_ex
blocked_attempts: list[str] = []

# Make well-known libraries refuse to go online even before reaching the socket layer.
OFFLINE_ENV = {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_DATASETS_OFFLINE": "1"}


class OfflineViolation(ConnectionError):
    pass


def _is_local(address) -> bool:
    if not isinstance(address, tuple):  # AF_UNIX path etc.
        return True
    host = str(address[0])
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host.split("%")[0]).is_loopback
    except ValueError:
        return False  # an unresolved hostname: not provably local


def _guarded_connect(self, address):
    if not _is_local(address):
        blocked_attempts.append(repr(address))
        raise OfflineViolation(f"Jarvis offline mode blocked a network connection to {address!r}")
    return _original_connect(self, address)


def _guarded_connect_ex(self, address):
    if not _is_local(address):
        blocked_attempts.append(repr(address))
        raise OfflineViolation(f"Jarvis offline mode blocked a network connection to {address!r}")
    return _original_connect_ex(self, address)


def enable() -> None:
    os.environ.update(OFFLINE_ENV)
    socket.socket.connect = _guarded_connect
    socket.socket.connect_ex = _guarded_connect_ex


def disable() -> None:
    socket.socket.connect = _original_connect
    socket.socket.connect_ex = _original_connect_ex


def is_enabled() -> bool:
    return socket.socket.connect is _guarded_connect
