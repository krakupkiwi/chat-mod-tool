"""
AutoMod queue endpoints.

POST /api/automod/approve  — allow a held message through
POST /api/automod/deny     — block a held message

Both proxy to PATCH /helix/moderation/automod/message
Required scope: moderator:manage:automod
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from twitch.token_store import TOKEN_BROADCASTER_ID, token_store

logger = logging.getLogger(__name__)
router = APIRouter()


class AutomodDecisionRequest(BaseModel):
    message_id: str
    action: str = "ALLOW"   # ALLOW | DENY


def _get_helix():
    import startup as main
    if main.moderation_engine is None:
        raise HTTPException(503, "ModerationEngine not ready")
    return main.moderation_engine._executor._helix


@router.post("/automod/approve")
async def approve_automod_message(body: AutomodDecisionRequest):
    return await _resolve(body.message_id, "ALLOW")


@router.post("/automod/deny")
async def deny_automod_message(body: AutomodDecisionRequest):
    return await _resolve(body.message_id, "DENY")


async def _resolve(message_id: str, action: str) -> dict:
    broadcaster_id = token_store.retrieve(TOKEN_BROADCASTER_ID) or ""
    moderator_id = token_store.retrieve("twitch_bot_user_id") or broadcaster_id
    if not broadcaster_id:
        raise HTTPException(503, "Broadcaster ID not configured")

    helix = _get_helix()
    resp = await helix.post(
        "/moderation/automod/message",
        json={
            "broadcaster_id": broadcaster_id,
            "moderator_id": moderator_id,
            "msg_id": message_id,
            "action": action,
        },
    )
    if resp.status_code not in (200, 204):
        raise HTTPException(resp.status_code, f"Helix error: {resp.text[:200]}")
    return {"message_id": message_id, "action": action, "ok": True}
