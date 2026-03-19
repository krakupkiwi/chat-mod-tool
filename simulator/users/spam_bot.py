"""
SpamBotModel — sends identical (or near-identical) messages at a fixed interval.

variation_rate=0.0  → perfectly identical messages (triggers duplicate detector)
variation_rate=0.15 → minor character variations (tests MinHash detector)
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field

from simulator.config import SimulatedMessage
from simulator.generators.template_lib import random_spam_message
from simulator.generators.username_gen import generate_bot_username, generate_user_id

# Cyrillic homoglyph substitution map
_HOMOGLYPHS: dict[str, str] = {
    "a": "\u0430",  # Cyrillic а
    "e": "\u0435",  # Cyrillic е
    "o": "\u043e",  # Cyrillic о
    "c": "\u0441",  # Cyrillic с
    "p": "\u0440",  # Cyrillic р
    "x": "\u0445",  # Cyrillic х
}


def _apply_variations(text: str, variation_rate: float) -> str:
    """Apply minor character variations to defeat exact-hash detection."""
    result = list(text)
    num_changes = max(1, int(len(result) * variation_rate))
    for _ in range(num_changes):
        if not result:
            break
        idx = random.randint(0, len(result) - 1)
        char = result[idx]
        if char.isalpha():
            result[idx] = char.upper() if char.islower() else char.lower()
        elif char == " " and random.random() < 0.3:
            result.insert(idx, " ")
    return "".join(result)


def _apply_homoglyphs(text: str, substitution_rate: float) -> str:
    """Replace ASCII letters with Cyrillic lookalikes."""
    result = []
    for char in text:
        lower = char.lower()
        if lower in _HOMOGLYPHS and random.random() < substitution_rate:
            replacement = _HOMOGLYPHS[lower]
            result.append(replacement)
        else:
            result.append(char)
    return "".join(result)


@dataclass
class SpamBotModel:
    user_id: str
    username: str
    account_age_days: int = field(default_factory=lambda: random.randint(0, 7))
    campaign_message: str = "Follow scambot123 for free subs!"
    variation_rate: float = 0.0
    homoglyph_rate: float = 0.0     # > 0 → evasion test
    message_interval: float = 20.0  # seconds between sends
    attack_type: str = "follower_bot"

    scenario: str = "unknown"
    channel_id: str = "sim_channel"
    cluster_id: str = "spam_cluster"

    def generate_message(self) -> str:
        msg = self.campaign_message or random_spam_message(self.attack_type)
        if self.homoglyph_rate > 0:
            msg = _apply_homoglyphs(msg, self.homoglyph_rate)
        elif self.variation_rate > 0:
            msg = _apply_variations(msg, self.variation_rate)
        return msg

    async def run(self, queue: asyncio.Queue, stop: asyncio.Event) -> None:
        # Stagger start slightly
        await asyncio.sleep(random.uniform(0, 2.0))

        while not stop.is_set():
            msg = SimulatedMessage(
                user_id=self.user_id,
                username=self.username,
                account_age_days=self.account_age_days,
                content=self.generate_message(),
                label="spam" if self.homoglyph_rate == 0 else "homoglyph",
                cluster_id=self.cluster_id,
                scenario=self.scenario,
                channel_id=self.channel_id,
            )
            await queue.put(msg)

            try:
                await asyncio.wait_for(stop.wait(), timeout=self.message_interval)
                break
            except asyncio.TimeoutError:
                pass


def make_spam_bot(
    campaign_message: str = "",
    variation_rate: float = 0.0,
    homoglyph_rate: float = 0.0,
    message_interval: float = 20.0,
    attack_type: str = "follower_bot",
    username_style: str = "sequential",
    account_age_range: tuple[int, int] = (0, 7),
    scenario: str = "unknown",
    channel_id: str = "sim_channel",
    cluster_id: str = "spam_cluster",
) -> SpamBotModel:
    uid = generate_user_id("spam")
    uname = generate_bot_username(username_style)
    age = random.randint(*account_age_range)
    return SpamBotModel(
        user_id=uid,
        username=uname,
        account_age_days=age,
        campaign_message=campaign_message,
        variation_rate=variation_rate,
        homoglyph_rate=homoglyph_rate,
        message_interval=message_interval,
        attack_type=attack_type,
        scenario=scenario,
        channel_id=channel_id,
        cluster_id=cluster_id,
    )
