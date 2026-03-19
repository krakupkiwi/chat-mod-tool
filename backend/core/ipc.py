"""
Stdout JSON protocol — Channel 1 IPC between Python and Electron main process.

Python writes newline-delimited JSON to stdout.
Electron main process reads and parses each line.

All messages must be flushed immediately (flush=True).
Non-JSON lines on stdout are silently ignored by Electron.
"""

from __future__ import annotations

import json
import sys
import time


def emit(type: str, **kwargs) -> None:
    """Write a structured status message to stdout for Electron."""
    msg = {"type": type, "ts": time.time(), **kwargs}
    print(json.dumps(msg), flush=True)  # flush=True is critical


def emit_ready(port: int, ipc_secret: str) -> None:
    emit("ready", port=port, ipc_secret=ipc_secret)


def emit_health(status: str = "ok", **extra) -> None:
    emit("health", status=status, **extra)


def emit_error(message: str, code: str = "UNKNOWN") -> None:
    emit("error", message=message, code=code)


def emit_shutdown(reason: str = "graceful") -> None:
    emit("shutdown", reason=reason)
