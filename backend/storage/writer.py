"""
Async batch message writer.

Collects ChatMessage objects and flushes them to SQLite in batches.
Flush triggers on whichever comes first:
  - Batch size reaches BATCH_SIZE (100)
  - FLUSH_INTERVAL_MS milliseconds have elapsed (100ms)

This keeps per-message latency near zero while keeping write throughput high.
The writer is a pipeline processor registered with MessageQueue.add_processor().
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

import aiosqlite

if TYPE_CHECKING:
    from pipeline.models import ChatMessage

logger = logging.getLogger(__name__)

BATCH_SIZE = 100
FLUSH_INTERVAL_MS = 100

_INSERT_SQL = """
INSERT INTO messages (
    received_at, channel, user_id, username,
    raw_text, normalized_text, content_hash,
    emoji_count, url_count, mention_count, word_count, char_count,
    caps_ratio, has_url, color,
    is_subscriber, is_moderator, is_vip, account_age_days
) VALUES (
    ?, ?, ?, ?,
    ?, ?, ?,
    ?, ?, ?, ?, ?,
    ?, ?, ?,
    ?, ?, ?, ?
)
"""


class MessageWriter:
    """
    Buffers ChatMessages and batch-inserts them into SQLite.

    Usage:
        writer = MessageWriter(db_path)
        await writer.start()
        queue.add_processor(writer.write)
        # ... at shutdown:
        await writer.flush()
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._pending: list[ChatMessage] = []
        self._last_flush = time.monotonic()
        self._total_written = 0
        self._flush_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the periodic flush background task."""
        self._flush_task = asyncio.create_task(self._flush_loop(), name="db-flush-loop")
        logger.info("MessageWriter started (batch=%d, interval=%dms)", BATCH_SIZE, FLUSH_INTERVAL_MS)

    async def write(self, msg: ChatMessage) -> None:
        """Pipeline processor callback — buffer one message."""
        self._pending.append(msg)
        if len(self._pending) >= BATCH_SIZE:
            await self.flush()

    async def flush(self) -> None:
        """Write all buffered messages to SQLite in a single transaction."""
        if not self._pending:
            return

        batch = self._pending
        self._pending = []
        self._last_flush = time.monotonic()

        rows = [
            (
                msg.received_at, msg.channel, msg.user_id, msg.username,
                msg.raw_text, msg.normalized_text, msg.content_hash,
                msg.emoji_count, msg.url_count, msg.mention_count,
                msg.word_count, msg.char_count,
                msg.caps_ratio, int(msg.has_url), msg.color,
                int(msg.is_subscriber), int(msg.is_moderator), int(msg.is_vip),
                msg.account_age_days,
            )
            for msg in batch
        ]

        try:
            async with aiosqlite.connect(self._db_path) as db:
                await db.executemany(_INSERT_SQL, rows)
                await db.commit()
            self._total_written += len(rows)
            logger.debug("Flushed %d messages to DB (total=%d)", len(rows), self._total_written)
        except Exception:
            logger.exception("DB flush failed — %d messages lost", len(rows))

    async def _flush_loop(self) -> None:
        """Periodic flush so low-volume channels still get written promptly."""
        interval = FLUSH_INTERVAL_MS / 1000.0
        while True:
            await asyncio.sleep(interval)
            elapsed = time.monotonic() - self._last_flush
            if elapsed >= interval and self._pending:
                await self.flush()

    @property
    def total_written(self) -> int:
        return self._total_written

    @property
    def pending_count(self) -> int:
        return len(self._pending)
