"""
TwitchIDS Backend — background asyncio tasks.

All long-running loops are defined here and started by startup.on_startup().
They access pipeline singletons via the `startup` module object so they always
read the current value (set during on_startup) rather than a stale None binding.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time

import startup  # access mutable singletons via startup.xxx attribute lookup

from core.config import settings
from core.ipc import emit_health, emit_shutdown
from twitch import manager as twitch_manager

logger = logging.getLogger(__name__)


async def pipeline_metrics_loop() -> None:
    """Log pipeline metrics every 30 seconds."""
    while True:
        await asyncio.sleep(30)
        if startup.message_queue is None:
            continue
        stats_5s  = startup.chat_buffer.stats(5)
        stats_60s = startup.chat_buffer.stats(60)
        logger.info(
            "Pipeline | msg/min=%.0f queue_depth=%d processed=%d dropped=%d "
            "unique_users_60s=%d dup_ratio_60s=%.2f%%",
            stats_60s.messages_per_second * 60,
            startup.message_queue.depth,
            startup.message_queue.processed,
            startup.message_queue.dropped,
            stats_60s.unique_users,
            stats_60s.duplicate_ratio * 100,
        )


async def detection_tick_loop() -> None:
    """Run the detection engine tick every 1 second, and persist health snapshot to DB."""
    import aiosqlite
    _health_write_interval = 5  # write to DB every 5 ticks to avoid I/O churn
    _tick_count = 0
    while True:
        await asyncio.sleep(1)
        if startup.detection_engine is None:
            continue
        try:
            await startup.detection_engine.tick()
            _tick_count += 1
            if _tick_count % _health_write_interval == 0:
                snap = startup.detection_engine.health_engine.last_snapshot
                if snap is not None:
                    try:
                        async with aiosqlite.connect(settings.db_path) as db:
                            await db.execute(
                                """INSERT INTO health_history
                                   (recorded_at, channel, health_score, msg_per_min,
                                    active_users, duplicate_ratio, sync_score)
                                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                                (
                                    snap.timestamp,
                                    "__sim__" if settings.simulator_active else settings.default_channel,
                                    snap.health_score,
                                    snap.messages_per_minute,
                                    snap.active_users,
                                    snap.duplicate_ratio,
                                    snap.metric_scores.get("temporal_sync", 0.0),
                                ),
                            )
                            await db.commit()
                    except Exception:
                        logger.debug("health_history write failed", exc_info=True)
        except Exception:
            logger.exception("Detection tick error")


async def retention_loop() -> None:
    """
    Purge old rows daily to prevent unbounded DB growth.
      - messages older than settings.message_retention_days (default 7d)
      - health_history older than settings.health_history_retention_days (default 30d)
    Runs once at startup (after a short delay) then every 24 hours.
    """
    import aiosqlite
    await asyncio.sleep(60)  # Let startup settle before first purge
    while True:
        try:
            now = time.time()
            msg_cutoff    = now - settings.message_retention_days * 86400
            health_cutoff = now - settings.health_history_retention_days * 86400
            async with aiosqlite.connect(settings.db_path) as db:
                cursor = await db.execute(
                    "DELETE FROM messages WHERE received_at < ?", (msg_cutoff,)
                )
                msg_deleted = cursor.rowcount
                cursor = await db.execute(
                    "DELETE FROM health_history WHERE recorded_at < ?", (health_cutoff,)
                )
                health_deleted = cursor.rowcount

                flagged_deleted = 0
                if settings.flagged_users_retention_days > 0:
                    cutoff = now - settings.flagged_users_retention_days * 86400
                    cursor = await db.execute(
                        "DELETE FROM flagged_users WHERE flagged_at < ?", (cutoff,)
                    )
                    flagged_deleted = cursor.rowcount

                actions_deleted = 0
                if settings.moderation_actions_retention_days > 0:
                    cutoff = now - settings.moderation_actions_retention_days * 86400
                    cursor = await db.execute(
                        "DELETE FROM moderation_actions WHERE created_at < ?", (cutoff,)
                    )
                    actions_deleted = cursor.rowcount

                await db.commit()
            logger.info(
                "Retention: purged %d message(s) >%dd, %d health_history >%dd, "
                "%d flagged_users, %d moderation_actions",
                msg_deleted, settings.message_retention_days,
                health_deleted, settings.health_history_retention_days,
                flagged_deleted, actions_deleted,
            )
            # Apply passive reputation decay — recover clean users toward 80
            if startup.reputation_store is not None:
                n = await startup.reputation_store.apply_passive_decay()
                if n:
                    logger.info("Passive reputation decay: %d user(s) recovered", n)
        except Exception:
            logger.exception("Retention loop error")
        await asyncio.sleep(86400)  # Run again in 24 hours


async def wal_checkpoint_loop() -> None:
    """
    Issue a passive WAL checkpoint every 5 minutes to prevent unbounded WAL growth.

    PASSIVE mode does not block writers — it checkpoints as many frames as possible
    without waiting for readers.  Safe to run at any time.
    """
    import aiosqlite
    while True:
        await asyncio.sleep(300)  # 5 minutes
        try:
            async with aiosqlite.connect(settings.db_path) as db:
                await db.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except Exception:
            logger.debug("WAL checkpoint failed", exc_info=True)


async def heartbeat_loop() -> None:
    """Emit health beat to Electron every 5 seconds via stdout."""
    try:
        import psutil as _psutil
        _hb_proc = _psutil.Process()
        _HAS_PSUTIL = True
    except ImportError:
        _HAS_PSUTIL = False

    while True:
        await asyncio.sleep(5)
        client = twitch_manager.get_client()
        emit_health(
            uptime=round(time.time() - startup.START_TIME, 1),
            ws_clients=0,  # ws_manager imported lazily to avoid circular import
            connected=client.is_connected if client else False,
        )

        if _HAS_PSUTIL:
            try:
                rss_mb = _hb_proc.memory_info().rss / 1_048_576
                if rss_mb > 850:
                    logger.warning(
                        "Python RSS %.0fMB exceeds 850MB warning threshold", rss_mb
                    )
            except Exception:
                pass


async def stdin_listener() -> None:
    """
    Listen for commands from Electron on stdin.
    Currently only handles graceful shutdown signal.

    Only active when stdin is a real pipe (i.e., spawned by Electron).
    Skipped when running interactively — Windows IOCP cannot attach to a TTY.
    """
    if sys.stdin.isatty():
        logger.debug("stdin is a TTY — skipping stdin listener (not spawned by Electron)")
        return

    import json
    loop = asyncio.get_event_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)

    try:
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)
        while True:
            line = await reader.readline()
            if not line:
                break
            try:
                msg = json.loads(line.decode().strip())
                if msg.get("type") == "shutdown":
                    logger.info("Shutdown requested by Electron")
                    emit_shutdown("graceful")
                    await asyncio.sleep(0.5)
                    import os
                    os._exit(0)
            except Exception:
                pass
    except Exception:
        pass  # stdin may not be available in all environments
