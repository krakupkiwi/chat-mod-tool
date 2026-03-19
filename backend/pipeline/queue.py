"""
Bounded async message queue and pipeline consumer.

The queue sits between the Twitch EventSub callback and all downstream
processing (buffer, storage writer, detectors).

Capacity: 10,000 messages. On overflow, the oldest message is dropped
(popleft from a shadow deque) and the new one is enqueued.

The consumer loop runs as a background asyncio Task. It:
  1. Dequeues one ChatMessage at a time
  2. Adds it to ChatBuffer (all windows)
  3. Dispatches it to registered processor callbacks (storage writer, detectors)
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Awaitable, Callable

from core.telemetry import telemetry
from .buffer import ChatBuffer
from .models import ChatMessage

logger = logging.getLogger(__name__)

QUEUE_MAXSIZE = 10_000
_OVERFLOW_DROP_COUNT = 0


class MessageQueue:
    """
    Bounded asyncio queue with drop-oldest overflow semantics and a
    single consumer loop that fans out to registered processors.
    """

    def __init__(self, buffer: ChatBuffer, maxsize: int = QUEUE_MAXSIZE) -> None:
        self._queue: asyncio.Queue[ChatMessage] = asyncio.Queue(maxsize=maxsize)
        self._maxsize = maxsize
        self._buffer = buffer
        self._processors: list[Callable[[ChatMessage], Awaitable[None]]] = []
        self._dropped = 0
        self._processed = 0
        self._running = False

    def add_processor(self, fn: Callable[[ChatMessage], Awaitable[None]]) -> None:
        """Register an async callback to receive every processed message."""
        self._processors.append(fn)

    def enqueue(self, msg: ChatMessage) -> None:
        """
        Non-blocking enqueue. If the queue is full, drop the oldest item
        and enqueue the new one. Must be called from the event loop.
        """
        if self._queue.full():
            try:
                self._queue.get_nowait()
                self._dropped += 1
                if self._dropped % 100 == 1:
                    logger.warning("Message queue overflow — dropped %d messages so far", self._dropped)
            except asyncio.QueueEmpty:
                pass
        try:
            self._queue.put_nowait(msg)
        except asyncio.QueueFull:
            pass  # Extremely unlikely race; skip silently

    async def run(self) -> None:
        """Consumer loop. Run as an asyncio Task via asyncio.create_task()."""
        self._running = True
        logger.info("Message pipeline consumer started")
        while self._running:
            try:
                msg = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                # Tick: prune expired buffer entries even when idle
                self._buffer.prune()
                continue

            # Add to buffer (all time windows)
            self._buffer.add(msg)
            self._buffer.prune()

            # Fan out to processors
            for fn in self._processors:
                try:
                    await fn(msg)
                except Exception:
                    logger.exception("Processor %s raised an exception", fn.__name__)

            self._processed += 1
            self._queue.task_done()
            telemetry.record_message()
            telemetry.queue_depth = self._queue.qsize()

    def stop(self) -> None:
        self._running = False

    @property
    def depth(self) -> int:
        return self._queue.qsize()

    @property
    def dropped(self) -> int:
        return self._dropped

    @property
    def processed(self) -> int:
        return self._processed
