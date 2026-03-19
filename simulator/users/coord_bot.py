"""
CoordinatedBotNetwork — bots that fire in synchronized bursts.

Each burst sends from a random subset of bots within a short jitter window,
simulating a command-and-control coordinated attack.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field

from simulator.config import SimulatedMessage
from simulator.users.spam_bot import SpamBotModel


@dataclass
class CoordinatedBotNetwork:
    bots: list[SpamBotModel]
    burst_size: int = 20
    burst_interval_seconds: float = 15.0
    sync_jitter_ms: float = 400.0       # Max spread within a burst

    scenario: str = "unknown"
    channel_id: str = "sim_channel"

    async def run(self, queue: asyncio.Queue, stop: asyncio.Event) -> None:
        """Fire bursts at regular intervals until stop is set."""
        # First burst fires immediately after a short warmup
        await asyncio.sleep(random.uniform(0.5, 2.0))

        while not stop.is_set():
            await self._fire_burst(queue)
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.burst_interval_seconds)
                break
            except asyncio.TimeoutError:
                pass

    async def _fire_burst(self, queue: asyncio.Queue) -> None:
        active = random.sample(self.bots, min(self.burst_size, len(self.bots)))

        async def delayed_send(bot: SpamBotModel) -> None:
            jitter = random.uniform(0, self.sync_jitter_ms / 1000.0)
            await asyncio.sleep(jitter)
            msg = SimulatedMessage(
                user_id=bot.user_id,
                username=bot.username,
                account_age_days=bot.account_age_days,
                content=bot.generate_message(),
                label="bot_cluster",
                cluster_id=bot.cluster_id,
                scenario=self.scenario,
                channel_id=self.channel_id,
            )
            await queue.put(msg)

        await asyncio.gather(*[delayed_send(bot) for bot in active])


def make_coord_network(
    num_bots: int = 100,
    campaign_message: str = "Follow {account} for free subs!",
    variation_rate: float = 0.10,
    burst_size: int = 30,
    burst_interval_seconds: float = 15.0,
    sync_jitter_ms: float = 400.0,
    username_style: str = "word_word_digits",
    account_age_range: tuple[int, int] = (0, 7),
    scenario: str = "unknown",
    channel_id: str = "sim_channel",
) -> CoordinatedBotNetwork:
    from simulator.users.spam_bot import make_spam_bot
    from simulator.generators.template_lib import render_template

    # Render the campaign message template once so all bots share the same base
    base_msg = render_template(campaign_message)

    bots = [
        make_spam_bot(
            campaign_message=base_msg,
            variation_rate=variation_rate,
            message_interval=burst_interval_seconds,
            username_style=username_style,
            account_age_range=account_age_range,
            scenario=scenario,
            channel_id=channel_id,
            cluster_id="coord_burst",
        )
        for _ in range(num_bots)
    ]

    return CoordinatedBotNetwork(
        bots=bots,
        burst_size=burst_size,
        burst_interval_seconds=burst_interval_seconds,
        sync_jitter_ms=sync_jitter_ms,
        scenario=scenario,
        channel_id=channel_id,
    )
