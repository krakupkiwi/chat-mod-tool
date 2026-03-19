"""
RiverAnomalyScorer — online account anomaly detection using River's HalfSpaceTrees.

Replaces the batch-fitted scikit-learn IsolationForest with an online learner
that updates incrementally on every account seen — no refitting required, and
it adapts to concept drift as bot tactics evolve over time.

River's HalfSpaceTrees (Tan et al., 2011):
  - O(log n) per sample for scoring and learning
  - Window-based: only the most recent `window_size` samples influence the model
  - No cold-start requirement (can score from sample 1, though early scores
    are unreliable — we gate on MIN_SAMPLES before emitting non-zero scores)

Feature vector is identical to IsolationForestScorer.AccountFeatureVector so
the two classes can be swapped in engine.py with no other changes.

Score range: 0–20 (matching IsolationForestScorer output convention).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

MIN_SAMPLES_BEFORE_SCORING = 30  # match IsolationForest cold-start threshold


@dataclass
class AccountFeatureVector:
    """Feature vector for a single account. Identical schema to isolation.py."""
    account_age_days: float
    messages_this_session: int
    unique_words_ratio: float
    avg_message_length: float
    emoji_frequency: float
    url_frequency: float
    mention_frequency: float
    messages_per_minute_peak: float
    username_entropy_score: float


def _to_dict(f: AccountFeatureVector) -> dict[str, float]:
    """Convert to a flat float dict for River (no numpy array needed)."""
    return {
        "account_age": min(f.account_age_days / 365, 10.0),
        "msg_count":   min(f.messages_this_session / 100, 1.0),
        "uniq_words":  f.unique_words_ratio,
        "avg_len":     min(f.avg_message_length / 200, 1.0),
        "emoji_freq":  min(f.emoji_frequency, 1.0),
        "url_freq":    min(f.url_frequency, 1.0),
        "mention_freq": min(f.mention_frequency, 1.0),
        "rate_peak":   min(f.messages_per_minute_peak / 30, 1.0),
        "usr_entropy": f.username_entropy_score,
    }


class RiverAnomalyScorer:
    """
    Online anomaly scorer using River's HalfSpaceTrees + StandardScaler.

    Identical public interface to IsolationForestScorer:
      - add_account(features)  → updates the model
      - score_account(features) → returns float 0–20
    """

    def __init__(self) -> None:
        try:
            from river import anomaly, preprocessing
            self._scaler = preprocessing.StandardScaler()
            self._model = anomaly.HalfSpaceTrees(
                n_trees=25,
                height=8,
                window_size=250,
                seed=42,
            )
            self._river_available = True
            logger.info("RiverAnomalyScorer: HalfSpaceTrees initialized")
        except ImportError:
            # Graceful fallback to IsolationForest if river is not installed
            logger.warning(
                "river not installed — falling back to IsolationForestScorer. "
                "Install with: pip install river"
            )
            from detection.batch.isolation import IsolationForestScorer
            self._fallback = IsolationForestScorer()
            self._river_available = False

        self._sample_count = 0

    def add_account(self, features: AccountFeatureVector) -> None:
        """Update the model with a new account observation."""
        self._sample_count += 1
        if not self._river_available:
            # Map to isolation.AccountFeatureVector if types differ
            from detection.batch.isolation import (
                AccountFeatureVector as IsoVec,
                IsolationForestScorer,
            )
            iso_vec = IsoVec(
                account_age_days=features.account_age_days,
                messages_this_session=features.messages_this_session,
                unique_words_ratio=features.unique_words_ratio,
                avg_message_length=features.avg_message_length,
                emoji_frequency=features.emoji_frequency,
                url_frequency=features.url_frequency,
                mention_frequency=features.mention_frequency,
                messages_per_minute_peak=features.messages_per_minute_peak,
                username_entropy_score=features.username_entropy_score,
            )
            self._fallback.add_account(iso_vec)
            return

        x = _to_dict(features)
        # Call learn_one and transform_one separately — River 0.21+ no longer
        # returns self from learn_one, so chaining causes AttributeError on None.
        self._scaler.learn_one(x)
        x_scaled = self._scaler.transform_one(x)
        if x_scaled is not None:
            self._model.learn_one(x_scaled)

    def score_account(self, features: AccountFeatureVector) -> float:
        """Returns anomaly risk score 0–20. Higher = more anomalous."""
        if not self._river_available:
            from detection.batch.isolation import AccountFeatureVector as IsoVec
            iso_vec = IsoVec(
                account_age_days=features.account_age_days,
                messages_this_session=features.messages_this_session,
                unique_words_ratio=features.unique_words_ratio,
                avg_message_length=features.avg_message_length,
                emoji_frequency=features.emoji_frequency,
                url_frequency=features.url_frequency,
                mention_frequency=features.mention_frequency,
                messages_per_minute_peak=features.messages_per_minute_peak,
                username_entropy_score=features.username_entropy_score,
            )
            return self._fallback.score_account(iso_vec)

        if self._sample_count < MIN_SAMPLES_BEFORE_SCORING:
            return 0.0

        x = _to_dict(features)
        x_scaled = self._scaler.transform_one(x)
        if x_scaled is None:
            return 0.0

        # HalfSpaceTrees.score_one() returns higher values for anomalies.
        # Scale from [0, ~1] to [0, 20].
        raw = self._model.score_one(x_scaled)
        # Normal scores cluster around 0.1–0.3; anomalies > 0.5
        if raw < 0.4:
            return 0.0
        return min((raw - 0.4) * 50, 20.0)
