"""
AdaptiveBaseline — calibrates detection sensitivity to the channel's own
normal traffic patterns.

Prevents false alarms in naturally high-volume channels. A channel that
normally has 500 msg/min should not alarm at 600; a quiet channel at 20
should alarm at 200.

After MIN_SAMPLES ticks, expresses raw risk as a z-score against the
rolling 30-minute history and scales it to 0–100.
"""

from __future__ import annotations

import statistics
from collections import deque


class AdaptiveBaseline:
    MIN_SAMPLES = 30

    def __init__(self, history_minutes: int = 30) -> None:
        self._history_seconds = history_minutes * 60
        # metric_name → deque of (timestamp, value)
        self._histories: dict[str, deque[tuple[float, float]]] = {}

    def record(self, metrics: dict[str, float], timestamp: float) -> None:
        """Record a snapshot of metric values."""
        for name, value in metrics.items():
            if name not in self._histories:
                self._histories[name] = deque()
            self._histories[name].append((timestamp, value))
            self._prune(name, timestamp)

    def calibrate(self, raw_risk: float) -> float:
        """
        Return calibrated risk score.
        Before MIN_SAMPLES: return raw_risk unchanged.
        After MIN_SAMPLES: z-score scaled to 0–100.
        """
        key = "raw_risk"
        history = self._histories.get(key)
        if not history or len(history) < self.MIN_SAMPLES:
            return raw_risk

        values = [v for _, v in history]
        mean = statistics.mean(values)
        stdev = max(statistics.stdev(values) if len(values) > 1 else 1.0, 0.5)

        z = (raw_risk - mean) / stdev
        # z=0 → 20 risk (baseline noise); z=3 → 80; z=5 → 100
        return min(20 + max(0.0, z) * 20, 100.0)

    def z_score(self, metric: str, value: float) -> float:
        history = self._histories.get(metric)
        if not history or len(history) < self.MIN_SAMPLES:
            return 0.0
        values = [v for _, v in history]
        mean = statistics.mean(values)
        stdev = max(statistics.stdev(values) if len(values) > 1 else 1.0, 0.1)
        return (value - mean) / stdev

    @property
    def is_calibrated(self) -> bool:
        h = self._histories.get("raw_risk")
        return h is not None and len(h) >= self.MIN_SAMPLES

    def reset(self) -> None:
        """Call after reconnect with long gap."""
        self._histories.clear()

    def _prune(self, name: str, now: float) -> None:
        cutoff = now - self._history_seconds
        buf = self._histories[name]
        while buf and buf[0][0] < cutoff:
            buf.popleft()
