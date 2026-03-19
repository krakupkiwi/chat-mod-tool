"""
User watchlist endpoints — flag users for heightened monitoring with notes.

GET    /api/watchlist          — list all watched users
POST   /api/watchlist          — add a user to the watchlist
DELETE /api/watchlist/{user_id} — remove from watchlist
PATCH  /api/watchlist/{user_id} — update note/priority
"""

from __future__ import annotations

import logging
import time

import aiosqlite
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()


class AddWatchRequest(BaseModel):
    user_id: str
    username: str
    note: str = ""
    priority: str = "normal"   # normal | high


class UpdateWatchRequest(BaseModel):
    note: str | None = None
    priority: str | None = None


@router.get("/watchlist")
async def list_watchlist():
    rows = []
    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT user_id, username, added_at, note, priority FROM user_watchlist ORDER BY added_at DESC"
        ) as cursor:
            async for row in cursor:
                rows.append(dict(row))
    return {"watched": rows, "total": len(rows)}


@router.post("/watchlist", status_code=201)
async def add_to_watchlist(body: AddWatchRequest):
    async with aiosqlite.connect(settings.db_path) as db:
        await db.execute(
            """
            INSERT INTO user_watchlist (user_id, username, added_at, note, priority)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username,
                note=excluded.note,
                priority=excluded.priority
            """,
            (body.user_id, body.username.lower(), time.time(), body.note, body.priority),
        )
        await db.commit()
    return {"user_id": body.user_id, "watching": True}


@router.delete("/watchlist/{user_id}")
async def remove_from_watchlist(user_id: str):
    async with aiosqlite.connect(settings.db_path) as db:
        cursor = await db.execute(
            "DELETE FROM user_watchlist WHERE user_id=?", (user_id,)
        )
        await db.commit()
        if cursor.rowcount == 0:
            raise HTTPException(404, f"User {user_id} not on watchlist")
    return {"user_id": user_id, "watching": False}


@router.patch("/watchlist/{user_id}")
async def update_watchlist_entry(user_id: str, body: UpdateWatchRequest):
    if body.note is None and body.priority is None:
        raise HTTPException(400, "Provide at least one field to update")
    async with aiosqlite.connect(settings.db_path) as db:
        if body.note is not None and body.priority is not None:
            await db.execute(
                "UPDATE user_watchlist SET note=?, priority=? WHERE user_id=?",
                (body.note, body.priority, user_id),
            )
        elif body.note is not None:
            await db.execute(
                "UPDATE user_watchlist SET note=? WHERE user_id=?",
                (body.note, user_id),
            )
        else:
            await db.execute(
                "UPDATE user_watchlist SET priority=? WHERE user_id=?",
                (body.priority, user_id),
            )
        await db.commit()
    return {"user_id": user_id, "updated": True}


@router.get("/watchlist/{user_id}")
async def get_watchlist_entry(user_id: str):
    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT user_id, username, added_at, note, priority FROM user_watchlist WHERE user_id=?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
    if row is None:
        return {"watching": False}
    return {"watching": True, **dict(row)}
