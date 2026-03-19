"""
RegexFilter — local regex-based message filter.

Fills the gap left by Twitch's AutoMod (literal terms only) with full
Python regex support. Runs in the fast-path after every normalised message.

Filters are loaded from SQLite on startup and reloaded via reload().
Each filter specifies: pattern, flags, action_type (delete | timeout | flag),
optional duration, and enabled state.

match() returns the first matching FilterHit, or None.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass

import aiosqlite

logger = logging.getLogger(__name__)


@dataclass
class CompiledFilter:
    id: int
    pattern_str: str
    regex: re.Pattern
    action_type: str          # delete | timeout | flag
    duration_seconds: int | None
    note: str
    enabled: bool


@dataclass
class FilterHit:
    filter_id: int
    pattern: str
    action_type: str
    duration_seconds: int | None
    note: str


class RegexFilterEngine:
    """Thread-safe (asyncio) collection of compiled regex filters."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._filters: list[CompiledFilter] = []
        self._lock = asyncio.Lock()

    async def load(self) -> None:
        """Load all enabled filters from DB. Safe to call on startup."""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT id, pattern, flags, action_type, duration_seconds, note, enabled "
                "FROM regex_filters ORDER BY id"
            ) as cursor:
                rows = await cursor.fetchall()

        compiled: list[CompiledFilter] = []
        for row in rows:
            if not row["enabled"]:
                continue
            try:
                flags = 0
                if "i" in (row["flags"] or ""):
                    flags |= re.IGNORECASE
                regex = re.compile(row["pattern"], flags)
                compiled.append(CompiledFilter(
                    id=row["id"],
                    pattern_str=row["pattern"],
                    regex=regex,
                    action_type=row["action_type"],
                    duration_seconds=row["duration_seconds"],
                    note=row["note"] or "",
                    enabled=True,
                ))
            except re.error as e:
                logger.warning("Invalid regex filter id=%d pattern=%r: %s", row["id"], row["pattern"], e)

        async with self._lock:
            self._filters = compiled
        logger.info("RegexFilterEngine: loaded %d active filters", len(compiled))

    async def reload(self) -> None:
        """Reload from DB after CRUD changes."""
        await self.load()

    def match(self, text: str) -> FilterHit | None:
        """
        Check text against all enabled filters. Returns the first hit, or None.
        O(n_filters) — filters are few in practice.
        Thread-safe: reads self._filters which is replaced atomically.
        """
        filters = self._filters  # snapshot
        for f in filters:
            if f.regex.search(text):
                return FilterHit(
                    filter_id=f.id,
                    pattern=f.pattern_str,
                    action_type=f.action_type,
                    duration_seconds=f.duration_seconds,
                    note=f.note,
                )
        return None

    async def increment_match_count(self, filter_id: int) -> None:
        """Increment hit counter asynchronously (fire-and-forget)."""
        try:
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(
                    "UPDATE regex_filters SET match_count = match_count + 1 WHERE id = ?",
                    (filter_id,),
                )
                await db.commit()
        except Exception as e:
            logger.debug("Could not increment match count for filter %d: %s", filter_id, e)


# Module-level singleton — injected at startup
regex_filter_engine: RegexFilterEngine | None = None
