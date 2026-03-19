"""
KnownBotRegistry — pre-filter against public Twitch bot username lists.

Sources:
  - joesondow/Namelists (GitHub raw) — 11.8M known bot usernames from CommanderRoot
  - TwitchInsights bot list — ~200k viewerlist bots

Stored in a Bloom filter (pybloom-live) instead of a frozenset:
  - frozenset with 12M Python strings: ~900MB RSS
  - BloomFilter at 0.1% false-positive rate:  ~22MB RSS  (42× reduction)

Trade-off: ~0.1% of non-bot usernames will test as present (false positives).
This is acceptable because known_bot is a supplementary signal only — it cannot
alone trigger any alert or moderation action (dual-signal requirement + 35-point
alert threshold both enforce this in alerting.py and moderation/engine.py).

Usernames are stored and tested lowercased — Twitch usernames are
case-insensitive.

A hit returns a raw signal score of 20.0 (low-confidence supplementary
signal). It is never sufficient alone to trigger a ban — the dual-signal
requirement and 90-confidence threshold are enforced by the moderation engine.
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

logger = logging.getLogger(__name__)

# Signal weight contribution — weak signal, supplementary only.
KNOWN_BOT_SCORE = 20.0

# Refresh interval: 24 hours
_REFRESH_INTERVAL = 86_400

# Bloom filter capacity — generously sized above the ~12M names currently in the
# source lists.  Over-provisioning wastes ~5MB vs an exactly-sized filter but
# eliminates the need to count unique names before insertion (which would require
# building a 900MB intermediate set just to get the count).
_BLOOM_CAPACITY = 15_000_000

# Bloom filter false-positive rate.
# At 0.1%: ~1 in 1000 innocent usernames will test as a known bot.
# The multi-signal alert guard (≥2 signals ≥ 0.2) prevents this from causing
# false threat alerts.
_BLOOM_ERROR_RATE = 0.001

# GitHub raw URLs — prefer the flat text files (one username per line).
_SOURCES = [
    # joesondow/Namelists — plain text list of known bot usernames (one per line)
    "https://raw.githubusercontent.com/joesondow/Namelists/master/namelist.txt",
    # TwitchInsights known bots — JSON {"bots": [["username", count, ts], ...]}
    "https://api.twitchinsights.net/v1/bots/all",
]


class KnownBotRegistry:
    """
    Bloom-filter registry of known Twitch bot usernames (lowercase).

    Memory: ~22MB for 12M entries at 0.1% false-positive rate.
    Lookup: O(1), cache-friendly bit-array ops.
    """

    def __init__(self) -> None:
        self._filter = None          # BloomFilter, set after first _refresh()
        self._count: int = 0         # number of unique names loaded
        self._last_refresh: float = 0.0
        self._loaded = False
        self._refresh_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_known_bot(self, username: str) -> bool:
        """
        O(1) membership test.

        Returns True if username is on a known-bot list.
        May return True for ~0.1% of non-bot usernames (Bloom false positive).
        Returns False until the first successful load completes.
        """
        if self._filter is None:
            return False
        return username.lower() in self._filter

    def signal_score(self, username: str) -> float:
        """Returns KNOWN_BOT_SCORE if username matches, else 0.0."""
        return KNOWN_BOT_SCORE if self.is_known_bot(username) else 0.0

    @property
    def size(self) -> int:
        """Number of unique usernames loaded into the filter."""
        return self._count

    # ------------------------------------------------------------------
    # Startup and refresh
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Load lists on startup and schedule background refresh."""
        await self._refresh()
        self._refresh_task = asyncio.create_task(
            self._refresh_loop(), name="known_bot_refresh"
        )

    async def stop(self) -> None:
        if self._refresh_task:
            self._refresh_task.cancel()

    async def _refresh_loop(self) -> None:
        while True:
            await asyncio.sleep(_REFRESH_INTERVAL)
            await self._refresh()

    async def _refresh(self) -> None:
        # Fetch raw text from each source on the event loop (async network I/O).
        # Parsing and filter construction are done entirely in the executor so that
        # the 12M string objects are created, inserted into the filter, and freed
        # within the thread — never accumulated into a ~900MB intermediate set on
        # the event loop heap.
        raw_sources: list[tuple[str, str]] = []  # (url, raw_text)
        async with httpx.AsyncClient(timeout=30.0) as client:
            for url in _SOURCES:
                try:
                    resp = await client.get(url, follow_redirects=True)
                    resp.raise_for_status()
                    raw_sources.append((url, resp.text))
                    logger.info("KnownBotRegistry: fetched %s (%d bytes)", url, len(resp.text))
                except Exception as exc:
                    logger.warning(
                        "KnownBotRegistry: failed to fetch %s — %s", url, exc
                    )

        if not raw_sources:
            if not self._loaded:
                logger.warning(
                    "KnownBotRegistry: all sources failed on first load — "
                    "known-bot pre-filter disabled until next refresh"
                )
            return

        # Build filter in executor: parse + deduplicate + insert all happen in the thread.
        # Peak RSS impact: only the raw text strings (already allocated above) + the
        # new Bloom filter (~22MB).  No intermediate 12M-entry set on the event loop heap.
        loop = asyncio.get_event_loop()
        new_filter, count = await loop.run_in_executor(
            None, _build_bloom_filter_from_raw, raw_sources
        )

        # Atomic reference swap — readers see either the old or new filter, never None.
        self._filter = new_filter
        self._count = count
        self._last_refresh = time.time()
        self._loaded = True
        logger.info(
            "KnownBotRegistry: %d unique bot usernames loaded "
            "(Bloom filter, error_rate=%.1f%%)",
            count, _BLOOM_ERROR_RATE * 100,
        )


# ------------------------------------------------------------------
# Per-source parsers
# ------------------------------------------------------------------


async def _fetch_source(client: httpx.AsyncClient, url: str) -> list[str]:
    """Fetch one source URL and return a list of lowercase usernames."""
    resp = await client.get(url, follow_redirects=True)
    resp.raise_for_status()

    content_type = resp.headers.get("content-type", "")

    if "json" in content_type or url.endswith(".json") or "twitchinsights" in url:
        return _parse_twitchinsights_json(resp.text)
    else:
        return _parse_plaintext(resp.text)


def _parse_plaintext(text: str) -> list[str]:
    """Parse a newline-delimited list of usernames."""
    names = []
    for line in text.splitlines():
        line = line.strip().lower()
        if line and not line.startswith("#"):
            names.append(line)
    return names


def _parse_twitchinsights_json(text: str) -> list[str]:
    """
    Parse TwitchInsights JSON: {"bots": [["username", count, timestamp], ...]}
    """
    import json

    try:
        data = json.loads(text)
        bots = data.get("bots", [])
        return [entry[0].lower() for entry in bots if entry and isinstance(entry[0], str)]
    except Exception:
        # Fall back to line-by-line parsing in case format changed
        return _parse_plaintext(text)


def _build_bloom_filter_from_raw(
    raw_sources: list[tuple[str, str]],
) -> tuple:
    """
    Parse all source texts and insert directly into a BloomFilter — no intermediate set.

    Bloom filter add() is idempotent: inserting a duplicate username twice is identical
    to inserting it once.  This means we can skip cross-source deduplication entirely
    and stream each source line-by-line into the filter.

    Memory profile during refresh:
      Before this fix:  raw_text (~130MB) + intermediate set (~900MB) + filter (~22MB)
      After this fix:   raw_text (~130MB) + filter (~22MB)  — ~900MB saving

    The filter is over-provisioned to _BLOOM_CAPACITY (15M) which is slightly above
    the 12M unique names actually loaded.  At error_rate=0.001 and capacity=15M,
    the filter uses ~27MB — 5MB more than an exactly-sized 12M filter, but avoids
    any need to count unique names before insertion.

    Returns (BloomFilter, approximate_total_insertions).
    CPU-bound (~1-2s for 12M items) — always called via run_in_executor.
    """
    import json
    from pybloom_live import BloomFilter

    bf = BloomFilter(capacity=_BLOOM_CAPACITY, error_rate=_BLOOM_ERROR_RATE)
    total = 0

    for url, text in raw_sources:
        if "twitchinsights" in url or url.endswith(".json"):
            try:
                data = json.loads(text)
                bots = data.get("bots", [])
                for entry in bots:
                    if entry and isinstance(entry[0], str):
                        bf.add(entry[0].lower())
                        total += 1
            except Exception:
                for line in text.splitlines():
                    line = line.strip().lower()
                    if line and not line.startswith("#"):
                        bf.add(line)
                        total += 1
        else:
            for line in text.splitlines():
                line = line.strip().lower()
                if line and not line.startswith("#"):
                    bf.add(line)
                    total += 1

    return bf, total


# ------------------------------------------------------------------
# Module-level singleton (populated in startup.py)
# ------------------------------------------------------------------

known_bot_registry: KnownBotRegistry | None = None
