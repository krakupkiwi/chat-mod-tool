"""
Account age cache — resolves Twitch account creation dates via Helix API.

Uses a TTLCache (cachetools) so entries expire after 1 hour without manual eviction.
Batches lookups: accumulates user IDs and resolves them in groups of 100
(the Helix /users endpoint limit).

On cache miss, the lookup is deferred — the message is processed immediately
without account age, and the age is backfilled asynchronously.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Optional

try:
    from cachetools import TTLCache
except ImportError:
    TTLCache = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

CACHE_MAXSIZE = 50_000
CACHE_TTL = 3600  # 1 hour
LOOKUP_BATCH_SIZE = 100
LOOKUP_INTERVAL_SECONDS = 5.0  # How often to drain the pending lookup queue


class AccountAgeCache:
    """
    Two-layer cache:
      1. TTLCache for resolved ages (user_id → age_days)
      2. Pending queue of user IDs awaiting Helix lookup

    Usage:
        cache = AccountAgeCache(client_id, access_token_fn)
        await cache.start()
        age = cache.get(user_id)  # None on cache miss — lookup queued automatically
    """

    def __init__(
        self,
        client_id: str,
        get_access_token: "callable[[], str | None]",
    ) -> None:
        self._client_id = client_id
        self._get_access_token = get_access_token
        if TTLCache is not None:
            self._cache: dict[str, int] = TTLCache(maxsize=CACHE_MAXSIZE, ttl=CACHE_TTL)
        else:
            logger.warning("cachetools not installed — using plain dict for account cache (no TTL)")
            self._cache = {}
        self._pending: set[str] = set()
        self._lookup_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the background lookup loop."""
        self._lookup_task = asyncio.create_task(self._lookup_loop(), name="account-age-lookup")
        logger.info("AccountAgeCache started")

    def get(self, user_id: str) -> Optional[int]:
        """
        Return cached age in days, or None on cache miss.
        On miss, queues the user_id for background resolution.
        """
        age = self._cache.get(user_id)
        if age is None and user_id not in self._pending:
            self._pending.add(user_id)
        return age

    def set(self, user_id: str, age_days: int) -> None:
        self._cache[user_id] = age_days

    async def _lookup_loop(self) -> None:
        """
        Background task: drain the pending user-ID queue in batches every
        LOOKUP_INTERVAL_SECONDS (5s).

        Flow:
          1. Sleep for LOOKUP_INTERVAL_SECONDS.
          2. If _pending is empty, skip the API call entirely (early exit).
          3. Pop up to LOOKUP_BATCH_SIZE (100) user IDs — the Helix /users limit.
          4. Call _resolve_batch() to fetch account creation dates from Helix and
             populate _cache with age_days values.
          5. Any IDs that Twitch does not return (banned/deleted accounts) are simply
             not added to the cache; they will be retried on the next message from
             that user since cache.get() will queue them again.

        The cache is a TTLCache — entries expire after CACHE_TTL (1 hour) without
        manual eviction, preventing unbounded memory growth on large channels.
        """
        while True:
            await asyncio.sleep(LOOKUP_INTERVAL_SECONDS)
            if not self._pending:
                continue

            batch = list(self._pending)[:LOOKUP_BATCH_SIZE]
            self._pending -= set(batch)

            try:
                await self._resolve_batch(batch)
            except Exception:
                logger.exception("Account age lookup failed for batch of %d users", len(batch))

    async def _resolve_batch(self, user_ids: list[str]) -> None:
        """
        Call Helix GET /users with up to 100 user IDs.
        Requires a valid user access token.
        """
        import httpx

        access_token = self._get_access_token()
        if not access_token:
            logger.debug("No access token — skipping account age lookup")
            return

        params = [("id", uid) for uid in user_ids]
        headers = {
            "Client-ID": self._client_id,
            "Authorization": f"Bearer {access_token}",
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                "https://api.twitch.tv/helix/users",
                params=params,
                headers=headers,
            )

        if response.status_code != 200:
            logger.warning("Helix /users returned %d", response.status_code)
            return

        now = datetime.now(timezone.utc)
        for user in response.json().get("data", []):
            uid = user.get("id")
            created_at_str = user.get("created_at")
            if not uid or not created_at_str:
                continue
            try:
                created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                age_days = (now - created_at).days
                self._cache[uid] = age_days
            except Exception:
                pass

        logger.debug("Resolved account ages for %d users", len(user_ids))
