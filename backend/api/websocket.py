"""
WebSocket broadcaster — Channel 2 IPC between Python backend and Electron renderer.

All live events (chat messages, health scores, alerts, moderation actions)
are pushed to connected clients via this module. The renderer never polls
for live data — everything is pushed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from core.telemetry import telemetry

logger = logging.getLogger(__name__)


_CHAT_BATCH_INTERVAL = 0.10  # seconds — flush chat_message queue every 100ms


class ConnectionManager:
    """Manages all active WebSocket connections from the Electron renderer."""

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        # Pending chat_message payloads waiting to be batched.
        # Flushed every _CHAT_BATCH_INTERVAL seconds via _flush_chat_batch().
        self._chat_batch: list[dict] = []
        self._batch_task: asyncio.Task | None = None

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._connections.add(ws)
        telemetry.ws_clients = len(self._connections)
        logger.info("WebSocket client connected (%d total)", len(self._connections))

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(ws)
        telemetry.ws_clients = len(self._connections)
        logger.info("WebSocket client disconnected (%d remaining)", len(self._connections))

    async def broadcast(self, data: dict[str, Any]) -> None:
        """Push a JSON event to all connected clients.

        Uses asyncio.gather() so all sends run concurrently — a single slow
        client's TCP send-buffer backpressure no longer stalls every other
        client.  Each send is individually exception-isolated so one dead
        socket cannot prevent delivery to the rest.
        """
        if not self._connections:
            return

        payload = json.dumps(data)

        async with self._lock:
            connections = list(self._connections)

        if not connections:
            return

        async def _send(ws: WebSocket) -> None:
            await asyncio.wait_for(ws.send_text(payload), timeout=0.05)

        results = await asyncio.gather(
            *[_send(ws) for ws in connections],
            return_exceptions=True,
        )

        # Clean up any connections that raised or timed out during send
        dead = [ws for ws, r in zip(connections, results) if isinstance(r, Exception)]
        if dead:
            async with self._lock:
                for ws in dead:
                    self._connections.discard(ws)

    async def broadcast_event(self, event_type: str, **kwargs) -> None:
        """Convenience wrapper that adds type and timestamp."""
        await self.broadcast({"type": event_type, "ts": time.time(), **kwargs})

    def queue_chat_message(self, payload: dict) -> None:
        """
        Buffer a chat_message event for batched delivery.

        Instead of broadcasting one WebSocket frame per incoming chat message
        (up to 83/s at 5K msg/min), this method accumulates messages and the
        background _flush_chat_batch() coroutine delivers them in a single
        'chat_messages_batch' frame every 100ms.  This cuts WS frame count by
        ~8x at high volume while keeping perceived latency < 150ms.

        Must be called from the asyncio event loop (not thread-safe).
        """
        self._chat_batch.append(payload)

    async def _flush_chat_batch(self) -> None:
        """
        Background loop: flush buffered chat_message payloads every 100ms.

        Called once from startup.py after the manager is created.  Runs for
        the lifetime of the process.
        """
        while True:
            await asyncio.sleep(_CHAT_BATCH_INTERVAL)
            if self._chat_batch and self._connections:
                batch = self._chat_batch
                self._chat_batch = []
                await self.broadcast({"type": "chat_messages_batch", "messages": batch})

    def start_batch_flusher(self) -> None:
        """Schedule the background batch-flush loop (call once from startup)."""
        if self._batch_task is None or self._batch_task.done():
            self._batch_task = asyncio.create_task(
                self._flush_chat_batch(), name="ws_chat_batch_flusher"
            )

    @property
    def connection_count(self) -> int:
        return len(self._connections)


# Module-level singleton used by FastAPI routes and the detection engine
manager = ConnectionManager()


async def handle_websocket(ws: WebSocket, ipc_secret: str, client_secret: str = "") -> None:
    """
    FastAPI WebSocket handler. Validates IPC secret, then listens until disconnect.
    The renderer mostly receives — it can also send commands (future use).
    client_secret is injected by the endpoint via FastAPI Query() — more reliable
    than reading ws.query_params manually.
    """
    import sys
    # Bypass logging — print directly to stderr so it always appears in the terminal
    print(
        f"[WEBSOCKET] handle_websocket called — "
        f"client_secret_len={len(client_secret)} "
        f"ipc_secret_len={len(ipc_secret)} "
        f"match={client_secret == ipc_secret}",
        file=sys.stderr,
        flush=True,
    )

    if client_secret != ipc_secret:
        await ws.accept()
        await ws.close(code=4003, reason="Forbidden")
        logger.warning("WebSocket connection rejected: secret mismatch")
        return

    await manager.connect(ws)
    try:
        while True:
            # Keep the connection alive; renderer rarely sends anything
            data = await ws.receive_text()
            # Future: handle commands from renderer here
            logger.debug("WebSocket received: %s", data[:100])
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug("WebSocket error: %s", e)
    finally:
        await manager.disconnect(ws)
