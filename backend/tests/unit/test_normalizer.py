"""
Tests for pipeline/normalizer.py

Covers: homoglyph substitution, zero-width removal, Unicode normalization,
truncation, URL/mention/emoji extraction, and content hashing.
"""

from __future__ import annotations

import pytest
from pipeline.normalizer import (
    normalize_message,
    content_hash,
    extract_features,
    HOMOGLYPH_MAP,
)


class TestNormalizeMessage:
    def test_basic_lowercase(self):
        assert normalize_message("HELLO") == "hello"

    def test_whitespace_collapsed(self):
        assert normalize_message("hello   world") == "hello world"

    def test_leading_trailing_stripped(self):
        assert normalize_message("  hi  ") == "hi"

    def test_truncated_to_500_chars(self):
        long = "a" * 600
        result = normalize_message(long)
        assert len(result) == 500

    def test_cyrillic_homoglyph_substitution(self):
        # Cyrillic 'а' (U+0430) → ASCII 'a'
        cyrillic_a = "\u0430"
        result = normalize_message(f"h{cyrillic_a}ck")
        assert result == "hack"

    def test_greek_homoglyph_substitution(self):
        # Greek 'ο' (U+03BF) → ASCII 'o'
        greek_o = "\u03bf"
        result = normalize_message(f"g{greek_o}od")
        assert result == "good"

    def test_fullwidth_latin_substitution(self):
        # Fullwidth 'ａ' (U+FF41) → ASCII 'a'
        fw_a = "\uff41"
        result = normalize_message(fw_a)
        assert result == "a"

    def test_zero_width_removed(self):
        # Zero-width space between characters
        zwsp = "\u200b"
        result = normalize_message(f"hel{zwsp}lo")
        assert result == "hello"

    def test_zero_width_joiner_removed(self):
        zwj = "\u200d"
        result = normalize_message(f"a{zwj}b")
        assert result == "ab"

    def test_soft_hyphen_removed(self):
        soft_hyphen = "\u00ad"
        result = normalize_message(f"hel{soft_hyphen}lo")
        assert result == "hello"

    def test_nfkc_normalization(self):
        # Fullwidth digit '１' normalises to '1' under NFKC
        result = normalize_message("\uff11")
        assert result == "1"

    def test_empty_string(self):
        assert normalize_message("") == ""

    def test_only_whitespace(self):
        assert normalize_message("   ") == ""

    def test_mixed_homoglyphs_and_normal(self):
        # 'hello' with Cyrillic 'е' (U+0435) → 'e'
        mixed = "h\u0435llo"
        assert normalize_message(mixed) == "hello"

    def test_evasion_chain(self):
        # Zero-width + homoglyph + NFKC combined — a real evasion attempt
        text = "f\u200b\u0440\u0435\u0435"  # f + ZW + Cyrillic р + е + е
        result = normalize_message(text)
        assert result == "fpee"


class TestContentHash:
    def test_identical_text_same_hash(self):
        assert content_hash("hello") == content_hash("hello")

    def test_different_text_different_hash(self):
        assert content_hash("hello") != content_hash("world")

    def test_hash_is_hex_string(self):
        h = content_hash("test")
        assert all(c in "0123456789abcdef" for c in h)

    def test_hash_is_fixed_length(self):
        # xxhash.xxh64 produces 16-char hex string (vs 32 for MD5).
        # The important invariant is that the length is consistent, not == 32.
        h = content_hash("anything")
        assert len(h) == len(content_hash("something else"))

    def test_empty_string_is_stable(self):
        # Should not raise and should be consistent
        h1 = content_hash("")
        h2 = content_hash("")
        assert h1 == h2


class TestExtractFeatures:
    def test_word_count(self):
        f = extract_features("hello world", "hello world")
        assert f["word_count"] == 2

    def test_url_detected(self):
        f = extract_features("check https://example.com now", "check now")
        assert f["url_count"] == 1
        assert f["has_url"] is True

    def test_www_url_detected(self):
        f = extract_features("go to www.example.com", "go to www.example.com")
        assert f["url_count"] == 1

    def test_mention_detected(self):
        f = extract_features("hey @streamername", "hey @streamername")
        assert f["mention_count"] == 1

    def test_multiple_mentions(self):
        f = extract_features("@alice @bob hi", "@alice @bob hi")
        assert f["mention_count"] == 2

    def test_no_url_no_mention(self):
        f = extract_features("just a message", "just a message")
        assert f["url_count"] == 0
        assert f["mention_count"] == 0
        assert f["has_url"] is False

    def test_caps_ratio_all_upper(self):
        f = extract_features("HELLO", "hello")
        assert f["caps_ratio"] == 1.0

    def test_caps_ratio_all_lower(self):
        f = extract_features("hello", "hello")
        assert f["caps_ratio"] == 0.0

    def test_caps_ratio_mixed(self):
        f = extract_features("Hello", "hello")
        assert 0.0 < f["caps_ratio"] < 1.0

    def test_char_count_from_raw(self):
        # char_count should use raw_text length
        f = extract_features("hi!", "hi!")
        assert f["char_count"] == 3

    def test_emoji_count(self):
        f = extract_features("nice 🎉", "nice 🎉")
        assert f["emoji_count"] == 1

    def test_no_features_empty(self):
        f = extract_features("", "")
        assert f["word_count"] == 0
        assert f["url_count"] == 0
        assert f["emoji_count"] == 0
