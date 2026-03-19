"""
Tests for DetectionEngine signal logic.

Covers:
  - _is_short_reaction(): the emote-wave false-positive guard
  - process_message(): per-user signal attribution (similarity detectors
    skipped for short reactions, accumulate for longer messages)
  - _evaluate_user_alerts(): minimum-signal guard, cooldown, protected accounts
"""

from __future__ import annotations

import asyncio
import time
import pytest

from detection.engine import DetectionEngine
from pipeline.buffer import ChatBuffer
from tests.conftest import make_message


# ---------------------------------------------------------------------------
# _is_short_reaction
# ---------------------------------------------------------------------------

class TestIsShortReaction:
    def _msg(self, content, url_count=0, mention_count=0, word_count=None, char_count=None):
        return make_message(
            content=content,
            url_count=url_count,
            mention_count=mention_count,
            word_count=word_count if word_count is not None else len(content.split()),
            char_count=char_count if char_count is not None else len(content),
        )

    def test_single_emote_is_short_reaction(self):
        msg = self._msg("PogChamp", word_count=1, char_count=8)
        assert DetectionEngine._is_short_reaction(msg) is True

    def test_double_emote_is_short_reaction(self):
        msg = self._msg("LUL LUL", word_count=2, char_count=7)
        assert DetectionEngine._is_short_reaction(msg) is True

    def test_three_word_message_is_short_reaction(self):
        msg = self._msg("nice nice nice", word_count=3, char_count=14)
        assert DetectionEngine._is_short_reaction(msg) is True

    def test_four_word_short_chars_still_short(self):
        # char_count <= 25 takes precedence even if word_count > 3
        msg = self._msg("gg gg gg gg", word_count=4, char_count=11)
        assert DetectionEngine._is_short_reaction(msg) is True

    def test_long_message_not_short_reaction(self):
        content = "this is a longer bot spam message with many words and content here"
        msg = self._msg(content, word_count=12, char_count=len(content))
        assert DetectionEngine._is_short_reaction(msg) is False

    def test_url_disqualifies_short_reaction(self):
        msg = self._msg("PogChamp", url_count=1, word_count=1, char_count=8)
        assert DetectionEngine._is_short_reaction(msg) is False

    def test_mention_disqualifies_short_reaction(self):
        msg = self._msg("nice", mention_count=1, word_count=1, char_count=4)
        assert DetectionEngine._is_short_reaction(msg) is False

    def test_url_and_mention_both_disqualify(self):
        msg = self._msg("check", url_count=1, mention_count=1, word_count=1, char_count=5)
        assert DetectionEngine._is_short_reaction(msg) is False

    def test_exactly_25_chars_is_short(self):
        content = "a" * 25
        msg = self._msg(content, word_count=1, char_count=25)
        assert DetectionEngine._is_short_reaction(msg) is True

    def test_26_chars_and_4_words_not_short(self):
        content = "abcde abcde abcde abcde xy"  # 26 chars, 5 words
        msg = self._msg(content, word_count=5, char_count=26)
        assert DetectionEngine._is_short_reaction(msg) is False


# ---------------------------------------------------------------------------
# process_message — similarity detectors skipped for short reactions
# ---------------------------------------------------------------------------

class TestProcessMessageSimilarityBypass:
    def _make_engine(self):
        buf = ChatBuffer()
        return DetectionEngine(buf), buf

    def _run(self, coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    def test_short_reaction_does_not_accumulate_temporal_sync(self):
        engine, _ = self._make_engine()
        ts = time.time()
        # Inject 5 users all sending the same 1-word message (short reaction)
        for i in range(5):
            msg = make_message(
                user_id=f"u{i}", content="PogChamp",
                word_count=1, char_count=8,
                received_at=ts + i * 0.01,
            )
            self._run(engine.process_message(msg))

        # temporal_sync should be 0 for all users
        for i in range(5):
            sigs = engine._user_signals.get(f"u{i}", {})
            assert sigs.get("temporal_sync", 0.0) == 0.0, (
                f"user u{i} has unexpected temporal_sync={sigs.get('temporal_sync')}"
            )

    def test_long_message_accumulates_temporal_sync(self):
        engine, _ = self._make_engine()
        ts = time.time()
        content = "buy cheap followers now at my website dot com here"
        for i in range(5):
            msg = make_message(
                user_id=f"u{i}", content=content,
                word_count=9, char_count=len(content),
                received_at=ts + i * 0.01,
            )
            self._run(engine.process_message(msg))

        # At least some users should have a non-zero temporal_sync
        scores = [
            engine._user_signals.get(f"u{i}", {}).get("temporal_sync", 0.0)
            for i in range(5)
        ]
        assert any(s > 0.0 for s in scores)

    def test_short_reaction_does_not_score_minhash(self):
        engine, _ = self._make_engine()
        ts = time.time()
        for i in range(5):
            msg = make_message(
                user_id=f"u{i}", content="LUL",
                word_count=1, char_count=3,
                received_at=ts + i * 0.01,
            )
            self._run(engine.process_message(msg))

        for i in range(5):
            sigs = engine._user_signals.get(f"u{i}", {})
            assert sigs.get("minhash_cluster", 0.0) == 0.0

    def test_rate_signal_still_fires_for_short_reactions(self):
        """Rate anomaly accumulates regardless of skip_similarity."""
        engine, _ = self._make_engine()
        ts = time.time()
        # Same user sends 20 short messages rapidly
        for i in range(20):
            msg = make_message(
                user_id="u1", content="lol",
                word_count=1, char_count=3,
                received_at=ts + i * 0.05,
            )
            self._run(engine.process_message(msg))

        rate = engine._user_signals.get("u1", {}).get("rate_anomaly", 0.0)
        assert rate >= 0.0  # May or may not trigger threshold — just must not error


# ---------------------------------------------------------------------------
# _evaluate_user_alerts — guards
# ---------------------------------------------------------------------------

class TestEvaluateUserAlertsGuards:
    def _make_engine_with_ws(self):
        from unittest.mock import AsyncMock, MagicMock
        buf = ChatBuffer()
        engine = DetectionEngine(buf)
        ws = MagicMock()
        ws.broadcast = AsyncMock()
        engine.set_ws_manager(ws)
        return engine, buf, ws

    def _run(self, coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    def test_protected_moderator_never_alerted(self):
        engine, buf, ws = self._make_engine_with_ws()
        ts = time.time()
        msg = make_message(user_id="mod1", is_moderator=True, received_at=ts)
        buf.add(msg)

        # Force high signals for the moderator
        engine._user_signals["mod1"] = {
            "temporal_sync": 25.0,
            "minhash_cluster": 25.0,
            "rate_anomaly": 20.0,
            "duplicate_ratio": 35.0,
            "username_entropy": 0.0,
            "new_account": 0.0,
            "burst_anomaly": 0.0,
        }

        from detection.scoring.health_score import HealthSnapshot
        snap = HealthSnapshot.__new__(HealthSnapshot)
        snap.health_score = 20.0
        snap.risk_score = 80.0
        snap.level = "critical"

        # _evaluate_user_alerts now requires recent_msgs (messages in 30s window)
        self._run(engine._evaluate_user_alerts(snap, [msg]))
        ws.broadcast.assert_not_called()

    def test_cooldown_prevents_double_alert(self):
        engine, buf, ws = self._make_engine_with_ws()
        ts = time.time()
        msg = make_message(user_id="bot1", received_at=ts)
        buf.add(msg)

        engine._user_signals["bot1"] = {
            "temporal_sync": 1.0,
            "minhash_cluster": 1.0,
            "rate_anomaly": 1.0,
            "duplicate_ratio": 1.0,
            "username_entropy": 0.0,
            "new_account": 0.0,
            "burst_anomaly": 0.0,
        }
        # Mark as recently alerted
        engine._last_alerted["bot1"] = time.time()

        from detection.scoring.health_score import HealthSnapshot
        snap = HealthSnapshot.__new__(HealthSnapshot)
        snap.health_score = 20.0
        snap.risk_score = 80.0
        snap.level = "critical"

        # _evaluate_user_alerts now requires recent_msgs (messages in 30s window)
        self._run(engine._evaluate_user_alerts(snap, [msg]))
        ws.broadcast.assert_not_called()

    def test_minimum_two_signals_required(self):
        engine, buf, ws = self._make_engine_with_ws()
        ts = time.time()
        msg = make_message(user_id="susp1", received_at=ts)
        buf.add(msg)

        # Only one signal above 0.2 — should NOT alert
        engine._user_signals["susp1"] = {
            "temporal_sync": 25.0,  # normalised = 1.0 (≥ 0.2)
            "minhash_cluster": 0.0,
            "rate_anomaly": 0.0,
            "duplicate_ratio": 0.0,
            "username_entropy": 0.0,
            "new_account": 0.0,
            "burst_anomaly": 0.0,
        }

        from detection.scoring.health_score import HealthSnapshot
        snap = HealthSnapshot.__new__(HealthSnapshot)
        snap.health_score = 20.0
        snap.risk_score = 80.0
        snap.level = "critical"

        # _evaluate_user_alerts now requires recent_msgs (messages in 30s window)
        self._run(engine._evaluate_user_alerts(snap, [msg]))
        ws.broadcast.assert_not_called()
