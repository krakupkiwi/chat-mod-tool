"""
TickMixin — 1-second tick loop and batch path for DetectionEngine.

Extracted from engine.py to keep individual modules under ~300 lines.
Mix into DetectionEngine via multiple inheritance.

Contains:
  - tick(): central 1-second coordination tick
  - _run_clustering(): async semantic clustering fire-and-forget task
  - _update_isolation_forest(): per-user feature collection for anomaly scoring
  - Channel-level metric helpers: _compute_velocity, _compute_new_account_score,
    _compute_entropy_score
  - _build_health_payload(): WebSocket payload builder

All methods access `self` attributes defined in DetectionEngine.__init__.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from core.telemetry import telemetry

if TYPE_CHECKING:
    from pipeline.models import ChatMessage
    from detection.scoring.health_score import HealthSnapshot

from core.config import settings
from detection.scoring.drift import DriftResult
from detection.batch.cooccurrence import CooccurrenceResult
from detection.batch.river_anomaly import AccountFeatureVector
from detection.fast.username import score_single_username
from detection.scoring.anomaly import classify_level
from detection.scoring.health_score import METRIC_MAX, METRIC_WEIGHTS, HealthSnapshot
from twitch.token_store import TOKEN_BROADCASTER_ID, token_store

logger = logging.getLogger(__name__)

# Semantic clustering interval (seconds)
_CLUSTER_INTERVAL = 10.0


class TickMixin:
    """
    Mixin providing the 1-second tick loop and batch path for DetectionEngine.

    Requires (from DetectionEngine.__init__):
        self._buffer, self._last_cluster_time, self._last_cluster_result,
        self._minhash_score, self._user_signals, self.temporal_sync,
        self.duplicate_tracker, self.burst_detector, self.health_engine,
        self.anomaly_detector, self.suppressor, self._ws_manager,
        self._moderation_engine, self.semantic_clusterer, self.isolation_forest,
        self.rate_detector, self._user_msg_count, self._user_word_counts,
        self._user_emoji_counts, self._user_url_counts, self._user_mention_counts
    """

    async def tick(self) -> None:
        """
        Central 1-second coordination tick. Called by detection_tick_loop in tasks.py.

        Each call does the following in order:

        1. Trigger semantic clustering (SemanticClusterer + DBSCAN) every 10s as a
           fire-and-forget asyncio task.  The task runs in a ThreadPoolExecutor so it
           never blocks this coroutine.

        2. Feed the IsolationForestScorer with per-user feature vectors built from the
           last 60s of messages.

        3. Compute raw metric scores from the rolling windows and individual detectors:
             temporal_sync  — peak score from TemporalSyncDetector this tick (then reset)
             duplicate_ratio — channel-level duplicate risk from IncrementalDuplicateTracker
             burst_anomaly   — message-velocity spike from BurstAnomalyDetector
             minhash_cluster — accumulated MinHash cluster signal (decays −2/tick)
             semantic_cluster — risk score from last SemanticClusterer run
             velocity        — channel-level msg/min above baseline
             new_account     — ratio of accounts < 7 days old in last 30s
             entropy         — average username entropy across active users

        4. Decay per-user temporal_sync and minhash_cluster scores by −2.0 each tick so
           stale coordination signals fade after the burst ends.

        5. Compute the HealthSnapshot via HealthScoreEngine and run the 2-cycle
           AnomalyDetector state machine to confirm or clear elevated states.

        6. Broadcast health_update event to all connected WebSocket clients.

        7. Call _evaluate_user_alerts() to emit threat alerts for any users whose
           aggregated per-user signal score exceeds _ALERT_THRESHOLD (55.0).

        Budget: must complete in < 50ms regardless of chat volume.  The only
        potentially slow operation (semantic clustering) is offloaded to a thread pool.
        """
        now = time.time()
        _tick_t0 = time.perf_counter()
        # Per-operation timing breakdown (milliseconds) — included in health payload
        # so PerfPanel.tsx and future tooling can identify regressions quickly.
        _t = time.perf_counter

        # Single-pass extraction for 30s and 60s windows: get messages AND stats
        # in one deque scan each, avoiding the redundant stats() scan that would
        # otherwise re-iterate the same entries immediately after recent_messages().
        _t_buf0 = _t()
        msgs_30s, stats_30s = self._buffer.messages_and_stats(30)
        msgs_60s, stats_60s_stats = self._buffer.messages_and_stats(60)
        _tick_buf_ms = (_t() - _t_buf0) * 1000

        # Trigger semantic clustering every 10s — guarded to prevent executor
        # queue buildup when embedding takes longer than the 10s interval.
        # If the previous clustering task is still running (timed out but thread
        # still active in the executor), skip this cycle rather than queuing another.
        _t_cluster0 = _t()
        if (
            now - self._last_cluster_time >= _CLUSTER_INTERVAL
            and (self._cluster_task is None or self._cluster_task.done())
        ):
            if msgs_30s:
                self._cluster_task = asyncio.create_task(
                    self._run_clustering(msgs_30s)
                )
            self._last_cluster_time = now
        _tick_cluster_ms = (_t() - _t_cluster0) * 1000

        # Feed isolation forest as a background task — River HalfSpaceTrees.learn_one()
        # is pure Python CPU work (~2-4ms/user) that would block the tick critical path
        # if awaited inline.  Fire-and-forget keeps tick() latency bounded.
        # Rate-limited to every 10 ticks (10s); guarded against overlap so the next
        # River task only starts when the previous one has completed.
        _t_river0 = _t()
        self._isolation_forest_tick += 1
        if (
            self._isolation_forest_tick % 10 == 0
            and (self._river_task is None or self._river_task.done())
        ):
            self._river_task = asyncio.create_task(
                self._update_isolation_forest(msgs_60s),
                name="river_update",
            )
        _tick_river_ms = (_t() - _t_river0) * 1000

        # 5s window still needs its own call (not pre-fetched as a list)
        stats_5s = self._buffer.stats(5)

        sync_raw    = self.temporal_sync.reset_tick()
        dup_raw     = self.duplicate_tracker.risk_score
        burst_raw   = self.burst_detector._compute_score()
        minhash_raw = self._minhash_score
        self._minhash_score = max(0.0, self._minhash_score - 2.0)
        cluster_raw = self._last_cluster_result.risk_score

        # Decay per-user temporal_sync and minhash_cluster scores each tick so stale
        # coordination signals fade after the burst ends (mirrors channel-level decay).
        # Only iterate _decay_signal_users (users with currently non-zero signals) to avoid
        # an O(all_users_ever) loop that grows unboundedly with session duration.
        _done: set[str] = set()
        for uid in self._decay_signal_users:
            uid_sigs = self._user_signals.get(uid)
            if uid_sigs is None:
                _done.add(uid)
                continue
            ts_new = max(0.0, uid_sigs.get("temporal_sync", 0.0) - 2.0)
            mh_new = max(0.0, uid_sigs.get("minhash_cluster", 0.0) - 2.0)
            uid_sigs["temporal_sync"] = ts_new
            uid_sigs["minhash_cluster"] = mh_new
            if ts_new == 0.0 and mh_new == 0.0:
                _done.add(uid)
        self._decay_signal_users -= _done

        # TTL eviction: every 20 ticks (≈20s) remove per-user state for users
        # not seen in the last 300 seconds.  Prevents unbounded dict growth during
        # long streams (e.g. 100K unique chatters in a 6-hour stream would otherwise
        # accumulate ~10–15 dict entries × 100K users ≈ ~1M live objects).
        # Running every 20 ticks (was 60) keeps dict sizes tighter between cycles
        # at trivial cost since eviction is O(n_stale).
        self._eviction_tick = getattr(self, "_eviction_tick", 0) + 1
        if self._eviction_tick % 20 == 0:
            eviction_cutoff = now - 300.0
            stale = [
                uid for uid, ts_deque in self._user_timestamps.items()
                if ts_deque and ts_deque[-1] < eviction_cutoff
            ]
            for uid in stale:
                self._user_signals.pop(uid, None)
                self._user_timestamps.pop(uid, None)
                self._user_msg_count.pop(uid, None)
                self._user_word_counts.pop(uid, None)
                self._user_emoji_counts.pop(uid, None)
                self._user_url_counts.pop(uid, None)
                self._user_mention_counts.pop(uid, None)
                self._last_alerted.pop(uid, None)
                self._decay_signal_users.discard(uid)
                # M1: evict rate_detector's internal per-user window — this dict is
                # not owned by DetectionEngine so it was not covered by the engine
                # eviction above.  Without this it grows unboundedly with unique
                # chatters (confirmed source of long-stream RSS growth).
                self.rate_detector._user_windows.pop(uid, None)
            # M1: remove empty buckets from username_family so their dict keys don't
            # accumulate indefinitely (deques self-prune by time but the key is never
            # deleted once the deque drains to empty).
            empty_buckets = [
                k for k, bkt in self.username_family._buckets.items() if not bkt
            ]
            for k in empty_buckets:
                del self.username_family._buckets[k]
            if stale or empty_buckets:
                logger.debug(
                    "_user_signals TTL eviction: removed %d inactive users "
                    "(+ %d empty username_family buckets)",
                    len(stale), len(empty_buckets),
                )

        velocity_raw                    = self._compute_velocity(stats_5s.messages_per_second)
        new_account_raw, entropy_raw    = self._compute_new_account_and_entropy(msgs_30s)

        raw_scores: dict[str, float] = {
            "temporal_sync":    sync_raw,
            "duplicate_ratio":  dup_raw,
            "semantic_cluster": cluster_raw,
            "velocity":         velocity_raw,
            "burst_anomaly":    burst_raw,
            "new_account":      new_account_raw,
            "entropy":          entropy_raw,
        }

        chat_stats = {
            "mpm":             stats_60s_stats.messages_per_second * 60,
            "active_users":    stats_60s_stats.unique_users,
            "duplicate_ratio": stats_30s.duplicate_ratio,
            "messages_in_5s":  stats_5s.message_count,
            "messages_in_30s": stats_30s.message_count,
        }

        level = classify_level(100.0 - sum(
            (raw_scores.get(n, 0.0) / METRIC_MAX.get(n, 1.0)) * w * 100
            for n, w in METRIC_WEIGHTS.items()
        ))

        _t_health0 = _t()
        snapshot: HealthSnapshot = self.health_engine.compute(
            raw_scores=raw_scores,
            chat_stats=chat_stats,
            clusters=self._last_cluster_result.clusters,
            level_duration=self.anomaly_detector.level_duration,
            level=level,
        )
        snapshot = self.anomaly_detector.evaluate(snapshot)

        # Drift detection (slow-ramp campaigns)
        drift_result: DriftResult = self.health_drift.update(
            mpm=snapshot.messages_per_minute,
            health_score=snapshot.health_score,
        )
        # D1: transition-based logging — only WARNING on False→True to avoid
        # logging the same event every second for minutes (alarm fatigue).
        # Log clearance at INFO so operators know the event ended.
        _prev_drift = getattr(self, "_drift_active", False)
        self._drift_active = drift_result.drift_detected
        if drift_result.drift_detected:
            if not _prev_drift:
                logger.warning("Drift detected: %s", drift_result.reason)
            else:
                logger.debug("Drift ongoing: %s", drift_result.reason)
        elif _prev_drift:
            logger.info("Drift cleared")
        _tick_health_ms = (_t() - _t_health0) * 1000

        # Broadcast health_update — fire-and-forget so GIL contention from the
        # SemanticClusterer thread pool cannot stall the tick critical path.
        # The broadcast has a 50ms per-connection timeout (websocket.py) so even
        # with a stalled connection the task completes quickly in the background.
        _t_broadcast0 = _t()
        if self._ws_manager:
            _tick_breakdown = {
                "tick_buf_ms":     round(_tick_buf_ms, 2),
                "tick_cluster_ms": round(_tick_cluster_ms, 2),
                "tick_river_ms":   round(_tick_river_ms, 2),
                "tick_health_ms":  round(_tick_health_ms, 2),
            }
            asyncio.create_task(
                self._ws_manager.broadcast(
                    self._build_health_payload(snapshot, drift_result, _tick_breakdown)
                ),
                name="health_broadcast",
            )
        _tick_broadcast_ms = (_t() - _t_broadcast0) * 1000

        # Evaluate per-user threats every 2 ticks (2s cadence) as a background task
        # so that DB writes for flagged users (typically 2-5ms each) do not block
        # the tick's critical path.  A new task is only created when the previous
        # alert evaluation has finished — this prevents overlap when alert evaluation
        # (including aiosqlite writes) takes longer than one 2-second cadence cycle.
        _t_alert0 = _t()
        self._alert_tick = getattr(self, "_alert_tick", 0) + 1
        if (
            not self.suppressor.is_suppressed
            and self._alert_tick % 2 == 0
            and (self._alert_task is None or self._alert_task.done())
        ):
            self._alert_task = asyncio.create_task(
                self._evaluate_user_alerts(snapshot, msgs_30s),
                name="evaluate_user_alerts",
            )
        _tick_alert_ms = (_t() - _t_alert0) * 1000

        # Record tick duration for telemetry
        _tick_ms = (time.perf_counter() - _tick_t0) * 1000
        telemetry.record_tick(_tick_ms)
        if _tick_ms > 40:
            logger.warning(
                "Tick loop slow: %.1fms (buf=%.1f cluster=%.1f river=%.1f "
                "health=%.1f broadcast=%.1f alert=%.1f)",
                _tick_ms, _tick_buf_ms, _tick_cluster_ms, _tick_river_ms,
                _tick_health_ms, _tick_broadcast_ms, _tick_alert_ms,
            )

    # ------------------------------------------------------------------
    # Batch path
    # ------------------------------------------------------------------

    async def _run_clustering(self, messages: list["ChatMessage"]) -> None:
        try:
            result = await asyncio.wait_for(
                self.semantic_clusterer.analyze(messages),
                timeout=8.0,
            )
        except asyncio.TimeoutError:
            logger.warning("SemanticClusterer timed out after 8s — skipping this batch")
            return
        except Exception as e:
            logger.error("SemanticClusterer error: %s", e)
            return
        self._last_cluster_result = result

        # Run cross-cluster bot network detection (igraph Infomap) in the same
        # thread pool as the semantic clusterer — Infomap is CPU-bound (O(n²) edge
        # construction + community detection) and would block the event loop for
        # seconds at high cluster counts if run synchronously here.
        loop = asyncio.get_event_loop()
        cooc = await loop.run_in_executor(
            self.semantic_clusterer._executor,
            self.cooccurrence_detector.detect,
            result.clusters,
        )
        self._last_cooccurrence_result = cooc
        if cooc.network_count > 0:
            logger.info(
                "Cross-cluster networks detected: %d network(s), risk=%.1f",
                cooc.network_count, cooc.risk_score,
            )

        # Auto cluster-timeout: if a cluster is high-risk and moderation is ready
        if (
            self._moderation_engine is not None
            and not self.suppressor.is_suppressed
            and settings.auto_timeout_enabled
            and not settings.dry_run
        ):
            broadcaster_id = token_store.retrieve(TOKEN_BROADCASTER_ID) or ""
            channel = settings.default_channel
            for cluster in result.clusters:
                # Trigger on clusters with 5+ distinct users
                if cluster.get("size", 0) >= 5:
                    user_ids = cluster["user_ids"]
                    # Build username map from recent buffer messages
                    recent = self._buffer.recent_messages(30)
                    usernames = {m.user_id: m.username for m in recent}
                    await self._moderation_engine.timeout_cluster(
                        cluster_user_ids=user_ids,
                        usernames=usernames,
                        channel=channel,
                        broadcaster_id=broadcaster_id,
                        duration_seconds=60,
                        reason=f"Semantic bot cluster [{cluster['cluster_id']}]",
                    )

    # Maximum users to feed into River per tick to bound worst-case latency.
    # At ~2ms per user (feature vector build + River learn_one), 15 users ≈ 30ms.
    # The remaining users are processed on the next River tick (10s later).
    _RIVER_MAX_USERS_PER_TICK = 15

    async def _update_isolation_forest(self, msgs: list) -> None:
        """
        Feed one feature vector per unique user seen in the 60s window.

        Made async so we can yield the event loop every 20 users — River's
        HalfSpaceTrees.learn_one() is pure Python and can accumulate 10–30ms
        of continuous CPU time at high user counts without these yields.

        User count is capped at _RIVER_MAX_USERS_PER_TICK (50) to keep each
        River tick under 20ms even at maximum chat volume.
        """
        seen: set[str] = set()
        batch = 0
        for msg in msgs:
            uid = msg.user_id
            if uid in seen:
                continue
            seen.add(uid)
            count = self._user_msg_count.get(uid, 1)
            emojis = self._user_emoji_counts.get(uid, [])
            urls   = self._user_url_counts.get(uid, [])
            mentions = self._user_mention_counts.get(uid, [])

            features = AccountFeatureVector(
                account_age_days=float(msg.account_age_days or 0),
                messages_this_session=count,
                unique_words_ratio=1.0,  # simplified; full impl needs word tracking
                avg_message_length=float(msg.char_count),
                emoji_frequency=sum(emojis) / max(count, 1),
                url_frequency=sum(urls) / max(count, 1),
                mention_frequency=sum(mentions) / max(count, 1),
                messages_per_minute_peak=self.rate_detector.score_for(uid),
                username_entropy_score=score_single_username(msg.username) / 15.0,
            )
            self.isolation_forest.add_account(features)
            batch += 1
            if batch >= self._RIVER_MAX_USERS_PER_TICK:
                break
            if batch % 20 == 0:
                # Yield every 20 users so the message consumer and other
                # coroutines get CPU time between River learn_one() calls.
                await asyncio.sleep(0)

    # ------------------------------------------------------------------
    # Channel-level metric helpers
    # ------------------------------------------------------------------

    def _compute_velocity(self, mps: float) -> float:
        mpm = mps * 60
        if mpm > 500:
            return min((mpm - 500) / 100, 1.0) * 30
        if mpm > 200:
            return min((mpm - 200) / 300, 1.0) * 15
        return 0.0

    def _compute_new_account_and_entropy(self, msgs: list) -> tuple[float, float]:
        """
        Single-pass replacement for _compute_new_account_score + _compute_entropy_score.
        Returns (new_account_raw, entropy_raw) from one O(n) loop over msgs_30s.
        """
        seen: set[str] = set()
        known_ages: list[float] = []
        entropy_total = 0.0
        user_count = 0
        for m in msgs:
            if m.account_age_days is not None:
                known_ages.append(m.account_age_days)
            if m.user_id not in seen:
                seen.add(m.user_id)
                entropy_total += self._user_signals[m.user_id].get("username_entropy", 0.0)
                user_count += 1

        if len(known_ages) >= 3:
            new_ratio = sum(1 for age in known_ages if age < 7) / len(known_ages)
            new_account_raw = min((new_ratio - 0.10) * 100, 20.0) if new_ratio >= 0.10 else 0.0
        else:
            new_account_raw = 0.0

        entropy_raw = min(entropy_total / user_count, 15.0) if user_count else 0.0
        return new_account_raw, entropy_raw

    # ------------------------------------------------------------------
    # Payload builder
    # ------------------------------------------------------------------

    def _build_health_payload(
        self,
        s: "HealthSnapshot",
        drift: "DriftResult | None" = None,
        tick_breakdown: dict | None = None,
    ) -> dict:
        perf = telemetry.snapshot()
        # Merge per-operation breakdown into the perf dict so PerfPanel.tsx
        # can display individual sub-operation latencies.
        if tick_breakdown:
            perf.update(tick_breakdown)
        return {
            "type": "health_update",
            "timestamp": s.timestamp,
            "health": {
                "score": s.health_score,
                "risk_score": s.risk_score,
                "level": s.level,
                "level_duration_seconds": s.level_duration_seconds,
                "trend": s.trend,
            },
            "chat_activity": {
                "messages_per_minute": s.messages_per_minute,
                "active_users": s.active_users,
                "messages_in_5s": s.messages_in_5s,
                "messages_in_30s": s.messages_in_30s,
                "duplicate_ratio": s.duplicate_ratio,
            },
            "signals": {
                **s.metric_scores,
                "active": s.active_signals,
            },
            "clusters": {
                "active_count": self._last_cluster_result.cluster_count,
                "clustered_ratio": round(self._last_cluster_result.clustered_ratio, 3),
                "clusters": s.clusters,
                "bot_networks": [
                    {
                        "network_id": n.network_id,
                        "size": n.size,
                        "user_ids": n.user_ids[:20],
                        "spanning_clusters": n.spanning_clusters,
                        "risk_score": round(n.risk_score, 1),
                    }
                    for n in self._last_cooccurrence_result.networks
                ],
                "bot_network_count": self._last_cooccurrence_result.network_count,
            },
            "response_state": {
                "dry_run_mode": settings.dry_run,
                "detection_suppressed": self.suppressor.is_suppressed,
                "suppression_reason": self.suppressor.reason,
            },
            "perf": perf,
            "drift": {
                "detected": drift.drift_detected if drift else False,
                "reason": drift.reason if drift else "",
            },
        }
