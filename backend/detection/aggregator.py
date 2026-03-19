"""
Confidence score aggregation — combines per-user signal scores into a
single threat score (0–100).

SIGNAL_WEIGHTS are intentionally over-100 — the normalization step brings the
output to a 0–100 range while preserving relative weighting.
Current sum: 195.0
"""

from __future__ import annotations

SIGNAL_WEIGHTS: dict[str, float] = {
    "temporal_sync":    30.0,  # Strongest: direct coordination evidence
    "minhash_cluster":  25.0,  # Strong: near-identical messages
    "rate_anomaly":     20.0,  # Moderate: machine-speed messaging
    "burst_anomaly":    15.0,  # Moderate: statistically abnormal volume
    "duplicate_ratio":  20.0,  # Strong: exact duplicates
    "username_entropy": 10.0,  # Weak: supplementary only
    "new_account":      15.0,  # Moderate: new accounts are a meaningful bot signal
    "known_bot":        25.0,  # Strong: on public known-bot list
    "pattern_match":    20.0,  # Strong: matched spam pattern corpus
    "timing_regularity": 15.0, # Moderate: machine-regular inter-arrival times
}

_MAX_POSSIBLE = sum(SIGNAL_WEIGHTS.values())


def compute_user_threat_score(signals: dict[str, float]) -> float:
    """
    signals: dict of signal_name → raw score (each already normalized 0–N
             per their individual max contribution).
    Returns composite threat score 0–100.
    """
    weighted_sum = sum(
        signals.get(name, 0.0) * weight
        for name, weight in SIGNAL_WEIGHTS.items()
    )
    # Each signal arrives pre-scaled to 0–1 by the engine before passing here
    return min((weighted_sum / _MAX_POSSIBLE) * 100, 100.0)
