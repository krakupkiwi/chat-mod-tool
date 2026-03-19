"""
Username generators for normal users and bot accounts.
"""

from __future__ import annotations

import random
import string
import uuid


_ADJECTIVES = [
    "cosmic", "purple", "dark", "silver", "ghost", "neon",
    "rapid", "storm", "wild", "iron", "swift", "blue", "rad",
    "hyper", "turbo", "sonic", "laser", "pixel", "cyber", "void",
]
_NOUNS = [
    "turtle", "wolf", "bear", "shark", "eagle", "fox",
    "hawk", "panda", "tiger", "lion", "viper", "cobra",
    "ninja", "wizard", "knight", "hunter", "ranger", "monk",
]


def generate_bot_username(style: str = "sequential") -> str:
    """Generate a bot-style username. Styles: sequential | random_chars | word_word_digits."""
    if style == "sequential":
        prefix = random.choice(["user", "chat", "twitch", "viewer", "fan", "bot"])
        number = random.randint(1, 9999)
        return f"{prefix}{number}"

    if style == "random_chars":
        length = random.randint(8, 15)
        chars = string.ascii_lowercase + string.digits
        return "".join(random.choices(chars, k=length))

    if style == "word_word_digits":
        word1 = random.choice(_ADJECTIVES).capitalize()
        word2 = random.choice(_NOUNS).capitalize()
        digits = random.randint(10, 99)
        return f"{word1}{word2}{digits}"

    # Default: lowercase base + trailing digits
    base = "".join(random.choices(string.ascii_lowercase, k=random.randint(5, 10)))
    return base + str(random.randint(1000, 9999))


def generate_normal_username() -> str:
    """Generate an organic-looking username."""
    styles = [
        # all lowercase
        lambda: "".join(random.choices(string.ascii_lowercase, k=random.randint(5, 10))),
        # prefix + word + optional suffix
        lambda: (
            random.choice(["xX_", "the_", "real", "its", ""])
            + "".join(random.choices(string.ascii_lowercase, k=random.randint(4, 8)))
            + random.choice(["_xX", "", str(random.randint(1, 999))])
        ),
        # word_word style
        lambda: (
            random.choice(_ADJECTIVES)
            + "_"
            + random.choice(_NOUNS)
        ),
    ]
    return random.choice(styles)()


def generate_user_id(prefix: str = "sim") -> str:
    """Generate a unique stable user ID."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"
