"""
Simulator runner — loads a YAML scenario, spins up user models, and streams
SimMessages to registered output adapters (WebSocket inject or JSONL file).

Usage:
    python simulator.py --scenario scenarios/bot_raid.yaml --rate 1.0 --duration 60
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from pathlib import Path
from typing import Callable, Awaitable

import yaml

from models import CoordinatedBotNetwork, NormalUserModel, SimMessage, SpamBotModel

logger = logging.getLogger(__name__)

OutputAdapter = Callable[[SimMessage], Awaitable[None]]


def load_scenario(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


class SimulatorRunner:
    def __init__(self, scenario: dict, rate_multiplier: float = 1.0) -> None:
        self._scenario = scenario
        self._rate = rate_multiplier
        self._adapters: list[OutputAdapter] = []

        self._normal_users: list[NormalUserModel] = []
        self._spam_bots: list[SpamBotModel] = []
        self._bot_networks: list[CoordinatedBotNetwork] = []

        self._build_models()

    def add_adapter(self, adapter: OutputAdapter) -> None:
        self._adapters.append(adapter)

    def _build_models(self) -> None:
        cfg = self._scenario

        # Normal users
        n_normal = cfg.get("normal_users", {}).get("count", 50)
        mpm = cfg.get("normal_users", {}).get("msgs_per_minute", 2.0) * self._rate
        for i in range(n_normal):
            self._normal_users.append(
                NormalUserModel(
                    user_id=f"viewer_{i:04d}",
                    username=f"viewer{random.randint(100, 9999)}",
                    msgs_per_minute=mpm * random.uniform(0.5, 2.0),
                )
            )

        # Spam bots
        spam_cfg = cfg.get("spam_bots", {})
        n_spam = spam_cfg.get("count", 0)
        spam_mpm = spam_cfg.get("msgs_per_minute", 15.0) * self._rate
        for i in range(n_spam):
            self._spam_bots.append(
                SpamBotModel(
                    user_id=f"spambot_{i:04d}",
                    username=f"bot{random.randint(1000, 9999)}",
                    msgs_per_minute=spam_mpm,
                    variation_rate=spam_cfg.get("variation_rate", 0.1),
                )
            )

        # Coordinated bot networks
        for net_cfg in cfg.get("bot_networks", []):
            self._bot_networks.append(
                CoordinatedBotNetwork(
                    bot_count=net_cfg.get("bot_count", 30),
                    burst_interval=net_cfg.get("burst_interval", 5.0),
                    jitter_seconds=net_cfg.get("jitter_seconds", 0.5),
                    username_prefix=net_cfg.get("username_prefix", "viewer"),
                )
            )

    async def run(self, duration_seconds: float) -> None:
        logger.info(
            "Simulator starting: %d normal users, %d spam bots, %d networks, duration=%ds",
            len(self._normal_users),
            len(self._spam_bots),
            len(self._bot_networks),
            int(duration_seconds),
        )

        end = time.monotonic() + duration_seconds
        total_sent = 0

        while time.monotonic() < end:
            now = time.monotonic()
            batch: list[SimMessage] = []

            for user in self._normal_users:
                msg = user.tick(now)
                if msg:
                    batch.append(msg)

            for bot in self._spam_bots:
                msg = bot.tick(now)
                if msg:
                    batch.append(msg)

            for network in self._bot_networks:
                batch.extend(network.tick(now))

            for msg in batch:
                for adapter in self._adapters:
                    try:
                        await adapter(msg)
                    except Exception:
                        logger.exception("Adapter error")
                total_sent += 1

            if batch:
                logger.debug("Sent %d messages (total=%d)", len(batch), total_sent)

            await asyncio.sleep(0.05)  # 50ms tick — 20 ticks/sec

        logger.info("Simulator finished. Total messages sent: %d", total_sent)


# ---------------------------------------------------------------------------
# Output adapters
# ---------------------------------------------------------------------------

def make_websocket_adapter(host: str, port: int, secret: str) -> OutputAdapter:
    """Streams messages to the detection engine's WebSocket inject endpoint."""
    import websockets

    _ws = None

    async def adapter(msg: SimMessage) -> None:
        nonlocal _ws
        url = f"ws://{host}:{port}/ws/inject?secret={secret}"

        if _ws is None or _ws.closed:
            _ws = await websockets.connect(url)

        payload = json.dumps({
            "type": "simulated_message",
            "data": {
                "user_id": msg.user_id,
                "username": msg.username,
                "content": msg.content,
                "channel_id": "simulator",
                "timestamp": msg.timestamp,
                "is_bot": msg.is_bot,
                "label": msg.label,
            },
        })
        await _ws.send(payload)

    return adapter


def make_jsonl_adapter(output_path: str) -> OutputAdapter:
    """Writes labeled messages to a JSONL file for offline analysis."""
    fh = open(output_path, "w", encoding="utf-8")

    async def adapter(msg: SimMessage) -> None:
        line = json.dumps({
            "user_id": msg.user_id,
            "username": msg.username,
            "content": msg.content,
            "timestamp": msg.timestamp,
            "is_bot": msg.is_bot,
            "label": msg.label,
        })
        fh.write(line + "\n")
        fh.flush()

    return adapter
