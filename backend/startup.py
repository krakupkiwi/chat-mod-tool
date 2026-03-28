"""
TwitchIDS Backend — startup/shutdown lifecycle and pipeline singletons.

This module owns:
  - IPC secret management (load-or-create, frontend env sync)
  - Pipeline singletons (chat_buffer, detection_engine, etc.)
  - on_startup() / on_shutdown() FastAPI lifecycle hooks
  - Helper functions called during startup
  - _enqueue_twitch_message() — Twitch → pipeline bridge

Imported by main.py (for on_startup / on_shutdown registration) and by
tasks.py (for access to the pipeline singletons via module-attribute lookup).
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import time

from core.config import settings
from core.ipc import emit_ready, emit_shutdown
from detection.engine import DetectionEngine
from detection.known_bots import KnownBotRegistry
from moderation.engine import ModerationEngine
from pipeline.account_cache import AccountAgeCache
from pipeline.buffer import ChatBuffer
from pipeline.builder import build_message
from pipeline.queue import MessageQueue
from storage.db import init_db
from storage.reputation import ReputationStore
from storage.writer import MessageWriter
from twitch import manager as twitch_manager
from twitch.token_store import TOKEN_ACCESS, TOKEN_CHANNEL, TOKEN_CLIENT_ID, token_store

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# IPC secret management
# ---------------------------------------------------------------------------

_BACKEND_DIR = os.path.dirname(__file__)
_ENV_FILE = os.path.join(_BACKEND_DIR, ".env")
_FRONTEND_ENV_FILE = os.path.join(_BACKEND_DIR, "..", "frontend", ".env.local")


def _load_or_create_ipc_secret() -> str:
    # Load existing secret if present
    if os.path.exists(_ENV_FILE):
        for line in open(_ENV_FILE).readlines():
            line = line.strip()
            if line.startswith("IPC_SECRET="):
                secret = line[len("IPC_SECRET="):]
                _sync_frontend_env(secret)
                return secret
    # Generate and persist new secret
    secret = secrets.token_urlsafe(32)
    with open(_ENV_FILE, "a") as f:
        f.write(f"IPC_SECRET={secret}\n")
    _sync_frontend_env(secret)
    return secret


def _sync_frontend_env(secret: str) -> None:
    """Write VITE_BACKEND_PORT and VITE_IPC_SECRET to frontend/.env.local."""
    try:
        port = 7842  # matches settings default; written before settings loads
        lines = [
            f"VITE_BACKEND_PORT={port}\n",
            f"VITE_IPC_SECRET={secret}\n",
        ]
        os.makedirs(os.path.dirname(_FRONTEND_ENV_FILE), exist_ok=True)
        with open(_FRONTEND_ENV_FILE, "w") as f:
            f.writelines(lines)
    except Exception:
        pass  # Non-fatal — Electron reads the secret via stdout anyway


IPC_SECRET: str = _load_or_create_ipc_secret()
START_TIME: float = time.time()

# ---------------------------------------------------------------------------
# Pipeline singletons — initialized in on_startup()
# ---------------------------------------------------------------------------

chat_buffer: ChatBuffer = ChatBuffer()
message_writer: MessageWriter | None = None
message_queue: MessageQueue | None = None
account_age_cache: AccountAgeCache | None = None
detection_engine: DetectionEngine | None = None
moderation_engine: ModerationEngine | None = None
reputation_store: ReputationStore | None = None


# ---------------------------------------------------------------------------
# Startup helpers
# ---------------------------------------------------------------------------

async def _load_whitelist(engine: DetectionEngine) -> None:
    """Load persisted whitelist entries from SQLite into the live ProtectedAccountChecker."""
    import aiosqlite
    try:
        async with aiosqlite.connect(settings.db_path) as db:
            async with db.execute("SELECT username FROM whitelist") as cursor:
                rows = await cursor.fetchall()
        for (username,) in rows:
            engine.protection.add_to_whitelist(username)
        if rows:
            logger.info(
                "Whitelist: loaded %d persisted entr%s",
                len(rows), "y" if len(rows) == 1 else "ies",
            )
    except Exception:
        logger.exception("Failed to load whitelist from DB")


async def _recover_pending_actions() -> None:
    """
    On startup, scan for moderation_actions rows stuck in status='pending'.
    These are actions that were written before a crash but never executed.
    Mark them 'failed' so they don't stay in limbo indefinitely.
    """
    import aiosqlite
    try:
        async with aiosqlite.connect(settings.db_path) as db:
            async with db.execute(
                "SELECT id, action_type, username FROM moderation_actions WHERE status='pending'"
            ) as cursor:
                rows = await cursor.fetchall()
            if rows:
                ids = [r[0] for r in rows]
                await db.execute(
                    f"UPDATE moderation_actions SET status='failed', error_message='Recovered from crash' "
                    f"WHERE id IN ({','.join('?' * len(ids))})",
                    ids,
                )
                await db.commit()
                logger.warning(
                    "Startup recovery: marked %d stuck pending action(s) as failed: %s",
                    len(rows),
                    [(r[0], r[1], r[2]) for r in rows],
                )
    except Exception:
        logger.exception("Startup action recovery failed")


# ---------------------------------------------------------------------------
# Twitch → pipeline bridge
# ---------------------------------------------------------------------------

def _enqueue_twitch_message(
    user_id: str,
    username: str,
    channel: str,
    raw_text: str,
    color: str | None,
    badges: list[str],
) -> None:
    """Build a ChatMessage and enqueue it. Called from the Twitch manager callback."""
    if message_queue is None:
        return
    msg = build_message(
        user_id=user_id,
        username=username,
        channel=channel,
        raw_text=raw_text,
        color=color,
        badges=badges,
    )
    # Annotate account age from cache (None on first seen — lookup queued in background)
    if account_age_cache is not None:
        msg.account_age_days = account_age_cache.get(user_id)
    message_queue.enqueue(msg)


# ---------------------------------------------------------------------------
# Raid lockdown auto-trigger
# ---------------------------------------------------------------------------

async def _apply_raid_profiles(engine) -> None:
    """Query lockdown_profiles for auto_on_raid=1 entries and apply them."""
    if engine is None:
        return
    try:
        import aiosqlite as _aiosqlite
        from api.routes.profiles import _apply_profile_modes
        from twitch.token_store import TOKEN_BROADCASTER_ID as _BCID, token_store as _ts

        broadcaster_id = _ts.retrieve(_BCID) or ""
        channel = settings.default_channel

        async with _aiosqlite.connect(settings.db_path) as db:
            async with db.execute(
                """
                SELECT id, name, emote_only, sub_only, unique_chat,
                       slow_mode, slow_mode_wait_time,
                       followers_only, followers_only_duration
                FROM lockdown_profiles WHERE auto_on_raid = 1
                """
            ) as cursor:
                rows = await cursor.fetchall()

        total = 0
        for row in rows:
            pid, name, emote_only, sub_only, unique_chat, slow_mode, slow_wait, followers_only, followers_dur = row
            n = _apply_profile_modes(
                engine=engine,
                broadcaster_id=broadcaster_id,
                channel=channel,
                triggered_by=f"profile:{pid}:auto_raid",
                emote_only=emote_only,
                sub_only=sub_only,
                unique_chat=unique_chat,
                slow_mode=slow_mode,
                slow_mode_wait_time=slow_wait,
                followers_only=followers_only,
                followers_only_duration=followers_dur,
            )
            total += n
            logger.info("Raid auto-trigger: applied profile %d (%r), %d action(s)", pid, name, n)

        if total:
            logger.info("Raid auto-trigger: %d total action(s) enqueued across %d profile(s)", total, len(rows))
    except Exception:
        logger.exception("Raid auto-trigger failed")


# ---------------------------------------------------------------------------
# Profile helpers
# ---------------------------------------------------------------------------

async def _load_profile_config() -> None:
    """Load per-profile config.json into settings (overrides .env defaults).

    Called early in on_startup() so that all downstream code sees the correct
    thresholds, dry_run state, and channel for this profile.
    """
    path = settings.config_json_path
    if not path or not os.path.exists(path):
        return
    import json
    FIELDS = [
        "dry_run", "auto_timeout_enabled", "auto_ban_enabled",
        "timeout_threshold", "ban_threshold", "alert_threshold",
        "emote_filter_sensitivity", "default_channel",
        "message_retention_days", "health_history_retention_days",
        "flagged_users_retention_days", "moderation_actions_retention_days",
    ]
    try:
        with open(path) as f:
            data = json.load(f)
        for field in FIELDS:
            if field in data:
                setattr(settings, field, data[field])
        logger.info("Loaded profile config from %s", path)
    except Exception:
        logger.exception("Failed to load profile config.json — using defaults")


# ---------------------------------------------------------------------------
# FastAPI lifecycle hooks
# ---------------------------------------------------------------------------

async def on_startup() -> None:
    global message_writer, message_queue, account_age_cache
    global detection_engine, moderation_engine, reputation_store

    # Guard: simulator injection endpoint must never be active in production.
    if settings.simulator_active and not settings.dev_mode:
        raise RuntimeError(
            "TWITCHIDS_SIMULATOR_ACTIVE=true requires TWITCHIDS_DEV=true. "
            "Never enable the simulator in a production install."
        )

    logger.info("TwitchIDS backend starting on port %d (profile=%s)", settings.port, settings.profile_id or "none")

    # Ensure AppData / profile directory exists
    os.makedirs(settings.app_data_dir, exist_ok=True)

    # Initialize SQLite schema
    await init_db(settings.db_path)

    # Load per-profile config.json (overrides .env defaults for this profile)
    await _load_profile_config()

    # Migrate legacy WCM tokens to the profile-namespaced service on first use
    if settings.profile_id:
        from twitch.token_store import migrate_legacy_tokens
        migrate_legacy_tokens(settings.profile_id)

    # Initialize reputation store (persistent cross-session user scores)
    reputation_store = ReputationStore(settings.db_path)
    import storage.reputation as _rep_module
    _rep_module.reputation_store = reputation_store

    # Start account age cache
    client_id = token_store.retrieve(TOKEN_CLIENT_ID) or settings.client_id
    account_age_cache = AccountAgeCache(
        client_id=client_id,
        get_access_token=lambda: (
            (c := twitch_manager.get_client()) and c.get_live_access_token()
        ) or token_store.retrieve(TOKEN_ACCESS),
    )
    await account_age_cache.start()

    # Start message writer
    message_writer = MessageWriter(settings.db_path)
    await message_writer.start()

    # Start moderation engine
    from api.websocket import manager as ws_manager
    moderation_engine = ModerationEngine(settings.db_path)
    moderation_engine.set_ws_manager(ws_manager)
    await moderation_engine.start()

    # Scan for stuck 'pending' actions from a previous crashed session
    await _recover_pending_actions()

    # Start detection engine
    detection_engine = DetectionEngine(chat_buffer)
    detection_engine.set_ws_manager(ws_manager)
    detection_engine.set_moderation_engine(moderation_engine)

    # Start known-bot registry (background refresh loop)
    known_bot_registry = KnownBotRegistry()
    asyncio.create_task(known_bot_registry.start(), name="known_bot_refresh_init")
    detection_engine.set_known_bot_registry(known_bot_registry)

    # Restore persisted whitelist
    await _load_whitelist(detection_engine)

    # Initialize regex filter engine
    from detection.fast.regex_filter import RegexFilterEngine
    import detection.fast.regex_filter as _rfe_module
    _rfe_module.regex_filter_engine = RegexFilterEngine(settings.db_path)
    await _rfe_module.regex_filter_engine.load()

    # Start message queue with buffer + writer + detection
    message_queue = MessageQueue(chat_buffer)
    message_queue.add_processor(message_writer.write)
    message_queue.add_processor(detection_engine.process_message)
    asyncio.create_task(message_queue.run(), name="pipeline-consumer")

    # Hook Twitch manager to enqueue messages through pipeline
    twitch_manager.set_message_handler(_enqueue_twitch_message)

    def _composite_event_handler(event_type: str, **kwargs) -> None:
        """Forward events to detection engine and auto-apply raid lockdown profiles."""
        detection_engine.on_event(event_type, **kwargs)
        if event_type == "channel_raid":
            asyncio.create_task(
                _apply_raid_profiles(moderation_engine),
                name="raid_lockdown",
            )

    twitch_manager.set_event_handler(_composite_event_handler)

    # Load persisted channel
    saved_channel = token_store.retrieve(TOKEN_CHANNEL)
    if saved_channel and not settings.default_channel:
        settings.default_channel = saved_channel
        logger.info("Loaded persisted channel: #%s", saved_channel)

    # Load secondary monitored channels from DB
    secondary_channels: list[str] = []
    try:
        import aiosqlite as _aiosqlite
        async with _aiosqlite.connect(settings.db_path) as _db:
            async with _db.execute("SELECT name FROM monitored_channels ORDER BY added_at") as _cursor:
                secondary_channels = [row[0] async for row in _cursor]
        if secondary_channels:
            logger.info("Loaded %d secondary channel(s): %s", len(secondary_channels), secondary_channels)
    except Exception:
        logger.exception("Failed to load secondary channels from DB")

    # Connect to Twitch (with secondary channels for multi-channel EventSub subscriptions)
    await twitch_manager.connect(extra_channels=secondary_channels)

    # Start WebSocket chat-message batch flusher (100ms window)
    ws_manager.start_batch_flusher()

    # Start background tasks (late import to avoid circular dependency)
    from tasks import (
        heartbeat_loop,
        stdin_listener,
        pipeline_metrics_loop,
        detection_tick_loop,
        retention_loop,
        wal_checkpoint_loop,
    )
    asyncio.create_task(heartbeat_loop())
    asyncio.create_task(stdin_listener())
    asyncio.create_task(pipeline_metrics_loop())
    asyncio.create_task(detection_tick_loop())
    asyncio.create_task(retention_loop(), name="retention")
    asyncio.create_task(wal_checkpoint_loop(), name="wal_checkpoint")

    # Signal readiness to Electron
    emit_ready(port=settings.port, ipc_secret=IPC_SECRET)
    logger.info("Backend ready — port=%d dry_run=%s", settings.port, settings.dry_run)


async def on_shutdown() -> None:
    logger.info("Backend shutting down")
    emit_shutdown("graceful")
