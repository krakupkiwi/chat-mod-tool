"""
Telemetry singleton — lightweight rolling performance metrics.

Thread-safe for reads; all writes come from the asyncio event loop.
No external dependencies beyond the standard library (psutil is optional).

Usage:
    from core.telemetry import telemetry

    telemetry.record_message()          # call after each message is consumed
    telemetry.record_tick(duration_ms)  # call at end of each detection tick
    telemetry.ws_clients = n            # update on WS connect/disconnect
    telemetry.queue_depth = n           # update from queue consumer

    payload = telemetry.snapshot()      # returns dict for inclusion in WS payload
"""

from __future__ import annotations

import collections
import os
import time
from dataclasses import dataclass, field

try:
    import psutil
    _proc = psutil.Process(os.getpid())
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False


def _percentile(data: list[float], pct: int) -> float | None:
    if not data:
        return None
    s = sorted(data)
    idx = int(len(s) * pct / 100)
    return round(s[min(idx, len(s) - 1)], 2)


@dataclass
class Telemetry:
    """Rolling performance metrics.

    _msg_times   — monotonic timestamps of each processed message (last 6 000,
                   enough to compute exact msg/min over a 60-second window).
    _tick_durations — wall-clock tick durations in ms (last 120 ticks = 2 min).
    """
    _msg_times: collections.deque = field(
        default_factory=lambda: collections.deque(maxlen=6000)
    )
    _tick_durations: collections.deque = field(
        default_factory=lambda: collections.deque(maxlen=120)
    )
    ws_clients: int = 0
    queue_depth: int = 0

    def record_message(self) -> None:
        """Record that one message was consumed from the pipeline queue."""
        self._msg_times.append(time.monotonic())

    def record_tick(self, duration_ms: float) -> None:
        """Record the wall-clock duration of a detection tick (in milliseconds)."""
        self._tick_durations.append(duration_ms)

    def snapshot(self) -> dict:
        """Return a JSON-serialisable performance snapshot."""
        now = time.monotonic()
        recent = [t for t in self._msg_times if now - t <= 60.0]
        ticks = list(self._tick_durations)

        mem_mb: float | None = None
        if HAS_PSUTIL:
            try:
                mem_mb = round(_proc.memory_info().rss / 1_048_576, 1)
            except Exception:
                pass

        return {
            "msg_per_min": len(recent),
            "tick_p50_ms": _percentile(ticks, 50),
            "tick_p95_ms": _percentile(ticks, 95),
            "tick_p99_ms": _percentile(ticks, 99),
            "queue_depth": self.queue_depth,
            "ws_clients": self.ws_clients,
            "memory_mb": mem_mb,
        }


# Module-level singleton — import this everywhere
telemetry = Telemetry()
