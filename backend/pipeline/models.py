"""
ChatMessage dataclass — canonical message representation throughout the pipeline.

Every incoming Twitch chat message is converted into this form immediately after
receipt and before any further processing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
import time


@dataclass(slots=True)
class ChatMessage:
    # --- Twitch identity ---
    user_id: str
    username: str
    channel: str

    # --- Content ---
    raw_text: str          # Original text from Twitch, untouched
    normalized_text: str   # After normalize_message()
    content_hash: str      # MD5 of normalized_text

    # --- Extracted features ---
    emoji_count: int
    url_count: int
    mention_count: int
    word_count: int
    char_count: int
    caps_ratio: float      # Fraction of alpha chars that are uppercase (pre-normalization)
    has_url: bool

    # --- Twitch metadata ---
    color: Optional[str] = None          # Hex color string, e.g. "#FF0000"
    badges: list[str] = field(default_factory=list)
    is_subscriber: bool = False
    is_moderator: bool = False
    is_vip: bool = False

    # --- Timing ---
    received_at: float = field(default_factory=time.time)  # Unix timestamp

    # --- Account info (populated later by account cache) ---
    account_age_days: Optional[int] = None
