"""
IATScorer — Inter-Arrival Time (IAT) Coefficient of Variation signal.

Bot scripts that use time.sleep(N) produce near-deterministic message timing.
Humans produce Poisson-like or heavy-tailed distributions.

The coefficient of variation (CV = stdev / mean) of IATs is the key metric:
  - CV ≈ 0.0: perfectly regular timing → likely bot
  - CV ≈ 1.0: Poisson-like (exponential distribution) → normal human
  - CV > 1.5: highly bursty (reactive chatter) → normal human

Scoring:
  - Requires at least 5 IAT samples (6 timestamps) to score.
  - CV < 0.15 → score 15.0  (very regular: strong bot signal)
  - CV < 0.30 → score 10.0  (regular: moderate signal)
  - CV < 0.50 → score 5.0   (mildly regular: weak signal)
  - CV >= 0.50 → score 0.0  (looks human)

Also checks for very short mean IAT (< 1s) as an independent rate guard —
sub-second messaging is machine-speed regardless of regularity.
"""

from __future__ import annotations

import math
from collections import deque

_MIN_SAMPLES = 6   # need at least 5 IATs
_CV_STRONG = 0.15
_CV_MODERATE = 0.30
_CV_WEAK = 0.50
_MIN_IAT_FOR_RATE_FLAG = 1.0  # seconds; mean IAT below this is machine-speed


class IATScorer:
    """
    Stateless scorer — takes a deque of timestamps and returns a risk score.
    No state is stored here; the engine manages the per-user timestamp deques.
    """

    def score(self, timestamps: "deque[float]") -> float:
        """
        Returns IAT regularity risk score in [0, 15.0].
        timestamps: deque of Unix timestamps (float, seconds) in arrival order.
        """
        if len(timestamps) < _MIN_SAMPLES:
            return 0.0

        ts = list(timestamps)
        iats = [ts[i + 1] - ts[i] for i in range(len(ts) - 1)]

        # Filter out gaps that look like the user was simply away (> 120s)
        # — these are not representative of the user's active messaging cadence
        active_iats = [iat for iat in iats if iat <= 120.0]
        if len(active_iats) < 5:
            return 0.0

        n_a = len(active_iats)
        mean_iat = sum(active_iats) / n_a
        if mean_iat <= 0:
            return 0.0

        # Machine-speed guard: mean IAT < 1s always flags regardless of CV
        if mean_iat < _MIN_IAT_FOR_RATE_FLAG:
            return 15.0

        if n_a < 2:
            return 0.0

        variance = sum((x - mean_iat) ** 2 for x in active_iats) / (n_a - 1)
        stdev_iat = math.sqrt(variance)
        cv = stdev_iat / mean_iat

        if cv < _CV_STRONG:
            return 15.0
        if cv < _CV_MODERATE:
            return 10.0
        if cv < _CV_WEAK:
            return 5.0
        return 0.0
