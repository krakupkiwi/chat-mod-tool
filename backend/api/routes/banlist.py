"""
Shared ban-list import endpoints.

POST /api/moderation/import-banlist/preview  — parse text, resolve usernames via
     Helix /users, de-dup against existing bans + whitelist, return target list.
POST /api/moderation/import-banlist/execute  — enqueue ban ModerationAction for
     each resolved user_id via the transactional moderation engine.

Supported input formats (auto-detected):
  - Plain text: one username per line
  - JSON array: ["user1", "user2", ...]  (CommanderRoot / generic export)
  - JSON object with "data" array: {"data": [{"user_login": "x"}, ...]}

Deduplication:
  - Already in whitelist → skipped (protected)
  - Already banned (completed ban in moderation_actions) → skipped
  - Appears more than once in the input → deduplicated silently

Rate limiting: preview resolves up to 1 000 users (10 Helix /users pages of 100).
Execute enqueues via engine._enqueue() which respects the rate limiter.
"""

from __future__ import annotations

import json
import logging

import aiosqlite
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core.config import settings
from twitch.token_store import TOKEN_BROADCASTER_ID, token_store

logger = logging.getLogger(__name__)
router = APIRouter()

_MAX_USERS = 1_000   # hard cap: 10 Helix /users pages × 100 logins each


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_helix():
    import startup as main
    if main.moderation_engine is None:
        raise HTTPException(503, "ModerationEngine not ready")
    return main.moderation_engine._executor._helix


def _get_engine():
    import startup as main
    if main.moderation_engine is None:
        raise HTTPException(503, "ModerationEngine not ready")
    return main.moderation_engine


def _get_ids() -> tuple[str, str]:
    broadcaster_id = token_store.retrieve(TOKEN_BROADCASTER_ID) or ""
    moderator_id = token_store.retrieve("twitch_bot_user_id") or broadcaster_id
    if not broadcaster_id:
        raise HTTPException(503, "Broadcaster ID not configured")
    return broadcaster_id, moderator_id


def _parse_input(text: str) -> list[str]:
    """
    Parse a ban-list text into a deduplicated lowercase list of usernames.
    Auto-detects plain-text, JSON array, or JSON {"data": [...]} format.
    """
    text = text.strip()
    usernames: list[str] = []

    if text.startswith("[") or text.startswith("{"):
        try:
            data = json.loads(text)
            if isinstance(data, list):
                # ["user1", "user2"] or [{"user_login": "x"}, ...]
                for item in data:
                    if isinstance(item, str):
                        usernames.append(item.strip().lower())
                    elif isinstance(item, dict):
                        name = item.get("user_login") or item.get("name") or item.get("login") or ""
                        if name:
                            usernames.append(name.strip().lower())
            elif isinstance(data, dict):
                # {"data": [...]}  (Helix-style export)
                for item in data.get("data", []):
                    if isinstance(item, dict):
                        name = item.get("user_login") or item.get("login") or item.get("name") or ""
                        if name:
                            usernames.append(name.strip().lower())
        except json.JSONDecodeError:
            pass  # Fall through to plain-text parsing

    if not usernames:
        # Plain text: one username per line, strip # prefix and comments
        for line in text.splitlines():
            line = line.split("#")[0].strip().lstrip("@").lower()
            if line:
                usernames.append(line)

    # Deduplicate preserving order
    seen: set[str] = set()
    result: list[str] = []
    for u in usernames:
        if u not in seen:
            seen.add(u)
            result.append(u)
    return result[:_MAX_USERS]


async def _resolve_usernames(helix, logins: list[str]) -> dict[str, str]:
    """Batch-resolve login names → user_id via GET /helix/users (100 per request)."""
    resolved: dict[str, str] = {}  # login → user_id
    for i in range(0, len(logins), 100):
        batch = logins[i : i + 100]
        try:
            resp = await helix.get("/users", params=[("login", l) for l in batch])
            if resp.status_code == 200:
                for user in resp.json().get("data", []):
                    resolved[user["login"].lower()] = str(user["id"])
        except Exception as exc:
            logger.warning("Helix /users batch failed (offset=%d): %s", i, exc)
    return resolved


async def _load_whitelist() -> set[str]:
    """Return the set of whitelisted lowercase usernames."""
    async with aiosqlite.connect(settings.db_path) as db:
        async with db.execute("SELECT username FROM whitelist") as cursor:
            return {row[0].lower() async for row in cursor}


async def _load_existing_bans() -> set[str]:
    """Return user_ids that already have a completed ban."""
    async with aiosqlite.connect(settings.db_path) as db:
        async with db.execute(
            "SELECT DISTINCT user_id FROM moderation_actions WHERE action_type='ban' AND status='completed'"
        ) as cursor:
            return {row[0] async for row in cursor}


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class PreviewRequest(BaseModel):
    text: str = Field(max_length=500_000)  # up to ~10k usernames at ~50 chars each


class ExecuteRequest(BaseModel):
    user_ids: list[str] = Field(max_length=_MAX_USERS)
    reason: str = "Shared ban list import"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/moderation/import-banlist/preview")
async def banlist_preview(body: PreviewRequest):
    """
    Parse the input text, resolve all usernames via Helix, and return a
    categorised list: targets to ban, skipped (whitelist), skipped (already banned),
    and unresolved (username not found on Twitch).
    """
    logins = _parse_input(body.text)
    if not logins:
        return {"parsed": 0, "targets": [], "skipped_whitelist": 0,
                "skipped_already_banned": 0, "unresolved": 0}

    helix = _get_helix()
    resolved = await _resolve_usernames(helix, logins)
    whitelist = await _load_whitelist()
    existing_bans = await _load_existing_bans()

    targets = []
    skipped_whitelist = 0
    skipped_already_banned = 0
    unresolved = 0

    for login in logins:
        user_id = resolved.get(login)
        if user_id is None:
            unresolved += 1
            continue
        if login in whitelist:
            skipped_whitelist += 1
            continue
        if user_id in existing_bans:
            skipped_already_banned += 1
            continue
        targets.append({"user_id": user_id, "username": login})

    return {
        "parsed": len(logins),
        "resolved": len(resolved),
        "unresolved": unresolved,
        "skipped_whitelist": skipped_whitelist,
        "skipped_already_banned": skipped_already_banned,
        "targets": targets,
    }


@router.post("/moderation/import-banlist/execute")
async def banlist_execute(body: ExecuteRequest):
    """
    Enqueue ban ModerationAction for each user_id in the list.
    Actions are processed by the transactional moderation engine — they respect
    dry-run mode and the existing rate limiter.
    """
    if not body.user_ids:
        return {"enqueued": 0, "dry_run": settings.dry_run}

    broadcaster_id, _ = _get_ids()
    engine = _get_engine()
    channel = settings.default_channel

    from moderation.actions import ModerationAction
    enqueued = 0
    for user_id in body.user_ids:
        action = ModerationAction(
            action_type="ban",
            broadcaster_id=broadcaster_id,
            user_id=user_id,
            username=user_id,   # username not stored at this point; logged by action_id
            channel=channel,
            duration_seconds=None,
            reason=body.reason[:500],
            triggered_by="manual:banlist",
        )
        engine._enqueue(action)
        enqueued += 1

    logger.info(
        "Ban list import: enqueued=%d dry_run=%s reason=%r",
        enqueued, settings.dry_run, body.reason,
    )
    return {"enqueued": enqueued, "dry_run": settings.dry_run}
