"""
User profile endpoint.

GET /api/users/{user_id}   — aggregate profile: recent messages + flag history
"""

from __future__ import annotations

import aiosqlite
from fastapi import APIRouter, HTTPException

from core.config import settings

router = APIRouter()


@router.get("/users/{user_id}")
async def get_user_profile(user_id: str, message_limit: int = 25):
    """
    Returns a user's recent messages, flag history, and derived stats.
    Uses data already stored in SQLite — no Helix API call needed.
    """
    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row

        # Recent messages (newest first)
        async with db.execute(
            """
            SELECT id, received_at, raw_text, emoji_count, url_count,
                   is_subscriber, is_moderator, is_vip, account_age_days, username
            FROM messages
            WHERE user_id = ?
            ORDER BY received_at DESC
            LIMIT ?
            """,
            (user_id, min(message_limit, 100)),
        ) as cur:
            messages = [dict(r) async for r in cur]

        if not messages:
            raise HTTPException(404, f"No messages found for user {user_id!r}")

        # Aggregate stats from message rows
        sample = messages[0]
        username = sample["username"]
        account_age_days = sample["account_age_days"]
        is_subscriber = bool(sample["is_subscriber"])
        is_moderator = bool(sample["is_moderator"])
        is_vip = bool(sample["is_vip"])

        # Message count in last session (all rows in DB for this user)
        async with db.execute(
            "SELECT COUNT(*) FROM messages WHERE user_id = ?", (user_id,)
        ) as cur:
            (total_messages,) = await cur.fetchone()

        # Flag history — include channel so cross-channel incidents are visible
        async with db.execute(
            """
            SELECT id, flagged_at, channel, threat_score, signals, status
            FROM flagged_users
            WHERE user_id = ? AND channel != '__sim__'
            ORDER BY flagged_at DESC
            LIMIT 20
            """,
            (user_id,),
        ) as cur:
            flags = [dict(r) async for r in cur]

        # Moderation actions against this user
        async with db.execute(
            """
            SELECT id, created_at, action_type, duration_seconds, reason,
                   status, triggered_by, confidence
            FROM moderation_actions
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT 10
            """,
            (user_id,),
        ) as cur:
            actions = [dict(r) async for r in cur]

    # Compute max threat score seen
    max_threat = max((f["threat_score"] for f in flags), default=0.0)
    # Collect all unique signals ever seen
    import json
    all_signals: set[str] = set()
    for f in flags:
        try:
            all_signals.update(json.loads(f["signals"]))
        except Exception:
            pass

    # Reputation score (cross-session)
    reputation = 100.0
    from storage.reputation import reputation_store as _rep
    if _rep is not None:
        reputation = await _rep.get(user_id)

    return {
        "user_id": user_id,
        "username": username,
        "account_age_days": account_age_days,
        "is_subscriber": is_subscriber,
        "is_moderator": is_moderator,
        "is_vip": is_vip,
        "total_messages": total_messages,
        "max_threat_score": round(max_threat, 1),
        "reputation": round(reputation, 1),
        "signals_seen": sorted(all_signals),
        "recent_messages": messages,
        "flag_history": flags,
        "moderation_actions": actions,
    }
