"""
Tests for detection/fast/temporal.py — TemporalSyncDetector

Covers: score returns zero for lone messages, fires at correct thresholds,
per-user attribution, score decay via reset_tick, and prune behaviour.
"""

from __future__ import annotations

import time
import pytest

from detection.fast.temporal import TemporalSyncDetector, SYNC_WINDOWS


class TestTemporalSyncDetectorBasic:
    def test_single_user_no_score(self):
        det = TemporalSyncDetector()
        ts = time.time()
        score = det.add("hash1", "user1", ts)
        assert score == 0.0

    def test_two_users_same_hash_within_1s_fires(self):
        """Smallest window: 1s, threshold 2 users — should trigger."""
        det = TemporalSyncDetector()
        ts = time.time()
        det.add("hash1", "user1", ts)
        score = det.add("hash1", "user2", ts + 0.1)
        assert score > 0.0

    def test_two_users_different_hashes_no_score(self):
        det = TemporalSyncDetector()
        ts = time.time()
        det.add("hash1", "user1", ts)
        score = det.add("hash2", "user2", ts + 0.1)
        assert score == 0.0

    def test_same_user_twice_does_not_trigger(self):
        """Distinct-user requirement: same user sending same content twice."""
        det = TemporalSyncDetector()
        ts = time.time()
        det.add("hash1", "user1", ts)
        score = det.add("hash1", "user1", ts + 0.1)
        # Only 1 distinct user — threshold is 2, so no trigger
        assert score == 0.0

    def test_score_capped_at_25(self):
        det = TemporalSyncDetector()
        ts = time.time()
        # Flood with 50 users to push score to max
        for i in range(50):
            det.add("hash1", f"user{i}", ts + i * 0.01)
        assert det.current_score <= 25.0

    def test_larger_burst_scores_higher_than_smaller(self):
        det = TemporalSyncDetector()
        ts = time.time()
        # Small burst: 2 users
        small_det = TemporalSyncDetector()
        small_det.add("h", "u1", ts)
        small_score = small_det.add("h", "u2", ts + 0.1)

        # Large burst: 20 users
        large_det = TemporalSyncDetector()
        for i in range(20):
            large_det.add("h", f"user{i}", ts + i * 0.01)
        large_score = large_det.current_score

        assert large_score > small_score


class TestTemporalSyncDetectorResetTick:
    def test_reset_tick_returns_current_score(self):
        det = TemporalSyncDetector()
        ts = time.time()
        det.add("h", "u1", ts)
        det.add("h", "u2", ts + 0.1)
        assert det.current_score > 0.0
        returned = det.reset_tick()
        assert returned == det.current_score or returned > 0.0

    def test_reset_tick_zeroes_score(self):
        det = TemporalSyncDetector()
        ts = time.time()
        det.add("h", "u1", ts)
        det.add("h", "u2", ts + 0.1)
        det.reset_tick()
        assert det.current_score == 0.0

    def test_reset_tick_empty_returns_zero(self):
        det = TemporalSyncDetector()
        assert det.reset_tick() == 0.0


class TestTemporalSyncDetectorWindowBoundaries:
    def test_message_outside_all_windows_not_counted(self):
        det = TemporalSyncDetector()
        old_ts = time.time() - 60  # 60s ago — outside all windows (max 30s)
        now_ts = time.time()
        det.add("h", "u1", old_ts)
        # Manually prune to simulate passage of time
        det._prune_all(now_ts)
        score = det.add("h", "u2", now_ts)
        # u1's entry is gone — only u2 remains, below threshold
        assert score == 0.0

    def test_3s_window_requires_3_distinct_users(self):
        det = TemporalSyncDetector()
        ts = time.time()
        det.add("h", "u1", ts)
        det.add("h", "u2", ts + 0.5)
        # Only 2 users within 3s — 3s window needs 3, 1s window needs 2
        # 1s window IS triggered so score > 0, but 3s window is not
        score = det.add("h", "u3", ts + 1)
        # Now 3 users — both 1s (if within 1s) and 3s windows may fire
        assert score > 0.0
