"""
Multi-channel management endpoints.

GET    /api/channels              — list all monitored channels (default + secondary)
POST   /api/channels              — add a secondary channel to monitor
DELETE /api/channels/{name}       — remove a secondary channel
GET    /api/channels/{name}/stats — per-channel basic health stats

The default channel (settings.default_channel) is always shown but cannot be
deleted via this API — change it via PATCH /api/config.

Secondary channels are persisted in the monitored_channels SQLite table and
re-subscribed on startup.  Subscriptions are added live without restart.
"""

from __future__ import annotations

import logging
import time

import aiosqlite
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.config import settings
from twitch.token_store import TOKEN_BROADCASTER_ID, token_store

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_helix():
    import startup as main
    if main.moderation_engine is None:
        raise HTTPException(503, "ModerationEngine not ready")
    return main.moderation_engine._executor._helix


def _default_channel() -> str:
    return settings.default_channel or ""


# ---------------------------------------------------------------------------
# Request/response schemas
# ---------------------------------------------------------------------------

class AddChannelRequest(BaseModel):
    channel: str
    note: str = ""


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/channels")
async def list_channels():
    """Return all monitored channels: the default channel plus any secondary ones."""
    default = _default_channel()
    channels = []
    if default:
        channels.append({"name": default, "is_default": True, "note": ""})

    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT name, broadcaster_id, added_at, note FROM monitored_channels ORDER BY added_at"
        ) as cursor:
            async for row in cursor:
                if row["name"] != default:  # don't duplicate the default
                    channels.append({
                        "name": row["name"],
                        "broadcaster_id": row["broadcaster_id"],
                        "is_default": False,
                        "note": row["note"],
                        "added_at": row["added_at"],
                    })

    return {"channels": channels}


@router.post("/channels")
async def add_channel(body: AddChannelRequest):
    """
    Add a secondary channel to monitor.

    Looks up the broadcaster_id via Helix, stores it in the DB, then subscribes
    to EventSub ChatMessageSubscription for that channel live (no restart needed).
    """
    name = body.channel.strip().lstrip("#").lower()
    if not name:
        raise HTTPException(400, "Channel name required")

    default = _default_channel()
    if name == default.lower():
        raise HTTPException(400, "That is already the primary channel")

    helix = _get_helix()

    # Resolve broadcaster_id
    resp = await helix.get("/users", params={"login": name})
    if resp.status_code != 200 or not resp.json().get("data"):
        raise HTTPException(404, f"Channel '{name}' not found on Twitch")

    broadcaster_id = str(resp.json()["data"][0]["id"])

    async with aiosqlite.connect(settings.db_path) as db:
        await db.execute(
            """
            INSERT INTO monitored_channels (name, broadcaster_id, added_at, note)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                broadcaster_id=excluded.broadcaster_id,
                note=excluded.note
            """,
            (name, broadcaster_id, time.time(), body.note.strip()),
        )
        await db.commit()

    # Subscribe live — no restart needed
    from twitch import manager as twitch_manager
    try:
        await twitch_manager.subscribe_channel(name, broadcaster_id)
        logger.info("Subscribed to secondary channel #%s (broadcaster_id=%s)", name, broadcaster_id)
    except Exception as exc:
        logger.warning("Could not subscribe to #%s live: %s", name, exc)

    return {"channel": name, "broadcaster_id": broadcaster_id, "ok": True}


@router.delete("/channels/{name}")
async def remove_channel(name: str):
    """Remove a secondary channel. The default channel cannot be removed here."""
    name = name.strip().lstrip("#").lower()
    default = _default_channel()
    if name == default.lower():
        raise HTTPException(400, "Cannot remove the primary channel via this endpoint")

    async with aiosqlite.connect(settings.db_path) as db:
        await db.execute("DELETE FROM monitored_channels WHERE name=?", (name,))
        await db.commit()

    logger.info("Removed secondary channel #%s (EventSub subscription will lapse at next reconnect)", name)
    return {"channel": name, "ok": True}


@router.get("/channels/{name}/stats")
async def channel_stats(name: str):
    """
    Per-channel basic health metrics for the ChannelBar:
      - messages_per_min (last 60s)
      - active_users (last 60s)
      - recent_alerts (flagged_users in last 300s)
    """
    name = name.strip().lstrip("#").lower()
    now = time.time()
    window_60 = now - 60
    window_300 = now - 300

    stats: dict = {
        "channel": name,
        "messages_per_min": 0,
        "active_users": 0,
        "recent_alerts": 0,
    }

    async with aiosqlite.connect(settings.db_path) as db:
        # msg/min and unique users in last 60s
        async with db.execute(
            "SELECT COUNT(*), COUNT(DISTINCT user_id) FROM messages WHERE channel=? AND received_at >= ?",
            (name, window_60),
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                stats["messages_per_min"] = row[0]
                stats["active_users"] = row[1]

        # recent alert count
        async with db.execute(
            "SELECT COUNT(*) FROM flagged_users WHERE channel=? AND flagged_at >= ?",
            (name, window_300),
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                stats["recent_alerts"] = row[0]

    return stats
