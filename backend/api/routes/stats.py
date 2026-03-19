"""
Stats REST endpoints — analytics queries against stored data.

GET  /api/stats/health        — recent health_history rows for timeline bootstrap
GET  /api/stats/summary       — session totals and aggregates
GET  /api/stats/top_threats   — top N users by max threat score
GET  /api/stats/timeline      — hourly health score buckets
GET  /api/stats/export/flagged_users       — CSV or JSON export (supports ?channel=, ?fmt=, ?hours=)
GET  /api/stats/export/moderation_actions  — CSV or JSON export (supports ?channel=, ?fmt=, ?hours=)
GET  /api/data/info           — DB path, size, row counts per channel
POST /api/data/purge          — manually delete rows older than N days from selected tables
"""

from __future__ import annotations

import asyncio
import csv
import io
import json as _json
import os
import time
from typing import Optional

import aiosqlite
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from core.config import settings
from storage.analytics import run_analytics

router = APIRouter()


@router.get("/stats/health")
async def health_history(minutes: int = 60):
    """Return health score history for the last N minutes (max 1440)."""
    minutes = min(minutes, 1440)
    cutoff = time.time() - minutes * 60
    rows = []
    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT recorded_at, health_score, msg_per_min, active_users, duplicate_ratio
            FROM health_history
            WHERE recorded_at >= ?
            ORDER BY recorded_at ASC
            """,
            (cutoff,),
        ) as cursor:
            async for row in cursor:
                rows.append(dict(row))
    return {"points": rows}


@router.get("/stats/summary")
async def session_summary(hours: float = 24):
    """
    Aggregate stats for the last N hours (default 24, max 168).
    Returns totals for messages, unique chatters, flagged users, and
    moderation actions, plus average and minimum health score.
    """
    hours = min(hours, 168)
    cutoff = time.time() - hours * 3600

    query = f"""
    SELECT
        (SELECT COUNT(*)          FROM ids.messages          WHERE received_at >= {cutoff}) AS total_messages,
        (SELECT COUNT(DISTINCT user_id) FROM ids.messages    WHERE received_at >= {cutoff}) AS unique_users,
        (SELECT COUNT(*)          FROM ids.flagged_users      WHERE flagged_at  >= {cutoff}) AS flagged_count,
        (SELECT COUNT(*)          FROM ids.moderation_actions WHERE created_at  >= {cutoff}) AS actions_taken,
        (SELECT COUNT(*)          FROM ids.moderation_actions
         WHERE created_at >= {cutoff} AND action_type = 'ban')   AS bans,
        (SELECT COUNT(*)          FROM ids.moderation_actions
         WHERE created_at >= {cutoff} AND action_type = 'timeout') AS timeouts,
        (SELECT ROUND(AVG(health_score), 1) FROM ids.health_history WHERE recorded_at >= {cutoff}) AS avg_health,
        (SELECT ROUND(MIN(health_score), 1) FROM ids.health_history WHERE recorded_at >= {cutoff}) AS min_health
    """

    try:
        rows = await asyncio.to_thread(run_analytics, settings.db_path, query)
        return rows[0] if rows else {}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/stats/top_threats")
async def top_threats(limit: int = 20, hours: float = 24):
    """
    Return the top N users by maximum threat score seen in the last N hours.
    Includes username, max score, number of detections, and most recent signals.
    """
    limit = min(limit, 100)
    hours = min(hours, 168)
    cutoff = time.time() - hours * 3600

    query = f"""
    SELECT
        user_id,
        username,
        ROUND(MAX(threat_score), 1) AS max_score,
        COUNT(*)                    AS detections,
        MAX(signals)                AS last_signals,
        MAX(flagged_at)             AS last_seen
    FROM ids.flagged_users
    WHERE flagged_at >= {cutoff}
    GROUP BY user_id, username
    ORDER BY max_score DESC
    LIMIT {limit}
    """

    try:
        rows = await asyncio.to_thread(run_analytics, settings.db_path, query)
        return {"users": rows}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/stats/timeline")
async def health_timeline(hours: float = 24, bucket_minutes: int = 5):
    """
    Return health score bucketed into N-minute intervals for the last N hours.
    Suitable for longer-range charts (vs /stats/health which returns raw points).
    """
    hours = min(hours, 168)
    bucket_minutes = max(1, min(bucket_minutes, 60))
    cutoff = time.time() - hours * 3600
    bucket_seconds = bucket_minutes * 60

    query = f"""
    SELECT
        CAST(FLOOR(recorded_at / {bucket_seconds}) * {bucket_seconds} AS BIGINT) AS bucket_ts,
        ROUND(AVG(health_score), 1) AS avg_health,
        ROUND(MIN(health_score), 1) AS min_health,
        ROUND(AVG(msg_per_min),  1) AS avg_msg_per_min,
        ROUND(AVG(active_users), 1) AS avg_active_users
    FROM ids.health_history
    WHERE recorded_at >= {cutoff}
    GROUP BY bucket_ts
    ORDER BY bucket_ts ASC
    """

    try:
        rows = await asyncio.to_thread(run_analytics, settings.db_path, query)
        return {"bucket_minutes": bucket_minutes, "points": rows}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Session summary (per-stream report)
# ---------------------------------------------------------------------------

@router.get("/stats/session")
async def session_report(hours: float = 2):
    """
    Per-stream session report for the last N hours (default 2, max 48).
    Returns everything needed for the stats page summary card:
      - Totals: messages, unique chatters, flagged, bans, timeouts
      - Health: avg, min, max, and trend (first-half avg vs second-half avg)
      - Top 5 threatiest users
      - Most active signals
    """
    hours = min(hours, 48)
    cutoff = time.time() - hours * 3600
    mid = cutoff + (hours / 2) * 3600

    summary_q = f"""
    SELECT
        (SELECT COUNT(*)               FROM ids.messages          WHERE received_at >= {cutoff}) AS total_messages,
        (SELECT COUNT(DISTINCT user_id) FROM ids.messages          WHERE received_at >= {cutoff}) AS unique_users,
        (SELECT COUNT(*)               FROM ids.flagged_users      WHERE flagged_at  >= {cutoff}) AS total_flagged,
        (SELECT COUNT(*)               FROM ids.moderation_actions WHERE created_at  >= {cutoff} AND action_type='ban')     AS total_bans,
        (SELECT COUNT(*)               FROM ids.moderation_actions WHERE created_at  >= {cutoff} AND action_type='timeout') AS total_timeouts,
        (SELECT ROUND(AVG(health_score),1) FROM ids.health_history WHERE recorded_at >= {cutoff})       AS avg_health,
        (SELECT ROUND(MIN(health_score),1) FROM ids.health_history WHERE recorded_at >= {cutoff})       AS min_health,
        (SELECT ROUND(MAX(health_score),1) FROM ids.health_history WHERE recorded_at >= {cutoff})       AS max_health,
        (SELECT ROUND(AVG(health_score),1) FROM ids.health_history WHERE recorded_at >= {cutoff} AND recorded_at < {mid}) AS first_half_health,
        (SELECT ROUND(AVG(health_score),1) FROM ids.health_history WHERE recorded_at >= {mid})          AS second_half_health
    """

    top_q = f"""
    SELECT username, ROUND(MAX(threat_score),1) AS max_score, COUNT(*) AS detections
    FROM ids.flagged_users
    WHERE flagged_at >= {cutoff}
    GROUP BY user_id, username
    ORDER BY max_score DESC
    LIMIT 5
    """

    signals_q = f"""
    SELECT
        TRIM(signal, '"') AS signal,
        COUNT(*) AS count
    FROM (
        SELECT UNNEST(json_extract(signals, '$[*]')::VARCHAR[]) AS signal
        FROM ids.flagged_users
        WHERE flagged_at >= {cutoff}
    )
    GROUP BY signal
    ORDER BY count DESC
    LIMIT 10
    """

    try:
        summary, top_users, top_signals = await asyncio.gather(
            asyncio.to_thread(run_analytics, settings.db_path, summary_q),
            asyncio.to_thread(run_analytics, settings.db_path, top_q),
            asyncio.to_thread(run_analytics, settings.db_path, signals_q),
        )
        result = summary[0] if summary else {}
        fh = result.get("first_half_health")
        sh = result.get("second_half_health")
        result["health_trend"] = round(sh - fh, 1) if (fh is not None and sh is not None) else None
        result["hours"] = hours
        result["top_threats"] = top_users
        result["top_signals"] = top_signals
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# CSV exports
# ---------------------------------------------------------------------------

def _rows_to_csv(rows: list[dict]) -> str:
    if not rows:
        return ""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


@router.get("/stats/export/flagged_users")
async def export_flagged_users(
    hours: float = Query(default=168, le=8760),
    channel: Optional[str] = Query(default=None),
    fmt: str = Query(default="csv", pattern="^(csv|json)$"),
):
    """Download flagged_users as CSV or JSON (default: last 7 days, all channels)."""
    cutoff = time.time() - hours * 3600
    channel_clause = "AND channel = ?" if channel else ""
    params: tuple = (channel,) if channel else ()
    query = f"""
    SELECT
        flagged_at, user_id, username, channel,
        ROUND(threat_score, 2) AS threat_score, signals, status
    FROM ids.flagged_users
    WHERE flagged_at >= {cutoff}
    {channel_clause}
    ORDER BY flagged_at DESC
    """
    try:
        rows = await asyncio.to_thread(run_analytics, settings.db_path, query, params)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    if fmt == "json":
        return StreamingResponse(
            iter([_json.dumps(rows, indent=2)]),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=flagged_users.json"},
        )
    return StreamingResponse(
        iter([_rows_to_csv(rows)]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=flagged_users.csv"},
    )


@router.get("/stats/export/moderation_actions")
async def export_moderation_actions(
    hours: float = Query(default=168, le=8760),
    channel: Optional[str] = Query(default=None),
    fmt: str = Query(default="csv", pattern="^(csv|json)$"),
):
    """Download moderation_actions as CSV or JSON (default: last 7 days, all channels)."""
    cutoff = time.time() - hours * 3600
    channel_clause = "AND channel = ?" if channel else ""
    params: tuple = (channel,) if channel else ()
    query = f"""
    SELECT
        created_at, completed_at, user_id, username, channel,
        action_type, duration_seconds, reason, status, triggered_by,
        ROUND(confidence, 3) AS confidence, error_message
    FROM ids.moderation_actions
    WHERE created_at >= {cutoff}
    {channel_clause}
    ORDER BY created_at DESC
    """
    try:
        rows = await asyncio.to_thread(run_analytics, settings.db_path, query, params)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    if fmt == "json":
        return StreamingResponse(
            iter([_json.dumps(rows, indent=2)]),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=moderation_actions.json"},
        )
    return StreamingResponse(
        iter([_rows_to_csv(rows)]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=moderation_actions.csv"},
    )


@router.get("/data/info")
async def data_info() -> dict:
    """
    Return database file path, size, and per-channel row counts for key tables.
    Used by the Data Manager modal.
    """
    db_path = str(settings.db_path)
    try:
        db_size = os.path.getsize(db_path)
    except OSError:
        db_size = 0

    counts: dict = {"flagged_users": [], "moderation_actions": [], "messages": []}

    try:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row

            for table, ts_col in [
                ("flagged_users", "flagged_at"),
                ("moderation_actions", "created_at"),
                ("messages", "received_at"),
            ]:
                async with db.execute(
                    f"""
                    SELECT channel,
                           COUNT(*) AS total,
                           MAX({ts_col}) AS latest
                    FROM {table}
                    GROUP BY channel
                    ORDER BY total DESC
                    """
                ) as cursor:
                    counts[table] = [dict(r) async for r in cursor]
    except Exception:
        pass

    return {
        "db_path": db_path,
        "db_size_bytes": db_size,
        "counts": counts,
    }


# Table config: name → timestamp column
_PURGEABLE_TABLES = {
    "messages":           "received_at",
    "flagged_users":      "flagged_at",
    "moderation_actions": "created_at",
    "health_history":     "recorded_at",
}


@router.post("/data/purge")
async def purge_data(
    older_than_days: int = Query(ge=0, le=3650, description="Delete rows older than this many days. 0 = delete all rows."),
    tables: str = Query(
        default="flagged_users,moderation_actions",
        description="Comma-separated table names to purge",
    ),
    channel: Optional[str] = Query(default=None, description="Restrict purge to a specific channel (e.g. __sim__)"),
) -> dict:
    """
    Delete rows older than `older_than_days` from the specified tables.
    Pass older_than_days=0 to delete all rows regardless of age.
    Optionally restrict to a single channel.
    Returns counts of deleted rows per table.
    """
    import logging as _log
    _logger = _log.getLogger(__name__)

    requested = [t.strip() for t in tables.split(",") if t.strip()]
    invalid = [t for t in requested if t not in _PURGEABLE_TABLES]
    if invalid:
        raise HTTPException(status_code=400, detail=f"Unknown tables: {invalid}")

    delete_all = older_than_days == 0
    cutoff = 0.0 if delete_all else time.time() - older_than_days * 86400
    deleted: dict[str, int] = {}

    try:
        async with aiosqlite.connect(settings.db_path) as db:
            for table in requested:
                ts_col = _PURGEABLE_TABLES[table]
                if delete_all:
                    if channel:
                        cursor = await db.execute(
                            f"DELETE FROM {table} WHERE channel = ?", (channel,)
                        )
                    else:
                        cursor = await db.execute(f"DELETE FROM {table}")
                elif channel:
                    cursor = await db.execute(
                        f"DELETE FROM {table} WHERE {ts_col} < ? AND channel = ?",
                        (cutoff, channel),
                    )
                else:
                    cursor = await db.execute(
                        f"DELETE FROM {table} WHERE {ts_col} < ?", (cutoff,)
                    )
                deleted[table] = cursor.rowcount
            await db.commit()
        age_label = "all" if delete_all else f">{older_than_days}d"
        _logger.info("Manual purge (%s, channel=%s): %s", age_label, channel or "all", deleted)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    # Run a WAL checkpoint so the file size actually shrinks on disk
    try:
        async with aiosqlite.connect(settings.db_path) as db:
            await db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:
        pass

    try:
        new_size = os.path.getsize(settings.db_path)
    except OSError:
        new_size = 0

    return {"deleted": deleted, "db_size_bytes": new_size}


@router.post("/data/purge_sim")
async def purge_simulated_data() -> dict:
    """
    Delete all data tagged with channel='__sim__' (injected by the simulator).
    No age filter — removes everything regardless of when it was recorded.
    """
    import logging as _log
    _logger = _log.getLogger(__name__)

    deleted: dict[str, int] = {}
    try:
        async with aiosqlite.connect(settings.db_path) as db:
            for table, ts_col in _PURGEABLE_TABLES.items():
                cursor = await db.execute(
                    f"DELETE FROM {table} WHERE channel = '__sim__'"
                )
                deleted[table] = cursor.rowcount
            await db.commit()
        _logger.info("Simulated data purged: %s", deleted)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    try:
        async with aiosqlite.connect(settings.db_path) as db:
            await db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:
        pass

    try:
        new_size = os.path.getsize(settings.db_path)
    except OSError:
        new_size = 0

    return {"deleted": deleted, "db_size_bytes": new_size}
