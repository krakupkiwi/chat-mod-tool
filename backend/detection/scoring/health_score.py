"""
HealthScoreEngine — combines all metric scores into a single 0–100
Chat Health Score and wraps it in a HealthSnapshot.

100 = perfectly healthy chat.
0   = active bot raid in progress.

Score weights and per-metric maximums are defined here as the single source
of truth. The engine delegates baseline calibration to AdaptiveBaseline.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from detection.scoring.baseline import AdaptiveBaseline

# Weight of each metric in the final risk score (must sum to 1.0)
METRIC_WEIGHTS: dict[str, float] = {
    "temporal_sync":    0.25,
    "duplicate_ratio":  0.25,
    "semantic_cluster": 0.20,
    "velocity":         0.15,
    "burst_anomaly":    0.08,
    "new_account":      0.04,
    "entropy":          0.03,
}

# Maximum raw value each metric can contribute (for 0–1 normalisation)
METRIC_MAX: dict[str, float] = {
    "temporal_sync":    25.0,
    "duplicate_ratio":  35.0,
    "semantic_cluster": 25.0,
    "velocity":         30.0,
    "burst_anomaly":    25.0,
    "new_account":      20.0,
    "entropy":          15.0,
}

# Minimum raw score for a signal to appear in the active_signals list
ACTIVE_THRESHOLDS: dict[str, float] = {
    "temporal_sync":    8.0,
    "duplicate_ratio":  12.0,
    "semantic_cluster": 8.0,
    "velocity":         10.0,
    "burst_anomaly":    8.0,
    "new_account":      6.0,
    "entropy":          5.0,
}


@dataclass
class HealthSnapshot:
    timestamp: float
    health_score: float
    risk_score: float
    level: str
    level_duration_seconds: int
    trend: str                          # worsening / stable / improving
    metric_scores: dict[str, float]
    active_signals: list[str]
    messages_per_minute: float
    active_users: int
    duplicate_ratio: float
    messages_in_5s: int
    messages_in_30s: int
    clusters: list[dict] = field(default_factory=list)


class HealthScoreEngine:
    def __init__(self) -> None:
        self.baseline = AdaptiveBaseline()
        self._prev_score: float = 100.0
        self.last_snapshot: HealthSnapshot | None = None

    def compute(
        self,
        raw_scores: dict[str, float],
        chat_stats: dict,
        clusters: list[dict],
        level_duration: int,
        level: str,
    ) -> HealthSnapshot:
        # Weighted risk — normalise each metric to 0–1 first
        weighted_risk = sum(
            (raw_scores.get(name, 0.0) / METRIC_MAX.get(name, 1.0))
            * weight
            * 100
            for name, weight in METRIC_WEIGHTS.items()
        )

        # Record raw_risk in baseline history for calibration
        now = time.time()
        self.baseline.record({"raw_risk": weighted_risk}, now)
        calibrated_risk = self.baseline.calibrate(weighted_risk)
        risk_score = max(0.0, min(100.0, calibrated_risk))
        health_score = 100.0 - risk_score

        # Trend
        delta = health_score - self._prev_score
        trend = "stable"
        if delta < -5:
            trend = "worsening"
        elif delta > 5:
            trend = "improving"
        self._prev_score = health_score

        active_signals = [
            name for name, threshold in ACTIVE_THRESHOLDS.items()
            if raw_scores.get(name, 0.0) >= threshold
        ]

        snapshot = HealthSnapshot(
            timestamp=now,
            health_score=round(health_score, 1),
            risk_score=round(risk_score, 1),
            level=level,
            level_duration_seconds=level_duration,
            trend=trend,
            metric_scores={k: round(v, 2) for k, v in raw_scores.items()},
            active_signals=active_signals,
            messages_per_minute=round(chat_stats.get("mpm", 0.0), 1),
            active_users=chat_stats.get("active_users", 0),
            duplicate_ratio=round(chat_stats.get("duplicate_ratio", 0.0), 4),
            messages_in_5s=chat_stats.get("messages_in_5s", 0),
            messages_in_30s=chat_stats.get("messages_in_30s", 0),
            clusters=clusters,
        )
        self.last_snapshot = snapshot
        return snapshot
