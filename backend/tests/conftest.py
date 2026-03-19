"""
Shared pytest fixtures for TwitchIDS backend tests.

Usage:
    cd backend
    .venv/Scripts/python.exe -m pytest tests/ -v
"""

from __future__ import annotations

import time
import pytest

from pipeline.models import ChatMessage


def make_message(
    user_id: str = "u1",
    username: str = "testuser",
    content: str = "hello world",
    normalized: str | None = None,
    word_count: int | None = None,
    char_count: int | None = None,
    url_count: int = 0,
    mention_count: int = 0,
    emoji_count: int = 0,
    account_age_days: int | None = None,
    is_moderator: bool = False,
    is_vip: bool = False,
    is_subscriber: bool = False,
    received_at: float | None = None,
    content_hash: str | None = None,
) -> ChatMessage:
    """Factory for ChatMessage test fixtures."""
    from pipeline.normalizer import normalize_message, content_hash as make_hash

    norm = normalized if normalized is not None else normalize_message(content)
    return ChatMessage(
        user_id=user_id,
        username=username,
        channel="testchannel",
        raw_text=content,
        normalized_text=norm,
        content_hash=content_hash if content_hash is not None else make_hash(norm),
        emoji_count=emoji_count,
        url_count=url_count,
        mention_count=mention_count,
        word_count=word_count if word_count is not None else len(norm.split()),
        char_count=char_count if char_count is not None else len(content),
        caps_ratio=0.0,
        has_url=url_count > 0,
        account_age_days=account_age_days,
        is_moderator=is_moderator,
        is_vip=is_vip,
        is_subscriber=is_subscriber,
        received_at=received_at if received_at is not None else time.time(),
    )
