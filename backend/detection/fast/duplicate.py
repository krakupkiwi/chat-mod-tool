"""
IncrementalDuplicateTracker — O(1) duplicate ratio tracking.

Maintains a rolling window of (timestamp, content_hash) pairs using a deque
and a Counter. Both add() and the duplicate_ratio property are O(1).

Risk contribution: 0–35 points.
"""

from __future__ import annotations

from collections import Counter, deque


class IncrementalDuplicateTracker:
    """
    Tracks exact-duplicate ratio with O(1) updates.

    add() is O(1) amortized (pruning is amortized across calls).
    duplicate_ratio and risk_score are O(1) reads.
    """

    def __init__(self, window_seconds: int = 30) -> None:
        self.window = window_seconds
        self._buffer: deque[tuple[float, str]] = deque()  # (timestamp, hash)
        self._hash_counts: Counter[str] = Counter()

    def add(self, content_hash: str, timestamp: float) -> None:
        self._buffer.append((timestamp, content_hash))
        self._hash_counts[content_hash] += 1
        self._prune(timestamp)

    def _prune(self, now: float) -> None:
        cutoff = now - self.window
        while self._buffer and self._buffer[0][0] < cutoff:
            _, old_hash = self._buffer.popleft()
            self._hash_counts[old_hash] -= 1
            if self._hash_counts[old_hash] == 0:
                del self._hash_counts[old_hash]

    @property
    def duplicate_ratio(self) -> float:
        total = len(self._buffer)
        if total < 5:
            return 0.0
        unique = len(self._hash_counts)
        return 1.0 - (unique / total)

    @property
    def risk_score(self) -> float:
        """0–35 risk contribution."""
        ratio = self.duplicate_ratio
        if ratio < 0.05:
            return 0.0
        if ratio < 0.15:
            return ratio * 50  # 0–7.5
        return min(ratio * 100, 35.0)
