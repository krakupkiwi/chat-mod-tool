"""
Twitch client singleton — shared between main.py (startup) and API routes
(reconnect on channel change) without circular imports.

All heavy imports are deferred inside connect() so this module is safe to
import at any time.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

_client = None  # TwitchClient instance, or None if not connected
_message_handler = None  # Optional callable(user_id, username, channel, raw_text, color, badges)
_event_handler = None    # Optional callable(event_type: str, **kwargs)


def get_client():
    """Return the active TwitchClient, or None if not connected."""
    return _client


async def subscribe_channel(channel_name: str, broadcaster_id: str) -> None:
    """
    Subscribe to chat messages for a secondary channel at runtime.
    Delegates to the active TwitchClient.subscribe_channel().
    Raises RuntimeError if the client is not connected.
    """
    if _client is None:
        raise RuntimeError("Twitch client not connected")
    await _client.subscribe_channel(channel_name, broadcaster_id)


def set_event_handler(handler) -> None:
    """
    Register a callback invoked for suppression-relevant EventSub events.
    Signature: handler(event_type: str, **kwargs)
    """
    global _event_handler
    _event_handler = handler


def set_message_handler(handler) -> None:
    """
    Register a callback invoked for every incoming chat message.
    Used by main.py to route messages into the processing pipeline.
    Signature: handler(user_id, username, channel, raw_text, color, badges)
    """
    global _message_handler
    _message_handler = handler


async def connect(extra_channels: list[str] | None = None) -> None:
    """
    Connect (or reconnect) the Twitch client using current settings and
    stored credentials.  Safe to call multiple times — stops the old client
    before starting a new one.

    extra_channels: list of secondary channel names to subscribe to in addition
    to the default channel.  Loaded from the monitored_channels DB table by startup.py.
    """
    global _client

    # Lazy imports keep this module import-safe everywhere
    from api.websocket import manager as ws_manager
    from core.config import settings
    from core.ipc import emit_error
    from twitch.client import create_client
    from twitch.token_store import TOKEN_ACCESS, TOKEN_CLIENT_ID, TOKEN_CLIENT_SECRET, TOKEN_REFRESH, token_store

    client_id = token_store.retrieve(TOKEN_CLIENT_ID) or settings.client_id
    client_secret = token_store.retrieve(TOKEN_CLIENT_SECRET) or ""
    access_token = token_store.retrieve(TOKEN_ACCESS)
    refresh_token = token_store.retrieve(TOKEN_REFRESH) or ""
    channel = settings.default_channel

    if not client_id or not client_secret or not access_token or not refresh_token:
        logger.info("Missing credentials — skipping Twitch connection")
        return

    if not channel:
        logger.info("No default_channel configured — skipping Twitch connection")
        return

    # Stop existing client before creating a new one
    if _client is not None:
        try:
            await _client.close()
        except Exception:
            pass
        _client = None

    try:
        new_client = await create_client(
            client_id=client_id,
            client_secret=client_secret,
            access_token=access_token,
            refresh_token=refresh_token,
            channel_name=channel,
            extra_channels=extra_channels or [],
        )

        async def on_chat_message(event) -> None:
            colour = event.colour
            uid = str(event.chatter.id)
            uname = event.chatter.name
            text = event.text
            color_str = colour.html if colour else None
            badge_list = [f"{b.set_id}/{b.id}" for b in getattr(event, "badges", [])]

            # Derive actual channel from the event broadcaster name.
            # Secondary channel messages have a different broadcaster than `channel`.
            broadcaster = getattr(event, "broadcaster", None)
            actual_channel = (getattr(broadcaster, "name", None) or channel).lower()

            # Route through processing pipeline
            if _message_handler is not None:
                _message_handler(uid, uname, actual_channel, text, color_str, badge_list)

            # Serialize fragments for emote rendering
            fragment_list = []
            for frag in getattr(event, "fragments", []):
                f: dict = {"type": frag.type, "text": frag.text}
                if frag.type == "emote" and frag.emote is not None:
                    f["emote_id"] = frag.emote.id
                fragment_list.append(f)

            # Buffer raw message for batched WebSocket delivery (100ms window).
            # queue_chat_message() is non-blocking — the batch flusher coroutine
            # delivers accumulated messages in a single 'chat_messages_batch' frame
            # every 100ms, cutting per-message WS overhead by ~8x at high volume.
            import time as _time
            ws_manager.queue_chat_message({
                "type": "chat_message",
                "ts": _time.time(),
                "user_id": uid,
                "username": uname,
                "content": text,
                "channel": actual_channel,
                "color": color_str,
                "badges": badge_list,
                "fragments": fragment_list,
            })

        async def on_twitch_event(event_type: str, event_data) -> None:
            # Build event-specific details for subscription events
            extra: dict = {}
            if event_type == "subscription_new":
                extra = {
                    "username": event_data.user.name if event_data.user else "anonymous",
                    "tier": getattr(event_data, "tier", "1000"),
                    "is_gift": bool(getattr(event_data, "gift", False)),
                }
            elif event_type == "subscription_resub":
                msg_text = ""
                if hasattr(event_data, "message") and event_data.message:
                    msg_text = getattr(event_data.message, "text", "") or ""
                extra = {
                    "username": event_data.user.name if event_data.user else "anonymous",
                    "tier": getattr(event_data, "tier", "1000"),
                    "cumulative_months": getattr(event_data, "cumulative_months", 0),
                    "months": getattr(event_data, "months", 0),
                    "message": msg_text,
                }
            elif event_type == "subscription_gift":
                gifter = getattr(event_data, "user", None)
                extra = {
                    "username": gifter.name if gifter else "anonymous",
                    "tier": getattr(event_data, "tier", "1000"),
                    "count": int(getattr(event_data, "total", 1)),
                    "cumulative_total": getattr(event_data, "cumulative_total", None),
                    "anonymous": bool(getattr(event_data, "anonymous", False)),
                }

            # AutoMod hold — forward full details to UI for mod review
            if event_type == "automod_message_hold":
                msg = getattr(event_data, "message", None)
                user = getattr(event_data, "user", None) or getattr(event_data, "chatter", None)
                await ws_manager.broadcast_event(
                    "automod_hold",
                    message_id=getattr(event_data, "message_id", ""),
                    user_id=str(user.id) if user else "",
                    username=user.name if user else "unknown",
                    content=getattr(msg, "text", "") if msg else "",
                    category=getattr(event_data, "category", ""),
                    level=getattr(event_data, "level", 0),
                    held_at=getattr(event_data, "held_at", None),
                )
                return  # don't forward as generic twitch_event

            await ws_manager.broadcast_event("twitch_event", twitch_event_type=event_type, **extra)
            if _event_handler is not None:
                kwargs = {}
                # Pass gift count for mass-gift suppression gating
                if hasattr(event_data, "total") and event_data.total:
                    kwargs["gift_count"] = int(event_data.total)
                _event_handler(event_type, **kwargs)

        async def on_ready() -> None:
            await ws_manager.broadcast_event(
                "connection_status", connected=True, channel=channel
            )

        new_client.on_chat_message(on_chat_message)
        new_client.on_twitch_event(on_twitch_event)
        new_client.on_ready(on_ready)

        _client = new_client
        asyncio.create_task(new_client.start(with_adapter=False))
        logger.info("Twitch client connecting to #%s", channel)

        # Let the renderer know we're attempting to connect
        await ws_manager.broadcast_event(
            "connection_status", connected=False, channel=channel
        )

    except Exception as e:
        logger.error("Failed to create Twitch client: %s", e)
        emit_error(f"Twitch connection failed: {e}", code="TWITCH_CONN_FAIL")
