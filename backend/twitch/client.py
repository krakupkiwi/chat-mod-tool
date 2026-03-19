"""
TwitchIO 3.x EventSub WebSocket client wrapper.

TwitchIO 3.x flow:
  1. Client(client_id, client_secret) — client_secret used to generate app token via
     client-credentials grant.
  2. load_tokens() override calls add_token(user_access_token, refresh_token) to register
     the streamer's user token for EventSub subscriptions that require user scope.
  3. setup_hook() override subscribes to EventSub events after login completes.
  4. Event handlers use the 3.x dispatch names (e.g. event_message, event_raid).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable

import twitchio
from twitchio.eventsub.subscriptions import (
    ChannelRaidSubscription,
    ChannelSubscribeMessageSubscription,
    ChannelSubscribeSubscription,
    ChannelSubscriptionGiftSubscription,
    ChatMessageSubscription,
    HypeTrainBeginSubscription,
    HypeTrainEndSubscription,
    StreamOnlineSubscription,
)

try:
    from twitchio.eventsub.subscriptions import AutomodMessageHoldSubscription as _AutomodHold
    _AUTOMOD_AVAILABLE = True
except ImportError:
    _AutomodHold = None
    _AUTOMOD_AVAILABLE = False

logger = logging.getLogger(__name__)


class TwitchClient(twitchio.Client):
    """
    Wraps TwitchIO Client with application-specific event handling.
    Fires callbacks registered via on_chat_message() and on_twitch_event().
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        access_token: str,
        refresh_token: str,
        channel_name: str,
        extra_channels: list[str] | None = None,
    ) -> None:
        # TwitchIO 3.x: client_secret is used for client-credentials app token generation.
        # The user access token is registered separately via load_tokens() -> add_token().
        super().__init__(client_id=client_id, client_secret=client_secret)
        self._user_access_token = access_token
        self._user_refresh_token = refresh_token
        self.channel_name = channel_name
        self._extra_channels: list[str] = extra_channels or []
        self._message_callbacks: list[Callable] = []
        self._event_callbacks: list[Callable] = []
        self._ready_callbacks: list[Callable] = []
        self._connected = False
        self._bot_user_id: str | None = None
        self._subscribed_broadcaster_ids: set[str] = set()

    def on_chat_message(self, callback: Callable) -> None:
        self._message_callbacks.append(callback)

    def on_twitch_event(self, callback: Callable) -> None:
        self._event_callbacks.append(callback)

    def on_ready(self, callback: Callable) -> None:
        self._ready_callbacks.append(callback)

    @property
    def is_connected(self) -> bool:
        return self._connected

    def get_live_access_token(self) -> str | None:
        """
        Return the current live access token from TwitchIO's internal HTTP client.
        TwitchIO refreshes tokens silently; this always returns the latest value.
        Falls back to the token stored in keyring.
        """
        try:
            if self._bot_user_id and hasattr(self, "_http") and self._http:
                tokens = getattr(self._http, "_tokens", {})
                entry = tokens.get(int(self._bot_user_id))
                if entry:
                    return getattr(entry, "token", None) or getattr(entry, "access_token", None)
        except Exception:
            pass
        return None

    # --- TwitchIO 3.x lifecycle ---

    async def load_tokens(self, path: str | None = None) -> None:
        """Register the streamer's user token so EventSub subscriptions can use it."""
        validated = await self.add_token(self._user_access_token, self._user_refresh_token)
        self._bot_user_id = str(validated.user_id)
        logger.info("Registered user token for user_id=%s", self._bot_user_id)

    async def setup_hook(self) -> None:
        """Subscribe to EventSub events after login completes."""
        if not self._bot_user_id:
            logger.error("setup_hook: bot_user_id not set — cannot subscribe to events")
            return

        # Resolve channel name → broadcaster_user_id
        users = await self.fetch_users(logins=[self.channel_name])
        if not users:
            logger.error("setup_hook: channel '%s' not found on Twitch", self.channel_name)
            return

        broadcaster_id = str(users[0].id)
        bot_id = self._bot_user_id
        logger.info("Subscribing to events for broadcaster_id=%s bot_id=%s", broadcaster_id, bot_id)

        # Persist broadcaster_id for moderation engine use
        from twitch.token_store import TOKEN_BROADCASTER_ID, token_store
        token_store.store(TOKEN_BROADCASTER_ID, broadcaster_id)

        # Chat messages require broadcaster + bot user token
        await self.subscribe_websocket(
            ChatMessageSubscription(broadcaster_user_id=broadcaster_id, user_id=bot_id),
            token_for=bot_id,
        )
        self._subscribed_broadcaster_ids.add(broadcaster_id)

        # Subscribe to secondary channels
        if self._extra_channels:
            extra_users = await self.fetch_users(logins=self._extra_channels)
            for eu in extra_users:
                eid = str(eu.id)
                try:
                    await self.subscribe_websocket(
                        ChatMessageSubscription(broadcaster_user_id=eid, user_id=bot_id),
                        token_for=bot_id,
                    )
                    self._subscribed_broadcaster_ids.add(eid)
                    logger.info("Subscribed to secondary channel #%s (id=%s)", eu.name, eid)
                except Exception as exc:
                    logger.warning("Could not subscribe to secondary channel #%s: %s", eu.name, exc)

        # Broadcaster-only events — use broadcaster's token (same user in single-account setup)
        for sub in [
            StreamOnlineSubscription(broadcaster_user_id=broadcaster_id),
            ChannelRaidSubscription(to_broadcaster_user_id=broadcaster_id),
            HypeTrainBeginSubscription(broadcaster_user_id=broadcaster_id),
            HypeTrainEndSubscription(broadcaster_user_id=broadcaster_id),
            ChannelSubscriptionGiftSubscription(broadcaster_user_id=broadcaster_id),
            ChannelSubscribeSubscription(broadcaster_user_id=broadcaster_id),
            ChannelSubscribeMessageSubscription(broadcaster_user_id=broadcaster_id),
        ]:
            try:
                await self.subscribe_websocket(sub, token_for=bot_id)
            except Exception as e:
                logger.warning("Could not subscribe to %s: %s", type(sub).__name__, e)

        # AutoMod message hold (requires moderator:manage:automod scope)
        if _AUTOMOD_AVAILABLE and _AutomodHold is not None:
            try:
                await self.subscribe_websocket(
                    _AutomodHold(broadcaster_user_id=broadcaster_id, moderator_user_id=bot_id),
                    token_for=bot_id,
                )
                logger.info("AutoMod message hold subscription active")
            except Exception as e:
                logger.warning("Could not subscribe to AutomodMessageHold: %s", e)

        self._connected = True
        logger.info("EventSub subscriptions active for #%s", self.channel_name)

        for cb in self._ready_callbacks:
            try:
                await cb() if asyncio.iscoroutinefunction(cb) else cb()
            except Exception:
                logger.exception("Error in ready callback")

    # --- TwitchIO 3.x event handlers (dispatch names per _SUB_MAPPING) ---

    async def event_message(self, payload: twitchio.ChatMessage) -> None:
        """channel.chat.message → dispatched as 'message'"""
        for callback in self._message_callbacks:
            try:
                await callback(payload) if asyncio.iscoroutinefunction(callback) else callback(payload)
            except Exception:
                logger.exception("Error in chat message callback")

    async def event_stream_online(self, payload: twitchio.StreamOnline) -> None:
        logger.info("Stream online: %s", self.channel_name)
        await self._fire_event("stream_online", payload)

    async def event_raid(self, payload: twitchio.ChannelRaid) -> None:
        logger.info("Incoming raid")
        await self._fire_event("channel_raid", payload)

    async def event_hype_train(self, payload: twitchio.HypeTrainBegin) -> None:
        logger.info("Hype train started")
        await self._fire_event("hype_train_begin", payload)

    async def event_hype_train_end(self, payload: twitchio.HypeTrainEnd) -> None:
        logger.info("Hype train ended")
        await self._fire_event("hype_train_end", payload)

    async def event_subscription_gift(self, payload: twitchio.ChannelSubscriptionGift) -> None:
        logger.info("Gift sub event")
        await self._fire_event("subscription_gift", payload)

    async def event_subscription(self, payload: twitchio.ChannelSubscribe) -> None:
        """channel.subscribe — new sub (not a resub)"""
        uname = payload.user.name if payload.user else "unknown"
        logger.info("New sub: %s tier=%s gift=%s", uname, payload.tier, payload.gift)
        await self._fire_event("subscription_new", payload)

    async def event_subscription_message(self, payload: twitchio.ChannelSubscriptionMessage) -> None:
        """channel.subscription.message — resub with a message"""
        uname = payload.user.name if payload.user else "unknown"
        logger.info("Resub: %s tier=%s months=%s", uname, payload.tier, payload.cumulative_months)
        await self._fire_event("subscription_resub", payload)

    async def subscribe_channel(self, channel_name: str, broadcaster_id: str) -> None:
        """
        Subscribe to chat messages for a secondary channel at runtime (no restart needed).
        broadcaster_id must be pre-resolved by the caller (stored in monitored_channels).
        """
        if not self._bot_user_id:
            raise RuntimeError("Bot user ID not set — setup_hook not yet complete")
        if broadcaster_id in self._subscribed_broadcaster_ids:
            logger.debug("Already subscribed to broadcaster_id=%s (%s)", broadcaster_id, channel_name)
            return
        await self.subscribe_websocket(
            ChatMessageSubscription(broadcaster_user_id=broadcaster_id, user_id=self._bot_user_id),
            token_for=self._bot_user_id,
        )
        self._subscribed_broadcaster_ids.add(broadcaster_id)
        logger.info("Runtime subscribe: #%s (broadcaster_id=%s)", channel_name, broadcaster_id)

    async def event_token_refreshed(self, payload: twitchio.authentication.ValidateTokenPayload) -> None:
        """
        Called by TwitchIO whenever it silently refreshes the user access token.
        Write the new token back to keyring so AccountAgeCache always has a valid one.
        """
        try:
            from twitch.token_store import TOKEN_ACCESS, TOKEN_REFRESH, token_store
            new_access = getattr(payload, "token", None) or getattr(payload, "access_token", None)
            new_refresh = getattr(payload, "refresh_token", None)
            if new_access:
                token_store.store(TOKEN_ACCESS, new_access)
                logger.debug("Refreshed access token written to keyring")
            if new_refresh:
                token_store.store(TOKEN_REFRESH, new_refresh)
        except Exception as e:
            logger.warning("Failed to write refreshed token to keyring: %s", e)

    async def event_automod_message_hold(self, payload) -> None:
        """automod.message.hold — a message is pending AutoMod review."""
        logger.debug("AutoMod hold event received")
        await self._fire_event("automod_message_hold", payload)

    async def event_error(self, payload: twitchio.EventErrorPayload) -> None:
        logger.error("TwitchIO error in listener '%s': %s", getattr(payload, 'listener', '?'), payload.error)

    async def _fire_event(self, event_type: str, event_data) -> None:
        for callback in self._event_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(event_type, event_data)
                else:
                    callback(event_type, event_data)
            except Exception:
                logger.exception("Error in event callback for %s", event_type)


async def create_client(
    client_id: str,
    client_secret: str,
    access_token: str,
    refresh_token: str,
    channel_name: str,
    extra_channels: list[str] | None = None,
) -> TwitchClient:
    """
    Factory: creates a TwitchClient configured for TwitchIO 3.x EventSub WebSocket.
    Call client.start() after registering callbacks to begin the connection.
    """
    client = TwitchClient(
        client_id=client_id,
        client_secret=client_secret,
        access_token=access_token,
        refresh_token=refresh_token,
        channel_name=channel_name,
        extra_channels=extra_channels or [],
    )
    logger.info("Creating Twitch client for channel: %s", channel_name)
    return client
