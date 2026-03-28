"""Config and auth REST endpoints."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException

from core.config import settings
from twitch import manager as twitch_manager
from twitch.auth import refresh_access_token, run_pkce_flow, validate_token
from twitch.token_store import TOKEN_ACCESS, TOKEN_CHANNEL, TOKEN_CLIENT_ID, TOKEN_CLIENT_SECRET, TOKEN_REFRESH, token_store
from api.schemas import (
    AppConfig,
    AuthInitRequest,
    AuthInitResponse,
    AuthStatusResponse,
    UpdateConfigRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# Track in-progress auth flow so we don't start two simultaneously
_auth_task: asyncio.Task | None = None


@router.get("/auth/status", response_model=AuthStatusResponse)
async def get_auth_status() -> AuthStatusResponse:
    """Check whether the app has valid stored credentials."""
    client_id = token_store.retrieve(TOKEN_CLIENT_ID) or settings.client_id
    access_token = token_store.retrieve(TOKEN_ACCESS)

    if not access_token:
        return AuthStatusResponse(
            authenticated=False,
            client_id_configured=bool(client_id),
        )

    # Validate with Twitch — if the network call fails, assume token is still valid
    # so we don't kick the user back to setup on every transient network error.
    try:
        info = await validate_token(access_token)
    except Exception as e:
        logger.warning("validate_token raised %s — assuming token valid", type(e).__name__)
        info = {"login": None}

    if not info:
        # Token expired — try refreshing before giving up
        refresh_token = token_store.retrieve(TOKEN_REFRESH)
        if refresh_token:
            logger.info("Access token expired — attempting refresh")
            new_access = await refresh_access_token(client_id, refresh_token)
            if new_access:
                try:
                    info = await validate_token(new_access)
                except Exception:
                    info = {"login": None}

    if info:
        return AuthStatusResponse(
            authenticated=True,
            username=info.get("login"),
            client_id_configured=bool(client_id),
        )

    return AuthStatusResponse(
        authenticated=False,
        client_id_configured=bool(client_id),
    )


@router.post("/auth/start", response_model=AuthInitResponse)
async def start_auth(request: AuthInitRequest) -> AuthInitResponse:
    """
    Begin the OAuth PKCE flow. Opens the system browser.
    client_id must be provided here if not set in environment.
    """
    global _auth_task

    # Store credentials
    token_store.store(TOKEN_CLIENT_ID, request.client_id)
    token_store.store(TOKEN_CLIENT_SECRET, request.client_secret)

    client_id = request.client_id
    client_secret = request.client_secret

    # Check if already authenticated
    access = token_store.retrieve(TOKEN_ACCESS)
    if access and await validate_token(access):
        return AuthInitResponse(status="already_authenticated", message="Already signed in")

    if _auth_task and not _auth_task.done():
        return AuthInitResponse(status="started", message="Authorization already in progress")

    async def _do_auth():
        try:
            tokens = await run_pkce_flow(client_id, client_secret)
            token_store.store(TOKEN_ACCESS, tokens["access_token"])
            if tokens.get("refresh_token"):
                token_store.store(TOKEN_REFRESH, tokens["refresh_token"])
            logger.info("Authentication completed successfully")
        except Exception as e:
            logger.error(
                "Authentication failed [%s]: %s",
                type(e).__name__,
                e,
                exc_info=True,
            )

    _auth_task = asyncio.create_task(_do_auth())
    return AuthInitResponse(status="started", message="Browser opened for authorization")


@router.delete("/auth")
async def sign_out() -> dict:
    """Remove all stored tokens."""
    token_store.clear_all()
    logger.info("User signed out — tokens cleared")
    return {"status": "signed_out"}


@router.post("/auth/reauth", response_model=AuthInitResponse)
async def reauth() -> AuthInitResponse:
    """
    Re-run the OAuth PKCE flow using the already-stored client credentials.
    Tokens are only replaced AFTER the new ones are successfully obtained,
    so an in-progress reload never lands on the setup screen.
    """
    global _auth_task

    client_id = token_store.retrieve(TOKEN_CLIENT_ID) or settings.client_id
    client_secret = token_store.retrieve(TOKEN_CLIENT_SECRET) or ""

    if not client_id:
        raise HTTPException(status_code=400, detail="No client_id configured — run initial setup first")

    if _auth_task and not _auth_task.done():
        return AuthInitResponse(status="started", message="Authorization already in progress")

    async def _do_reauth():
        try:
            tokens = await run_pkce_flow(client_id, client_secret)
            # Only replace tokens after successful auth — avoids setup screen on reload
            token_store.store(TOKEN_ACCESS, tokens["access_token"])
            if tokens.get("refresh_token"):
                token_store.store(TOKEN_REFRESH, tokens["refresh_token"])
            logger.info("Re-authentication completed successfully")
            await twitch_manager.connect()
        except Exception as e:
            logger.error("Re-authentication failed [%s]: %s", type(e).__name__, e, exc_info=True)

    _auth_task = asyncio.create_task(_do_reauth())
    return AuthInitResponse(status="started", message="Browser opened for re-authorization")


@router.get("/config", response_model=AppConfig)
async def get_config() -> AppConfig:
    return AppConfig(
        dry_run=settings.dry_run,
        auto_timeout_enabled=settings.auto_timeout_enabled,
        auto_ban_enabled=settings.auto_ban_enabled,
        timeout_threshold=settings.timeout_threshold,
        ban_threshold=settings.ban_threshold,
        alert_threshold=settings.alert_threshold,
        emote_filter_sensitivity=settings.emote_filter_sensitivity,
        default_channel=settings.default_channel,
        message_retention_days=settings.message_retention_days,
        health_history_retention_days=settings.health_history_retention_days,
        flagged_users_retention_days=settings.flagged_users_retention_days,
        moderation_actions_retention_days=settings.moderation_actions_retention_days,
    )


@router.patch("/config", response_model=AppConfig)
async def update_config(request: UpdateConfigRequest) -> AppConfig:
    """Update runtime configuration. Changes take effect immediately."""
    if request.dry_run is not None:
        settings.dry_run = request.dry_run
        logger.info("Dry-run mode: %s", settings.dry_run)
    if request.auto_timeout_enabled is not None:
        settings.auto_timeout_enabled = request.auto_timeout_enabled
    if request.auto_ban_enabled is not None:
        settings.auto_ban_enabled = request.auto_ban_enabled
    if request.timeout_threshold is not None:
        settings.timeout_threshold = request.timeout_threshold
    if request.ban_threshold is not None:
        settings.ban_threshold = request.ban_threshold
    if request.alert_threshold is not None:
        settings.alert_threshold = request.alert_threshold
    if request.emote_filter_sensitivity is not None:
        settings.emote_filter_sensitivity = request.emote_filter_sensitivity
        logger.info("Emote filter sensitivity: %d", settings.emote_filter_sensitivity)
    if request.default_channel is not None:
        channel = request.default_channel.lstrip("#").strip()
        settings.default_channel = channel
        # Persist so the channel survives backend restarts
        if channel:
            token_store.store(TOKEN_CHANNEL, channel)
        else:
            token_store.delete(TOKEN_CHANNEL)
        # Reconnect the Twitch client with the new channel
        asyncio.create_task(twitch_manager.connect())
        logger.info("Channel updated to #%s — reconnecting Twitch client", channel)
    if request.message_retention_days is not None:
        settings.message_retention_days = request.message_retention_days
    if request.health_history_retention_days is not None:
        settings.health_history_retention_days = request.health_history_retention_days
    if request.flagged_users_retention_days is not None:
        settings.flagged_users_retention_days = request.flagged_users_retention_days
        logger.info("Flagged-users retention: %d days (0=keep forever)", settings.flagged_users_retention_days)
    if request.moderation_actions_retention_days is not None:
        settings.moderation_actions_retention_days = request.moderation_actions_retention_days
        logger.info("Mod-actions retention: %d days (0=keep forever)", settings.moderation_actions_retention_days)

    # Persist changes back to per-profile config.json (no-op when not using profiles)
    await _persist_profile_config()

    return await get_config()


async def _persist_profile_config() -> None:
    """Write the current settings snapshot to the profile's config.json.

    This is a no-op when the backend is running without a profile (legacy mode).
    """
    import json
    path = settings.config_json_path
    if not path:
        return
    data = {
        "dry_run": settings.dry_run,
        "auto_timeout_enabled": settings.auto_timeout_enabled,
        "auto_ban_enabled": settings.auto_ban_enabled,
        "timeout_threshold": settings.timeout_threshold,
        "ban_threshold": settings.ban_threshold,
        "alert_threshold": settings.alert_threshold,
        "emote_filter_sensitivity": settings.emote_filter_sensitivity,
        "default_channel": settings.default_channel,
        "message_retention_days": settings.message_retention_days,
        "health_history_retention_days": settings.health_history_retention_days,
        "flagged_users_retention_days": settings.flagged_users_retention_days,
        "moderation_actions_retention_days": settings.moderation_actions_retention_days,
    }
    try:
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.warning("Failed to persist profile config.json: %s", e)
