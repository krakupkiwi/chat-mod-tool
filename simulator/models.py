"""
User models for the simulator.

NormalUserModel — Poisson-distributed timing, varied organic messages.
SpamBotModel    — high-rate identical/near-identical messages.
CoordinatedBotNetwork — synchronised burst firing with configurable jitter.
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Message templates
# ---------------------------------------------------------------------------

ORGANIC_MESSAGES = [
    "LUL that was so good",
    "PogChamp let's go!!",
    "lmao imagine",
    "honestly same",
    "W streamer W chat",
    "no way that just happened",
    "actually insane",
    "chat is cooked today",
    "this is hilarious",
    "bro what",
    "clipclipclip",
    "LETS GO",
    "that was clean",
    "ngl kinda based",
    "monkaS",
    "oh my god",
    "banger stream as always",
    "I can't stop laughing",
    "first time watching, already a fan",
    "POG",
    "omg omg omg",
    "this game is so hard",
    "skill issue",
    "wait what happened",
    "KEKW",
]

SPAM_TEMPLATES = [
    "Follow {bot_channel} for FREE SUBS! Massive giveaway happening NOW",
    "BOOST your stream — buy followers at {bot_channel} use code TWITCH50",
    "FREE BITS at {bot_channel} — limited time offer!",
    "Visit {bot_channel} for the best streaming tips and giveaways",
    "WIN FREE SUBS just follow {bot_channel} RIGHT NOW",
]

BOT_CHANNEL_NAMES = ["scambot_tv", "freesubs_xyz", "boost_stream_99", "giveaway_central"]


@dataclass
class SimMessage:
    user_id: str
    username: str
    content: str
    timestamp: float
    is_bot: bool = False
    label: str = "normal"   # "normal" | "spam" | "bot_raid"


# ---------------------------------------------------------------------------
# Normal user model
# ---------------------------------------------------------------------------

class NormalUserModel:
    """Simulates a single organic viewer."""

    def __init__(self, user_id: str, username: str, msgs_per_minute: float = 2.0) -> None:
        self.user_id = user_id
        self.username = username
        self._rate = msgs_per_minute / 60.0  # msgs/sec
        self._next_send = time.monotonic() + random.expovariate(self._rate)

    def tick(self, now: float) -> SimMessage | None:
        if now < self._next_send:
            return None
        interval = random.expovariate(self._rate)
        self._next_send = now + interval
        content = random.choice(ORGANIC_MESSAGES)
        # Occasional variation
        if random.random() < 0.2:
            content = content + " " + random.choice(["Pog", "KEKW", "LUL", "monkaS", ":)"])
        return SimMessage(
            user_id=self.user_id,
            username=self.username,
            content=content,
            timestamp=time.time(),
            is_bot=False,
            label="normal",
        )


# ---------------------------------------------------------------------------
# Spam bot model
# ---------------------------------------------------------------------------

class SpamBotModel:
    """Single spam bot — sends near-identical messages at a fixed rate."""

    def __init__(
        self,
        user_id: str,
        username: str,
        msgs_per_minute: float = 20.0,
        variation_rate: float = 0.1,
    ) -> None:
        self.user_id = user_id
        self.username = username
        self._rate = msgs_per_minute / 60.0
        self._variation_rate = variation_rate
        self._template = random.choice(SPAM_TEMPLATES)
        self._bot_channel = random.choice(BOT_CHANNEL_NAMES)
        self._next_send = time.monotonic()

    def tick(self, now: float) -> SimMessage | None:
        if now < self._next_send:
            return None
        self._next_send = now + (1.0 / self._rate) * random.uniform(0.9, 1.1)
        content = self._template.format(bot_channel=self._bot_channel)
        if random.random() < self._variation_rate:
            # Minor variation to evade exact-hash detection
            content = content.replace("FREE", random.choice(["FREE", "free", "F R E E"]))
        return SimMessage(
            user_id=self.user_id,
            username=self.username,
            content=content,
            timestamp=time.time(),
            is_bot=True,
            label="spam",
        )


# ---------------------------------------------------------------------------
# Coordinated bot network
# ---------------------------------------------------------------------------

class CoordinatedBotNetwork:
    """
    A network of bots that burst-fire the same message simultaneously,
    with configurable jitter (in seconds) to simulate evasion attempts.
    """

    def __init__(
        self,
        bot_count: int = 30,
        burst_interval: float = 5.0,
        jitter_seconds: float = 0.5,
        username_prefix: str = "viewer",
    ) -> None:
        self._burst_interval = burst_interval
        self._jitter = jitter_seconds
        self._template = random.choice(SPAM_TEMPLATES)
        self._bot_channel = random.choice(BOT_CHANNEL_NAMES)

        self._bots = [
            {
                "user_id": f"bot_{i:04d}",
                "username": f"{username_prefix}{random.randint(1000, 9999)}",
                "next_send": time.monotonic() + random.uniform(0, burst_interval),
            }
            for i in range(bot_count)
        ]
        self._next_burst = time.monotonic() + burst_interval

    def tick(self, now: float) -> list[SimMessage]:
        messages = []
        if now < self._next_burst:
            return messages

        self._next_burst = now + self._burst_interval
        template = random.choice(SPAM_TEMPLATES)
        bot_channel = self._bot_channel

        for bot in self._bots:
            jitter = random.uniform(0, self._jitter)
            send_at = now + jitter
            content = template.format(bot_channel=bot_channel)
            messages.append(
                SimMessage(
                    user_id=bot["user_id"],
                    username=bot["username"],
                    content=content,
                    timestamp=time.time() + jitter,
                    is_bot=True,
                    label="bot_raid",
                )
            )

        return messages
