"""
Moderation action dataclasses and type definitions.

Every action is created with status='pending', written to the DB,
then executed. On completion the DB row is updated to 'completed' or 'failed'.
This ensures crash-safe, auditable moderation history.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Literal

ActionType = Literal["ban", "timeout", "warn", "delete", "slow_mode", "slow_mode_off", "followers_only", "followers_only_off", "emote_only", "emote_only_off", "sub_only", "sub_only_off", "unique_chat", "unique_chat_off"]
ActionStatus = Literal["pending", "completed", "failed", "undone"]
ActionTrigger = Literal["manual", "auto"]


@dataclass
class ModerationAction:
    # Identity
    action_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    action_type: ActionType = "timeout"

    # Target
    broadcaster_id: str = ""
    user_id: str = ""
    username: str = ""
    channel: str = ""

    # Parameters
    duration_seconds: int | None = None   # for timeout
    reason: str = ""
    message_id: str | None = None         # for delete

    # Metadata
    status: ActionStatus = "pending"
    triggered_by: str = "manual"          # "manual" or "auto:<signal>"
    confidence: float | None = None
    db_id: int | None = None              # row id after DB insert

    created_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    error_message: str | None = None


# ---------------------------------------------------------------------------
# Threshold table: confidence score → action
# ---------------------------------------------------------------------------

# (min_score, max_score, action_type, duration_seconds)
ESCALATION_TABLE: list[tuple[float, float, ActionType, int | None]] = [
    (0.0,  39.9, "timeout", None),       # Below threshold — no action
    (40.0, 59.9, "timeout", None),       # Log/flag only
    (60.0, 74.9, "delete",  None),       # Delete message
    (75.0, 84.9, "timeout", 60),         # 60-second timeout
    (85.0, 94.9, "timeout", 600),        # 10-minute timeout
    (95.0, 100.0, "ban",    None),       # Permanent ban (dual-signal required)
]


def get_escalation_action(
    threat_score: float,
) -> tuple[ActionType | None, int | None]:
    """
    Returns (action_type, duration_seconds) for a given threat score.
    Returns (None, None) if below actionable threshold.
    """
    if threat_score < 60.0:
        return None, None
    for min_s, max_s, action_type, duration in ESCALATION_TABLE:
        if min_s <= threat_score <= max_s:
            return action_type, duration
    return None, None
