"""
ChatBuffer — multi-resolution ring buffers for sliding-window metrics.

Each window is a deque of (timestamp, ChatMessage) pairs.
add() is O(1). prune() is O(k) where k = number of expired entries (amortized O(1)).
Never rebuilt from scratch on each tick.

Windows: 5s, 10s, 30s, 60s, 300s
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Sequence

from .models import ChatMessage

WINDOW_SECONDS: tuple[int, ...] = (5, 10, 30, 60, 300)


@dataclass
class WindowStats:
    """Snapshot of a single time window."""
    window_seconds: int
    message_count: int
    unique_users: int
    unique_content_hashes: int
    duplicate_ratio: float   # 1 - (unique_hashes / total_messages)
    messages_per_second: float


class ChatBuffer:
    """
    Maintains one deque per window size. All windows share the same messages —
    shorter windows are subsets of longer ones pruned to their time limit.

    Thread-safety: not thread-safe. Must be called from the asyncio event loop only.
    """

    def __init__(self, windows: Sequence[int] = WINDOW_SECONDS) -> None:
        self._windows: dict[int, deque[tuple[float, ChatMessage]]] = {
            w: deque() for w in windows
        }

    def add(self, msg: ChatMessage) -> None:
        """Add a message to all windows. O(1) per window."""
        ts = msg.received_at
        for buf in self._windows.values():
            buf.append((ts, msg))

    def prune(self) -> None:
        """Remove expired entries from all windows. Call once per tick."""
        now = time.time()
        for window_sec, buf in self._windows.items():
            cutoff = now - window_sec
            while buf and buf[0][0] < cutoff:
                buf.popleft()

    def stats(self, window_seconds: int) -> WindowStats:
        """Return metrics for the given window size."""
        buf = self._windows.get(window_seconds)
        if buf is None:
            raise ValueError(f"Unknown window: {window_seconds}s. Valid: {list(self._windows)}")

        total = len(buf)
        if total == 0:
            return WindowStats(
                window_seconds=window_seconds,
                message_count=0,
                unique_users=0,
                unique_content_hashes=0,
                duplicate_ratio=0.0,
                messages_per_second=0.0,
            )

        # Single pass over the deque instead of 3 separate comprehensions.
        # At high chat volumes (400+ msg/60s) this cuts stats() cost by ~3x.
        unique_users: set = set()
        unique_hashes: set = set()
        for _, msg in buf:
            unique_users.add(msg.user_id)
            unique_hashes.add(msg.content_hash)
        n_unique_users = len(unique_users)
        n_unique_hashes = len(unique_hashes)
        dup_ratio = 1.0 - (n_unique_hashes / total)

        return WindowStats(
            window_seconds=window_seconds,
            message_count=total,
            unique_users=n_unique_users,
            unique_content_hashes=n_unique_hashes,
            duplicate_ratio=round(dup_ratio, 4),
            messages_per_second=round(total / window_seconds, 2),
        )

    def all_stats(self) -> dict[int, WindowStats]:
        """Return stats for all windows."""
        return {w: self.stats(w) for w in self._windows}

    def recent_messages(self, window_seconds: int) -> list[ChatMessage]:
        """Return messages within the given window, oldest first."""
        buf = self._windows.get(window_seconds, deque())
        return [msg for _, msg in buf]

    def messages_and_stats(self, window_seconds: int) -> tuple[list[ChatMessage], "WindowStats"]:
        """
        Return (messages, WindowStats) for the given window in a single deque pass.
        Avoids the redundant scan that happens when callers call recent_messages()
        then stats() separately.
        """
        buf = self._windows.get(window_seconds)
        if buf is None:
            raise ValueError(f"Unknown window: {window_seconds}s. Valid: {list(self._windows)}")

        msgs: list[ChatMessage] = []
        unique_users: set = set()
        unique_hashes: set = set()
        for _, msg in buf:
            msgs.append(msg)
            unique_users.add(msg.user_id)
            unique_hashes.add(msg.content_hash)

        total = len(msgs)
        if total == 0:
            return msgs, WindowStats(
                window_seconds=window_seconds,
                message_count=0,
                unique_users=0,
                unique_content_hashes=0,
                duplicate_ratio=0.0,
                messages_per_second=0.0,
            )

        dup_ratio = 1.0 - (len(unique_hashes) / total)
        return msgs, WindowStats(
            window_seconds=window_seconds,
            message_count=total,
            unique_users=len(unique_users),
            unique_content_hashes=len(unique_hashes),
            duplicate_ratio=round(dup_ratio, 4),
            messages_per_second=round(total / window_seconds, 2),
        )

    @property
    def total_buffered(self) -> int:
        """Total messages in the largest window."""
        largest = max(self._windows)
        return len(self._windows[largest])
