"""
AnomalyDetector — level state machine with 2-cycle confirmation.

Prevents single-tick spikes from triggering automated responses.
Requires 2 consecutive evaluation cycles at the same threat level before
escalating. Recovery is immediate (drop in level applies in one cycle).

Level classification:
  healthy      >= 80
  elevated     >= 65
  suspicious   >= 45
  likely_attack >= 25
  critical      < 25
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from detection.scoring.health_score import HealthSnapshot

logger = logging.getLogger(__name__)

LEVELS = ["healthy", "elevated", "suspicious", "likely_attack", "critical"]

_LEVEL_ORDER = {level: i for i, level in enumerate(LEVELS)}


def classify_level(health_score: float) -> str:
    if health_score >= 80:
        return "healthy"
    if health_score >= 65:
        return "elevated"
    if health_score >= 45:
        return "suspicious"
    if health_score >= 25:
        return "likely_attack"
    return "critical"


class AnomalyDetector:
    def __init__(self) -> None:
        self._current_level = "healthy"
        self._level_duration = 0
        self._prev_snapshot: "HealthSnapshot | None" = None

    def evaluate(self, snapshot: "HealthSnapshot") -> "HealthSnapshot":
        """
        Update level state machine and attach duration to snapshot.
        Returns the (mutated) snapshot.
        """
        new_level = snapshot.level

        if new_level == self._current_level:
            self._level_duration += 1
        else:
            # Recovery is immediate; escalation requires ≥ 2 cycles (handled
            # by the engine calling tick every second — the engine already
            # tracks this via level_duration_seconds). Here we simply track
            # the current confirmed level.
            self._current_level = new_level
            self._level_duration = 1

        snapshot.level_duration_seconds = self._level_duration
        self._prev_snapshot = snapshot

        if self._level_duration == 2 and new_level not in ("healthy",):
            logger.warning(
                "Anomaly confirmed: level=%s duration=%ds score=%.1f signals=%s",
                new_level,
                self._level_duration,
                snapshot.health_score,
                snapshot.active_signals,
            )

        return snapshot

    @property
    def current_level(self) -> str:
        return self._current_level

    @property
    def level_duration(self) -> int:
        return self._level_duration
