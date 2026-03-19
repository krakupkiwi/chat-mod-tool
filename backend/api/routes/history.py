"""
GET /api/history/messages — paginated message history from SQLite.
GET /api/threats           — flagged user history from flagged_users table.
"""

from __future__ import annotations

import json as _json
import logging
from typing import Optional

import aiosqlite
from fastapi import APIRouter, Query

from core.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/history/messages")
async def get_message_history(
    channel: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    before_id: Optional[int] = Query(default=None, description="Return messages with id < before_id (cursor pagination)"),
    user_id: Optional[str] = Query(default=None),
) -> dict:
    """
    Paginated message history. Returns up to `limit` messages, newest first.

    Pagination: use the `id` of the last returned message as `before_id` in the next request.
    """
    conditions: list[str] = []
    params: list = []

    if channel:
        conditions.append("channel = ?")
        params.append(channel)
    if user_id:
        conditions.append("user_id = ?")
        params.append(user_id)
    if before_id is not None:
        conditions.append("id < ?")
        params.append(before_id)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"""
        SELECT id, received_at, channel, user_id, username, raw_text,
               content_hash, emoji_count, url_count, has_url,
               is_subscriber, is_moderator, is_vip, account_age_days
        FROM messages
        {where}
        ORDER BY id DESC
        LIMIT ?
    """
    params.append(limit)

    rows = []
    try:
        async with aiosqlite.connect(settings.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(sql, params) as cursor:
                rows = [dict(row) async for row in cursor]
    except Exception:
        logger.exception("Failed to fetch message history")
        return {"messages": [], "error": "Database error"}

    return {
        "messages": rows,
        "count": len(rows),
        "next_before_id": rows[-1]["id"] if rows else None,
    }


@router.get("/threats")
async def get_threats(
    limit: int = Query(default=100, ge=1, le=500),
    status: Optional[str] = Query(default=None, description="Filter by status (active, resolved, false_positive). Omit for all."),
    max_age_days: Optional[int] = Query(default=None, ge=1, le=365, description="Exclude entries older than this many days."),
    channel: Optional[str] = Query(default=None, description="Filter to a specific channel. Omit for all real channels."),
) -> dict:
    """
    Recent flagged-user history from the flagged_users table.
    Returns up to `limit` entries, newest first.
    Simulated data (channel='__sim__') is always excluded unless explicitly requested.
    """
    import time as _time
    conditions: list[str] = []
    params: list = []

    if channel:
        conditions.append("channel = ?")
        params.append(channel)
    else:
        # Always hide simulator data from real channel views
        conditions.append("channel != '__sim__'")

    if status:
        conditions.append("status = ?")
        params.append(status)

    if max_age_days is not None:
        cutoff = _time.time() - max_age_days * 86400
        conditions.append("flagged_at >= ?")
        params.append(cutoff)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"""
        SELECT id, flagged_at, user_id, username, channel,
               threat_score, signals, status,
               (SELECT COUNT(*) FROM flagged_users c WHERE c.user_id = flagged_users.user_id) AS flag_count
        FROM flagged_users
        {where}
        ORDER BY flagged_at DESC
        LIMIT ?
    """
    params.append(limit)

    rows = []
    try:
        async with aiosqlite.connect(settings.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(sql, params) as cursor:
                rows = [dict(r) async for r in cursor]
        for row in rows:
            try:
                row["signals"] = _json.loads(row["signals"])
            except Exception:
                row["signals"] = []
    except Exception:
        logger.exception("Failed to fetch threats")
        return {"threats": [], "error": "Database error"}

    return {"threats": rows, "count": len(rows)}
