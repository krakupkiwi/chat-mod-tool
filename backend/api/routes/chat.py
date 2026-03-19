"""
Chat REST endpoints.

POST /api/chat/send  — send a chat message to the monitored channel
"""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core.config import settings
from twitch.token_store import (
    TOKEN_ACCESS,
    TOKEN_BROADCASTER_ID,
    TOKEN_CLIENT_ID,
    token_store,
)
from twitch import manager as twitch_manager

logger = logging.getLogger(__name__)
router = APIRouter()

_HELIX_CHAT_MESSAGES = "https://api.twitch.tv/helix/chat/messages"
_MAX_MESSAGE_LENGTH = 500  # Twitch hard limit


class SendMessageRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=_MAX_MESSAGE_LENGTH)


@router.post("/chat/send")
async def send_chat_message(body: SendMessageRequest) -> dict:
    """
    Send a chat message to the configured channel on behalf of the authenticated user.
    Requires a valid access token with chat:write scope.
    """
    access_token = (
        (c := twitch_manager.get_client()) and c.get_live_access_token()
    ) or token_store.retrieve(TOKEN_ACCESS)

    client_id = token_store.retrieve(TOKEN_CLIENT_ID) or settings.client_id
    broadcaster_id = token_store.retrieve(TOKEN_BROADCASTER_ID)

    # sender_id is the authenticated user's numeric ID (same account in single-account setup)
    client = twitch_manager.get_client()
    sender_id = (client._bot_user_id if client else None) or broadcaster_id

    if not access_token or not client_id or not broadcaster_id or not sender_id:
        raise HTTPException(503, "Twitch not connected — cannot send message")

    payload = {
        "broadcaster_id": broadcaster_id,
        "sender_id": sender_id,
        "message": body.message,
    }
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Client-Id": client_id,
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=10) as http:
        resp = await http.post(_HELIX_CHAT_MESSAGES, json=payload, headers=headers)

    if resp.status_code not in (200, 204):
        logger.warning(
            "send_chat_message failed: status=%d body=%s", resp.status_code, resp.text[:200]
        )
        raise HTTPException(502, f"Twitch API error: {resp.status_code}")

    logger.info("Chat message sent to #%s", settings.default_channel)
    return {"ok": True}
