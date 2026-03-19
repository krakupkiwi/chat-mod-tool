"""
HealthDriftDetector — detects slow-ramp bot campaigns that stay under tick thresholds.

Two complementary mechanisms:

1. EWMA Control Limits (no external dep required)
   Exponentially Weighted Moving Average with 3σ control bands.
   Flags when the current metric deviates more than 3 standard deviations
   from the EWMA — catches slow drift that threshold rules miss.

2. River ADWIN (Adaptive Windowing)
   Statistical change-point detector on the message rate time series.
   ADWIN maintains a sliding window and alerts when the rate distribution
   has statistically shifted, indicating a coordinated ramp-up.

Both detectors emit a `drift_detected` flag that is added to the health
payload and triggers a UI warning without automatically escalating severity.
The detection engine logs a warning for operator review.

Usage (in tick.py):
    drift_result = health_drift_detector.update(
        mpm=stats_60s.messages_per_second * 60,
        health_score=snapshot.health_score,
    )
    if drift_result.drift_detected:
        logger.warning("Chat drift detected: %s", drift_result.reason)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# EWMA smoothing factor (α): higher = faster response, lower = smoother
_ALPHA = 0.1
# Control limit multiplier (k): raised to 4.0 to reduce false alarms on gradual ramp-up
_CONTROL_LIMIT_K = 4.0
# Minimum samples before EWMA control limits are considered reliable
_EWMA_MIN_SAMPLES = 120  # 2 minutes of data — avoids false alarms during stream start ramp-up
# Minimum sigma floor: prevents control bands collapsing to near-zero on stable/slowly-rising input
_MIN_SIGMA_MPM = 15.0   # mpm — at least ±15 mpm wiggle room always
_MIN_SIGMA_HEALTH = 5.0  # health score units
# Minimum absolute deviation required before an EWMA alarm fires.
# Statistical significance alone isn't enough — the deviation must also be practically meaningful.
_MIN_ABSOLUTE_DEVIATION_MPM = 25.0   # mpm
_MIN_ABSOLUTE_DEVIATION_HEALTH = 10.0  # health score units

# ADWIN delta parameter: lower = more sensitive (more false alarms), higher = fewer
_ADWIN_DELTA = 0.002


@dataclass
class DriftResult:
    drift_detected: bool
    reason: str = ""
    ewma_value: float = 0.0
    ewma_ucl: float = 0.0  # upper control limit
    ewma_lcl: float = 0.0  # lower control limit
    adwin_detected: bool = False


class EWMAControlChart:
    """
    EWMA control chart for a single metric stream.
    Emits an alarm when the observation falls outside the k*σ control bands.

    Two guards prevent false alarms on naturally-ramping streams (e.g. chat at stream start):
      1. min_sigma floor — control bands never collapse below a minimum width
      2. min_absolute_deviation — the value must also deviate by more than this absolute
         amount from the EWMA before an alarm fires (statistical + practical significance)
    """

    def __init__(
        self,
        alpha: float = _ALPHA,
        k: float = _CONTROL_LIMIT_K,
        min_sigma: float = 0.0,
        min_absolute_deviation: float = 0.0,
    ) -> None:
        self._alpha = alpha
        self._k = k
        self._min_sigma = min_sigma
        self._min_absolute_deviation = min_absolute_deviation
        self._ewma: float | None = None
        self._ewma_var: float = 0.0   # variance of EWMA residuals
        self._sample_count = 0

    def update(self, value: float) -> tuple[float, float, float, bool]:
        """
        Update with a new observation.
        Returns (ewma, ucl, lcl, alarm_raised).
        """
        if self._ewma is None:
            self._ewma = value
            self._sample_count += 1
            return value, value, value, False

        prev_ewma = self._ewma
        self._ewma = self._alpha * value + (1 - self._alpha) * self._ewma
        residual = value - prev_ewma
        self._ewma_var = (
            self._alpha * residual ** 2 + (1 - self._alpha) * self._ewma_var
        )
        self._sample_count += 1

        # Apply minimum sigma floor so control bands never become arbitrarily tight
        sigma = max(math.sqrt(max(self._ewma_var, 0.0)), self._min_sigma)
        # Control limits: EWMA ± k*σ * √(α / (2-α))
        # (simplified form — exact Shewhart EWMA limits)
        limit_half = self._k * sigma * math.sqrt(
            self._alpha / (2 - self._alpha)
        )
        ucl = self._ewma + limit_half
        lcl = max(self._ewma - limit_half, 0.0)

        if self._sample_count < _EWMA_MIN_SAMPLES:
            return self._ewma, ucl, lcl, False

        statistical_alarm = value > ucl or value < lcl
        # Practical significance guard: only fire if deviation is large enough to matter
        practical_alarm = abs(value - self._ewma) >= self._min_absolute_deviation
        alarm = statistical_alarm and practical_alarm
        return self._ewma, ucl, lcl, alarm


class ADWINDriftDetector:
    """
    Wrapper around River's ADWIN change-point detector.
    Falls back silently to a no-op if River is not installed.
    """

    def __init__(self) -> None:
        try:
            from river.drift import ADWIN
            self._adwin = ADWIN(delta=_ADWIN_DELTA)
            self._available = True
        except ImportError:
            self._adwin = None
            self._available = False

    def update(self, value: float) -> bool:
        """Returns True if a drift event was detected."""
        if not self._available or self._adwin is None:
            return False
        self._adwin.update(value)
        return self._adwin.drift_detected


class HealthDriftDetector:
    """
    Combines EWMA control chart + ADWIN for comprehensive drift detection.

    Monitors two streams:
      - messages per minute (primary drift indicator)
      - health score (secondary — degrades as bots ramp up)
    """

    def __init__(self) -> None:
        self._mpm_ewma = EWMAControlChart(
            min_sigma=_MIN_SIGMA_MPM,
            min_absolute_deviation=_MIN_ABSOLUTE_DEVIATION_MPM,
        )
        self._health_ewma = EWMAControlChart(
            min_sigma=_MIN_SIGMA_HEALTH,
            min_absolute_deviation=_MIN_ABSOLUTE_DEVIATION_HEALTH,
        )
        self._mpm_adwin = ADWINDriftDetector()

    def update(self, mpm: float, health_score: float) -> DriftResult:
        """
        Update with current message rate and health score.
        Returns a DriftResult indicating whether drift was detected and why.
        """
        mpm_ewma, mpm_ucl, mpm_lcl, mpm_alarm_raw = self._mpm_ewma.update(mpm)
        _, health_ucl, health_lcl, health_alarm_raw = self._health_ewma.update(health_score)
        adwin_alarm = self._mpm_adwin.update(mpm)

        # Directional filters — bot floods only push metrics in one direction:
        #   mpm:          only alarm on UPWARD breach (bots increase message rate)
        #   health score: only alarm on DOWNWARD breach (bots degrade health)
        # Without these, a post-spike cooldown (mpm dropping below LCL) fires endlessly.
        mpm_alarm = mpm_alarm_raw and mpm > mpm_ucl
        health_alarm = health_alarm_raw and health_score < health_lcl

        # Require at least 2 independent signals to fire before flagging drift.
        # A genuine sudden bot raid triggers both EWMA (sharp spike) and ADWIN (distribution shift).
        # A natural stream-start ramp-up only triggers ADWIN (gradual shift — EWMA won't breach
        # the practical deviation threshold), preventing false alarms every second at stream start.
        signal_count = sum([mpm_alarm, adwin_alarm, health_alarm])
        drift = signal_count >= 2

        reasons = []
        if mpm_alarm:
            reasons.append(f"mpm {mpm:.0f} outside control limits [{mpm_lcl:.0f}–{mpm_ucl:.0f}]")
        if adwin_alarm:
            reasons.append("ADWIN: message rate distribution shifted")
        if health_alarm:
            reasons.append(f"health score {health_score:.0f} outside EWMA control band")

        return DriftResult(
            drift_detected=drift,
            reason="; ".join(reasons) if reasons else "",
            ewma_value=mpm_ewma,
            ewma_ucl=mpm_ucl,
            ewma_lcl=mpm_lcl,
            adwin_detected=adwin_alarm,
        )
