"""
Twitch OAuth 2.0 Authorization Code + PKCE flow for desktop apps.

No client secret is stored or distributed in the binary.
Opens the system browser for user consent, starts a temporary local
HTTP server on an OS-assigned port to receive the OAuth callback.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import secrets
import webbrowser
from urllib.parse import parse_qs, urlencode, urlparse

from aiohttp import web

from .token_store import TOKEN_ACCESS, TOKEN_REFRESH, token_store

logger = logging.getLogger(__name__)

AUTH_URL = "https://id.twitch.tv/oauth2/authorize"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"

# Scopes required for chat monitoring + moderation
REQUIRED_SCOPES = [
    "user:read:chat",
    "user:write:chat",
    "user:bot",
    "moderator:manage:banned_users",
    "moderator:manage:chat_messages",
    "moderator:manage:chat_settings",
    "moderator:manage:warnings",        # warn action (POST /helix/moderation/warnings)
    "moderator:manage:automod",         # AutoMod queue approve/deny + EventSub hold
    "moderator:manage:unban_requests",  # unban request list + resolve
    "moderator:read:chatters",
    "moderator:read:followers",         # follower audit (GET /helix/channels/followers)
    "channel:read:hype_train",
    "channel:read:subscriptions",
]


def _make_code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _build_auth_url(client_id: str, challenge: str, state: str, redirect_uri: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(REQUIRED_SCOPES),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "force_verify": "true",
    }
    return f"{AUTH_URL}?{urlencode(params)}"


async def run_pkce_flow(client_id: str, client_secret: str) -> dict[str, str]:
    """
    Execute PKCE flow. Opens the system browser and waits for the OAuth callback.
    Returns {'access_token': ..., 'refresh_token': ...} on success.
    Raises RuntimeError on failure or timeout.

    Note: Twitch requires client_secret in the token exchange even for PKCE flows,
    which is non-standard but documented in their API reference.
    """
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = _make_code_challenge(code_verifier)
    state = secrets.token_urlsafe(16)
    result_future: asyncio.Future = asyncio.get_running_loop().create_future()

    # --- Temporary callback server ---
    async def handle_callback(request: web.Request) -> web.Response:
        query = dict(request.rel_url.query)

        if query.get("state") != state:
            result_future.set_exception(RuntimeError("OAuth state mismatch — possible CSRF"))
            return web.Response(text="State mismatch. Please close this tab.", status=400)

        if "error" in query:
            result_future.set_exception(
                RuntimeError(f"OAuth error: {query.get('error_description', query['error'])}")
            )
            return web.Response(
                text="Authorization denied. You can close this tab.", status=400
            )

        code = query.get("code")
        if not code:
            result_future.set_exception(RuntimeError("No code in OAuth callback"))
            return web.Response(text="Missing code. Please close this tab.", status=400)

        result_future.set_result(code)
        return web.Response(
            text="Authorization successful! You can close this tab and return to TwitchIDS.",
            content_type="text/html",
        )

    app = web.Application()
    app.router.add_get("/callback", handle_callback)
    runner = web.AppRunner(app)
    await runner.setup()
    # Bind to port 0 — the OS assigns a free port, avoiding conflicts with
    # Node dev servers, other Electron apps, or anything else on port 3000.
    site = web.TCPSite(runner, "localhost", 0)
    await site.start()

    # Read back the actual port the OS assigned.
    actual_port = site._server.sockets[0].getsockname()[1]
    redirect_uri = f"http://localhost:{actual_port}/callback"
    auth_url = _build_auth_url(client_id, code_challenge, state, redirect_uri)

    logger.info("Opening browser for Twitch authorization (callback port %d)...", actual_port)
    webbrowser.open(auth_url)

    try:
        # Wait up to 5 minutes for the user to complete authorization
        code = await asyncio.wait_for(result_future, timeout=300)
    except asyncio.TimeoutError:
        raise RuntimeError("Authorization timed out. Please try again.")
    finally:
        await runner.cleanup()

    # Exchange authorization code for tokens
    import httpx

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "code_verifier": code_verifier,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
        )

    if response.status_code != 200:
        raise RuntimeError(
            f"Token exchange failed: {response.status_code} {response.text}"
        )

    data = response.json()

    if "access_token" not in data:
        raise RuntimeError(f"No access_token in response: {list(data.keys())}")

    logger.info("OAuth flow completed successfully")
    return {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token", ""),
    }


async def refresh_access_token(client_id: str, refresh_token: str, client_secret: str = "") -> str | None:
    """
    Use a refresh token to get a new access token.
    Returns the new access token, or None on failure.
    Also updates the stored refresh token if Twitch rotates it.
    """
    import httpx

    from .token_store import TOKEN_CLIENT_SECRET

    if not client_secret:
        client_secret = token_store.retrieve(TOKEN_CLIENT_SECRET) or ""

    payload = {
        "client_id": client_id,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    if client_secret:
        payload["client_secret"] = client_secret

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(TOKEN_URL, data=payload)

    if response.status_code != 200:
        logger.warning("Token refresh failed: %s", response.status_code)
        return None

    data = response.json()
    new_access = data.get("access_token")
    new_refresh = data.get("refresh_token")

    if new_access:
        token_store.store(TOKEN_ACCESS, new_access)
    if new_refresh:
        token_store.store(TOKEN_REFRESH, new_refresh)

    return new_access


async def validate_token(access_token: str) -> dict | None:
    """
    Call Twitch token validation endpoint.
    Returns token info dict or None if invalid/expired.
    """
    import httpx

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            "https://id.twitch.tv/oauth2/validate",
            headers={"Authorization": f"OAuth {access_token}"},
        )

    if response.status_code == 200:
        return response.json()
    return None
