"""
Emote spam filter — classify whether a message is predominantly emote spam.

Twitch emotes follow one of three patterns in raw message text:
  - All-caps:      LUL, KEKW, OMEGALUL, PogChamp (all uppercase, 2+ chars)
  - PascalCase:    PogChamp, HeyGuys, VoHiYo (capital letter embedded mid-word)
  - Alphanumeric:  Kreygasm1, KEKW1 (letters + digits)

Emoji characters (counted separately on the ChatMessage object) are also
treated as emote tokens because they serve the same function.

Usage
-----
from detection.fast.emote_filter import emote_ratio, sensitivity_to_threshold

ratio = emote_ratio(msg.raw_text, msg.emoji_count, msg.word_count)
threshold = sensitivity_to_threshold(settings.emote_filter_sensitivity)
is_emote_heavy = ratio >= threshold
"""

from __future__ import annotations

import re

# Matches Twitch-style emote tokens:
#   - All uppercase, 2+ characters:  LUL, KEKW, PogChamp, OMEGALUL
#   - PascalCase with embedded cap:  VoHiYo, HeyGuys, PogChamp
#   - Alphanumeric mix:              Kreygasm1, KEKW1
_EMOTE_TOKEN = re.compile(
    r"^(?:"
    r"[A-Z]{2,}"                       # all-caps: LUL, KEKW
    r"|[A-Z][a-z]+[A-Z][a-zA-Z]*"     # PascalCase: PogChamp, VoHiYo
    r"|[A-Za-z]+\d+"                   # alphanumeric: Kreygasm1
    r")$"
)


def emote_ratio(raw_text: str, emoji_count: int, word_count: int) -> float:
    """
    Return the fraction of tokens in *raw_text* that look like Twitch emotes.

    emoji_count  — pre-counted Unicode emoji in the message (ChatMessage.emoji_count)
    word_count   — pre-counted word tokens (ChatMessage.word_count); used as denominator.

    Returns a value in [0.0, 1.0].  Returns 0.0 if word_count is 0.
    """
    if word_count == 0:
        return 0.0
    tokens = raw_text.split()
    emote_tokens = sum(1 for t in tokens if _EMOTE_TOKEN.match(t))
    return min((emote_tokens + emoji_count) / word_count, 1.0)


def sensitivity_to_threshold(sensitivity: int) -> float:
    """
    Map a user-facing sensitivity setting (0–100) to a ratio threshold.

      sensitivity = 0   → threshold = 1.1  (filter is effectively OFF)
      sensitivity = 50  → threshold = 0.70 (≥70% emote tokens → emote-heavy)
      sensitivity = 100 → threshold = 0.40 (≥40% emote tokens → emote-heavy)

    Formula: max(0.40, 1.0 − (sensitivity / 100) × 0.60)
    A sensitivity of 0 is special-cased to 1.1 so the filter never fires.
    """
    if sensitivity <= 0:
        return 1.1  # never triggers
    return max(0.40, 1.0 - (sensitivity / 100) * 0.60)
