"""
Converts a raw Twitch EventSub chat payload (dict) into a ChatMessage.
Single entry point — call build_message() from the queue enqueue path.
"""

from __future__ import annotations

from .models import ChatMessage
from .normalizer import content_hash, extract_features, normalize_message


def build_message(
    user_id: str,
    username: str,
    channel: str,
    raw_text: str,
    color: str | None = None,
    badges: list[str] | None = None,
) -> ChatMessage:
    """Build a fully-populated ChatMessage from raw Twitch payload fields."""
    normalized = normalize_message(raw_text)
    chash = content_hash(normalized)
    feats = extract_features(raw_text, normalized)

    badge_list = badges or []
    return ChatMessage(
        user_id=user_id,
        username=username,
        channel=channel,
        raw_text=raw_text,
        normalized_text=normalized,
        content_hash=chash,
        emoji_count=feats["emoji_count"],
        url_count=feats["url_count"],
        mention_count=feats["mention_count"],
        word_count=feats["word_count"],
        char_count=feats["char_count"],
        caps_ratio=feats["caps_ratio"],
        has_url=feats["has_url"],
        color=color,
        badges=badge_list,
        is_subscriber=any("subscriber" in b for b in badge_list),
        is_moderator=any("moderator" in b or "broadcaster" in b for b in badge_list),
        is_vip=any("vip" in b for b in badge_list),
    )
