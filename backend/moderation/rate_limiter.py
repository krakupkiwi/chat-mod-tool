"""
Token bucket rate limiter for Twitch moderation API calls.

Twitch allows up to 100 moderation actions/minute per broadcaster.
We conservatively cap at 80/min to leave headroom.

The bucket refills continuously at rate = capacity / window_seconds tokens/sec.
acquire() suspends until a token is available (non-blocking to the caller via await).
"""

from __future__ import annotations

import asyncio
import time


class TokenBucketRateLimiter:
    def __init__(self, capacity: int = 80, window_seconds: float = 60.0) -> None:
        self._capacity = float(capacity)
        self._refill_rate = capacity / window_seconds   # tokens per second
        self._tokens = float(capacity)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait until a token is available, then consume one."""
        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                # Calculate wait time for next token
                wait = (1.0 - self._tokens) / self._refill_rate

            await asyncio.sleep(wait)

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self._capacity, self._tokens + elapsed * self._refill_rate)
        self._last_refill = now

    @property
    def available_tokens(self) -> float:
        self._refill()
        return self._tokens
