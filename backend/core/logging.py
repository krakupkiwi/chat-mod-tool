from __future__ import annotations

import logging
import re
import sys
from pathlib import Path


# Patterns that must never appear in log output
_SENSITIVE_PATTERNS = [
    re.compile(r"oauth:[a-zA-Z0-9]{20,}", re.IGNORECASE),
    re.compile(r'"access_token"\s*:\s*"[^"]{10,}"', re.IGNORECASE),
    re.compile(r'"refresh_token"\s*:\s*"[^"]{10,}"', re.IGNORECASE),
    re.compile(r"access_token=[a-zA-Z0-9_\-]{10,}", re.IGNORECASE),
    re.compile(r"refresh_token=[a-zA-Z0-9_\-]{10,}", re.IGNORECASE),
    re.compile(r"client_secret[=:\s]+[\"']?[a-zA-Z0-9_\-]{10,}", re.IGNORECASE),
    re.compile(r"X-IPC-Secret[:\s]+[a-zA-Z0-9_\-]{10,}", re.IGNORECASE),
    re.compile(r"ipc_secret[\"'\s:]+[a-zA-Z0-9_\-]{10,}", re.IGNORECASE),
    re.compile(r"Bearer [a-zA-Z0-9_\-]{10,}", re.IGNORECASE),
    # Catch Authorization header logged as a JSON key-value pair
    re.compile(r'"Authorization"\s*:\s*"[^"]+"', re.IGNORECASE),
]


class SensitiveFilter(logging.Filter):
    """Redact credentials and secrets from all log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._redact(str(record.msg))
        if record.args:
            if isinstance(record.args, tuple):
                record.args = tuple(
                    self._redact(str(a)) if isinstance(a, str) else a
                    for a in record.args
                )
            elif isinstance(record.args, dict):
                record.args = {
                    k: self._redact(str(v)) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
        return True

    @staticmethod
    def _redact(text: str) -> str:
        for pattern in _SENSITIVE_PATTERNS:
            text = pattern.sub("[REDACTED]", text)
        return text


def configure_logging(log_path: str | None = None) -> None:
    """
    Call this as the very first thing in main.py, before any other imports
    that might create loggers. Installs SensitiveFilter on the root logger.
    """
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addFilter(SensitiveFilter())

    fmt = logging.Formatter(
        fmt="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler (stdout — read by tests and dev mode)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    # Do NOT write to stderr — stderr is used by Electron for error detection
    root.addHandler(console)

    # File handler (optional, for production)
    if log_path:
        try:
            Path(log_path).parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(log_path, encoding="utf-8")
            file_handler.setFormatter(fmt)
            root.addHandler(file_handler)
        except OSError:
            pass  # Can't write log file — not fatal

    # Silence noisy third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("twitchio").setLevel(logging.INFO)
