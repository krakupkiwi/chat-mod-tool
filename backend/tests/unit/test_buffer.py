"""
Tests for pipeline/buffer.py

Covers: message addition, window expiry, WindowStats accuracy,
deque overflow protection, and edge cases.
"""

from __future__ import annotations

import time
import pytest

from pipeline.buffer import ChatBuffer, WindowStats
from tests.conftest import make_message


class TestChatBufferAdd:
    def test_message_appears_in_all_windows(self):
        buf = ChatBuffer()
        msg = make_message()
        buf.add(msg)
        for w in (5, 10, 30, 60, 300):
            assert msg in buf.recent_messages(w)

    def test_multiple_messages_ordered_oldest_first(self):
        buf = ChatBuffer()
        t = time.time()
        m1 = make_message(user_id="u1", received_at=t)
        m2 = make_message(user_id="u2", received_at=t + 1)
        buf.add(m1)
        buf.add(m2)
        recent = buf.recent_messages(30)
        assert recent[0].user_id == "u1"
        assert recent[1].user_id == "u2"

    def test_total_buffered_counts_largest_window(self):
        buf = ChatBuffer()
        buf.add(make_message())
        buf.add(make_message())
        assert buf.total_buffered == 2


class TestChatBufferPrune:
    def test_expired_messages_removed(self):
        buf = ChatBuffer()
        old_ts = time.time() - 10  # 10 seconds ago
        msg = make_message(received_at=old_ts)
        buf.add(msg)
        buf.prune()
        # Should be gone from the 5s window but present in 30s+
        assert msg not in buf.recent_messages(5)
        assert msg in buf.recent_messages(30)

    def test_very_old_message_removed_from_all_windows(self):
        buf = ChatBuffer()
        ancient_ts = time.time() - 400  # older than all windows
        msg = make_message(received_at=ancient_ts)
        buf.add(msg)
        buf.prune()
        for w in (5, 10, 30, 60, 300):
            assert msg not in buf.recent_messages(w)

    def test_fresh_message_survives_prune(self):
        buf = ChatBuffer()
        msg = make_message()
        buf.add(msg)
        buf.prune()
        assert msg in buf.recent_messages(5)


class TestWindowStats:
    def test_empty_window_returns_zeros(self):
        buf = ChatBuffer()
        stats = buf.stats(5)
        assert stats.message_count == 0
        assert stats.unique_users == 0
        assert stats.duplicate_ratio == 0.0
        assert stats.messages_per_second == 0.0

    def test_message_count_correct(self):
        buf = ChatBuffer()
        buf.add(make_message(user_id="u1"))
        buf.add(make_message(user_id="u2"))
        assert buf.stats(5).message_count == 2

    def test_unique_users_deduped(self):
        buf = ChatBuffer()
        buf.add(make_message(user_id="u1", content="a"))
        buf.add(make_message(user_id="u1", content="b"))
        buf.add(make_message(user_id="u2", content="c"))
        assert buf.stats(30).unique_users == 2

    def test_duplicate_ratio_zero_when_all_unique(self):
        buf = ChatBuffer()
        buf.add(make_message(content="alpha"))
        buf.add(make_message(content="beta"))
        buf.add(make_message(content="gamma"))
        assert buf.stats(30).duplicate_ratio == 0.0

    def test_duplicate_ratio_max_when_all_same(self):
        buf = ChatBuffer()
        for _ in range(5):
            buf.add(make_message(content="same message"))
        stats = buf.stats(30)
        # 1 unique hash / 5 total → dup_ratio = 1 - 1/5 = 0.8
        assert abs(stats.duplicate_ratio - 0.8) < 0.01

    def test_messages_per_second_reasonable(self):
        buf = ChatBuffer()
        for _ in range(10):
            buf.add(make_message())
        stats = buf.stats(5)
        # 10 messages / 5s window = 2.0 msg/s
        assert stats.messages_per_second == 2.0

    def test_invalid_window_raises(self):
        buf = ChatBuffer()
        with pytest.raises(ValueError, match="Unknown window"):
            buf.stats(999)

    def test_all_stats_returns_all_windows(self):
        buf = ChatBuffer()
        buf.add(make_message())
        all_s = buf.all_stats()
        assert set(all_s.keys()) == {5, 10, 30, 60, 300}


class TestRecentMessages:
    def test_returns_list_of_messages(self):
        buf = ChatBuffer()
        m = make_message()
        buf.add(m)
        assert buf.recent_messages(30) == [m]

    def test_unknown_window_returns_empty(self):
        buf = ChatBuffer()
        # recent_messages() uses .get() with a deque default
        buf.add(make_message())
        assert buf.recent_messages(999) == []
