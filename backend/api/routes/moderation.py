"""
Moderation REST endpoints.

POST /api/moderation/ban              — manually ban a user
POST /api/moderation/timeout          — manually timeout a user
POST /api/moderation/undo/{id}        — undo a completed action
GET  /api/moderation/history          — recent moderation_actions rows
POST /api/moderation/chat-mode        — toggle a channel chat mode
POST /api/moderation/cluster/timeout  — timeout all users in a detected cluster
POST /api/moderation/cluster/ban      — ban all users in a detected cluster
"""

from __future__ import annotations

import logging
from typing import Literal

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core.config import settings
from twitch.token_store import TOKEN_BROADCASTER_ID, token_store

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_moderation_engine():
    """Dependency: returns the live ModerationEngine instance."""
    import startup as main
    if main.moderation_engine is None:
        raise HTTPException(503, "ModerationEngine not ready")
    return main.moderation_engine


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class BanRequest(BaseModel):
    user_id: str
    username: str
    reason: str = "Manual ban"


class TimeoutRequest(BaseModel):
    user_id: str
    username: str
    duration_seconds: int = Field(default=60, ge=1, le=1_209_600)
    reason: str = "Manual timeout"


class WarnRequest(BaseModel):
    user_id: str
    username: str
    reason: str = "Warned by moderator"


ChatModeType = Literal[
    "emote_only", "sub_only", "unique_chat", "slow_mode", "followers_only"
]


class ChatModeRequest(BaseModel):
    mode: ChatModeType
    enabled: bool
    # slow_mode: seconds (3–120); followers_only: minutes (0 = any follower)
    duration: int | None = None


class ClusterActionRequest(BaseModel):
    cluster_id: str
    user_ids: list[str]
    usernames: dict[str, str] = {}   # user_id → username (best-effort)
    duration_seconds: int = Field(default=300, ge=1, le=1_209_600)
    reason: str = "Coordinated bot cluster"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/moderation/ban")
async def manual_ban(
    body: BanRequest,
    engine=Depends(_get_moderation_engine),
):
    broadcaster_id = token_store.retrieve(TOKEN_BROADCASTER_ID) or ""
    channel = settings.default_channel
    action = await engine.manual_ban(
        user_id=body.user_id,
        username=body.username,
        channel=channel,
        broadcaster_id=broadcaster_id,
        reason=body.reason,
    )
    return {
        "action_id": action.action_id,
        "status": action.status,
        "dry_run": settings.dry_run,
    }


@router.post("/moderation/timeout")
async def manual_timeout(
    body: TimeoutRequest,
    engine=Depends(_get_moderation_engine),
):
    broadcaster_id = token_store.retrieve(TOKEN_BROADCASTER_ID) or ""
    channel = settings.default_channel
    action = await engine.manual_timeout(
        user_id=body.user_id,
        username=body.username,
        channel=channel,
        broadcaster_id=broadcaster_id,
        duration_seconds=body.duration_seconds,
        reason=body.reason,
    )
    return {
        "action_id": action.action_id,
        "status": action.status,
        "duration_seconds": body.duration_seconds,
        "dry_run": settings.dry_run,
    }


@router.post("/moderation/warn")
async def manual_warn(
    body: WarnRequest,
    engine=Depends(_get_moderation_engine),
):
    broadcaster_id = token_store.retrieve(TOKEN_BROADCASTER_ID) or ""
    channel = settings.default_channel
    action = await engine.manual_warn(
        user_id=body.user_id,
        username=body.username,
        channel=channel,
        broadcaster_id=broadcaster_id,
        reason=body.reason,
    )
    return {
        "action_id": action.action_id,
        "status": action.status,
        "dry_run": settings.dry_run,
    }


@router.post("/moderation/undo/{db_id}")
async def undo_action(
    db_id: int,
    engine=Depends(_get_moderation_engine),
):
    success = await engine.undo_action(db_id)
    if not success:
        raise HTTPException(400, f"Could not undo action {db_id}")
    return {"db_id": db_id, "undone": True}


@router.post("/moderation/chat-mode")
async def set_chat_mode(
    body: ChatModeRequest,
    engine=Depends(_get_moderation_engine),
):
    """Toggle a channel chat mode on or off."""
    from moderation.actions import ModerationAction
    broadcaster_id = token_store.retrieve(TOKEN_BROADCASTER_ID) or ""
    channel = settings.default_channel

    # Map (mode, enabled) → action_type
    action_type_map = {
        ("emote_only", True):  "emote_only",
        ("emote_only", False): "emote_only_off",
        ("sub_only", True):    "sub_only",
        ("sub_only", False):   "sub_only_off",
        ("unique_chat", True): "unique_chat",
        ("unique_chat", False):"unique_chat_off",
        ("slow_mode", True):   "slow_mode",
        ("slow_mode", False):  "slow_mode_off",
        ("followers_only", True):  "followers_only",
        ("followers_only", False): "followers_only_off",
    }
    action_type = action_type_map[(body.mode, body.enabled)]

    action = ModerationAction(
        action_type=action_type,
        broadcaster_id=broadcaster_id,
        user_id="",
        username="[channel]",
        channel=channel,
        duration_seconds=body.duration,
        reason=f"Manual chat mode: {action_type}",
        triggered_by="manual",
    )
    engine._enqueue(action)
    return {
        "action_id": action.action_id,
        "action_type": action_type,
        "dry_run": settings.dry_run,
    }


@router.post("/moderation/cluster/timeout")
async def cluster_timeout(
    body: ClusterActionRequest,
    engine=Depends(_get_moderation_engine),
):
    """Timeout all users in a detected bot cluster."""
    broadcaster_id = token_store.retrieve(TOKEN_BROADCASTER_ID) or ""
    channel = settings.default_channel
    enqueued = await engine.timeout_cluster(
        cluster_user_ids=body.user_ids,
        usernames=body.usernames,
        channel=channel,
        broadcaster_id=broadcaster_id,
        duration_seconds=body.duration_seconds,
        reason=body.reason,
    )
    return {"enqueued": enqueued, "cluster_id": body.cluster_id, "dry_run": settings.dry_run}


@router.post("/moderation/cluster/ban")
async def cluster_ban(
    body: ClusterActionRequest,
    engine=Depends(_get_moderation_engine),
):
    """Ban all users in a detected bot cluster (dual-signal gate bypassed for manual actions)."""
    from moderation.actions import ModerationAction
    broadcaster_id = token_store.retrieve(TOKEN_BROADCASTER_ID) or ""
    channel = settings.default_channel
    enqueued = 0
    for uid in body.user_ids:
        uname = body.usernames.get(uid, uid)
        action = ModerationAction(
            action_type="ban",
            broadcaster_id=broadcaster_id,
            user_id=uid,
            username=uname,
            channel=channel,
            reason=body.reason,
            triggered_by="manual:cluster_ban",
        )
        engine._enqueue(action)
        enqueued += 1
    return {"enqueued": enqueued, "cluster_id": body.cluster_id, "dry_run": settings.dry_run}


@router.get("/moderation/chat-settings")
async def get_chat_settings():
    """Proxy GET /helix/chat/settings so the frontend can read current modes without CORS."""
    broadcaster_id = token_store.retrieve(TOKEN_BROADCASTER_ID) or ""
    if not broadcaster_id:
        raise HTTPException(503, "Broadcaster ID not configured")
    import startup as main
    if main.moderation_engine is None:
        raise HTTPException(503, "ModerationEngine not ready")
    helix = main.moderation_engine._executor._helix
    resp = await helix.get(
        "/chat/settings",
        params={"broadcaster_id": broadcaster_id, "moderator_id": broadcaster_id},
    )
    if resp.status_code != 200:
        raise HTTPException(resp.status_code, "Helix error")
    data = resp.json().get("data", [{}])[0]
    return {
        "emote_mode":        data.get("emote_mode", False),
        "subscriber_mode":   data.get("subscriber_mode", False),
        "unique_chat_mode":  data.get("unique_chat_mode", False),
        "slow_mode":         data.get("slow_mode", False),
        "slow_mode_wait_time": data.get("slow_mode_wait_time"),
        "follower_mode":     data.get("follower_mode", False),
        "follower_mode_duration": data.get("follower_mode_duration"),
    }


@router.get("/moderation/history")
async def moderation_history(limit: int = 100):
    rows = []
    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, created_at, completed_at, user_id, username, channel,
                   action_type, duration_seconds, reason, status,
                   triggered_by, confidence, error_message
            FROM moderation_actions
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ) as cursor:
            async for row in cursor:
                rows.append(dict(row))
    return {"actions": rows}
