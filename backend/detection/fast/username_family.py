"""
UsernameFamilyDetector — detects when many accounts in a session share the
same structural username pattern (e.g. CosmicTurtle91, CosmicWave42).

Catches bot farms that generate organic-looking names from the same template.
Risk contribution: 0–20 points.
Requires 10+ distinct accounts matching the same pattern within window.
"""

from __future__ import annotations

import re
from collections import defaultdict, deque

PATTERNS: dict[str, re.Pattern] = {
    "word_word_digits":    re.compile(r"^[A-Z][a-z]+[A-Z][a-z]+\d{2,4}$"),
    "lower_digits_suffix": re.compile(r"^[a-z]{5,15}\d{3,4}$"),
    "underscore_digits":   re.compile(r"^[a-z]+_[a-z]+\d{2,4}$"),
    "xx_word_xx":          re.compile(r"^x+_?\w+_?x+$", re.IGNORECASE),
    "prefix_sequential":   re.compile(r"^([a-z]+)(\d+)$"),
}

MIN_FAMILY_SIZE = 10


class UsernameFamilyDetector:
    def __init__(self, session_window_seconds: int = 600) -> None:
        self._window = session_window_seconds
        # pattern_name → deque of (timestamp, username)
        self._buckets: dict[str, deque[tuple[float, str]]] = defaultdict(deque)
        # Cached score to avoid O(bucket_size × n_patterns) scan per message.
        # The family signal is session-level; recomputing it every 2 seconds
        # is more than sufficient — it takes many messages for a family to
        # accumulate to MIN_FAMILY_SIZE (10).
        self._cached_score: float = 0.0
        self._last_score_time: float = 0.0
        _SCORE_CACHE_TTL = 2.0  # recompute at most every 2 seconds
        self._score_ttl = _SCORE_CACHE_TTL

    def add(self, username: str, timestamp: float) -> float:
        """Returns risk score 0–20."""
        matched = self._classify(username)
        for pattern in matched:
            self._buckets[pattern].append((timestamp, username))
        self._prune(timestamp)
        # Only recompute the expensive O(n) score if cache is stale
        if timestamp - self._last_score_time >= self._score_ttl:
            self._cached_score = self._compute_score(timestamp)
            self._last_score_time = timestamp
        return self._cached_score

    def _classify(self, username: str) -> list[str]:
        return [name for name, pat in PATTERNS.items() if pat.match(username)]

    def _compute_score(self, now: float) -> float:
        cutoff = now - self._window
        for bucket in self._buckets.values():
            # Count distinct users within the window in a single deque pass.
            # The deque is pruned so all entries are within window after _prune().
            distinct = len({u for t, u in bucket if t >= cutoff})
            if distinct >= MIN_FAMILY_SIZE:
                return min(distinct / 20.0, 1.0) * 20.0
        return 0.0

    def _prune(self, now: float) -> None:
        cutoff = now - self._window
        for bucket in self._buckets.values():
            while bucket and bucket[0][0] < cutoff:
                bucket.popleft()
