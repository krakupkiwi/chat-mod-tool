"""
Message normalizer — runs on every message before any detection.

Critical for defeating evasion techniques such as homoglyph substitution,
zero-width character injection, and mixed-script text.
"""

from __future__ import annotations

import re
import unicodedata

try:
    import xxhash as _xxhash
    _HAS_XXHASH = True
except ImportError:  # pragma: no cover
    import hashlib as _hashlib
    _HAS_XXHASH = False

# Cyrillic, Greek, and fullwidth lookalikes mapped to ASCII equivalents
HOMOGLYPH_MAP: dict[str, str] = {
    # Cyrillic
    '\u0430': 'a', '\u0435': 'e', '\u043e': 'o', '\u0441': 'c',
    '\u0440': 'p', '\u0445': 'x', '\u0432': 'b', '\u043c': 'm',
    '\u043d': 'h', '\u0442': 't', '\u0443': 'y', '\u0456': 'i',
    # Greek
    '\u03b1': 'a', '\u03b5': 'e', '\u03bf': 'o', '\u03c5': 'u',
    # Fullwidth Latin
    '\uff41': 'a', '\uff42': 'b', '\uff43': 'c', '\uff44': 'd',
    '\uff45': 'e', '\uff4f': 'o', '\uff53': 's', '\uff54': 't',
    # Zero-width and invisible
    '\u200b': '', '\u200c': '', '\u200d': '', '\ufeff': '',
    '\u00ad': '',  # Soft hyphen
}

_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001F9FF"
    "\U00002600-\U000027BF"
    "\U0001FA00-\U0001FA9F"
    "\U00002702-\U000027B0]+",
    re.UNICODE,
)
_URL_RE = re.compile(r'https?://\S+|www\.\S+', re.IGNORECASE)
_MENTION_RE = re.compile(r'@\w+')
_WHITESPACE_RE = re.compile(r'\s+')
_PRINTABLE_RE = re.compile(r'[^\x20-\x7E\U0001F300-\U0001F9FF\U00002600-\U000027BF]')


def normalize_message(text: str) -> str:
    """
    Full normalization pipeline. Applied before any detection runs.

    1. NFKC Unicode normalization (catches many lookalikes automatically)
    2. Explicit homoglyph substitution
    3. Strip non-printable / invisible characters
    4. Lowercase
    5. Collapse multiple spaces
    6. Truncate to 500 characters
    """
    # Step 1: NFKC
    text = unicodedata.normalize('NFKC', text)
    # Step 2: Homoglyphs
    text = ''.join(HOMOGLYPH_MAP.get(c, c) for c in text)
    # Step 3+4: Strip non-printable, lowercase
    text = _PRINTABLE_RE.sub('', text).lower().strip()
    # Step 5: Collapse whitespace
    text = _WHITESPACE_RE.sub(' ', text)
    # Step 6: Truncate
    return text[:500]


def content_hash(normalized_text: str) -> str:
    """
    xxhash-64 (or MD5 fallback) of the normalized text — exact deduplication only.

    xxhash.xxh64 is 3-5x faster than MD5 for short strings and is perfectly
    suitable for deduplication (not a security/integrity use).  The hash value
    is different from MD5; old SQLite rows computed with MD5 will rotate out
    within the 7-day retention window without causing errors.
    """
    if _HAS_XXHASH:
        return _xxhash.xxh64(normalized_text).hexdigest()
    return _hashlib.md5(normalized_text.encode()).hexdigest()  # pragma: no cover


def extract_features(raw_text: str, normalized_text: str) -> dict:
    """
    Compute lightweight features from both raw and normalized text.
    All operations are O(n) and complete in < 1ms on typical messages.
    """
    alpha_chars = [c for c in raw_text if c.isalpha()]
    upper_chars = [c for c in alpha_chars if c.isupper()]
    caps_ratio = len(upper_chars) / len(alpha_chars) if alpha_chars else 0.0

    urls = _URL_RE.findall(raw_text)
    mentions = _MENTION_RE.findall(raw_text)
    emojis = _EMOJI_RE.findall(normalized_text)
    words = normalized_text.split()

    return {
        "emoji_count": len(emojis),
        "url_count": len(urls),
        "mention_count": len(mentions),
        "word_count": len(words),
        "char_count": len(raw_text),
        "caps_ratio": round(caps_ratio, 3),
        "has_url": len(urls) > 0,
    }
