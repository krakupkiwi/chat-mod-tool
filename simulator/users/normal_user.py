"""
NormalUserModel — simulates organic viewer behavior.

Timing: Poisson-distributed inter-message delay (exponential distribution).
Messages: drawn from a curated template bank with emote variation.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field

from simulator.config import SimulatedMessage
from simulator.generators.markov import random_markov_message
from simulator.generators.template_lib import random_normal_message
from simulator.generators.username_gen import generate_user_id


@dataclass
class NormalUserModel:
    user_id: str
    username: str
    account_age_days: int = field(default_factory=lambda: random.randint(30, 2000))
    # Average messages per minute (personal rate)
    base_rate_mpm: float = field(default_factory=lambda: max(0.2, random.gauss(1.5, 1.0)))
    emoji_rate: float = field(default_factory=lambda: random.uniform(0.1, 0.5))
    # Probability of sitting out a cycle (lurker behavior)
    lurk_probability: float = field(default_factory=lambda: random.uniform(0.0, 0.35))

    scenario: str = "unknown"
    channel_id: str = "sim_channel"
    # When True, use Markov chain for organic-looking messages;
    # when False fall back to template bank (default for speed).
    use_markov: bool = True

    def next_delay(self) -> float:
        """Exponentially-distributed delay — Poisson process."""
        mean_delay = 60.0 / max(self.base_rate_mpm, 0.1)
        delay = random.expovariate(1.0 / mean_delay)
        return max(2.0, delay)

    def generate_message(self) -> str:
        msg = random_markov_message() if self.use_markov else random_normal_message()
        # Sometimes append an emote
        if random.random() < self.emoji_rate:
            emote = random.choice(["KEKW", "LUL", "PogChamp", "Pog", "monkaS", "Clap"])
            msg = f"{msg} {emote}"
        return msg

    async def run(self, queue: asyncio.Queue, stop: asyncio.Event) -> None:
        """Run until stop is set, generating messages at Poisson rate."""
        # Initial stagger so all users don't fire at t=0
        await asyncio.sleep(random.uniform(0, min(30.0, 60.0 / self.base_rate_mpm)))

        while not stop.is_set():
            delay = self.next_delay()
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
                break  # stop was set
            except asyncio.TimeoutError:
                pass

            # Lurker: sometimes skip
            if random.random() < self.lurk_probability:
                continue

            msg = SimulatedMessage(
                user_id=self.user_id,
                username=self.username,
                account_age_days=self.account_age_days,
                content=self.generate_message(),
                label="normal",
                scenario=self.scenario,
                channel_id=self.channel_id,
            )
            await queue.put(msg)


def make_normal_user(
    scenario: str = "unknown",
    channel_id: str = "sim_channel",
    account_age_range: tuple[int, int] = (30, 2000),
    avg_rate_mpm: float = 1.5,
    rate_stddev: float = 1.0,
    username_override: str | None = None,
) -> NormalUserModel:
    from simulator.generators.username_gen import generate_normal_username
    uid = generate_user_id("normal")
    uname = username_override or generate_normal_username()
    age = random.randint(*account_age_range)
    rate = max(0.2, random.gauss(avg_rate_mpm, rate_stddev))
    return NormalUserModel(
        user_id=uid,
        username=uname,
        account_age_days=age,
        base_rate_mpm=rate,
        scenario=scenario,
        channel_id=channel_id,
    )
