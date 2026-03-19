"""
Regex filter CRUD endpoints.

GET    /api/filters/regex           — list all filters
POST   /api/filters/regex           — create a new filter
DELETE /api/filters/regex/{id}      — delete a filter
PATCH  /api/filters/regex/{id}      — update a filter
POST   /api/filters/regex/test      — test a pattern against recent messages
"""

from __future__ import annotations

import logging
import re
import time

import aiosqlite
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from core.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

_VALID_ACTIONS = {"delete", "timeout", "flag"}


class CreateFilterRequest(BaseModel):
    pattern: str
    flags: str = "i"
    action_type: str = "delete"
    duration_seconds: int | None = None
    note: str = ""

    @field_validator("pattern")
    @classmethod
    def validate_pattern(cls, v: str) -> str:
        try:
            re.compile(v)
        except re.error as e:
            raise ValueError(f"Invalid regex: {e}") from e
        return v

    @field_validator("action_type")
    @classmethod
    def validate_action(cls, v: str) -> str:
        if v not in _VALID_ACTIONS:
            raise ValueError(f"action_type must be one of {_VALID_ACTIONS}")
        return v


class UpdateFilterRequest(BaseModel):
    pattern: str | None = None
    flags: str | None = None
    action_type: str | None = None
    duration_seconds: int | None = None
    note: str | None = None
    enabled: bool | None = None

    @field_validator("pattern")
    @classmethod
    def validate_pattern(cls, v: str | None) -> str | None:
        if v is not None:
            try:
                re.compile(v)
            except re.error as e:
                raise ValueError(f"Invalid regex: {e}") from e
        return v


class TestFilterRequest(BaseModel):
    pattern: str
    flags: str = "i"
    lookback_seconds: int = 300   # test against last N seconds of messages


async def _reload_engine() -> None:
    from detection.fast.regex_filter import regex_filter_engine
    if regex_filter_engine is not None:
        await regex_filter_engine.reload()


@router.get("/filters/regex")
async def list_filters():
    rows = []
    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, pattern, flags, action_type, duration_seconds, note, enabled, created_at, match_count "
            "FROM regex_filters ORDER BY created_at DESC"
        ) as cursor:
            async for row in cursor:
                rows.append(dict(row))
    return {"filters": rows}


@router.post("/filters/regex", status_code=201)
async def create_filter(body: CreateFilterRequest):
    async with aiosqlite.connect(settings.db_path) as db:
        cursor = await db.execute(
            """
            INSERT INTO regex_filters (pattern, flags, action_type, duration_seconds, note, enabled, created_at)
            VALUES (?, ?, ?, ?, ?, 1, ?)
            """,
            (body.pattern, body.flags, body.action_type, body.duration_seconds, body.note, time.time()),
        )
        await db.commit()
        filter_id = cursor.lastrowid
    await _reload_engine()
    return {"id": filter_id, "created": True}


@router.delete("/filters/regex/{filter_id}")
async def delete_filter(filter_id: int):
    async with aiosqlite.connect(settings.db_path) as db:
        cursor = await db.execute("DELETE FROM regex_filters WHERE id=?", (filter_id,))
        await db.commit()
        if cursor.rowcount == 0:
            raise HTTPException(404, f"Filter {filter_id} not found")
    await _reload_engine()
    return {"id": filter_id, "deleted": True}


@router.patch("/filters/regex/{filter_id}")
async def update_filter(filter_id: int, body: UpdateFilterRequest):
    fields = []
    values = []
    for field, val in body.model_dump(exclude_none=True).items():
        fields.append(f"{field}=?")
        values.append(val)
    if not fields:
        raise HTTPException(400, "No fields to update")
    values.append(filter_id)
    async with aiosqlite.connect(settings.db_path) as db:
        cursor = await db.execute(
            f"UPDATE regex_filters SET {', '.join(fields)} WHERE id=?", values
        )
        await db.commit()
        if cursor.rowcount == 0:
            raise HTTPException(404, f"Filter {filter_id} not found")
    await _reload_engine()
    return {"id": filter_id, "updated": True}


@router.post("/filters/regex/test")
async def test_filter(body: TestFilterRequest):
    """Test a regex pattern against recent messages from the DB."""
    try:
        flags = re.IGNORECASE if "i" in body.flags else 0
        pattern = re.compile(body.pattern, flags)
    except re.error as e:
        raise HTTPException(400, f"Invalid regex: {e}")

    since = time.time() - body.lookback_seconds
    matches = []
    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT username, raw_text, received_at FROM messages WHERE received_at >= ? ORDER BY received_at DESC LIMIT 500",
            (since,),
        ) as cursor:
            async for row in cursor:
                if pattern.search(row["raw_text"]):
                    matches.append({
                        "username": row["username"],
                        "text": row["raw_text"],
                        "received_at": row["received_at"],
                    })
                    if len(matches) >= 50:
                        break
    return {
        "pattern": body.pattern,
        "match_count": len(matches),
        "matches": matches,
        "lookback_seconds": body.lookback_seconds,
    }
