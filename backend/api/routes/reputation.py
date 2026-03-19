"""
GET /api/reputation — paginated list of user reputation records for the dashboard.

Returns users ordered by reputation score ascending (worst first) so the
dashboard can surface repeat offenders at the top of the list.
"""

from __future__ import annotations

import logging

import aiosqlite
from fastapi import APIRouter, Query

from core.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)

# Computed on read — not stored — to keep the DB schema simple.
# Non-linear so the curve drops steeply below 50:
#   rep=100 → 0.00,  rep=80 → 0.13,  rep=50 → 0.34,  rep=27 → 0.57,  rep=0 → 1.00
def _bot_probability(reputation: float) -> float:
    return round(1.0 - (max(reputation, 0.0) / 100.0) ** 0.6, 3)


def _behavior_flags(row: dict) -> list[str]:
    flags = []
    if row["total_flags"] >= 3:
        flags.append("repeat_offender")
    if row["total_actions"] >= 1:
        flags.append("previously_actioned")
    if row["false_positives"] >= 1:
        flags.append("has_false_positives")
    return flags


@router.get("/reputation")
async def list_reputation(
    min_score: float = Query(default=0.0, ge=0.0, le=100.0),
    max_score: float = Query(default=100.0, ge=0.0, le=100.0),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """
    Return paginated reputation records, worst first.

    Query params:
      min_score  — lower bound (default 0)
      max_score  — upper bound (default 100)
      limit      — page size (max 200)
      offset     — pagination offset
    """
    try:
        async with aiosqlite.connect(settings.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT
                    user_id,
                    username,
                    reputation,
                    total_flags,
                    total_actions,
                    false_positives,
                    last_seen,
                    updated_at
                FROM user_reputation
                WHERE reputation BETWEEN ? AND ?
                ORDER BY reputation ASC, total_flags DESC
                LIMIT ? OFFSET ?
                """,
                (min_score, max_score, limit, offset),
            ) as cursor:
                rows = await cursor.fetchall()

            async with db.execute(
                "SELECT COUNT(*) FROM user_reputation WHERE reputation BETWEEN ? AND ?",
                (min_score, max_score),
            ) as cursor:
                total_row = await cursor.fetchone()
            total = total_row[0] if total_row else 0

    except Exception:
        logger.exception("GET /api/reputation failed")
        return {"users": [], "total": 0, "limit": limit, "offset": offset}

    users = []
    for row in rows:
        r = dict(row)
        users.append({
            "user_id":         r["user_id"],
            "username":        r["username"],
            "reputation_score": round(r["reputation"], 1),
            "bot_probability":  _bot_probability(r["reputation"]),
            "behavior_flags":   _behavior_flags(r),
            "stats": {
                "total_flags":    r["total_flags"],
                "total_actions":  r["total_actions"],
                "false_positives": r["false_positives"],
            },
            "last_activity":   r["last_seen"],
        })

    return {
        "users":  users,
        "total":  total,
        "limit":  limit,
        "offset": offset,
    }
