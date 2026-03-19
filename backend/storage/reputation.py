"""
User Reputation Store — persistent cross-session reputation tracking.

Maintains a `user_reputation` table with accumulated stats per user_id.
Updated after every detection event and after moderation actions.

Reputation score is a float 0–100:
  100 = no history of suspicious activity (default for new users)
   0  = confirmed bot / repeat offender

The score applies as a modifier in the detection engine:
  threat_modifier = (100 - reputation) / 100  * REPUTATION_WEIGHT
  effective_threat = base_threat + threat_modifier * 30
where REPUTATION_WEIGHT defaults to 0.4 (40% max reputation boost to threat score).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict

import aiosqlite

# Maximum number of users to hold in the in-memory reputation cache.
# LRU eviction keeps memory bounded even during long streams with many chatters.
_CACHE_MAX = 10_000

logger = logging.getLogger(__name__)

REPUTATION_WEIGHT = 0.4  # How strongly reputation amplifies base threat score

# DDL — called from storage.db.init_db (migration-safe)
REPUTATION_DDL = """
CREATE TABLE IF NOT EXISTS user_reputation (
    user_id         TEXT    PRIMARY KEY,
    username        TEXT    NOT NULL,
    reputation      REAL    NOT NULL DEFAULT 100.0,  -- 0 (worst) to 100 (clean)
    total_flags     INTEGER NOT NULL DEFAULT 0,
    total_actions   INTEGER NOT NULL DEFAULT 0,
    false_positives INTEGER NOT NULL DEFAULT 0,
    last_seen       REAL    NOT NULL,
    updated_at      REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_user_reputation_reputation ON user_reputation(reputation);
"""

# Penalty / recovery constants
_FLAG_PENALTY   = 8.0   # Reputation points lost per flag event
_ACTION_PENALTY = 15.0  # Reputation points lost per moderation action
_FP_RECOVERY    = 5.0   # Reputation points gained per false-positive resolution
_MIN_SCORE      = 0.0
_MAX_SCORE      = 100.0

# Passive decay constants — applied once daily by retention_loop
_PASSIVE_RECOVERY_RATE      = 2.0   # points per day of clean inactivity
_PASSIVE_RECOVERY_MIN_DAYS  = 3     # must be clean for ≥ 3 days before recovery starts
_PASSIVE_RECOVERY_CAP       = 80.0  # passive recovery stops here; reach 100 via FP resolution only


class ReputationStore:
    """
    Async reputation store.  All public methods are coroutines and safe to
    call from any asyncio task.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        # In-memory LRU cache: user_id → score (float).
        # Capped at _CACHE_MAX=10_000 entries — the least-recently-accessed entry
        # is evicted when the cache is full.  An unbounded plain dict would grow
        # to O(unique_chatters) over a long stream (potentially 100K+ entries).
        self._cache: OrderedDict[str, float] = OrderedDict()
        self._lock = asyncio.Lock()

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    async def get(self, user_id: str) -> float:
        """Return current reputation score (0–100). Default 100.0 for unknown users."""
        if user_id in self._cache:
            self._cache.move_to_end(user_id)  # LRU: mark as recently used
            return self._cache[user_id]
        score = await self._fetch(user_id)
        self._cache[user_id] = score
        # Evict least-recently-used entry if cache is over capacity
        if len(self._cache) > _CACHE_MAX:
            self._cache.popitem(last=False)
        return score

    async def apply_threat_modifier(self, user_id: str, base_score: float) -> float:
        """
        Amplify base threat score using reputation history.
        Returns effective threat score (still clamped to 0–100).
        """
        rep = await self.get(user_id)
        # Low reputation = boost to threat score
        reputation_boost = (1.0 - rep / 100.0) * REPUTATION_WEIGHT * 30.0
        return min(100.0, base_score + reputation_boost)

    async def record_flag(self, user_id: str, username: str) -> None:
        """Decrease reputation on a detection event (flag)."""
        await self._adjust(user_id, username, delta=-_FLAG_PENALTY, flag=True)

    async def record_action(self, user_id: str, username: str) -> None:
        """Decrease reputation on a moderation action (timeout/ban)."""
        await self._adjust(user_id, username, delta=-_ACTION_PENALTY, action=True)

    async def record_false_positive(self, user_id: str, username: str) -> None:
        """Recover reputation when a detection is marked as a false positive."""
        await self._adjust(user_id, username, delta=+_FP_RECOVERY, fp=True)

    async def apply_passive_decay(self) -> int:
        """
        Apply time-based score recovery to users who have been clean for at least
        _PASSIVE_RECOVERY_MIN_DAYS days since their last update (flag or action).

        Rules:
          - Only users with reputation < _PASSIVE_RECOVERY_CAP (80) are eligible.
            Users at or above 80 don't need passive help; they reach 100 via explicit
            false-positive resolution by a moderator.
          - Recovery = _PASSIVE_RECOVERY_RATE (2.0) × days_clean, capped so the
            result never exceeds _PASSIVE_RECOVERY_CAP.
          - Updates are written in a single batch to avoid per-user DB round trips.

        Returns the number of users whose score was updated.
        Called once per day from retention_loop in tasks.py.
        """
        cutoff = time.time() - (_PASSIVE_RECOVERY_MIN_DAYS * 86400)
        now = time.time()
        updated = 0
        try:
            async with aiosqlite.connect(self._db_path) as db:
                async with db.execute(
                    """SELECT user_id, username, reputation, updated_at
                       FROM user_reputation
                       WHERE reputation < ? AND updated_at < ?""",
                    (_PASSIVE_RECOVERY_CAP, cutoff),
                ) as cursor:
                    rows = await cursor.fetchall()

                for user_id, username, reputation, updated_at in rows:
                    days_clean = (now - updated_at) / 86400
                    recovery = min(
                        _PASSIVE_RECOVERY_RATE * days_clean,
                        _PASSIVE_RECOVERY_CAP - reputation,
                    )
                    if recovery <= 0:
                        continue
                    new_score = min(reputation + recovery, _PASSIVE_RECOVERY_CAP)
                    await db.execute(
                        "UPDATE user_reputation SET reputation = ?, updated_at = ? "
                        "WHERE user_id = ?",
                        (new_score, now, user_id),
                    )
                    # Sync cache if the user is currently loaded
                    self._cache[user_id] = new_score
                    updated += 1
                    logger.info(
                        "Passive recovery: %s %.1f → %.1f (+%.1f, %.0f days clean)",
                        username, reputation, new_score, recovery, days_clean,
                    )

                if updated:
                    await db.commit()

        except Exception:
            logger.exception("apply_passive_decay failed")

        return updated

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    async def _fetch(self, user_id: str) -> float:
        try:
            async with aiosqlite.connect(self._db_path) as db:
                async with db.execute(
                    "SELECT reputation FROM user_reputation WHERE user_id = ?", (user_id,)
                ) as cursor:
                    row = await cursor.fetchone()
            return row[0] if row else 100.0
        except Exception:
            return 100.0

    async def _adjust(
        self,
        user_id: str,
        username: str,
        delta: float,
        flag: bool = False,
        action: bool = False,
        fp: bool = False,
    ) -> None:
        async with self._lock:
            current = self._cache.get(user_id) or await self._fetch(user_id)
            new_score = max(_MIN_SCORE, min(_MAX_SCORE, current + delta))
            self._cache[user_id] = new_score
            self._cache.move_to_end(user_id)  # LRU: mark as most-recently written
            if len(self._cache) > _CACHE_MAX:
                self._cache.popitem(last=False)
            now = time.time()
            try:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute(
                        """
                        INSERT INTO user_reputation
                            (user_id, username, reputation, total_flags, total_actions,
                             false_positives, last_seen, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(user_id) DO UPDATE SET
                            username        = excluded.username,
                            reputation      = excluded.reputation,
                            total_flags     = total_flags     + excluded.total_flags,
                            total_actions   = total_actions   + excluded.total_actions,
                            false_positives = false_positives + excluded.false_positives,
                            last_seen       = excluded.last_seen,
                            updated_at      = excluded.updated_at
                        """,
                        (
                            user_id,
                            username,
                            new_score,
                            int(flag),
                            int(action),
                            int(fp),
                            now,
                            now,
                        ),
                    )
                    await db.commit()
            except Exception:
                logger.exception("ReputationStore._adjust failed for %s", user_id)


# Module-level singleton — initialized in main.py on_startup
reputation_store: ReputationStore | None = None
