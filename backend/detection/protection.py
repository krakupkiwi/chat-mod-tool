"""
ProtectedAccountChecker — ensures moderators, VIPs, long-term subscribers,
and known-good bots are never actioned by the detection engine.

Protected accounts are never flagged, timed out, or banned — regardless
of their threat score.

Sources of protection (checked in priority order):
  1. Manual whitelist (user-configurable via settings)
  2. Known bot whitelist (Nightbot, StreamElements, etc.)
  3. Channel moderators (badge "moderator" or "broadcaster")
  4. VIPs (badge "vip")
  5. Subscribers >= 60 days (requires account_age_days and subscriber badge)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pipeline.models import ChatMessage

# Well-known service bots — always whitelisted
KNOWN_BOTS: frozenset[str] = frozenset(
    {
        "nightbot",
        "streamelements",
        "streamlabs",
        "moobot",
        "fossabot",
        "wizebot",
        "botisimo",
        "cloudbot",
        "phantombot",
        "deepbot",
        "ohbot",
        "coebot",
        "stay_hydrated_bot",
        "soundalerts",
        "sery_bot",
        "kofistreambot",
    }
)

SUBSCRIBER_PROTECTION_DAYS = 60


class ProtectedAccountChecker:
    def __init__(self, manual_whitelist: set[str] | None = None) -> None:
        # User-configurable whitelist (user_ids or lowercase usernames)
        self._whitelist: set[str] = manual_whitelist or set()

    def add_to_whitelist(self, identifier: str) -> None:
        self._whitelist.add(identifier.lower())

    def remove_from_whitelist(self, identifier: str) -> None:
        self._whitelist.discard(identifier.lower())

    def is_protected(self, msg: "ChatMessage") -> tuple[bool, str]:
        """
        Returns (is_protected, reason_string).
        Checks all protection sources in priority order.
        """
        uid = msg.user_id
        uname = msg.username.lower()

        if uid in self._whitelist or uname in self._whitelist:
            return True, "manual_whitelist"

        if uname in KNOWN_BOTS:
            return True, "known_bot"

        if msg.is_moderator:
            return True, "moderator"

        if msg.is_vip:
            return True, "vip"

        if msg.is_subscriber and msg.account_age_days is not None:
            if msg.account_age_days >= SUBSCRIBER_PROTECTION_DAYS:
                return True, f"subscriber_{msg.account_age_days}d"

        return False, ""
