"""
Orchestrator — loads a scenario config, creates user pools, and runs phases.

Flow:
  1. Load SimulationConfig (from YAML or dataclass)
  2. For each phase: spawn user tasks, handle phase transitions
  3. Messages go to an asyncio.Queue consumed by the output adapter
  4. Collect stats and emit a run summary at the end
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import yaml

from simulator.config import PhaseConfig, SimulatedMessage, SimulationConfig
from simulator.users.coord_bot import make_coord_network
from simulator.users.normal_user import make_normal_user
from simulator.users.spam_bot import make_spam_bot

logger = logging.getLogger(__name__)

# Per-phase message queue capacity
_QUEUE_SIZE = 50_000


def load_scenario(path: str) -> SimulationConfig:
    """Load a YAML scenario file into a SimulationConfig.

    Supports two formats:
    1. Phases-based: explicit ``phases`` list with start/end/bots per phase.
    2. Flat format: top-level ``normal_users``, ``spam_bots``, ``bot_networks``
       keys — automatically synthesised into a single full-duration phase.
    """
    with open(path, encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f)

    duration = int(raw.get("duration_seconds", 120))

    phases: list[PhaseConfig] = []

    if "phases" in raw:
        # Explicit phases format
        for p in raw["phases"]:
            bot_cfg = p.get("bot_config", {})
            phases.append(PhaseConfig(
                start=int(p["start"]),
                end=int(p["end"]),
                normal_users=int(p.get("normal_users", 0)),
                bots=int(p.get("bots", 0)),
                bot_type=str(p.get("bot_type", "spam_bot")),
                bot_config=bot_cfg,
                target_rate_mpm=int(p.get("target_rate_mpm", 0)),
                description=str(p.get("description", "")),
            ))
    else:
        # Flat format — synthesise a single phase covering the full duration
        normal_cfg = raw.get("normal_users", {})
        normal_count = int(normal_cfg.get("count", 0))
        normal_mpm = float(normal_cfg.get("msgs_per_minute", 2.0))

        spam_cfg = raw.get("spam_bots", {})
        spam_count = int(spam_cfg.get("count", 0))
        spam_mpm = float(spam_cfg.get("msgs_per_minute", 20.0))
        spam_variation = float(spam_cfg.get("variation_rate", 0.05))

        bot_networks = raw.get("bot_networks", [])

        # Add a normal+spam_bot phase if applicable
        if normal_count > 0 or spam_count > 0:
            phases.append(PhaseConfig(
                start=0,
                end=duration,
                normal_users=normal_count,
                bots=spam_count,
                bot_type="spam_bot",
                bot_config={
                    "variation_rate": spam_variation,
                    "message_interval": 60.0 / spam_mpm if spam_mpm > 0 else 30.0,
                },
                target_rate_mpm=0,
                description="normal + spam bots",
            ))

        # Add a coordinated bot network phase for each network entry
        for net in bot_networks:
            burst_interval = float(net.get("burst_interval", 3.0))
            jitter = float(net.get("jitter_seconds", 1.0))
            bot_count = int(net.get("bot_count", 50))
            phases.append(PhaseConfig(
                start=0,
                end=duration,
                normal_users=0,  # normal users already added above
                bots=bot_count,
                bot_type="coordinated",
                bot_config={
                    "burst_interval_seconds": burst_interval,
                    "sync_jitter_ms": jitter * 1000.0,
                    "burst_size": max(1, bot_count // 5),
                    "username_style": "sequential",  # sequential | word_word_digits | random_chars
                    "account_age_range": [0, 7],
                },
                target_rate_mpm=0,
                description=f"coord bot network ({bot_count} bots)",
            ))

        # Derive normal user config from flat format
        normal_mpm_per_user = normal_mpm if normal_count > 0 else 1.5

    normal_user_config = raw.get("normal_user_config", {})
    # Prefer explicit normal_user_config, fall back to flat normal_users.msgs_per_minute
    flat_normal = raw.get("normal_users", {})
    avg_rate = float(
        normal_user_config.get("avg_rate_mpm")
        or flat_normal.get("msgs_per_minute", 1.5)
    )

    return SimulationConfig(
        name=str(raw.get("name", path)),
        duration_seconds=duration,
        phases=phases,
        scenario_file=path,
        normal_avg_rate_mpm=avg_rate,
        normal_rate_stddev=float(normal_user_config.get("rate_stddev", 0.5)),
        normal_account_age_range=tuple(normal_user_config.get("account_age_range", [30, 2000])),
        normal_username_style=str(normal_user_config.get("username_style", "organic")),
    )


class RunStats:
    def __init__(self, scenario: str) -> None:
        self.scenario = scenario
        self.start_time = time.time()
        self.total_messages = 0
        self.normal_messages = 0
        self.bot_messages = 0
        self.phase_stats: list[dict] = []

    def record(self, msg: SimulatedMessage) -> None:
        self.total_messages += 1
        if msg.is_bot():
            self.bot_messages += 1
        else:
            self.normal_messages += 1

    def elapsed(self) -> float:
        return time.time() - self.start_time

    def summary(self) -> dict:
        elapsed = self.elapsed()
        return {
            "scenario": self.scenario,
            "duration_seconds": round(elapsed, 1),
            "total_messages": self.total_messages,
            "normal_messages": self.normal_messages,
            "bot_messages": self.bot_messages,
            "effective_mpm": round(self.total_messages / max(elapsed / 60, 0.001), 1),
        }


class Orchestrator:
    def __init__(self, config: SimulationConfig, channel_id: str = "sim_channel") -> None:
        self._config = config
        self._channel_id = channel_id
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_SIZE)
        self._stats = RunStats(config.name)

    @property
    def queue(self) -> asyncio.Queue:
        return self._queue

    @property
    def stats(self) -> RunStats:
        return self._stats

    async def run(self, output_adapter=None, rate_limit_mpm: int = 0) -> RunStats:
        """
        Run all phases of the scenario.
        output_adapter: WebSocketAdapter | JSONLAdapter | None (None = collect stats only)
        """
        cfg = self._config
        logger.info("Starting scenario: %s (%ds)", cfg.name, cfg.duration_seconds)

        stop_output = asyncio.Event()

        # Start output adapter drain task
        if output_adapter is not None:
            if hasattr(output_adapter, "drain_queue"):
                if "rate_limit_mpm" in output_adapter.drain_queue.__code__.co_varnames:
                    drain_task = asyncio.create_task(
                        output_adapter.drain_queue(self._queue, stop_output, rate_limit_mpm)
                    )
                else:
                    drain_task = asyncio.create_task(
                        output_adapter.drain_queue(self._queue, stop_output)
                    )
            else:
                drain_task = None
        else:
            # No adapter — drain the queue ourselves (prevents it filling up)
            drain_task = asyncio.create_task(self._drain_to_stats(stop_output))

        scenario_start = time.time()
        all_tasks: list[asyncio.Task] = []
        phase_stops: list[asyncio.Event] = []

        try:
            for phase_idx, phase in enumerate(cfg.phases):
                phase_start_wall = scenario_start + phase.start
                now = time.time()
                wait = phase_start_wall - now
                if wait > 0:
                    await asyncio.sleep(wait)

                logger.info(
                    "[t=%ds] Phase %d: %s — %d normal + %d %s bots",
                    int(time.time() - scenario_start),
                    phase_idx + 1,
                    phase.description or "—",
                    phase.normal_users,
                    phase.bots,
                    phase.bot_type,
                )

                phase_stop = asyncio.Event()
                phase_stops.append(phase_stop)
                phase_tasks = await self._start_phase(phase, phase_stop)
                all_tasks.extend(phase_tasks)

                # Stop this phase at its end time
                phase_duration = phase.end - phase.start
                asyncio.get_event_loop().call_later(
                    phase_duration, phase_stop.set
                )

            # Wait until the scenario duration is complete
            elapsed = time.time() - scenario_start
            remaining = cfg.duration_seconds - elapsed
            if remaining > 0:
                await asyncio.sleep(remaining)

        finally:
            # Signal all phases to stop
            for ps in phase_stops:
                ps.set()

            # Give tasks 2s to finish
            if all_tasks:
                await asyncio.wait(all_tasks, timeout=2.0)
                for t in all_tasks:
                    if not t.done():
                        t.cancel()

            stop_output.set()
            if drain_task:
                await asyncio.wait_for(drain_task, timeout=5.0)

        summary = self._stats.summary()
        logger.info(
            "Scenario complete: %s | %d msgs (%.0f mpm) | %d normal, %d bot",
            summary["scenario"],
            summary["total_messages"],
            summary["effective_mpm"],
            summary["normal_messages"],
            summary["bot_messages"],
        )
        return self._stats

    async def _start_phase(
        self, phase: PhaseConfig, stop: asyncio.Event
    ) -> list[asyncio.Task]:
        """Spawn user tasks for one phase."""
        cfg = self._config
        tasks: list[asyncio.Task] = []

        # Normal users
        for _ in range(phase.normal_users):
            user = make_normal_user(
                scenario=cfg.name,
                channel_id=self._channel_id,
                account_age_range=tuple(cfg.normal_account_age_range),
                avg_rate_mpm=cfg.normal_avg_rate_mpm,
                rate_stddev=cfg.normal_rate_stddev,
            )
            tasks.append(asyncio.create_task(user.run(self._queue, stop)))

        # Bot tasks
        if phase.bots > 0:
            bot_tasks = await self._start_bots(phase, stop)
            tasks.extend(bot_tasks)

        return tasks

    async def _start_bots(
        self, phase: PhaseConfig, stop: asyncio.Event
    ) -> list[asyncio.Task]:
        cfg = self._config
        bc = phase.bot_config
        tasks: list[asyncio.Task] = []

        if phase.bot_type == "coordinated":
            network = make_coord_network(
                num_bots=phase.bots,
                campaign_message=bc.get("campaign_message", "Follow {account} for free subs!"),
                variation_rate=float(bc.get("variation_rate", 0.10)),
                burst_size=int(bc.get("burst_size", 20)),
                burst_interval_seconds=float(bc.get("burst_interval_seconds", 15.0)),
                sync_jitter_ms=float(bc.get("sync_jitter_ms", 400.0)),
                username_style=bc.get("username_style", "word_word_digits"),
                account_age_range=tuple(bc.get("account_age_range", [0, 7])),
                scenario=cfg.name,
                channel_id=self._channel_id,
            )
            tasks.append(asyncio.create_task(network.run(self._queue, stop)))

        elif phase.bot_type == "homoglyph_evasion":
            for _ in range(phase.bots):
                bot = make_spam_bot(
                    campaign_message=bc.get("base_message", "Follow scamaccount for free subs!"),
                    homoglyph_rate=float(bc.get("substitution_rate", 0.5)),
                    message_interval=float(bc.get("message_interval", 10.0)),
                    username_style=bc.get("username_style", "sequential"),
                    scenario=cfg.name,
                    channel_id=self._channel_id,
                    cluster_id="homoglyph_cluster",
                )
                tasks.append(asyncio.create_task(bot.run(self._queue, stop)))

        else:
            # spam_bot (default)
            for _ in range(phase.bots):
                bot = make_spam_bot(
                    campaign_message=bc.get("campaign_message", ""),
                    variation_rate=float(bc.get("variation_rate", 0.0)),
                    message_interval=float(bc.get("message_interval", 20.0)),
                    attack_type=bc.get("attack_type", "follower_bot"),
                    username_style=bc.get("username_style", "sequential"),
                    account_age_range=tuple(bc.get("account_age_range", [0, 7])),
                    scenario=cfg.name,
                    channel_id=self._channel_id,
                )
                tasks.append(asyncio.create_task(bot.run(self._queue, stop)))

        return tasks

    async def _drain_to_stats(self, stop: asyncio.Event) -> None:
        """Drain the queue and accumulate stats (no output adapter)."""
        while not stop.is_set() or not self._queue.empty():
            try:
                msg = self._queue.get_nowait()
                self._stats.record(msg)
            except asyncio.QueueEmpty:
                if stop.is_set():
                    break
                await asyncio.sleep(0.005)
