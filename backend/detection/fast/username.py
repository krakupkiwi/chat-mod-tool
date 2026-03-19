"""
Username entropy scoring — supplementary signal only.

score_single_username() is stateless and fast (O(n) on username length).
Maximum contribution to risk score: 15 points. Cannot alone trigger any action.
"""

from __future__ import annotations

import math
import re


def shannon_entropy(text: str) -> float:
    counts: dict[str, int] = {}
    for c in text:
        counts[c] = counts.get(c, 0) + 1
    n = len(text)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def score_single_username(username: str) -> float:
    """
    Returns risk score 0–15 for a single username.
    Higher = more likely bot-generated.
    """
    lower = username.lower()
    n = len(lower)
    if n == 0:
        return 0.0

    entropy = shannon_entropy(lower)
    digit_ratio = sum(c.isdigit() for c in lower) / n
    has_trailing_digits = bool(re.search(r'\d{4,}$', lower))
    has_only_lower_no_sep = lower == username and '_' not in username
    very_long = n > 15

    bot_signals = 0
    if entropy < 2.5:
        bot_signals += 1
    if digit_ratio > 0.30:
        bot_signals += 1
    if has_trailing_digits:
        bot_signals += 1
    if has_only_lower_no_sep and very_long:
        bot_signals += 1

    return min(bot_signals / 3.0, 1.0) * 15
