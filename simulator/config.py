"""
SimulatedMessage dataclass and scenario configuration types.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SimulatedMessage:
    """A synthetic chat message with ground-truth label for evaluation."""
    user_id: str
    username: str
    content: str
    label: str          # 'normal' | 'spam' | 'bot_cluster' | 'homoglyph' | 'link_spam'

    account_age_days: int = 365
    timestamp: float = field(default_factory=time.time)
    cluster_id: str | None = None
    scenario: str = "unknown"
    channel_id: str = "sim_channel"
    message_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def is_bot(self) -> bool:
        return self.label != "normal"


@dataclass
class PhaseConfig:
    start: int                          # seconds into scenario
    end: int
    normal_users: int = 0
    bots: int = 0
    bot_type: str = "spam_bot"          # spam_bot | coordinated | homoglyph_evasion
    bot_config: dict[str, Any] = field(default_factory=dict)
    target_rate_mpm: int = 0            # 0 = uncapped (natural rate)
    description: str = ""


@dataclass
class SimulationConfig:
    name: str
    duration_seconds: int
    phases: list[PhaseConfig]
    scenario_file: str = ""

    # Normal user defaults (can be overridden per scenario)
    normal_avg_rate_mpm: float = 1.5
    normal_rate_stddev: float = 1.0
    normal_account_age_range: tuple[int, int] = (30, 2000)
    normal_username_style: str = "organic"
