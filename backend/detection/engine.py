"""
DetectionEngine — fast-path message processing and coordination hub.

The detection pipeline is split across three modules:
  - engine.py (this file):   __init__, event hooks, fast-path process_message,
                              _is_short_reaction static helper
  - detection/tick.py:       1-second tick, batch path, channel-level metrics
  - detection/alerting.py:   per-user alert evaluation and action dispatch

Fast path (per message, O(1)):
  - IncrementalDuplicateTracker
  - TemporalSyncDetector
  - MinHashDuplicateDetector
  - UserRateDetector
  - UsernameEntropyScorer
  - UsernameFamilyDetector
  - BurstAnomalyDetector
  - ProtectedAccountChecker (guards against actioning protected users)

Batch path (every 10 seconds, thread pool):
  - SemanticClusterer (MiniLM + DBSCAN)

1-second tick:
  - HealthScoreEngine (weighted metric combination)
  - AdaptiveBaseline (channel-calibrated risk)
  - AnomalyDetector (2-cycle confirmation state machine)
  - Alert evaluation + DB write + WebSocket push

Suppression:
  - DetectionSuppressor gates all fast-path and tick processing
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict, deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from api.websocket import ConnectionManager
    from pipeline.buffer import ChatBuffer
    from pipeline.models import ChatMessage

from detection.alerting import AlertingMixin
from detection.tick import TickMixin
from detection.batch.clustering import ClusterResult, SemanticClusterer
from detection.batch.cooccurrence import CooccurrenceDetector, CooccurrenceResult
from detection.batch.river_anomaly import AccountFeatureVector, RiverAnomalyScorer
from detection.fast.burst import BurstAnomalyDetector
from detection.fast.emote_filter import emote_ratio, sensitivity_to_threshold
from detection.fast.duplicate import IncrementalDuplicateTracker
from detection.fast.minhash import MinHashDuplicateDetector
from detection.fast.pattern_match import SpamPatternMatcher
from detection.fast.rate import UserRateDetector
from detection.fast.temporal import TemporalSyncDetector
from detection.fast.timing import IATScorer
from detection.fast.username import score_single_username
from detection.fast.username_family import UsernameFamilyDetector
from detection.known_bots import KnownBotRegistry
from detection.protection import ProtectedAccountChecker
from detection.scoring.anomaly import AnomalyDetector
from detection.scoring.drift import HealthDriftDetector
from detection.scoring.health_score import HealthScoreEngine
from detection.suppressor import DetectionSuppressor

logger = logging.getLogger(__name__)


class DetectionEngine(TickMixin, AlertingMixin):
    def __init__(self, chat_buffer: "ChatBuffer") -> None:
        self._buffer = chat_buffer

        # Fast-path detectors
        self.duplicate_tracker = IncrementalDuplicateTracker(window_seconds=30)
        self.temporal_sync = TemporalSyncDetector()
        self.minhash = MinHashDuplicateDetector()
        self.rate_detector = UserRateDetector()
        self.burst_detector = BurstAnomalyDetector()
        self.username_family = UsernameFamilyDetector()
        self.pattern_matcher = SpamPatternMatcher()
        self.iat_scorer = IATScorer()
        self.known_bot_registry: KnownBotRegistry | None = None  # injected at startup

        # Batch detectors
        self.semantic_clusterer = SemanticClusterer()
        self.isolation_forest = RiverAnomalyScorer()
        self.cooccurrence_detector = CooccurrenceDetector()

        # Scoring pipeline
        self.health_engine = HealthScoreEngine()
        self.anomaly_detector = AnomalyDetector()

        # Drift detection
        self.health_drift = HealthDriftDetector()

        # Support components
        self.suppressor = DetectionSuppressor()
        self.protection = ProtectedAccountChecker()

        # Per-user inter-arrival timestamps for IAT CV scoring
        self._user_timestamps: dict[str, deque] = defaultdict(lambda: deque(maxlen=50))

        # Per-user tracking: user_id → {signal_name: raw_score}
        self._user_signals: dict[str, dict[str, float]] = defaultdict(dict)
        # Per-user message counts for isolation forest features.
        # Lists are capped at 200 entries (deque(maxlen=200)) so a single extreme
        # spammer cannot grow one dict entry without bound.
        self._user_msg_count: dict[str, int] = defaultdict(int)
        self._user_word_counts: dict[str, deque] = defaultdict(lambda: deque(maxlen=200))
        self._user_emoji_counts: dict[str, deque] = defaultdict(lambda: deque(maxlen=200))
        self._user_url_counts: dict[str, deque] = defaultdict(lambda: deque(maxlen=200))
        self._user_mention_counts: dict[str, deque] = defaultdict(lambda: deque(maxlen=200))

        # Cluster state from last batch run
        self._last_cluster_result: ClusterResult = ClusterResult(0, 0.0)
        self._last_cooccurrence_result: CooccurrenceResult = CooccurrenceResult()
        self._last_cluster_time: float = 0.0
        self._minhash_score: float = 0.0   # accumulates, decays per tick

        # Background clustering task — guarded to prevent queue buildup when
        # embedding takes longer than the 10s clustering interval.
        self._cluster_task: "asyncio.Task | None" = None

        # Performance: rate-limit River isolation forest training (every 5 ticks)
        self._isolation_forest_tick: int = 0

        # Tracks users with non-zero temporal_sync or minhash_cluster so the per-tick
        # decay loop only iterates active users instead of ALL users ever seen.
        # Without this, the loop grows unboundedly with session duration.
        self._decay_signal_users: set[str] = set()

        # TTL eviction counter: prune stale per-user state every 60 ticks (60s).
        # Users not seen for > 300s are removed from all per-user dicts to prevent
        # unbounded memory growth during long streams with many unique chatters.
        self._eviction_tick: int = 0

        # Alert dedup
        self._last_alerted: dict[str, float] = {}

        # Background alert evaluation task — fire-and-forget so DB writes for flagged
        # users don't block the 1-second tick loop.  Guarded to prevent overlap:
        # a new task is only created when the previous one has finished.
        self._alert_task: "asyncio.Task | None" = None

        # Background River update task — fire-and-forget so River HalfSpaceTrees
        # learn_one() calls don't block the tick critical path.  Guarded to prevent
        # overlap with the every-10-tick rate limit.
        self._river_task: "asyncio.Task | None" = None

        # WebSocket manager (injected after construction)
        self._ws_manager: "ConnectionManager | None" = None

        # Moderation engine (injected after construction)
        self._moderation_engine = None

    def set_ws_manager(self, manager: "ConnectionManager") -> None:
        self._ws_manager = manager

    def set_moderation_engine(self, engine) -> None:
        self._moderation_engine = engine

    def set_known_bot_registry(self, registry: "KnownBotRegistry") -> None:
        self.known_bot_registry = registry

    # ------------------------------------------------------------------
    # External event hooks (called from Twitch manager)
    # ------------------------------------------------------------------

    def on_event(self, event_type: str, **kwargs) -> None:
        """Route an EventSub event to the suppressor."""
        gift_count = kwargs.get("gift_count", 0)
        self.suppressor.on_event(event_type, gift_count=gift_count)

    def on_reconnect(self) -> None:
        self.suppressor.on_reconnect()
        self.health_engine.baseline.reset()

    # ------------------------------------------------------------------
    # Fast path — O(1) per message
    # ------------------------------------------------------------------

    @staticmethod
    def _is_emote_heavy(msg: "ChatMessage", sensitivity: int) -> bool:
        """
        Return True when the message is predominantly Twitch emotes/emoji and
        the emote filter sensitivity is non-zero.

        Emote-heavy messages are treated identically to short reactions for the
        purpose of similarity detectors (duplicate_tracker, temporal_sync,
        MinHash).  They still accumulate rate, username, account-age, and
        pattern signals — a bot account that spams emotes can still be flagged
        by those.
        """
        if sensitivity <= 0 or msg.url_count > 0 or msg.mention_count > 0:
            return False
        threshold = sensitivity_to_threshold(sensitivity)
        ratio = emote_ratio(msg.raw_text, msg.emoji_count, msg.word_count)
        return ratio >= threshold

    @staticmethod
    def _is_short_reaction(msg: "ChatMessage") -> bool:
        """
        Return True for messages that look like viewer reactions rather than
        coordinated bot output — short text with no URLs or @mentions.

        These are exempt from temporal-sync, MinHash, and duplicate-ratio
        attribution because legitimate emote waves (e.g. 100 viewers
        simultaneously typing "PogChamp" or "LUL LUL") generate real
        coordination signals that are indistinguishable from bot spam at the
        detector level but are harmless viewer behaviour.

        Rate, username-entropy, and account-age signals still accumulate
        normally — a bot that only spams short emotes can still be caught by
        the username or new-account signals.

        Criteria (all must hold):
          - No URL and no @mention (bots almost always include one or the other)
          - Short: ≤ 3 words OR ≤ 25 characters after normalisation
            (covers single/double emotes, "lol", "gg", "nice", "PogChamp", etc.)
        """
        if msg.url_count > 0 or msg.mention_count > 0:
            return False
        return msg.word_count <= 3 or msg.char_count <= 25

    async def process_message(self, msg: "ChatMessage") -> None:
        if self.suppressor.is_suppressed:
            return

        ts = msg.received_at
        uid = msg.user_id

        # Determine whether this looks like a viewer reaction (short, no links/mentions)
        # OR is predominantly Twitch emotes/emoji.  Either condition skips similarity
        # detectors to prevent emote-wave false positives.
        from core.config import settings as _settings
        skip_similarity = (
            self._is_short_reaction(msg)
            or self._is_emote_heavy(msg, _settings.emote_filter_sensitivity)
        )

        # 1. Duplicate tracker — check BEFORE adding so we can record per-user
        #    participation.  A user who repeatedly sends the same text is likely a bot;
        #    a user whose unique message happens to exist in the channel buffer is not.
        #    Skipped for short reactions to avoid emote-wave false positives.
        if not skip_similarity:
            is_duplicate = self.duplicate_tracker._hash_counts.get(msg.content_hash, 0) > 0
            self.duplicate_tracker.add(msg.content_hash, ts)
            if is_duplicate:
                self._user_signals[uid]["_dup_count"] = (
                    self._user_signals[uid].get("_dup_count", 0) + 1
                )
                # Use the count AFTER this message (msg_count incremented below)
                user_msg_n = max(self._user_msg_count.get(uid, 0) + 1, 1)
                dup_ratio = self._user_signals[uid]["_dup_count"] / user_msg_n
                self._user_signals[uid]["duplicate_ratio"] = (
                    min(dup_ratio * 100, 35.0) if dup_ratio > 0.15 else 0.0
                )
            else:
                self._user_signals[uid].setdefault("duplicate_ratio", 0.0)
        else:
            self._user_signals[uid].setdefault("duplicate_ratio", 0.0)

        # 2. Temporal sync — add() returns a score > 0 only when THIS content hash
        #    is part of a coordinated burst.  Store per-user so the channel-level peak
        #    does not bleed onto unrelated users.
        #    Skipped for short reactions to avoid emote-wave false positives.
        if not skip_similarity:
            sync_score = self.temporal_sync.add(msg.content_hash, uid, ts)
            new_sync = max(self._user_signals[uid].get("temporal_sync", 0.0), sync_score)
            self._user_signals[uid]["temporal_sync"] = new_sync
            if new_sync > 0.0:
                self._decay_signal_users.add(uid)
        else:
            self._user_signals[uid].setdefault("temporal_sync", 0.0)

        # 3. MinHash near-duplicate — attribute cluster membership only to users
        #    whose message is actually in the returned cluster, not to all users.
        #    Skipped for short reactions to avoid emote-wave false positives.
        if not skip_similarity:
            cluster = self.minhash.add(
                message_id=f"{uid}:{ts:.3f}",
                content=msg.normalized_text,
                user_id=uid,
                timestamp=ts,
            )
            if cluster:
                self._minhash_score = min(self._minhash_score + 5.0, 25.0)
                for entry in cluster:
                    member_uid = entry.get("user_id", "")
                    if member_uid:
                        prev = self._user_signals[member_uid].get("minhash_cluster", 0.0)
                        self._user_signals[member_uid]["minhash_cluster"] = min(prev + 5.0, 25.0)
                        self._decay_signal_users.add(member_uid)

        # 4. Per-user rate
        rate_score = self.rate_detector.add(uid, ts)
        self._user_signals[uid]["rate_anomaly"] = rate_score

        # 5. Burst (channel-level)
        self.burst_detector.add_message(ts)

        # 6. Username signals
        entropy_score = score_single_username(msg.username)
        self._user_signals[uid]["username_entropy"] = entropy_score

        family_score = self.username_family.add(msg.username, ts)
        self._user_signals[uid]["username_family"] = family_score

        # 7. Account age signal — graded so brand-new accounts score highest
        age = msg.account_age_days
        if age is not None:
            if age < 1:
                new_acct_score = 20.0   # created today
            elif age < 7:
                new_acct_score = 15.0   # < 1 week
            elif age < 30:
                new_acct_score = 8.0    # < 1 month
            elif age < 90:
                new_acct_score = 3.0    # < 3 months
            else:
                new_acct_score = 0.0
            self._user_signals[uid]["new_account"] = new_acct_score
        else:
            self._user_signals[uid].setdefault("new_account", 0.0)

        # 8. Known-bot registry pre-filter
        if self.known_bot_registry is not None:
            kb_score = self.known_bot_registry.signal_score(msg.username)
            self._user_signals[uid]["known_bot"] = kb_score
        else:
            self._user_signals[uid].setdefault("known_bot", 0.0)

        # 9. Spam pattern match (content-based)
        pattern_score = self.pattern_matcher.score(msg.normalized_text)
        self._user_signals[uid]["pattern_match"] = pattern_score

        # 10. Inter-arrival time (IAT) coefficient of variation
        #     Only meaningful once we have 5+ timestamps
        self._user_timestamps[uid].append(ts)
        iat_score = self.iat_scorer.score(self._user_timestamps[uid])
        self._user_signals[uid]["timing_regularity"] = iat_score

        # 11. Regex filter (runs against raw text for full expressiveness)
        from detection.fast.regex_filter import regex_filter_engine as _rfe
        if _rfe is not None:
            hit = _rfe.match(msg.raw_text)
            if hit is not None:
                asyncio.create_task(_rfe.increment_match_count(hit.filter_id))
                if self._moderation_engine is not None:
                    from twitch.token_store import TOKEN_BROADCASTER_ID, token_store as _ts
                    bid = _ts.retrieve(TOKEN_BROADCASTER_ID) or ""
                    from moderation.actions import ModerationAction as _MA
                    act = _MA(
                        action_type=hit.action_type if hit.action_type in ("delete", "timeout", "ban") else "delete",
                        broadcaster_id=bid,
                        user_id=uid,
                        username=msg.username,
                        channel=msg.channel,
                        duration_seconds=hit.duration_seconds,
                        message_id=getattr(msg, "message_id", None),
                        reason=f"Regex filter: {hit.pattern[:60]}",
                        triggered_by="auto:regex_filter",
                        confidence=100.0,
                    )
                    self._moderation_engine._enqueue(act)

        # 13. Accumulate isolation forest features
        self._user_msg_count[uid] += 1
        self._user_word_counts[uid].append(msg.word_count)
        self._user_emoji_counts[uid].append(msg.emoji_count)
        self._user_url_counts[uid].append(msg.url_count)
        self._user_mention_counts[uid].append(msg.mention_count)
