"""
MinHashDuplicateDetector — near-duplicate detection via MinHash LSH.

Catches messages that are near-identical (same spam template with minor
variations) across multiple accounts. Requires the `datasketch` package.

If datasketch is not installed, this detector is disabled gracefully.

Cluster threshold: 3+ distinct accounts with Jaccard similarity >= 0.70.
Window: 30 seconds.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from datasketch import MinHash, MinHashLSH
    _DATASKETCH_AVAILABLE = True
except ImportError:
    _DATASKETCH_AVAILABLE = False
    logger.warning("datasketch not installed — MinHashDuplicateDetector disabled")


# Minimum normalised content length to bother with an LSH check.
# Shorter strings have too few trigrams for reliable Jaccard estimation.
_MIN_CONTENT_LEN = 10

# Hard cap on the number of entries kept in the LSH index.
# At 5K msg/min with a 30s window the practical maximum is ~2,500 entries.
# The cap keeps LSH query time bounded even under extreme benchmark or burst load
# (each extra entry adds to the set-union work done inside datasketch).
_MAX_LSH_ENTRIES = 2500


class MinHashDuplicateDetector:
    def __init__(
        self,
        similarity_threshold: float = 0.70,
        num_perm: int = 32,
        window_seconds: int = 30,
    ) -> None:
        self._enabled = _DATASKETCH_AVAILABLE
        self._window = window_seconds

        if self._enabled:
            self._lsh = MinHashLSH(threshold=similarity_threshold, num_perm=num_perm)
            self._num_perm = num_perm
            # Pre-built template: _init_permutations() runs once here, then
            # each message calls .copy() which skips permutation init entirely.
            self._minhash_template: "MinHash" = MinHash(num_perm=num_perm)
        else:
            self._lsh = None
            self._num_perm = num_perm
            self._minhash_template = None  # type: ignore

        # Ordered eviction: deque of (timestamp, key)
        self._time_index: deque[tuple[float, str]] = deque()
        self._key_meta: dict[str, dict] = {}  # key → {user_id, timestamp}

    def add(
        self,
        message_id: str,
        content: str,
        user_id: str,
        timestamp: float,
    ) -> Optional[list[dict]]:
        """
        Returns list of similar-message metadata if a cluster of 3+ distinct
        accounts is found, else None.
        """
        if not self._enabled:
            return None

        if len(content) < _MIN_CONTENT_LEN:
            return None

        # Evict expired entries first so the size check reflects real window occupancy.
        self._evict_old(timestamp)

        mh = self._make_minhash(content)

        # Query before insert (don't self-match)
        similar_keys: list[str] = self._lsh.query(mh)

        # Only insert if under the size cap — keeps query latency bounded.
        if len(self._key_meta) < _MAX_LSH_ENTRIES:
            try:
                self._lsh.insert(message_id, mh)
                self._time_index.append((timestamp, message_id))
                self._key_meta[message_id] = {"user_id": user_id, "timestamp": timestamp}
            except ValueError:
                pass  # Duplicate key — skip

        if similar_keys:
            similar_users = {
                self._key_meta[k]["user_id"]
                for k in similar_keys
                if k in self._key_meta
            }
            similar_users.add(user_id)

            if len(similar_users) >= 3:
                return [self._key_meta[k] for k in similar_keys if k in self._key_meta]

        return None

    def _make_minhash(self, text: str) -> "MinHash":
        # copy() reuses pre-computed permutation tables — avoids _init_permutations()
        mh = self._minhash_template.copy()
        for i in range(max(0, len(text) - 2)):
            mh.update(text[i : i + 3].encode("utf-8"))
        return mh

    def _evict_old(self, now: float) -> None:
        cutoff = now - self._window
        while self._time_index and self._time_index[0][0] < cutoff:
            _, old_key = self._time_index.popleft()
            try:
                self._lsh.remove(old_key)
            except (KeyError, Exception):
                pass
            self._key_meta.pop(old_key, None)
