"""
GET /api/history/messages    — paginated message history from SQLite.
GET /api/threats             — flagged user history from flagged_users table.
GET /api/history/threats     — paginated threat history with rich filters.
GET /api/history/clusters    — paginated cluster detection history.
GET /api/history/moderation  — paginated moderation action history.
GET /api/history/escalations — paginated health level transition history.
GET /api/history/users       — aggregated user view (activity + reputation + flags).
"""

from __future__ import annotations

import json as _json
import logging
import time as _time
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
    offset: int = Query(default=0, ge=0),
    before_id: Optional[int] = Query(default=None, description="Cursor pagination: return id < before_id (legacy)"),
    user_id: Optional[str] = Query(default=None),
    hours: Optional[float] = Query(default=None, ge=0.5, le=720, description="Time window in hours"),
    search: Optional[str] = Query(default=None, description="Substring match on username or message text"),
    flagged_only: bool = Query(default=False, description="Only return messages from flagged users"),
    has_url: Optional[bool] = Query(default=None, description="Filter by whether message contains a URL"),
) -> dict:
    """
    Paginated message history with optional rich filters.

    Supports both cursor pagination (before_id) for backwards compatibility
    and offset pagination (offset) for the History page.
    """
    conditions: list[str] = ["channel != '__sim__'"]
    params: list = []

    if channel:
        conditions[0] = "channel = ?"
        params.append(channel)
    if user_id:
        conditions.append("user_id = ?")
        params.append(user_id)
    if hours is not None:
        cutoff = _time.time() - hours * 3600
        conditions.append("received_at >= ?")
        params.append(cutoff)
    if before_id is not None:
        conditions.append("id < ?")
        params.append(before_id)
    if search:
        conditions.append("(LOWER(username) LIKE ? OR LOWER(raw_text) LIKE ?)")
        term = f"%{search.lower()}%"
        params.extend([term, term])
    if has_url is not None:
        conditions.append("has_url = ?")
        params.append(1 if has_url else 0)
    if flagged_only:
        conditions.append(
            "user_id IN (SELECT DISTINCT user_id FROM flagged_users WHERE channel != '__sim__')"
        )

    where = "WHERE " + " AND ".join(conditions)
    count_sql = f"SELECT COUNT(*) FROM messages {where}"
    sql = f"""
        SELECT id, received_at, channel, user_id, username, raw_text,
               emoji_count, url_count, word_count, char_count, has_url,
               is_subscriber, is_moderator, is_vip, account_age_days
        FROM messages
        {where}
        ORDER BY id DESC
        LIMIT ? OFFSET ?
    """

    rows = []
    total = 0
    try:
        async with aiosqlite.connect(settings.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(count_sql, params) as cur:
                row = await cur.fetchone()
                total = row[0] if row else 0
            async with db.execute(sql, params + [limit, offset]) as cursor:
                rows = [dict(row) async for row in cursor]
    except Exception:
        logger.exception("Failed to fetch message history")
        return {"messages": [], "total": 0, "error": "Database error"}

    return {
        "messages": rows,
        "total": total,
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


# ---------------------------------------------------------------------------
# Rich paginated history endpoints (used by HistoryPage.tsx)
# ---------------------------------------------------------------------------

def _build_where(conditions: list[str], params: list) -> str:
    return ("WHERE " + " AND ".join(conditions)) if conditions else ""


@router.get("/history/threats")
async def get_threat_history(
    hours: float = Query(default=24, ge=0.5, le=720, description="Look-back window in hours (max 30 days)."),
    channel: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None, description="active | resolved | false_positive"),
    signal: Optional[str] = Query(default=None, description="Filter to rows that contain this signal name."),
    min_score: Optional[float] = Query(default=None, ge=0, le=100),
    search: Optional[str] = Query(default=None, description="Substring match on username (case-insensitive)."),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """Paginated threat history with rich filters for the History page."""
    cutoff = _time.time() - hours * 3600
    conditions: list[str] = ["flagged_at >= ?"]
    params: list = [cutoff]

    if channel:
        conditions.append("channel = ?")
        params.append(channel)
    else:
        conditions.append("channel != '__sim__'")

    if status:
        conditions.append("status = ?")
        params.append(status)
    if min_score is not None:
        conditions.append("threat_score >= ?")
        params.append(min_score)
    if search:
        conditions.append("LOWER(username) LIKE ?")
        params.append(f"%{search.lower()}%")
    if signal:
        # signals column is a JSON array; LIKE covers the quoted signal name
        conditions.append("signals LIKE ?")
        params.append(f'%"{signal}"%')

    where = _build_where(conditions, params)
    count_sql = f"SELECT COUNT(*) FROM flagged_users {where}"
    data_sql = f"""
        SELECT id, flagged_at, user_id, username, channel,
               threat_score, signals, status,
               (SELECT COUNT(*) FROM flagged_users c WHERE c.user_id = flagged_users.user_id) AS flag_count
        FROM flagged_users
        {where}
        ORDER BY flagged_at DESC
        LIMIT ? OFFSET ?
    """

    rows: list[dict] = []
    total = 0
    try:
        async with aiosqlite.connect(settings.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(count_sql, params) as cur:
                row = await cur.fetchone()
                total = row[0] if row else 0
            async with db.execute(data_sql, params + [limit, offset]) as cur:
                rows = [dict(r) async for r in cur]
        for row in rows:
            try:
                row["signals"] = _json.loads(row["signals"])
            except Exception:
                row["signals"] = []
    except Exception:
        logger.exception("Failed to fetch threat history")
        return {"threats": [], "total": 0, "error": "Database error"}

    return {"threats": rows, "total": total, "limit": limit, "offset": offset}


@router.get("/history/clusters")
async def get_cluster_history(
    hours: float = Query(default=24, ge=0.5, le=720),
    channel: Optional[str] = Query(default=None),
    min_members: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """Paginated cluster detection history."""
    cutoff = _time.time() - hours * 3600
    conditions: list[str] = ["detected_at >= ?", "member_count >= ?"]
    params: list = [cutoff, min_members]

    if channel:
        conditions.append("channel = ?")
        params.append(channel)

    where = _build_where(conditions, params)
    count_sql = f"SELECT COUNT(*) FROM cluster_events {where}"
    data_sql = f"""
        SELECT id, detected_at, channel, cluster_id, member_count,
               sample_message, user_ids, risk_score
        FROM cluster_events
        {where}
        ORDER BY detected_at DESC
        LIMIT ? OFFSET ?
    """

    rows: list[dict] = []
    total = 0
    try:
        async with aiosqlite.connect(settings.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(count_sql, params) as cur:
                row = await cur.fetchone()
                total = row[0] if row else 0
            async with db.execute(data_sql, params + [limit, offset]) as cur:
                rows = [dict(r) async for r in cur]
        for row in rows:
            try:
                row["user_ids"] = _json.loads(row["user_ids"])
            except Exception:
                row["user_ids"] = []
    except Exception:
        logger.exception("Failed to fetch cluster history")
        return {"clusters": [], "total": 0, "error": "Database error"}

    return {"clusters": rows, "total": total, "limit": limit, "offset": offset}


@router.get("/history/moderation")
async def get_moderation_history(
    hours: float = Query(default=24, ge=0.5, le=720),
    channel: Optional[str] = Query(default=None),
    action_type: Optional[str] = Query(default=None, description="ban | timeout | delete | slow_mode | followers_only"),
    status: Optional[str] = Query(default=None, description="pending | completed | failed | undone"),
    triggered_by: Optional[str] = Query(default=None, description="manual | auto (prefix match)"),
    search: Optional[str] = Query(default=None, description="Substring match on username."),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """Paginated moderation action history."""
    cutoff = _time.time() - hours * 3600
    conditions: list[str] = ["created_at >= ?"]
    params: list = [cutoff]

    if channel:
        conditions.append("channel = ?")
        params.append(channel)
    if action_type:
        conditions.append("action_type = ?")
        params.append(action_type)
    if status:
        conditions.append("status = ?")
        params.append(status)
    if triggered_by == "manual":
        conditions.append("triggered_by = 'manual'")
    elif triggered_by == "auto":
        conditions.append("triggered_by LIKE 'auto:%'")
    if search:
        conditions.append("LOWER(username) LIKE ?")
        params.append(f"%{search.lower()}%")

    where = _build_where(conditions, params)
    count_sql = f"SELECT COUNT(*) FROM moderation_actions {where}"
    data_sql = f"""
        SELECT id, created_at, completed_at, user_id, username, channel,
               action_type, duration_seconds, reason, status,
               triggered_by, confidence, error_message
        FROM moderation_actions
        {where}
        ORDER BY created_at DESC
        LIMIT ? OFFSET ?
    """

    rows: list[dict] = []
    total = 0
    try:
        async with aiosqlite.connect(settings.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(count_sql, params) as cur:
                row = await cur.fetchone()
                total = row[0] if row else 0
            async with db.execute(data_sql, params + [limit, offset]) as cur:
                rows = [dict(r) async for r in cur]
    except Exception:
        logger.exception("Failed to fetch moderation history")
        return {"actions": [], "total": 0, "error": "Database error"}

    return {"actions": rows, "total": total, "limit": limit, "offset": offset}


@router.get("/history/escalations")
async def get_escalation_history(
    hours: float = Query(default=24, ge=0.5, le=720),
    channel: Optional[str] = Query(default=None),
    direction: Optional[str] = Query(default=None, description="worsening | recovery"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """Paginated health level transition history."""
    cutoff = _time.time() - hours * 3600
    conditions: list[str] = ["occurred_at >= ?"]
    params: list = [cutoff]

    if channel:
        conditions.append("channel = ?")
        params.append(channel)

    # Level ordering for direction filter
    _LEVEL_ORDER = {"healthy": 0, "elevated": 1, "suspicious": 2, "likely_attack": 3, "critical": 4}
    direction_clause = ""
    if direction == "worsening":
        # to_level has a higher severity rank than from_level
        # We can't do this easily in SQLite without a CASE expression
        direction_clause = ""  # handled in Python post-filter for simplicity
    elif direction == "recovery":
        direction_clause = ""  # handled in Python post-filter

    where = _build_where(conditions, params)
    count_sql = f"SELECT COUNT(*) FROM health_escalation_events {where}"
    data_sql = f"""
        SELECT id, occurred_at, channel, from_level, to_level, health_score, msg_per_min
        FROM health_escalation_events
        {where}
        ORDER BY occurred_at DESC
        LIMIT ? OFFSET ?
    """

    rows: list[dict] = []
    total = 0
    try:
        async with aiosqlite.connect(settings.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(count_sql, params) as cur:
                row = await cur.fetchone()
                total = row[0] if row else 0
            async with db.execute(data_sql, params + [limit, offset]) as cur:
                rows = [dict(r) async for r in cur]
    except Exception:
        logger.exception("Failed to fetch escalation history")
        return {"escalations": [], "total": 0, "error": "Database error"}

    # Apply direction filter in Python (avoids complex SQL CASE on text columns)
    if direction in ("worsening", "recovery"):
        filtered = []
        for row in rows:
            from_ord = _LEVEL_ORDER.get(row["from_level"], -1)
            to_ord = _LEVEL_ORDER.get(row["to_level"], -1)
            if direction == "worsening" and to_ord > from_ord:
                filtered.append(row)
            elif direction == "recovery" and to_ord < from_ord:
                filtered.append(row)
        rows = filtered
        total = len(rows)  # approximate after in-memory filter

    return {"escalations": rows, "total": total, "limit": limit, "offset": offset}


@router.get("/history/users")
async def get_user_history(
    hours: float = Query(default=24, ge=0.5, le=720, description="Activity window in hours"),
    channel: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None, description="Substring match on username"),
    flagged_only: bool = Query(default=False, description="Only users who appear in flagged_users"),
    sort_by: str = Query(default="message_count", description="message_count | reputation | total_flags | max_threat_score | last_seen"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """
    Aggregated user view: chatters seen in the time window enriched with
    reputation scores, flag counts, and peak threat scores.
    """
    cutoff = _time.time() - hours * 3600
    chan_filter = "AND m.channel = ?" if channel else "AND m.channel != '__sim__'"
    params: list = [cutoff]
    if channel:
        params.append(channel)

    search_clause = ""
    if search:
        search_clause = "AND LOWER(m.username) LIKE ?"
        params.append(f"%{search.lower()}%")

    flagged_clause = ""
    if flagged_only:
        flagged_clause = "AND m.user_id IN (SELECT DISTINCT user_id FROM flagged_users WHERE channel != '__sim__')"

    _SORT_COLS = {
        "message_count":   "message_count DESC",
        "reputation":      "reputation ASC",        # low rep = most suspicious first
        "total_flags":     "total_flags DESC",
        "max_threat_score":"max_threat_score DESC",
        "last_seen":       "last_seen DESC",
    }
    order = _SORT_COLS.get(sort_by, "message_count DESC")

    flagged_chan = "AND f.channel = ?" if channel else "AND f.channel != '__sim__'"
    # Params for messages WHERE clause, then repeated for flagged subquery
    msg_params = list(params)   # [cutoff, optional_channel]
    flag_params = [cutoff] + ([channel] if channel else [])

    count_sql = f"""
        SELECT COUNT(DISTINCT m.user_id)
        FROM messages m
        WHERE m.received_at >= ?
          {chan_filter}
          {search_clause}
          {flagged_clause}
    """
    data_sql = f"""
        SELECT
            m.user_id,
            MAX(m.username)           AS username,
            COUNT(m.id)               AS message_count,
            MIN(m.received_at)        AS first_seen,
            MAX(m.received_at)        AS last_seen,
            MAX(m.is_subscriber)      AS is_subscriber,
            MAX(m.is_moderator)       AS is_moderator,
            MAX(m.is_vip)             AS is_vip,
            MAX(m.account_age_days)   AS account_age_days,
            ROUND(AVG(m.char_count), 1) AS avg_msg_length,
            SUM(m.has_url)            AS url_msg_count,
            COALESCE(r.reputation, 100.0)    AS reputation,
            COALESCE(r.total_flags, 0)       AS total_flags,
            COALESCE(r.total_actions, 0)     AS total_actions,
            COALESCE(r.false_positives, 0)   AS false_positives,
            COALESCE(
                (SELECT COUNT(*) FROM flagged_users f
                 WHERE f.user_id = m.user_id AND f.flagged_at >= ? {flagged_chan}), 0
            ) AS recent_flags,
            COALESCE(
                (SELECT MAX(f2.threat_score) FROM flagged_users f2
                 WHERE f2.user_id = m.user_id AND f2.flagged_at >= ? {flagged_chan}), 0.0
            ) AS max_threat_score,
            (SELECT MAX(f3.flagged_at) FROM flagged_users f3
             WHERE f3.user_id = m.user_id AND f3.flagged_at >= ? {flagged_chan}) AS last_flagged,
            (SELECT f4.signals FROM flagged_users f4
             WHERE f4.user_id = m.user_id AND f4.flagged_at >= ? {flagged_chan}
             ORDER BY f4.flagged_at DESC LIMIT 1) AS last_signals
        FROM messages m
        LEFT JOIN user_reputation r ON r.user_id = m.user_id
        WHERE m.received_at >= ?
          {chan_filter}
          {search_clause}
          {flagged_clause}
        GROUP BY m.user_id, r.reputation, r.total_flags, r.total_actions, r.false_positives
        ORDER BY {order}
        LIMIT ? OFFSET ?
    """
    # Build full param list:
    # 4× (cutoff + optional channel) for the 4 flagged subqueries, then msg_params, then limit/offset
    flag_block = flag_params * 4
    full_params = flag_block + msg_params

    rows: list[dict] = []
    total = 0
    try:
        async with aiosqlite.connect(settings.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(count_sql, msg_params) as cur:
                row = await cur.fetchone()
                total = row[0] if row else 0
            async with db.execute(data_sql, full_params + [limit, offset]) as cur:
                rows = [dict(r) async for r in cur]
        for row in rows:
            try:
                row["last_signals"] = _json.loads(row["last_signals"]) if row.get("last_signals") else []
            except Exception:
                row["last_signals"] = []
    except Exception:
        logger.exception("Failed to fetch user history")
        return {"users": [], "total": 0, "error": "Database error"}

    return {"users": rows, "total": total, "limit": limit, "offset": offset}


@router.get("/history/clusters/{cluster_id}/messages")
async def get_cluster_messages(cluster_id: int) -> dict:
    """
    Return users and messages for a specific cluster detection event.

    Fetches the stored cluster_events row, then queries the messages table for
    all users in that cluster within a ±90-second window around detection time.
    Results are returned as a flat time-ordered message list plus a per-user
    message count summary.
    """
    cluster: dict | None = None
    try:
        async with aiosqlite.connect(settings.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM cluster_events WHERE id = ?", (cluster_id,)
            ) as cur:
                row = await cur.fetchone()
                if row:
                    cluster = dict(row)
    except Exception:
        logger.exception("Failed to look up cluster %s", cluster_id)
        return {"error": "Database error"}

    if cluster is None:
        return {"error": "Cluster not found", "messages": [], "users": []}

    try:
        user_ids: list[str] = _json.loads(cluster["user_ids"])
    except Exception:
        user_ids = []

    if not user_ids:
        return {"cluster": cluster, "messages": [], "users": []}

    detected_at: float = cluster["detected_at"]
    channel: str = cluster.get("channel") or ""
    window_start = detected_at - 90.0
    window_end   = detected_at + 90.0

    # Build IN clause dynamically — user_ids is bounded (stored from detection)
    placeholders = ",".join("?" * len(user_ids))
    msg_params: list = [window_start, window_end] + user_ids
    if channel:
        chan_clause = "AND m.channel = ?"
        msg_params.append(channel)
    else:
        chan_clause = ""

    messages: list[dict] = []
    try:
        async with aiosqlite.connect(settings.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                f"""
                SELECT m.id, m.received_at, m.user_id, m.username, m.raw_text,
                       m.emoji_count, m.url_count, m.has_url,
                       m.is_subscriber, m.is_moderator, m.is_vip,
                       m.account_age_days
                FROM messages m
                WHERE m.received_at BETWEEN ? AND ?
                  AND m.user_id IN ({placeholders})
                  {chan_clause}
                ORDER BY m.received_at ASC
                LIMIT 500
                """,
                msg_params,
            ) as cur:
                messages = [dict(r) async for r in cur]
    except Exception:
        logger.exception("Failed to fetch messages for cluster %s", cluster_id)
        return {"cluster": cluster, "messages": [], "users": []}

    # Build per-user summary from the fetched messages
    user_summary: dict[str, dict] = {}
    for msg in messages:
        uid = msg["user_id"]
        if uid not in user_summary:
            user_summary[uid] = {
                "user_id": uid,
                "username": msg["username"],
                "message_count": 0,
                "is_subscriber": msg["is_subscriber"],
                "is_moderator": msg["is_moderator"],
                "is_vip": msg["is_vip"],
                "account_age_days": msg["account_age_days"],
            }
        user_summary[uid]["message_count"] += 1

    # Include users from cluster who sent no messages in the window
    for uid in user_ids:
        if uid not in user_summary:
            user_summary[uid] = {
                "user_id": uid, "username": uid,
                "message_count": 0,
                "is_subscriber": 0, "is_moderator": 0, "is_vip": 0,
                "account_age_days": None,
            }

    users_sorted = sorted(
        user_summary.values(), key=lambda u: u["message_count"], reverse=True
    )
    cluster["user_ids"] = user_ids  # already parsed

    return {"cluster": cluster, "messages": messages, "users": users_sorted}
