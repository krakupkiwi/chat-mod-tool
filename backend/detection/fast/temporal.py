"""
TemporalSyncDetector — detects multiple accounts sending the same message
within a short time window (coordination signal).

Multi-window design catches both tight (1s) and spread-out (30s) coordination.
Risk contribution: 0–25 points per message.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

# (window_seconds, min_distinct_accounts_to_trigger)
SYNC_WINDOWS = [
    (1,  2),
    (3,  3),
    (5,  4),
    (15, 8),
    (30, 15),
]

_MAX_WINDOW = max(w for w, _ in SYNC_WINDOWS)


class TemporalSyncDetector:
    def __init__(self) -> None:
        # content_hash → deque of (timestamp, user_id)
        self._buckets: dict[str, deque[tuple[float, str]]] = defaultdict(deque)
        self._last_prune: float = time.monotonic()
        # Running max score for this tick window (reset by DetectionEngine each tick)
        self.current_score: float = 0.0

    def add(self, content_hash: str, user_id: str, timestamp: float) -> float:
        """Add message and return risk score 0–25."""
        self._buckets[content_hash].append((timestamp, user_id))

        if timestamp - self._last_prune > 5:
            self._prune_all(timestamp)
            self._last_prune = timestamp

        score = self._compute_score(content_hash, timestamp)
        if score > self.current_score:
            self.current_score = score
        return score

    def reset_tick(self) -> float:
        """Return current score and reset for next tick."""
        score = self.current_score
        self.current_score = 0.0
        return score

    def _compute_score(self, content_hash: str, now: float) -> float:
        bucket = self._buckets[content_hash]
        scores: list[float] = []

        for window_s, threshold in SYNC_WINDOWS:
            cutoff = now - window_s
            distinct_users = {uid for ts, uid in bucket if ts >= cutoff}
            count = len(distinct_users)
            if count >= threshold:
                window_weight = 1.0 / window_s
                score = min(count / threshold, 3.0) * window_weight * 10
                scores.append(score)

        return min(sum(scores), 25.0) if scores else 0.0

    def _prune_all(self, now: float) -> None:
        cutoff = now - _MAX_WINDOW
        empty = []
        for key, bucket in self._buckets.items():
            while bucket and bucket[0][0] < cutoff:
                bucket.popleft()
            if not bucket:
                empty.append(key)
        for key in empty:
            del self._buckets[key]
