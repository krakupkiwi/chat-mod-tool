"""
UserRateDetector — detects individual accounts sending at machine-speed rates.

Signals:
  - Volume: > 15 msg/min (mild), > 30 msg/min (strong)
  - Regularity: coefficient of variation < 0.05 (bots have suspiciously regular timing)

Risk contribution per user: 0–20 points.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque


class UserRateDetector:
    def __init__(self, window_seconds: int = 60) -> None:
        self.window = window_seconds
        # user_id → deque of timestamps (capped at 200 to bound memory)
        self._user_windows: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=200)
        )

    def add(self, user_id: str, timestamp: float) -> float:
        """Add message timestamp for user. Returns risk score 0–20."""
        window = self._user_windows[user_id]
        window.append(timestamp)
        return self._compute_score(window, timestamp)

    def score_for(self, user_id: str) -> float:
        """Return latest score for a user without adding a new timestamp."""
        window = self._user_windows.get(user_id)
        if not window:
            return 0.0
        return self._compute_score(window, window[-1])

    def _compute_score(self, window: deque, now: float) -> float:
        cutoff = now - self.window
        timestamps = [t for t in window if t >= cutoff]

        if len(timestamps) < 3:
            return 0.0

        msg_per_minute = len(timestamps) * (60.0 / self.window)

        volume_score = 0.0
        if msg_per_minute > 30:
            volume_score = min((msg_per_minute - 30) / 10, 1.0) * 15
        elif msg_per_minute > 15:
            volume_score = min((msg_per_minute - 15) / 15, 1.0) * 8

        # Regularity bonus: bots fire at very consistent intervals
        # Use inline mean/stdev instead of statistics.mean/stdev — the stdlib
        # statistics module uses Fraction arithmetic and is ~200x slower than
        # a plain float loop at this call frequency (~83/s at 5K msg/min).
        intervals = [timestamps[i + 1] - timestamps[i] for i in range(len(timestamps) - 1)]
        n_iv = len(intervals)
        if n_iv >= 4:
            mean_interval = sum(intervals) / n_iv
            if n_iv > 1:
                variance = sum((x - mean_interval) ** 2 for x in intervals) / (n_iv - 1)
                stdev_interval = math.sqrt(variance)
            else:
                stdev_interval = 0.0
            cv = stdev_interval / max(mean_interval, 0.001)
            if cv < 0.05 and mean_interval < 5:
                volume_score += 10

        return min(volume_score, 20.0)
