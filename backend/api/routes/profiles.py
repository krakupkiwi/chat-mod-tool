"""
Lockdown Profile endpoints.

GET    /api/profiles              — list all profiles
POST   /api/profiles              — create a new profile
DELETE /api/profiles/{id}         — delete a profile
POST   /api/profiles/{id}/apply   — enqueue ModerationActions for every
                                    non-NULL mode setting in the profile

A lockdown profile is a named bundle of chat-mode settings (emote-only,
sub-only, unique-chat, slow-mode, followers-only) that can be applied in one
click.  Profiles with auto_on_raid=1 are applied automatically when the
backend detects an incoming raid via EventSub.

Each mode field can be:
  NULL  — don't touch this mode when applying the profile
  1     — enable the mode
  0     — disable the mode
"""

from __future__ import annotations

import logging
import time

import aiosqlite
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core.config import settings
from twitch.token_store import TOKEN_BROADCASTER_ID, token_store

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class ProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    auto_on_raid: bool = False
    emote_only: bool | None = None
    sub_only: bool | None = None
    unique_chat: bool | None = None
    slow_mode: bool | None = None
    slow_mode_wait_time: int | None = Field(default=None, ge=3, le=120)
    followers_only: bool | None = None
    followers_only_duration: int | None = Field(default=None, ge=0, le=43200)


def _row_to_dict(row) -> dict:
    return {
        "id": row[0],
        "name": row[1],
        "created_at": row[2],
        "auto_on_raid": bool(row[3]),
        "emote_only": row[4],
        "sub_only": row[5],
        "unique_chat": row[6],
        "slow_mode": row[7],
        "slow_mode_wait_time": row[8],
        "followers_only": row[9],
        "followers_only_duration": row[10],
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/profiles")
async def list_profiles():
    async with aiosqlite.connect(settings.db_path) as db:
        async with db.execute(
            """
            SELECT id, name, created_at, auto_on_raid,
                   emote_only, sub_only, unique_chat,
                   slow_mode, slow_mode_wait_time,
                   followers_only, followers_only_duration
            FROM lockdown_profiles
            ORDER BY created_at
            """
        ) as cursor:
            rows = await cursor.fetchall()
    return {"profiles": [_row_to_dict(r) for r in rows]}


@router.post("/profiles", status_code=201)
async def create_profile(body: ProfileCreate):
    now = time.time()
    async with aiosqlite.connect(settings.db_path) as db:
        cursor = await db.execute(
            """
            INSERT INTO lockdown_profiles
                (name, created_at, auto_on_raid,
                 emote_only, sub_only, unique_chat,
                 slow_mode, slow_mode_wait_time,
                 followers_only, followers_only_duration)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                body.name,
                now,
                int(body.auto_on_raid),
                None if body.emote_only is None else int(body.emote_only),
                None if body.sub_only is None else int(body.sub_only),
                None if body.unique_chat is None else int(body.unique_chat),
                None if body.slow_mode is None else int(body.slow_mode),
                body.slow_mode_wait_time,
                None if body.followers_only is None else int(body.followers_only),
                body.followers_only_duration,
            ),
        )
        await db.commit()
        profile_id = cursor.lastrowid

    return {"id": profile_id, "name": body.name}


@router.delete("/profiles/{profile_id}", status_code=204)
async def delete_profile(profile_id: int):
    async with aiosqlite.connect(settings.db_path) as db:
        result = await db.execute(
            "DELETE FROM lockdown_profiles WHERE id = ?", (profile_id,)
        )
        await db.commit()
        if result.rowcount == 0:
            raise HTTPException(404, "Profile not found")


@router.post("/profiles/{profile_id}/apply")
async def apply_profile(profile_id: int):
    """
    Enqueue a ModerationAction for every non-NULL mode in the profile.
    Returns the count of actions queued.
    """
    async with aiosqlite.connect(settings.db_path) as db:
        async with db.execute(
            """
            SELECT id, name, auto_on_raid,
                   emote_only, sub_only, unique_chat,
                   slow_mode, slow_mode_wait_time,
                   followers_only, followers_only_duration
            FROM lockdown_profiles WHERE id = ?
            """,
            (profile_id,),
        ) as cursor:
            row = await cursor.fetchone()

    if row is None:
        raise HTTPException(404, "Profile not found")

    import startup as main
    if main.moderation_engine is None:
        raise HTTPException(503, "ModerationEngine not ready")

    _, name, _, emote_only, sub_only, unique_chat, slow_mode, slow_wait, followers_only, followers_dur = row

    broadcaster_id = token_store.retrieve(TOKEN_BROADCASTER_ID) or ""
    channel = settings.default_channel
    engine = main.moderation_engine

    enqueued = _apply_profile_modes(
        engine=engine,
        broadcaster_id=broadcaster_id,
        channel=channel,
        triggered_by=f"profile:{profile_id}",
        emote_only=emote_only,
        sub_only=sub_only,
        unique_chat=unique_chat,
        slow_mode=slow_mode,
        slow_mode_wait_time=slow_wait,
        followers_only=followers_only,
        followers_only_duration=followers_dur,
    )

    logger.info("Lockdown profile %d (%r) applied: %d action(s) enqueued", profile_id, name, enqueued)
    return {"profile_id": profile_id, "name": name, "enqueued": enqueued, "dry_run": settings.dry_run}


# ---------------------------------------------------------------------------
# Shared helper — also called by the raid auto-trigger in startup.py
# ---------------------------------------------------------------------------

def _apply_profile_modes(
    *,
    engine,
    broadcaster_id: str,
    channel: str,
    triggered_by: str,
    emote_only,
    sub_only,
    unique_chat,
    slow_mode,
    slow_mode_wait_time,
    followers_only,
    followers_only_duration,
) -> int:
    """Enqueue ModerationActions for each non-NULL mode. Returns count enqueued."""
    from moderation.actions import ModerationAction

    _MODE_MAP = {
        "emote_only":    ("emote_only",    "emote_only_off"),
        "sub_only":      ("sub_only",       "sub_only_off"),
        "unique_chat":   ("unique_chat",    "unique_chat_off"),
        "slow_mode":     ("slow_mode",      "slow_mode_off"),
        "followers_only":("followers_only", "followers_only_off"),
    }

    modes = [
        ("emote_only",    emote_only,    None),
        ("sub_only",      sub_only,      None),
        ("unique_chat",   unique_chat,   None),
        ("slow_mode",     slow_mode,     slow_mode_wait_time),
        ("followers_only", followers_only, followers_only_duration),
    ]

    enqueued = 0
    for mode_name, value, duration in modes:
        if value is None:
            continue
        on_type, off_type = _MODE_MAP[mode_name]
        action_type = on_type if value else off_type
        action = ModerationAction(
            action_type=action_type,
            broadcaster_id=broadcaster_id,
            user_id="",
            username="[channel]",
            channel=channel,
            duration_seconds=duration if value else None,
            reason=f"Lockdown profile: {triggered_by}",
            triggered_by=triggered_by,
        )
        engine._enqueue(action)
        enqueued += 1

    return enqueued
