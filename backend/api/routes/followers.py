"""
Follow-bot follower audit.

GET  /api/followers/audit   — scan recent followers against KnownBotRegistry,
                              return suspected bot followers
POST /api/followers/remove  — remove a list of user_ids from channel followers
                              via DELETE /helix/channels/followers

Required scopes:
  moderator:read:followers     (GET)
  moderator:manage:followers   (DELETE)
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from twitch.token_store import TOKEN_BROADCASTER_ID, token_store

logger = logging.getLogger(__name__)
router = APIRouter()

_FOLLOWERS_PER_PAGE = 100
_MAX_PAGES = 10   # 1 000 followers maximum per scan


def _get_helix():
    import startup as main
    if main.moderation_engine is None:
        raise HTTPException(503, "ModerationEngine not ready")
    return main.moderation_engine._executor._helix


def _get_registry():
    """Returns the KnownBotRegistry singleton (may be None if not yet loaded)."""
    import startup as main
    engine = getattr(main, "detection_engine", None)
    if engine is None:
        raise HTTPException(503, "DetectionEngine not ready")
    return engine.known_bot_registry


def _get_ids() -> tuple[str, str]:
    broadcaster_id = token_store.retrieve(TOKEN_BROADCASTER_ID) or ""
    moderator_id = token_store.retrieve("twitch_bot_user_id") or broadcaster_id
    if not broadcaster_id:
        raise HTTPException(503, "Broadcaster ID not configured")
    return broadcaster_id, moderator_id


class RemoveFollowersRequest(BaseModel):
    user_ids: list[str] = Field(max_length=1000)


@router.get("/followers/audit")
async def follower_audit(max_followers: int = 500):
    """
    Scan up to max_followers recent channel followers against the KnownBotRegistry.
    Returns only those that match (suspected bots).

    Fetches followers in pages of 100 (up to 10 pages = 1 000 max).
    Each follower's login is checked with an O(1) Bloom filter lookup.
    """
    broadcaster_id, moderator_id = _get_ids()
    helix = _get_helix()
    registry = _get_registry()

    max_followers = max(100, min(max_followers, _MAX_PAGES * _FOLLOWERS_PER_PAGE))

    all_followers: list[dict] = []
    cursor: str | None = None
    pages_fetched = 0

    while len(all_followers) < max_followers and pages_fetched < _MAX_PAGES:
        want = min(_FOLLOWERS_PER_PAGE, max_followers - len(all_followers))
        params: dict = {
            "broadcaster_id": broadcaster_id,
            "moderator_id": moderator_id,
            "first": want,
        }
        if cursor:
            params["after"] = cursor

        resp = await helix.get("/channels/followers", params=params)
        if resp.status_code != 200:
            raise HTTPException(resp.status_code, f"Helix error: {resp.text[:200]}")

        data = resp.json()
        batch = data.get("data", [])
        all_followers.extend(batch)
        pages_fetched += 1

        cursor = data.get("pagination", {}).get("cursor")
        if not cursor or not batch:
            break

    # Check each follower against the KnownBotRegistry (O(1) Bloom filter)
    suspected: list[dict] = []
    for follower in all_followers:
        username = (follower.get("user_login") or "").lower()
        if registry and registry.is_known_bot(username):
            suspected.append({
                "user_id": follower.get("user_id", ""),
                "username": follower.get("user_login", ""),
                "display_name": follower.get("user_name", ""),
                "followed_at": follower.get("followed_at", ""),
            })

    return {
        "scanned": len(all_followers),
        "suspected_bots": len(suspected),
        "registry_loaded": registry is not None and getattr(registry, "_loaded", False),
        "registry_size": registry.size if registry else 0,
        "followers": suspected,
    }


@router.post("/followers/remove")
async def remove_followers(body: RemoveFollowersRequest):
    """
    Remove a list of user_ids from the channel's followers list.

    Issues one DELETE /helix/channels/followers per user, throttled to
    10 req/s (100 ms between calls) to stay within Helix rate limits.
    Errors on individual users are logged and counted but do not abort
    the rest of the batch.
    """
    if not body.user_ids:
        return {"removed": 0, "failed": 0, "total": 0}

    broadcaster_id, _ = _get_ids()
    helix = _get_helix()

    removed = 0
    failed = 0

    for user_id in body.user_ids:
        try:
            resp = await helix.delete(
                "/channels/followers",
                params={"broadcaster_id": broadcaster_id, "user_id": user_id},
            )
            if resp.status_code in (200, 204):
                removed += 1
            else:
                logger.warning(
                    "Failed to remove follower %s: HTTP %d %s",
                    user_id, resp.status_code, resp.text[:100],
                )
                failed += 1
        except Exception as exc:
            logger.error("Error removing follower %s: %s", user_id, exc)
            failed += 1

        # 10 req/s — well within Helix limits for moderator endpoints
        await asyncio.sleep(0.1)

    logger.info("Follower removal: removed=%d failed=%d total=%d", removed, failed, len(body.user_ids))
    return {"removed": removed, "failed": failed, "total": len(body.user_ids)}
