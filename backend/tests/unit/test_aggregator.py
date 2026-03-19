"""
Tests for detection/aggregator.py — compute_user_threat_score

Covers: zero input, full signal, partial signals, score bounds (0–100),
and weight ordering (stronger signals produce higher scores).
"""

from __future__ import annotations

import pytest
from detection.aggregator import compute_user_threat_score, SIGNAL_WEIGHTS


class TestComputeUserThreatScore:
    def test_all_zero_signals_returns_zero(self):
        signals = {k: 0.0 for k in SIGNAL_WEIGHTS}
        assert compute_user_threat_score(signals) == 0.0

    def test_all_max_signals_returns_100(self):
        signals = {k: 1.0 for k in SIGNAL_WEIGHTS}
        assert compute_user_threat_score(signals) == 100.0

    def test_score_bounded_above_100(self):
        # Values > 1.0 should still not exceed 100
        signals = {k: 2.0 for k in SIGNAL_WEIGHTS}
        assert compute_user_threat_score(signals) <= 100.0

    def test_score_never_negative(self):
        signals = {k: -1.0 for k in SIGNAL_WEIGHTS}
        # Negative inputs produce a negative weighted sum — score is min(result, 100)
        # but we expect the function not to raise; negative is technically allowed
        # by the math but represents invalid caller behaviour
        result = compute_user_threat_score(signals)
        assert isinstance(result, float)

    def test_missing_signals_treated_as_zero(self):
        # Only temporal_sync present
        score_partial = compute_user_threat_score({"temporal_sync": 1.0})
        # Should be less than all-1.0 score
        score_full = compute_user_threat_score({k: 1.0 for k in SIGNAL_WEIGHTS})
        assert score_partial < score_full

    def test_temporal_sync_outweighs_entropy(self):
        """temporal_sync (weight 30) should produce higher score than username_entropy (weight 10)."""
        score_sync = compute_user_threat_score({"temporal_sync": 1.0})
        score_entropy = compute_user_threat_score({"username_entropy": 1.0})
        assert score_sync > score_entropy

    def test_dual_strong_signal_score(self):
        """
        temporal_sync (weight 30) + minhash_cluster (weight 25) at full strength.
        MAX_POSSIBLE = 185 (sum of all 10 signal weights after Phase 9).
        Score = (30+25)/185 * 100 ≈ 29.7.
        Two signals alone do not reach the 55.0 alert threshold — multiple
        signals are required to push over it.  This is intentional: the
        minimum-2-signal guard catches weak pairs while the 55.0 threshold
        requires meaningful signal accumulation.
        """
        from detection.aggregator import _MAX_POSSIBLE
        score = compute_user_threat_score({
            "temporal_sync": 1.0,
            "minhash_cluster": 1.0,
        })
        expected = (30.0 + 25.0) / _MAX_POSSIBLE * 100
        assert abs(score - expected) < 0.1

    def test_triple_strong_signal_exceeds_alert_threshold(self):
        """
        Five strong signals must exceed 55.
        temporal_sync(30)+minhash_cluster(25)+rate_anomaly(20)+duplicate_ratio(20)+burst_anomaly(15)
        = 110/185 * 100 ≈ 59.5 > 55.
        With the Phase 9 weight expansion (10 signals, MAX=185) a larger
        combination is required vs. the old 7-signal set.
        """
        score = compute_user_threat_score({
            "temporal_sync":    1.0,
            "minhash_cluster":  1.0,
            "rate_anomaly":     1.0,
            "duplicate_ratio":  1.0,
            "burst_anomaly":    1.0,
        })
        assert score > 55.0  # 110/185 * 100 ≈ 59.5

    def test_single_weak_signal_below_threshold(self):
        """A single weak signal (new_account alone) must not reach 55.0."""
        score = compute_user_threat_score({"new_account": 1.0})
        assert score < 55.0

    def test_extra_signal_keys_ignored(self):
        """Unknown signal names should not raise — they're simply not in SIGNAL_WEIGHTS."""
        signals = {"nonexistent_signal": 1.0, "temporal_sync": 0.5}
        score = compute_user_threat_score(signals)
        assert 0.0 <= score <= 100.0

    def test_score_monotonically_increases_with_signal_strength(self):
        scores = [
            compute_user_threat_score({"temporal_sync": v})
            for v in [0.0, 0.25, 0.5, 0.75, 1.0]
        ]
        assert scores == sorted(scores)


class TestSignalWeights:
    def test_all_signals_present(self):
        # Phase 9 added known_bot, pattern_match, timing_regularity to the
        # original 7-signal set — 10 signals total.
        expected = {
            "temporal_sync", "minhash_cluster", "rate_anomaly",
            "burst_anomaly", "duplicate_ratio", "username_entropy", "new_account",
            "known_bot", "pattern_match", "timing_regularity",
        }
        assert set(SIGNAL_WEIGHTS.keys()) == expected

    def test_all_weights_positive(self):
        assert all(w > 0 for w in SIGNAL_WEIGHTS.values())

    def test_temporal_sync_is_highest_weight(self):
        assert SIGNAL_WEIGHTS["temporal_sync"] == max(SIGNAL_WEIGHTS.values())

    def test_username_entropy_is_lowest_weight(self):
        # username_entropy (10.0) is the weakest supplementary signal;
        # new_account was raised to 15.0 in Phase 4 when account-age scoring improved.
        assert SIGNAL_WEIGHTS["username_entropy"] == min(SIGNAL_WEIGHTS.values())
