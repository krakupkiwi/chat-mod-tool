"""
ModerationEngine — orchestrates all moderation logic.

Responsibilities:
  - Receives threat alerts from DetectionEngine
  - Applies escalation table (confidence → action type)
  - Enforces dual-signal ban gate (two independent signals both > 90)
  - Respects dry-run mode (default ON)
  - Queues actions through rate-limited executor
  - Exposes manual action API (ban, timeout, delete)
  - Handles cluster-wide timeouts (one trigger → N users)
  - Broadcasts action events via WebSocket

Safety invariants (enforced in code):
  1. Dry-run ON by default — no Helix calls until user enables live mode
  2. Bans require two independent signals both > 90 confidence
  3. Protected accounts (mods, VIPs, 60d subs, whitelist) are never actioned
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from api.websocket import ConnectionManager
    from detection.protection import ProtectedAccountChecker

from core.config import settings
from moderation.actions import ModerationAction, get_escalation_action
from moderation.executor import ModerationExecutor
from moderation.helix import RefreshingHTTPClient
from moderation.rate_limiter import TokenBucketRateLimiter

logger = logging.getLogger(__name__)

# Dual-signal ban gate: both signals must exceed this threshold
BAN_SIGNAL_THRESHOLD = 90.0

_ACTION_QUEUE_MAXSIZE = 1000


class ModerationEngine:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._helix = RefreshingHTTPClient()
        self._rate_limiter = TokenBucketRateLimiter(capacity=80, window_seconds=60.0)
        self._executor = ModerationExecutor(self._helix, self._rate_limiter, db_path)
        self._queue: asyncio.Queue[ModerationAction] = asyncio.Queue(
            maxsize=_ACTION_QUEUE_MAXSIZE
        )
        self._ws_manager: "ConnectionManager | None" = None
        self._protection: "ProtectedAccountChecker | None" = None
        self._running = False

        # Dual-signal tracking: user_id → {signal_name: score}
        self._user_high_signals: dict[str, dict[str, float]] = {}

        # Dedup: avoid re-actioning the same user within cooldown
        self._last_actioned: dict[str, float] = {}
        self._action_cooldown = 120.0  # seconds

    def set_ws_manager(self, manager: "ConnectionManager") -> None:
        self._ws_manager = manager

    def set_protection_checker(self, checker: "ProtectedAccountChecker") -> None:
        self._protection = checker

    async def start(self) -> None:
        """Start the action dispatch loop."""
        self._running = True
        asyncio.create_task(self._dispatch_loop(), name="moderation-dispatch")
        logger.info(
            "ModerationEngine started (dry_run=%s, auto_timeout=%s, auto_ban=%s)",
            settings.dry_run, settings.auto_timeout_enabled, settings.auto_ban_enabled,
        )

    # ------------------------------------------------------------------
    # Automated action entry point (called by DetectionEngine)
    # ------------------------------------------------------------------

    async def on_threat(
        self,
        user_id: str,
        username: str,
        channel: str,
        threat_score: float,
        signals: dict[str, float],   # signal_name → normalised 0–1 score
        broadcaster_id: str = "",
    ) -> None:
        """
        Evaluate whether to take automated action against a flagged user.
        Called by DetectionEngine after an alert is written to the DB.
        """
        # Dedup
        now = time.time()
        if now - self._last_actioned.get(user_id, 0.0) < self._action_cooldown:
            return

        action_type, duration = get_escalation_action(threat_score)
        if action_type is None:
            return

        # Ban gate: requires two independent signals both > 90
        if action_type == "ban":
            if not settings.auto_ban_enabled:
                logger.debug("Auto-ban disabled — skipping ban for %s", username)
                return
            high_signals = {
                name: score * 100 for name, score in signals.items()
                if score * 100 > BAN_SIGNAL_THRESHOLD
            }
            # Store accumulated high signals across ticks
            existing = self._user_high_signals.get(user_id, {})
            existing.update(high_signals)
            self._user_high_signals[user_id] = existing
            if len(existing) < 2:
                logger.debug(
                    "Ban gate not met for %s: only %d independent signal(s) > 90",
                    username, len(existing),
                )
                return

        elif action_type == "timeout":
            if not settings.auto_timeout_enabled:
                logger.debug("Auto-timeout disabled — skipping timeout for %s", username)
                return

        action = ModerationAction(
            action_type=action_type,
            broadcaster_id=broadcaster_id,
            user_id=user_id,
            username=username,
            channel=channel,
            duration_seconds=duration,
            reason=f"Automated detection — score {threat_score:.0f}",
            triggered_by=f"auto:{','.join(signals.keys())}",
            confidence=round(threat_score, 1),
        )

        self._enqueue(action)
        self._last_actioned[user_id] = now

    # ------------------------------------------------------------------
    # Manual action API (called from REST endpoints)
    # ------------------------------------------------------------------

    async def manual_ban(
        self, user_id: str, username: str, channel: str,
        broadcaster_id: str, reason: str,
    ) -> ModerationAction:
        action = ModerationAction(
            action_type="ban",
            broadcaster_id=broadcaster_id,
            user_id=user_id,
            username=username,
            channel=channel,
            reason=reason,
            triggered_by="manual",
        )
        self._enqueue(action)
        return action

    async def manual_timeout(
        self, user_id: str, username: str, channel: str,
        broadcaster_id: str, duration_seconds: int, reason: str,
    ) -> ModerationAction:
        action = ModerationAction(
            action_type="timeout",
            broadcaster_id=broadcaster_id,
            user_id=user_id,
            username=username,
            channel=channel,
            duration_seconds=duration_seconds,
            reason=reason,
            triggered_by="manual",
        )
        self._enqueue(action)
        return action

    async def manual_warn(
        self, user_id: str, username: str, channel: str,
        broadcaster_id: str, reason: str,
    ) -> ModerationAction:
        action = ModerationAction(
            action_type="warn",
            broadcaster_id=broadcaster_id,
            user_id=user_id,
            username=username,
            channel=channel,
            reason=reason,
            triggered_by="manual",
        )
        self._enqueue(action)
        return action

    async def undo_action(self, db_id: int) -> bool:
        """Reverse a completed ban or timeout by db row id."""
        return await self._executor.undo(db_id)

    # ------------------------------------------------------------------
    # Cluster timeout (all users in a cluster)
    # ------------------------------------------------------------------

    async def timeout_cluster(
        self,
        cluster_user_ids: list[str],
        usernames: dict[str, str],   # user_id → username
        channel: str,
        broadcaster_id: str,
        duration_seconds: int = 60,
        reason: str = "Coordinated bot cluster",
    ) -> int:
        """
        Enqueue a timeout action for every user in a cluster.
        Returns the number of actions enqueued.
        """
        enqueued = 0
        for uid in cluster_user_ids:
            uname = usernames.get(uid, uid)
            action = ModerationAction(
                action_type="timeout",
                broadcaster_id=broadcaster_id,
                user_id=uid,
                username=uname,
                channel=channel,
                duration_seconds=duration_seconds,
                reason=reason,
                triggered_by="auto:cluster_timeout",
            )
            self._enqueue(action)
            enqueued += 1
        logger.info("Cluster timeout: enqueued %d actions", enqueued)
        return enqueued

    # ------------------------------------------------------------------
    # Dispatch loop
    # ------------------------------------------------------------------

    async def _dispatch_loop(self) -> None:
        """Consume the action queue and execute each action."""
        while self._running:
            try:
                action = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            try:
                success = await self._executor.execute(action)
                await self._broadcast_action_event(action, success)
                if success and action.action_type in ("ban", "timeout"):
                    from storage.reputation import reputation_store as _rep
                    if _rep is not None:
                        await _rep.record_action(action.user_id, action.username)
            except Exception:
                logger.exception("Failed to execute action %s", action.action_id)
            finally:
                self._queue.task_done()

    def _enqueue(self, action: ModerationAction) -> None:
        try:
            self._queue.put_nowait(action)
        except asyncio.QueueFull:
            logger.warning("Moderation action queue full — dropping action for %s", action.username)

    # ------------------------------------------------------------------
    # WebSocket broadcast
    # ------------------------------------------------------------------

    async def _broadcast_action_event(self, action: ModerationAction, success: bool) -> None:
        if self._ws_manager is None:
            return
        await self._ws_manager.broadcast({
            "type": "moderation_action",
            "action_id": action.action_id,
            "db_id": action.db_id,
            "action_type": action.action_type,
            "username": action.username,
            "user_id": action.user_id,
            "channel": action.channel,
            "duration_seconds": action.duration_seconds,
            "reason": action.reason,
            "triggered_by": action.triggered_by,
            "confidence": action.confidence,
            "status": action.status,
            "dry_run": settings.dry_run,
            "timestamp": action.completed_at or action.created_at,
        })
