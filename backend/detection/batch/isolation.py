"""
IsolationForestScorer — session-level account anomaly detection.

Computes a feature vector per account and scores it against the Isolation
Forest model fitted on the session's observed normal user distribution.

Refits every 20 new accounts once the minimum training sample (30) is met.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)

MIN_TRAINING_SAMPLES = 30
REFIT_INTERVAL = 20   # refit every N new accounts after initial fit


@dataclass
class AccountFeatureVector:
    account_age_days: float
    messages_this_session: int
    unique_words_ratio: float      # unique words / total words
    avg_message_length: float
    emoji_frequency: float         # emojis per message
    url_frequency: float           # messages with URL / total
    mention_frequency: float       # @mentions per message
    messages_per_minute_peak: float
    username_entropy_score: float  # 0–1 from username scorer


def _to_array(f: AccountFeatureVector) -> np.ndarray:
    return np.array(
        [
            min(f.account_age_days / 365, 10),
            min(f.messages_this_session / 100, 1),
            f.unique_words_ratio,
            min(f.avg_message_length / 200, 1),
            min(f.emoji_frequency, 1),
            min(f.url_frequency, 1),
            min(f.mention_frequency, 1),
            min(f.messages_per_minute_peak / 30, 1),
            f.username_entropy_score,
        ],
        dtype=np.float32,
    )


class IsolationForestScorer:
    def __init__(self) -> None:
        from sklearn.ensemble import IsolationForest

        self._model = IsolationForest(
            contamination=0.05, random_state=42, n_estimators=100
        )
        self._fitted = False
        self._training: list[np.ndarray] = []
        self._since_last_fit = 0

    def add_account(self, features: AccountFeatureVector) -> None:
        """Add account to training pool; refit when thresholds are met."""
        self._training.append(_to_array(features))
        self._since_last_fit += 1

        n = len(self._training)
        if n >= MIN_TRAINING_SAMPLES:
            if not self._fitted or self._since_last_fit >= REFIT_INTERVAL:
                self._fit()
                self._since_last_fit = 0

    def score_account(self, features: AccountFeatureVector) -> float:
        """Returns anomaly risk score 0–20. Higher = more anomalous."""
        if not self._fitted:
            return 0.0

        vec = _to_array(features).reshape(1, -1)
        score = self._model.decision_function(vec)[0]

        # decision_function: negative = anomalous
        # -0.5 → ~20 risk;  -0.1 → ~0 risk;  >= -0.1 → 0 risk
        if score >= -0.1:
            return 0.0
        return min(abs(score + 0.1) * 40, 20.0)

    def _fit(self) -> None:
        X = np.array(self._training)
        self._model.fit(X)
        self._fitted = True
        logger.debug("IsolationForest refit on %d accounts", len(self._training))
