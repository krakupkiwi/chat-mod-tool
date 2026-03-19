"""
AlertingMixin — per-user threat evaluation for DetectionEngine.

Extracted from engine.py to keep individual modules under ~300 lines.
Mix into DetectionEngine via multiple inheritance.

All methods access `self` attributes defined in DetectionEngine.__init__.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from detection.scoring.health_score import HealthSnapshot

from core.config import settings
from detection.aggregator import compute_user_threat_score
from detection.alerts import build_threat_alert_event, write_flagged_user
from twitch.token_store import TOKEN_BROADCASTER_ID, token_store

logger = logging.getLogger(__name__)

# Alert cooldown: don't re-flag the same user within this window (seconds)
_ALERT_COOLDOWN = 60.0
# Maximum new alerts to process per tick — prevents a burst of simultaneous
# threshold crossings from triggering dozens of DB writes in a single tick.
# Remaining users are deferred to the next alert evaluation (2s later).
_MAX_ALERTS_PER_TICK = 15
# Minimum threat score to write a DB alert.
#
# History:
#   40 → original value (worked with MAX_POSSIBLE=125 before Phase 9)
#   55 → raised after per-user signal attribution bug fix to compensate for
#          channel-level signals no longer bleeding onto all users
#   40 → restored after Phase 9 added three new signals (known_bot, pattern_match,
#          timing_regularity), raising MAX_POSSIBLE from 125 → 185
#   35 → lowered after simulation analysis showed bot_raid bots score 34–47%
#          (variable due to temporal_sync decay between 3s bursts). At threshold=40,
#          bots in mid-decay fell below the detection floor. Legitimate users max
#          out at ~15-20% with per-user signals only, so 35 keeps a comfortable
#          15-point buffer.  spam_patterns corpus extended with follower-bot campaign
#          phrases so pattern_match now fires for bot_raid bots (+5% score floor).
_ALERT_THRESHOLD = 35.0


class AlertingMixin:
    """
    Mixin that provides per-user threat evaluation and alerting.

    Requires (from DetectionEngine.__init__):
        self._buffer, self._last_alerted, self._user_signals,
        self.protection, self._ws_manager, self._moderation_engine
    """

    async def _evaluate_user_alerts(self, snapshot: "HealthSnapshot", recent_msgs: list) -> None:
        """
        Evaluate per-user threat scores for everyone active in the last 30s and
        emit alerts for users who exceed _ALERT_THRESHOLD (55.0).

        Signal attribution rules — critical to understand to avoid false positives:
          - temporal_sync, minhash_cluster, duplicate_ratio: stored per-user during
            process_message().  Only users whose messages were *members* of a returned
            cluster or burst receive a non-zero score.  The channel-level peak is never
            applied globally.
          - burst_anomaly: channel-level only.  Intentionally set to 0.0 per-user so
            a bot flood does not cascade onto every innocent user in the window.  This
            was the root cause of a 95%+ false-positive rate before the fix.
          - rate_anomaly, username_entropy, new_account: always per-user.

        Guards applied before scoring:
          1. Alert cooldown: skip users alerted within the last 60s (_ALERT_COOLDOWN).
          2. Protected accounts: moderators, VIPs, 60+ day subscribers, whitelisted
             users, and known-good bots are never scored or alerted.
          3. Minimum 2 meaningful signals: at least 2 signals must be ≥ 0.2 normalised
             before an alert is issued.  Prevents weak multi-signal accumulation
             (e.g. username_entropy + new_account alone) from triggering alerts.

        On alert:
          - Writes to flagged_users table via write_flagged_user().
          - Increments reputation penalty via ReputationStore.record_flag().
          - Broadcasts threat_alert WebSocket event to the dashboard.
          - Passes threat to ModerationEngine.on_threat() for potential action
            (subject to dry_run, auto_timeout_enabled, auto_ban_enabled, dual-signal
            requirement for bans, and protected-account checks in the moderation layer).
        """
        now = time.time()
        channel = "__sim__" if settings.simulator_active else settings.default_channel

        # Move reputation store lookup outside per-user loop (avoid repeated import overhead)
        from storage.reputation import reputation_store as _rep

        users_in_window: dict[str, object] = {}
        for msg in recent_msgs:
            users_in_window.setdefault(msg.user_id, msg)

        _alert_iter = 0
        _alerts_issued = 0
        for uid, sample_msg in users_in_window.items():
            _alert_iter += 1
            if _alert_iter % 50 == 0:
                # Yield every 50 users so the message consumer and other
                # coroutines can run during large-window alert evaluations
                # (e.g. 500 active users at 5K msg/min = 500 iterations).
                await asyncio.sleep(0)
            # Skip recently alerted
            if now - self._last_alerted.get(uid, 0.0) < _ALERT_COOLDOWN:
                continue

            # Skip protected accounts
            protected, _ = self.protection.is_protected(sample_msg)
            if protected:
                continue

            # Build normalised signal vector.
            # IMPORTANT: temporal_sync, minhash_cluster, and duplicate_ratio are stored
            # per-user (populated during process_message) so we do NOT apply the
            # channel-level peak to every user in the window.  burst_anomaly is a
            # pure channel-level signal and is intentionally excluded from per-user
            # scoring to avoid false-positives when bots flood the channel.
            user_sigs = self._user_signals.get(uid, {})
            signals_norm = {
                "temporal_sync":     min(user_sigs.get("temporal_sync", 0.0) / 25.0, 1.0),
                "minhash_cluster":   min(user_sigs.get("minhash_cluster", 0.0) / 25.0, 1.0),
                "rate_anomaly":      min(user_sigs.get("rate_anomaly", 0.0) / 20.0, 1.0),
                "burst_anomaly":     0.0,  # channel-level only — not attributed per-user
                "duplicate_ratio":   min(user_sigs.get("duplicate_ratio", 0.0) / 35.0, 1.0),
                "username_entropy":  min(user_sigs.get("username_entropy", 0.0) / 15.0, 1.0),
                "new_account":       min(user_sigs.get("new_account", 0.0) / 15.0, 1.0),
                "known_bot":         min(user_sigs.get("known_bot", 0.0) / 25.0, 1.0),
                "pattern_match":     min(user_sigs.get("pattern_match", 0.0) / 20.0, 1.0),
                "timing_regularity": min(user_sigs.get("timing_regularity", 0.0) / 15.0, 1.0),
            }

            # Guard: require at least 2 signals above 0.2 to avoid false positives
            # from many weak signals accumulating (e.g. username_entropy + new_account alone).
            meaningful = sum(1 for v in signals_norm.values() if v >= 0.2)
            if meaningful < 2:
                continue

            threat_score = compute_user_threat_score(signals_norm)

            # Apply cross-session reputation modifier (past offenders score higher)
            if _rep is not None:
                threat_score = await _rep.apply_threat_modifier(uid, threat_score)

            if threat_score < _ALERT_THRESHOLD:
                continue

            active_sigs = [k for k, v in signals_norm.items() if v > 0.1]
            alert_id = str(uuid.uuid4())

            # Build SHAP-style explanation: top signal contributors
            explanation = _build_explanation(signals_norm)

            # Record flag in reputation store
            if _rep is not None:
                await _rep.record_flag(uid, sample_msg.username)

            await write_flagged_user(
                db_path=settings.db_path,
                user_id=uid,
                username=sample_msg.username,
                channel=channel,
                threat_score=threat_score,
                signals=active_sigs,
            )

            if self._ws_manager:
                payload = build_threat_alert_event(
                    alert_id=alert_id,
                    user_id=uid,
                    username=sample_msg.username,
                    threat_score=threat_score,
                    signals=active_sigs,
                    channel=channel,
                    explanation=explanation,
                )
                await self._ws_manager.broadcast(payload)

            if self._moderation_engine is not None:
                broadcaster_id = token_store.retrieve(TOKEN_BROADCASTER_ID) or ""
                await self._moderation_engine.on_threat(
                    user_id=uid,
                    username=sample_msg.username,
                    channel=channel,
                    threat_score=threat_score,
                    signals=signals_norm,
                    broadcaster_id=broadcaster_id,
                )

            self._last_alerted[uid] = now
            logger.info(
                "Threat alert: %s score=%.1f signals=%s",
                sample_msg.username, threat_score, active_sigs,
            )
            _alerts_issued += 1
            if _alerts_issued >= _MAX_ALERTS_PER_TICK:
                break


def _build_explanation(signals_norm: dict[str, float]) -> list[dict]:
    """
    Build a SHAP-style explanation: top signal contributors as percentage of
    total threat score.  Returns top 3 signals sorted by contribution descending.

    Each entry: {"signal": str, "contribution": float (0-100%), "label": str}
    """
    from detection.aggregator import SIGNAL_WEIGHTS
    from detection.alerts import _SIGNAL_PRETTY  # imported lazily to avoid circular

    weighted = {
        name: signals_norm.get(name, 0.0) * weight
        for name, weight in SIGNAL_WEIGHTS.items()
    }
    total = sum(weighted.values()) or 1.0
    top = sorted(weighted.items(), key=lambda x: x[1], reverse=True)[:3]
    return [
        {
            "signal": name,
            "contribution": round((score / total) * 100, 1),
            "label": _SIGNAL_PRETTY.get(name, name.replace("_", " ").title()),
        }
        for name, score in top
        if score > 0
    ]
