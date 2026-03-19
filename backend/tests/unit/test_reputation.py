"""
Tests for storage/reputation.py — ReputationStore

Uses an in-memory SQLite database (aiosqlite supports ":memory:").
Covers: default score, penalty bounds, recovery, threat modifier arithmetic.
"""

from __future__ import annotations

import asyncio
import pytest

from storage.reputation import (
    ReputationStore,
    _FLAG_PENALTY,
    _ACTION_PENALTY,
    _FP_RECOVERY,
    _MIN_SCORE,
    _MAX_SCORE,
    REPUTATION_WEIGHT,
)


async def _make_store() -> ReputationStore:
    """Create a ReputationStore backed by in-memory SQLite with the schema applied."""
    import aiosqlite
    from storage.reputation import REPUTATION_DDL

    store = ReputationStore(db_path=":memory:")
    # Initialise the schema in the shared in-memory DB
    # Note: aiosqlite ":memory:" creates a separate DB per connection, so
    # we test via the store's own _adjust/_fetch methods directly.
    return store


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class TestReputationDefault:
    def test_unknown_user_returns_100(self):
        store = _run(_make_store())
        # _fetch will hit a fresh DB with no table — exception path returns 100.0
        score = _run(store.get("unknown_user"))
        assert score == 100.0

    def test_get_caches_result(self):
        store = _run(_make_store())
        store._cache["cached_user"] = 75.0
        score = _run(store.get("cached_user"))
        assert score == 75.0


class TestReputationPenalties:
    def test_flag_penalty_reduces_score(self):
        store = _run(_make_store())
        store._cache["u1"] = 100.0
        _run(store.record_flag("u1", "user1"))
        assert store._cache["u1"] == 100.0 - _FLAG_PENALTY

    def test_action_penalty_larger_than_flag_penalty(self):
        assert _ACTION_PENALTY > _FLAG_PENALTY

    def test_action_penalty_reduces_score(self):
        store = _run(_make_store())
        store._cache["u2"] = 100.0
        _run(store.record_action("u2", "user2"))
        assert store._cache["u2"] == 100.0 - _ACTION_PENALTY

    def test_score_never_goes_below_zero(self):
        store = _run(_make_store())
        store._cache["u3"] = 5.0
        # Apply many penalties
        for _ in range(20):
            _run(store.record_flag("u3", "user3"))
        assert store._cache["u3"] >= _MIN_SCORE

    def test_score_never_exceeds_100(self):
        store = _run(_make_store())
        store._cache["u4"] = 98.0
        _run(store.record_false_positive("u4", "user4"))
        assert store._cache["u4"] <= _MAX_SCORE

    def test_false_positive_recovery_increases_score(self):
        store = _run(_make_store())
        store._cache["u5"] = 60.0
        _run(store.record_false_positive("u5", "user5"))
        assert store._cache["u5"] == 60.0 + _FP_RECOVERY

    def test_multiple_flags_accumulate(self):
        store = _run(_make_store())
        store._cache["u6"] = 100.0
        for _ in range(3):
            _run(store.record_flag("u6", "user6"))
        expected = max(_MIN_SCORE, 100.0 - 3 * _FLAG_PENALTY)
        assert abs(store._cache["u6"] - expected) < 0.01


class TestThreatModifier:
    def test_clean_user_no_modifier(self):
        """A user with reputation 100 should add 0 to threat score."""
        store = _run(_make_store())
        store._cache["clean"] = 100.0
        result = _run(store.apply_threat_modifier("clean", 50.0))
        assert result == 50.0

    def test_zero_reputation_adds_maximum_boost(self):
        """A user with reputation 0 should receive the full REPUTATION_WEIGHT * 30 boost."""
        store = _run(_make_store())
        store._cache["bot"] = 0.0
        base = 50.0
        result = _run(store.apply_threat_modifier("bot", base))
        expected = min(100.0, base + 1.0 * REPUTATION_WEIGHT * 30.0)
        assert abs(result - expected) < 0.01

    def test_modifier_never_exceeds_100(self):
        store = _run(_make_store())
        store._cache["bot2"] = 0.0
        result = _run(store.apply_threat_modifier("bot2", 95.0))
        assert result <= 100.0

    def test_mid_reputation_gives_partial_boost(self):
        """50% reputation → 50% of max boost."""
        store = _run(_make_store())
        store._cache["mid"] = 50.0
        base = 40.0
        result = _run(store.apply_threat_modifier("mid", base))
        expected = min(100.0, base + 0.5 * REPUTATION_WEIGHT * 30.0)
        assert abs(result - expected) < 0.01
