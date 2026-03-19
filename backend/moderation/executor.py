"""
Transactional moderation action executor.

Protocol (crash-safe):
  1. Write action row with status='pending' to DB
  2. Execute the Helix API call
  3. Update row to status='completed' or 'failed'

If the process crashes between steps 1 and 2, startup recovery (main.py)
scans for stuck 'pending' rows and resolves them.

All actual Helix calls are guarded by the rate limiter.
Dry-run mode: steps 1 and 3 happen, but the Helix call is skipped.
"""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING

import aiosqlite

from core.config import settings

if TYPE_CHECKING:
    from moderation.actions import ModerationAction
    from moderation.helix import RefreshingHTTPClient
    from moderation.rate_limiter import TokenBucketRateLimiter

logger = logging.getLogger(__name__)

_INSERT_SQL = """
INSERT INTO moderation_actions (
    created_at, user_id, username, channel, action_type,
    duration_seconds, reason, status, triggered_by, confidence
) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
"""

_UPDATE_SQL = """
UPDATE moderation_actions
SET status=?, completed_at=?, error_message=?
WHERE id=?
"""


class ModerationExecutor:
    def __init__(
        self,
        helix: "RefreshingHTTPClient",
        rate_limiter: "TokenBucketRateLimiter",
        db_path: str,
    ) -> None:
        self._helix = helix
        self._limiter = rate_limiter
        self._db_path = db_path

    async def execute(self, action: "ModerationAction") -> bool:
        """
        Execute one moderation action transactionally.
        Returns True on success (or dry-run), False on failure.
        """
        # Step 1: write pending row
        db_id = await self._write_pending(action)
        action.db_id = db_id

        if settings.dry_run:
            logger.info(
                "[DRY-RUN] %s %s (%s) duration=%s reason=%s",
                action.action_type.upper(), action.username,
                action.user_id, action.duration_seconds, action.reason,
            )
            await self._update_status(db_id, "completed", None)
            action.status = "completed"
            action.completed_at = time.time()
            return True

        # Step 2: rate-limited API call
        await self._limiter.acquire()
        error: str | None = None
        success = False

        try:
            success = await self._call_helix(action)
        except Exception as exc:
            error = str(exc)
            logger.exception("Helix API call failed for action %s", action.action_id)

        # Step 3: update row
        status = "completed" if success else "failed"
        await self._update_status(db_id, status, error)
        action.status = status
        action.completed_at = time.time()
        action.error_message = error
        return success

    async def undo(self, db_id: int) -> bool:
        """Reverse a completed action. Returns True if reversed successfully."""
        action = await self._load_action(db_id)
        if action is None:
            logger.warning("Cannot undo action %d — not found", db_id)
            return False

        if action["status"] not in ("completed",):
            logger.warning("Cannot undo action %d — status=%s", db_id, action["status"])
            return False

        success = await self._reverse_action(action)
        if success:
            await self._update_status(db_id, "undone", None)
        return success

    # ------------------------------------------------------------------
    # Helix API calls
    # ------------------------------------------------------------------

    async def _call_helix(self, action: "ModerationAction") -> bool:
        broadcaster_id = action.broadcaster_id
        if not broadcaster_id:
            # Look up broadcaster ID from token_store if not provided
            from twitch.token_store import TOKEN_BROADCASTER_ID, token_store
            broadcaster_id = token_store.retrieve(TOKEN_BROADCASTER_ID) or ""

        if action.action_type == "ban":
            return await self._ban(broadcaster_id, action.user_id, action.reason)

        if action.action_type == "timeout":
            return await self._timeout(
                broadcaster_id, action.user_id,
                action.duration_seconds or 60, action.reason,
            )

        if action.action_type == "warn":
            return await self._warn(broadcaster_id, action.user_id, action.reason)

        if action.action_type == "delete":
            return await self._delete_message(
                broadcaster_id, action.message_id or ""
            )

        if action.action_type == "slow_mode":
            return await self._set_chat_setting(broadcaster_id, {"slow_mode": True, "slow_mode_wait_time": action.duration_seconds or 30})

        if action.action_type == "slow_mode_off":
            return await self._set_chat_setting(broadcaster_id, {"slow_mode": False})

        if action.action_type == "followers_only":
            return await self._set_chat_setting(broadcaster_id, {"follower_mode": True, "follower_mode_duration": action.duration_seconds or 0})

        if action.action_type == "followers_only_off":
            return await self._set_chat_setting(broadcaster_id, {"follower_mode": False})

        if action.action_type == "emote_only":
            return await self._set_chat_setting(broadcaster_id, {"emote_mode": True})

        if action.action_type == "emote_only_off":
            return await self._set_chat_setting(broadcaster_id, {"emote_mode": False})

        if action.action_type == "sub_only":
            return await self._set_chat_setting(broadcaster_id, {"subscriber_mode": True})

        if action.action_type == "sub_only_off":
            return await self._set_chat_setting(broadcaster_id, {"subscriber_mode": False})

        if action.action_type == "unique_chat":
            return await self._set_chat_setting(broadcaster_id, {"unique_chat_mode": True})

        if action.action_type == "unique_chat_off":
            return await self._set_chat_setting(broadcaster_id, {"unique_chat_mode": False})

        logger.error("Unknown action type: %s", action.action_type)
        return False

    async def _ban(self, broadcaster_id: str, user_id: str, reason: str) -> bool:
        from twitch.token_store import TOKEN_ACCESS, token_store
        moderator_id = token_store.retrieve("twitch_bot_user_id") or broadcaster_id
        resp = await self._helix.post(
            "/moderation/bans",
            params={"broadcaster_id": broadcaster_id, "moderator_id": moderator_id},
            json={"data": {"user_id": user_id, "reason": reason[:500]}},
        )
        ok = resp.status_code in (200, 204)
        if not ok:
            logger.warning("Ban failed: HTTP %d %s", resp.status_code, resp.text[:200])
        return ok

    async def _timeout(
        self, broadcaster_id: str, user_id: str, duration: int, reason: str
    ) -> bool:
        from twitch.token_store import token_store
        moderator_id = token_store.retrieve("twitch_bot_user_id") or broadcaster_id
        resp = await self._helix.post(
            "/moderation/bans",
            params={"broadcaster_id": broadcaster_id, "moderator_id": moderator_id},
            json={"data": {"user_id": user_id, "duration": duration, "reason": reason[:500]}},
        )
        ok = resp.status_code in (200, 204)
        if not ok:
            logger.warning("Timeout failed: HTTP %d %s", resp.status_code, resp.text[:200])
        return ok

    async def _warn(self, broadcaster_id: str, user_id: str, reason: str) -> bool:
        from twitch.token_store import token_store
        moderator_id = token_store.retrieve("twitch_bot_user_id") or broadcaster_id
        resp = await self._helix.post(
            "/moderation/warnings",
            params={"broadcaster_id": broadcaster_id, "moderator_id": moderator_id},
            json={"data": {"user_id": user_id, "reason": reason[:500]}},
        )
        ok = resp.status_code in (200, 201, 204)
        if not ok:
            logger.warning("Warn failed: HTTP %d %s", resp.status_code, resp.text[:200])
        return ok

    async def _delete_message(self, broadcaster_id: str, message_id: str) -> bool:
        from twitch.token_store import token_store
        moderator_id = token_store.retrieve("twitch_bot_user_id") or broadcaster_id
        resp = await self._helix.delete(
            "/moderation/chat",
            params={
                "broadcaster_id": broadcaster_id,
                "moderator_id": moderator_id,
                "message_id": message_id,
            },
        )
        return resp.status_code in (200, 204)

    async def _set_chat_setting(self, broadcaster_id: str, body: dict) -> bool:
        from twitch.token_store import token_store
        moderator_id = token_store.retrieve("twitch_bot_user_id") or broadcaster_id
        resp = await self._helix.patch(
            "/chat/settings",
            params={"broadcaster_id": broadcaster_id, "moderator_id": moderator_id},
            json=body,
        )
        ok = resp.status_code in (200, 204)
        if not ok:
            logger.warning("Chat setting update failed: HTTP %d %s", resp.status_code, resp.text[:200])
        return ok

    async def _reverse_action(self, action: dict) -> bool:
        """Reverse a completed ban or timeout by unbanning."""
        broadcaster_id = action.get("broadcaster_id", "")
        user_id = action.get("user_id", "")
        action_type = action.get("action_type", "")

        if action_type in ("ban", "timeout"):
            from twitch.token_store import token_store
            moderator_id = token_store.retrieve("twitch_bot_user_id") or broadcaster_id
            resp = await self._helix.delete(
                "/moderation/bans",
                params={
                    "broadcaster_id": broadcaster_id,
                    "moderator_id": moderator_id,
                    "user_id": user_id,
                },
            )
            return resp.status_code in (200, 204)

        logger.warning("Undo not supported for action type: %s", action_type)
        return False

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    async def _write_pending(self, action: "ModerationAction") -> int:
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                _INSERT_SQL,
                (
                    action.created_at,
                    action.user_id,
                    action.username,
                    action.channel,
                    action.action_type,
                    action.duration_seconds,
                    action.reason,
                    action.triggered_by,
                    action.confidence,
                ),
            )
            await db.commit()
            return cursor.lastrowid

    async def _update_status(
        self, db_id: int, status: str, error: str | None
    ) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                _UPDATE_SQL,
                (status, time.time(), error, db_id),
            )
            await db.commit()

    async def _load_action(self, db_id: int) -> dict | None:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM moderation_actions WHERE id=?", (db_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None
