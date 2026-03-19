"""
Nuke Tool endpoints.

POST /api/moderation/nuke/preview  — find users matching a phrase/regex in recent messages
POST /api/moderation/nuke/execute  — enqueue timeout/ban actions for matched users

"Nuke" = retroactive bulk action: take a phrase or pattern, find everyone who
said it in the last N seconds, and timeout or ban them all at once.

Safety: preview is always shown before execute. All actions go through the
transactional moderation queue and respect dry-run mode.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Literal

import aiosqlite
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core.config import settings
from twitch.token_store import TOKEN_BROADCASTER_ID, token_store

logger = logging.getLogger(__name__)
router = APIRouter()


class NukePreviewRequest(BaseModel):
    pattern: str                         # literal phrase or regex
    use_regex: bool = False
    lookback_seconds: int = Field(default=300, ge=10, le=86400)  # max 24h


class NukeExecuteRequest(BaseModel):
    pattern: str
    use_regex: bool = False
    lookback_seconds: int = Field(default=300, ge=10, le=86400)
    action: Literal["timeout", "ban"] = "timeout"
    duration_seconds: int = Field(default=300, ge=1, le=1_209_600)
    reason: str = "Nuke: bulk moderation action"


class NukeTarget(BaseModel):
    user_id: str
    username: str
    match_count: int
    sample_message: str


def _compile(pattern: str, use_regex: bool) -> re.Pattern:
    try:
        if use_regex:
            return re.compile(pattern, re.IGNORECASE)
        else:
            return re.compile(re.escape(pattern), re.IGNORECASE)
    except re.error as e:
        raise HTTPException(400, f"Invalid pattern: {e}") from e


async def _find_targets(pattern: str, use_regex: bool, lookback_seconds: int) -> list[NukeTarget]:
    regex = _compile(pattern, use_regex)
    since = time.time() - lookback_seconds

    # Aggregate: user_id → (username, match_count, sample)
    user_hits: dict[str, dict] = {}
    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT user_id, username, raw_text FROM messages WHERE received_at >= ? ORDER BY received_at DESC",
            (since,),
        ) as cursor:
            async for row in cursor:
                if regex.search(row["raw_text"]):
                    uid = row["user_id"]
                    if uid not in user_hits:
                        user_hits[uid] = {
                            "username": row["username"],
                            "match_count": 0,
                            "sample_message": row["raw_text"],
                        }
                    user_hits[uid]["match_count"] += 1

    return [
        NukeTarget(
            user_id=uid,
            username=info["username"],
            match_count=info["match_count"],
            sample_message=info["sample_message"][:200],
        )
        for uid, info in user_hits.items()
    ]


def _get_moderation_engine():
    import startup as main
    if main.moderation_engine is None:
        raise HTTPException(503, "ModerationEngine not ready")
    return main.moderation_engine


@router.post("/moderation/nuke/preview")
async def nuke_preview(body: NukePreviewRequest):
    targets = await _find_targets(body.pattern, body.use_regex, body.lookback_seconds)
    return {
        "pattern": body.pattern,
        "use_regex": body.use_regex,
        "lookback_seconds": body.lookback_seconds,
        "target_count": len(targets),
        "targets": [t.model_dump() for t in targets],
    }


@router.post("/moderation/nuke/execute")
async def nuke_execute(body: NukeExecuteRequest):
    targets = await _find_targets(body.pattern, body.use_regex, body.lookback_seconds)
    if not targets:
        return {"enqueued": 0, "dry_run": settings.dry_run}

    broadcaster_id = token_store.retrieve(TOKEN_BROADCASTER_ID) or ""
    engine = _get_moderation_engine()
    channel = settings.default_channel

    from moderation.actions import ModerationAction
    enqueued = 0
    for target in targets:
        action = ModerationAction(
            action_type=body.action,
            broadcaster_id=broadcaster_id,
            user_id=target.user_id,
            username=target.username,
            channel=channel,
            duration_seconds=body.duration_seconds if body.action == "timeout" else None,
            reason=body.reason[:500],
            triggered_by="manual:nuke",
        )
        engine._enqueue(action)
        enqueued += 1

    logger.info(
        "Nuke executed: pattern=%r action=%s targets=%d dry_run=%s",
        body.pattern, body.action, enqueued, settings.dry_run,
    )
    return {
        "enqueued": enqueued,
        "action": body.action,
        "dry_run": settings.dry_run,
        "targets": [t.model_dump() for t in targets],
    }
