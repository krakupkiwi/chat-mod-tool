"""
Whitelist REST endpoints.

GET    /api/config/whitelist          — list all whitelisted usernames
POST   /api/config/whitelist          — add a username
DELETE /api/config/whitelist/{username} — remove a username

All changes are persisted to SQLite and applied immediately to the live
ProtectedAccountChecker instance in the running DetectionEngine.
"""

from __future__ import annotations

import logging
import time

import aiosqlite
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_protection():
    """Return the live ProtectedAccountChecker from the running DetectionEngine."""
    import startup as main
    if main.detection_engine is None:
        raise HTTPException(503, "DetectionEngine not ready")
    return main.detection_engine.protection


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class WhitelistEntry(BaseModel):
    username: str
    added_at: float
    note: str


class AddWhitelistRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    note: str = Field(default="", max_length=128)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/config/whitelist", response_model=list[WhitelistEntry])
async def list_whitelist() -> list[WhitelistEntry]:
    """Return all manually whitelisted usernames."""
    rows: list[WhitelistEntry] = []
    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT username, added_at, note FROM whitelist ORDER BY added_at DESC"
        ) as cursor:
            async for row in cursor:
                rows.append(WhitelistEntry(
                    username=row["username"],
                    added_at=row["added_at"],
                    note=row["note"],
                ))
    return rows


@router.post("/config/whitelist", response_model=WhitelistEntry, status_code=201)
async def add_to_whitelist(body: AddWhitelistRequest) -> WhitelistEntry:
    """Add a username to the protection whitelist."""
    username = body.username.strip().lstrip("#").lower()
    if not username:
        raise HTTPException(400, "Username cannot be empty")

    now = time.time()
    async with aiosqlite.connect(settings.db_path) as db:
        await db.execute(
            "INSERT OR REPLACE INTO whitelist (username, added_at, note) VALUES (?, ?, ?)",
            (username, now, body.note),
        )
        await db.commit()

    # Apply to live engine immediately
    protection = _get_protection()
    protection.add_to_whitelist(username)
    logger.info("Whitelist: added %r (note=%r)", username, body.note)

    return WhitelistEntry(username=username, added_at=now, note=body.note)


@router.delete("/config/whitelist/{username}", status_code=200)
async def remove_from_whitelist(username: str) -> dict:
    """Remove a username from the protection whitelist."""
    username = username.strip().lower()
    async with aiosqlite.connect(settings.db_path) as db:
        cursor = await db.execute(
            "DELETE FROM whitelist WHERE username = ?", (username,)
        )
        await db.commit()
        if cursor.rowcount == 0:
            raise HTTPException(404, f"{username!r} not in whitelist")

    # Apply to live engine immediately
    protection = _get_protection()
    protection.remove_from_whitelist(username)
    logger.info("Whitelist: removed %r", username)

    return {"removed": username}
