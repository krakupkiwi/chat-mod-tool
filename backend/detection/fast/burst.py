"""
BurstAnomalyDetector — channel-adaptive z-score spike detection.

Measures message rate in 5-second intervals and computes a z-score against
a rolling 5-minute baseline. A naturally high-volume channel requires a
larger spike to trigger than a quiet channel.

Risk contribution: 0–25 points.
"""

from __future__ import annotations

import statistics
import time
from collections import deque


class BurstAnomalyDetector:
    def __init__(
        self,
        baseline_window_seconds: int = 300,
        sample_interval: float = 5.0,
    ) -> None:
        self._baseline_window = baseline_window_seconds
        self._sample_interval = sample_interval

        # Rolling history of (interval_start, count_in_interval)
        self._history: deque[tuple[float, int]] = deque()

        # Current interval state
        self._interval_start: float = time.monotonic()
        self._interval_count: int = 0

    def add_message(self, timestamp: float) -> float:
        """Call for each message. Returns risk score 0–25."""
        self._interval_count += 1

        if timestamp - self._interval_start >= self._sample_interval:
            self._history.append((self._interval_start, self._interval_count))
            self._interval_start = timestamp
            self._interval_count = 0
            self._prune(timestamp)

        return self._compute_score()

    def _compute_score(self) -> float:
        if len(self._history) < 10:
            return 0.0  # Insufficient baseline

        counts = [c for _, c in self._history]
        mean = statistics.mean(counts)
        stdev = max(statistics.stdev(counts) if len(counts) > 1 else 1.0, 0.5)

        z = (self._interval_count - mean) / stdev

        if z < 1.5:
            return 0.0
        return min((z - 1.5) * 8, 25.0)

    def _prune(self, now: float) -> None:
        cutoff = now - self._baseline_window
        while self._history and self._history[0][0] < cutoff:
            self._history.popleft()
