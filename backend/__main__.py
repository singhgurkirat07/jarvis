"""Run Jarvis:  py -3.12 -m backend   (binds to 127.0.0.1 by default)."""
from __future__ import annotations

import argparse
import signal
import sys

import uvicorn

from .app import _is_loopback, create_app
from .config import load_settings


def main() -> int:
    ap = argparse.ArgumentParser(description="Jarvis local assistant server")
    ap.add_argument("--config", help="path to settings JSON (default config/settings.json)")
    ap.add_argument("--port", type=int)
    args = ap.parse_args()

    overrides = {"port": args.port} if args.port else {}
    settings = load_settings(args.config, **overrides)
    if not _is_loopback(settings.host) and not settings.allow_remote:
        print(f"Refusing to bind to non-loopback host {settings.host!r}. "
              "Set allow_remote=true in the config if you really want network exposure.", file=sys.stderr)
        return 2

    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, signal.default_int_handler)

    app = create_app(settings)
    # uvicorn handles Ctrl+C / SIGTERM / SIGBREAK: it stops accepting requests, finishes
    # in-flight ones (up to the timeout) and runs the app lifespan shutdown to free the model.
    try:
        uvicorn.run(app, host=settings.host, port=settings.port, log_level="warning",
                    timeout_graceful_shutdown=10)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
