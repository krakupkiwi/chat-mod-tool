"""
SpamPatternMatcher — Aho-Corasick multi-pattern matching for known spam campaigns.

Uses pyahocorasick for O(n + m) matching where n = message length, m = number
of pattern matches. Much faster than re.search() per pattern for large pattern
corpora.

Falls back to simple substring matching if ahocorasick is not installed (no
functionality loss, just slower at corpus scale).

Signal scale:
  - 1 weak pattern match (e.g. "link in bio"): 5.0
  - 1 strong pattern match (e.g. "free bitcoin"): 10.0
  - 2+ pattern matches: 20.0 (capped)

Pattern corpus is loaded from backend/data/spam_patterns.json.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Iterable

logger = logging.getLogger(__name__)

_DATA_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "data", "spam_patterns.json",
)

# Patterns that are strong standalone indicators (high signal weight per hit)
_STRONG_CATEGORIES = {"crypto_scam", "phishing_links", "follower_bots", "fake_giveaway"}
_STRONG_SCORE_PER_HIT = 10.0
_WEAK_SCORE_PER_HIT = 5.0
_MAX_SCORE = 20.0


class SpamPatternMatcher:
    """
    Fast multi-pattern spam detector using Aho-Corasick automaton.

    Thread-safe: the automaton is read-only after construction.
    """

    def __init__(self) -> None:
        self._automaton = None
        self._pattern_meta: dict[str, str] = {}  # pattern → category
        self._fallback_patterns: list[tuple[str, str]] = []  # (pattern, category)
        self._use_ahocorasick = False
        self._load()

    def _load(self) -> None:
        patterns: list[tuple[str, str]] = []
        try:
            with open(_DATA_FILE, encoding="utf-8") as f:
                data = json.load(f)
            for category, items in data.get("categories", {}).items():
                for pattern in items:
                    patterns.append((pattern.lower(), category))
        except Exception:
            logger.warning("SpamPatternMatcher: failed to load corpus — using empty set")
            return

        try:
            import ahocorasick
            A = ahocorasick.Automaton()
            for idx, (pattern, category) in enumerate(patterns):
                A.add_word(pattern, (idx, pattern, category))
            A.make_automaton()
            self._automaton = A
            self._use_ahocorasick = True
            logger.info(
                "SpamPatternMatcher: loaded %d patterns (Aho-Corasick)", len(patterns)
            )
        except ImportError:
            self._fallback_patterns = patterns
            logger.info(
                "SpamPatternMatcher: ahocorasick not available, using fallback (%d patterns)",
                len(patterns),
            )

    def score(self, text: str) -> float:
        """
        Score a message for spam patterns.
        Returns a value in [0, 20.0].
        """
        if not text:
            return 0.0
        lower = text.lower()
        hits = list(self._iter_matches(lower))
        if not hits:
            return 0.0
        total = 0.0
        for _, category in hits:
            if category in _STRONG_CATEGORIES:
                total += _STRONG_SCORE_PER_HIT
            else:
                total += _WEAK_SCORE_PER_HIT
        return min(total, _MAX_SCORE)

    def matched_categories(self, text: str) -> list[str]:
        """Return list of matched category names (deduplicated, for logging)."""
        lower = text.lower()
        cats = {category for _, category in self._iter_matches(lower)}
        return sorted(cats)

    def _iter_matches(self, lower_text: str) -> Iterable[tuple[str, str]]:
        """Yield (pattern, category) for every hit in text."""
        if self._use_ahocorasick and self._automaton is not None:
            for _, (_, pattern, category) in self._automaton.iter(lower_text):
                yield pattern, category
        else:
            for pattern, category in self._fallback_patterns:
                if pattern in lower_text:
                    yield pattern, category
