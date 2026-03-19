"""
Unban request endpoints.

GET  /api/unban-requests          — list pending requests from Helix
POST /api/unban-requests/{id}/approve  — approve with optional resolution text
POST /api/unban-requests/{id}/deny     — deny with optional resolution text
GET  /api/unban-requests/history       — local decision log from SQLite

Helix API: GET /moderation/unban_requests, PATCH /moderation/unban_requests/resolve
Required scope: moderator:manage:unban_requests
"""

from __future__ import annotations

import logging
import time

import aiosqlite
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.config import settings
from twitch.token_store import TOKEN_BROADCASTER_ID, token_store

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_helix():
    import startup as main
    if main.moderation_engine is None:
        raise HTTPException(503, "ModerationEngine not ready")
    return main.moderation_engine._executor._helix


def _get_ids() -> tuple[str, str]:
    broadcaster_id = token_store.retrieve(TOKEN_BROADCASTER_ID) or ""
    moderator_id = token_store.retrieve("twitch_bot_user_id") or broadcaster_id
    if not broadcaster_id:
        raise HTTPException(503, "Broadcaster ID not configured")
    return broadcaster_id, moderator_id


class ResolveRequest(BaseModel):
    resolution_text: str = ""
    # Metadata for local history — filled by the client
    user_id: str = ""
    username: str = ""
    request_text: str = ""


@router.get("/unban-requests")
async def list_unban_requests(status: str = "pending"):
    """Proxy GET /helix/moderation/unban_requests."""
    broadcaster_id, moderator_id = _get_ids()
    helix = _get_helix()
    resp = await helix.get(
        "/moderation/unban_requests",
        params={
            "broadcaster_id": broadcaster_id,
            "moderator_id": moderator_id,
            "status": status,
            "first": 20,
        },
    )
    if resp.status_code in (401, 403):
        raise HTTPException(403, "missing_scope:moderator:manage:unban_requests")
    if resp.status_code != 200:
        raise HTTPException(resp.status_code, f"Helix error: {resp.text[:200]}")
    data = resp.json()
    return {"requests": data.get("data", []), "total": len(data.get("data", []))}


@router.post("/unban-requests/{request_id}/approve")
async def approve_unban_request(request_id: str, body: ResolveRequest):
    return await _resolve(request_id, "approved", body)


@router.post("/unban-requests/{request_id}/deny")
async def deny_unban_request(request_id: str, body: ResolveRequest):
    return await _resolve(request_id, "denied", body)


async def _resolve(request_id: str, decision: str, body: ResolveRequest) -> dict:
    broadcaster_id, moderator_id = _get_ids()
    helix = _get_helix()
    resp = await helix.patch(
        "/moderation/unban_requests/resolve",
        params={"broadcaster_id": broadcaster_id, "moderator_id": moderator_id},
        json={
            "data": {
                "unban_request_id": request_id,
                "status": decision,
                "resolution_text": body.resolution_text[:500],
            }
        },
    )
    if resp.status_code not in (200, 204):
        raise HTTPException(resp.status_code, f"Helix error: {resp.text[:200]}")

    # Log decision locally
    async with aiosqlite.connect(settings.db_path) as db:
        await db.execute(
            """
            INSERT INTO unban_decisions
                (unban_request_id, user_id, username, request_text, decision, resolution_text, decided_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(unban_request_id) DO UPDATE SET
                decision=excluded.decision,
                resolution_text=excluded.resolution_text,
                decided_at=excluded.decided_at
            """,
            (
                request_id,
                body.user_id,
                body.username,
                body.request_text,
                decision,
                body.resolution_text,
                time.time(),
            ),
        )
        await db.commit()

    return {"request_id": request_id, "decision": decision, "ok": True}


@router.get("/unban-requests/history")
async def unban_history(limit: int = 50):
    rows = []
    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT unban_request_id, user_id, username, request_text,
                   decision, resolution_text, decided_at
            FROM unban_decisions
            ORDER BY decided_at DESC LIMIT ?
            """,
            (limit,),
        ) as cursor:
            async for row in cursor:
                rows.append(dict(row))
    return {"history": rows}
