"""
WebSocketAdapter — injects simulated messages into the running detection engine.

Connects to ws://127.0.0.1:{port}/ws/inject?secret={ipc_secret}
The backend must be started with --dev flag to enable the inject endpoint.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict

logger = logging.getLogger(__name__)


class WebSocketAdapter:
    def __init__(self, url: str) -> None:
        self.url = url
        self._ws = None
        self._send_count = 0
        self._error_count = 0

    async def connect(self) -> None:
        import websockets
        self._ws = await websockets.connect(
            self.url,
            ping_interval=20,
            ping_timeout=10,
            max_size=None,
        )
        logger.info("Simulator connected to %s", self.url)

    async def send(self, message: "SimulatedMessage") -> None:
        if self._ws is None:
            await self.connect()
        payload = {"type": "simulated_message", "data": asdict(message)}
        try:
            await self._ws.send(json.dumps(payload))
            self._send_count += 1
        except Exception as exc:
            self._error_count += 1
            logger.warning("WS send error: %s", exc)
            # Try to reconnect once
            try:
                await self.connect()
                await self._ws.send(json.dumps(payload))
                self._send_count += 1
            except Exception:
                pass

    async def close(self) -> None:
        if self._ws:
            await self._ws.close()
            self._ws = None

    @property
    def stats(self) -> dict:
        return {"sent": self._send_count, "errors": self._error_count}

    # --- Drain loop ---

    async def drain_queue(
        self,
        queue: asyncio.Queue,
        stop: asyncio.Event,
        rate_limit_mpm: int = 0,
    ) -> None:
        """
        Consume messages from the queue and send to the backend.
        rate_limit_mpm=0 means no cap (send as fast as possible).
        """
        await self.connect()

        if rate_limit_mpm > 0:
            min_interval = 60.0 / rate_limit_mpm
        else:
            min_interval = 0.0

        last_send = 0.0

        while not stop.is_set() or not queue.empty():
            try:
                msg = queue.get_nowait()
            except asyncio.QueueEmpty:
                if stop.is_set():
                    break
                await asyncio.sleep(0.005)
                continue

            if min_interval > 0:
                now = asyncio.get_event_loop().time()
                elapsed = now - last_send
                if elapsed < min_interval:
                    await asyncio.sleep(min_interval - elapsed)
                last_send = asyncio.get_event_loop().time()

            await self.send(msg)

        await self.close()
        logger.info("WS adapter done — sent=%d errors=%d", self._send_count, self._error_count)
