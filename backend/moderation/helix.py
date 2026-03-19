"""
RefreshingHTTPClient — authenticated Helix API client.

Automatically refreshes the access token on 401 responses using the
stored refresh token, then retries the request once.

All requests go to https://api.twitch.tv/helix/ with the correct
Client-ID and Authorization headers injected automatically.

Token rotation is written back to Windows Credential Manager via token_store.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from twitch.token_store import (
    TOKEN_ACCESS,
    TOKEN_CLIENT_ID,
    TOKEN_CLIENT_SECRET,
    TOKEN_REFRESH,
    token_store,
)

logger = logging.getLogger(__name__)

HELIX_BASE = "https://api.twitch.tv/helix"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"


class RefreshingHTTPClient:
    """
    Thin async Helix API wrapper with automatic 401 token refresh.

    Usage:
        client = RefreshingHTTPClient()
        resp = await client.post("/moderation/bans", json={...})
    """

    def __init__(self) -> None:
        self._http = httpx.AsyncClient(timeout=10.0, base_url=HELIX_BASE)

    async def get(self, path: str, **kwargs) -> httpx.Response:
        return await self._request("GET", path, **kwargs)

    async def post(self, path: str, **kwargs) -> httpx.Response:
        return await self._request("POST", path, **kwargs)

    async def delete(self, path: str, **kwargs) -> httpx.Response:
        return await self._request("DELETE", path, **kwargs)

    async def patch(self, path: str, **kwargs) -> httpx.Response:
        return await self._request("PATCH", path, **kwargs)

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        headers = self._auth_headers()
        kwargs.setdefault("headers", {}).update(headers)

        resp = await self._http.request(method, path, **kwargs)

        if resp.status_code == 401:
            logger.info("Helix 401 — attempting token refresh")
            refreshed = await self._refresh_token()
            if refreshed:
                kwargs["headers"].update(self._auth_headers())
                resp = await self._http.request(method, path, **kwargs)
            else:
                logger.error("Token refresh failed — cannot retry request")

        return resp

    def _auth_headers(self) -> dict[str, str]:
        access_token = token_store.retrieve(TOKEN_ACCESS) or ""
        client_id = token_store.retrieve(TOKEN_CLIENT_ID) or ""
        return {
            "Authorization": f"Bearer {access_token}",
            "Client-ID": client_id,
            "Content-Type": "application/json",
        }

    async def _refresh_token(self) -> bool:
        """Exchange refresh token for new access token. Returns True on success."""
        client_id = token_store.retrieve(TOKEN_CLIENT_ID) or ""
        client_secret = token_store.retrieve(TOKEN_CLIENT_SECRET) or ""
        refresh_token = token_store.retrieve(TOKEN_REFRESH) or ""

        if not all([client_id, client_secret, refresh_token]):
            logger.error("Missing credentials for token refresh")
            return False

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    TOKEN_URL,
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                        "client_id": client_id,
                        "client_secret": client_secret,
                    },
                )
            if resp.status_code != 200:
                logger.error("Token refresh HTTP %d: %s", resp.status_code, resp.text[:200])
                return False

            data = resp.json()
            token_store.store(TOKEN_ACCESS, data["access_token"])
            if "refresh_token" in data:
                token_store.store(TOKEN_REFRESH, data["refresh_token"])
            logger.info("Access token refreshed successfully")
            return True

        except Exception:
            logger.exception("Token refresh exception")
            return False

    async def aclose(self) -> None:
        await self._http.aclose()
