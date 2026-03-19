"""
DetectionSuppressor — suspends detection during legitimate mass-chat events.

Raid, hype train, and mass gift sub events cause sudden spikes in chat
activity that would trigger false positives. During suppression, all
detector scores are zeroed and no automated actions fire.

Also suppresses for 15 seconds after a reconnect (warmup period).

Call on_event(event_type) when EventSub delivers a suppression trigger.
Call is_suppressed() on every tick.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

# EventSub type → suppression duration in seconds
SUPPRESSION_RULES: dict[str, int] = {
    "channel.raid":               90,
    "channel.hype_train.begin":   120,
    "channel.hype_train.end":     30,
    "channel.subscription.gift":  60,   # applied when gift count >= 10
}

RECONNECT_WARMUP_SECONDS = 15


class DetectionSuppressor:
    def __init__(self) -> None:
        self._suppress_until: float = 0.0
        self._reason: str | None = None

    def on_event(self, event_type: str, gift_count: int = 0) -> None:
        """
        Trigger suppression for the given EventSub event type.
        For subscription.gift, only suppresses when gift_count >= 10.
        """
        if event_type == "channel.subscription.gift" and gift_count < 10:
            return

        duration = SUPPRESSION_RULES.get(event_type)
        if duration is None:
            return

        until = time.monotonic() + duration
        if until > self._suppress_until:
            self._suppress_until = until
            self._reason = event_type
            logger.info(
                "Detection suppressed for %ds due to %s", duration, event_type
            )

    def on_reconnect(self) -> None:
        """Suppress for warmup period after a reconnect."""
        until = time.monotonic() + RECONNECT_WARMUP_SECONDS
        if until > self._suppress_until:
            self._suppress_until = until
            self._reason = "reconnect_warmup"
            logger.info(
                "Detection suppressed for %ds (reconnect warmup)",
                RECONNECT_WARMUP_SECONDS,
            )

    @property
    def is_suppressed(self) -> bool:
        return time.monotonic() < self._suppress_until

    @property
    def reason(self) -> str | None:
        return self._reason if self.is_suppressed else None
