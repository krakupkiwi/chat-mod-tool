"""
Curated message template banks for each attack type.
Templates use {key} placeholders filled by FILLER at render time.
"""

from __future__ import annotations

import random
import re

TEMPLATES: dict[str, list[str]] = {
    "crypto_scam": [
        "Free BTC giveaway at {url} - first {n} people get {amount}!",
        "Elon Musk is doubling all crypto at {url}!",
        "I just made {amount} in 10 minutes at {url} check it out!",
        "Limited time: send 0.1 BTC to {wallet} get 0.2 back guaranteed",
        "Get rich fast with crypto! {url} join before it expires",
        "{amount} giveaway right now at {url} hurry!!",
    ],
    "follower_bot": [
        "Follow {account} for a follow back!",
        "I follow everyone back! Check out {account}",
        "{account} - free follows for everyone who follows!",
        "Sub4sub at {account} lets grow together!!",
        "Follow me @{account} I follow back 100%",
    ],
    "link_spam": [
        "Check this out {url}",
        "Free {item} at {url} hurry!",
        "This streamer is better: {url}",
        "Get {item} free here {url} limited time",
        "Wow {url} this is amazing",
    ],
    "viewer_scam": [
        "Get free channel points at {url}",
        "Amazon Prime free sub at {url}",
        "Claim your free {item} at {url} before it expires!",
        "Free bits at {url} for first {n} claimers",
    ],
    "normal_chat": [
        "Pog",
        "PogChamp",
        "nice!",
        "lets go",
        "LUL",
        "KEKW",
        "clip it",
        "W",
        "gg",
        "that was crazy",
        "omg",
        "no way",
        "lol",
        "haha",
        "Sadge",
        "monkaS",
        "PauseChamp",
        "actually insane",
        "bro what",
        "clip that",
        "he's cooking",
        "chat is he cooking",
        "W streamer",
        "that play was nuts",
        "OMEGALUL",
        "I can't believe that",
        "this is the best stream",
        "rip",
        "F in chat",
        "skill diff",
    ],
}

FILLER: dict[str, list[str]] = {
    "url": [
        "twitch.tv/scam123",
        "bit.ly/free-subs",
        "t.co/scam99",
        "discordapp.com/invite/freestuff",
        "freesubsgiveaway.xyz",
    ],
    "amount": ["$500", "1 BTC", "100 subs", "1000 followers", "$200", "0.5 ETH"],
    "item": ["subs", "bits", "followers", "channel points", "Amazon gift card"],
    "account": ["scambot99", "followback42", "freefollow123", "growfast99"],
    "wallet": ["1A2B3C4D5E6F...", "0xABCDEF1234..."],
    "n": ["10", "50", "100", "25"],
}


def render_template(template: str) -> str:
    """Fill {key} placeholders with random values from FILLER."""
    def replace(match: re.Match) -> str:
        key = match.group(1)
        options = FILLER.get(key, [key])
        return random.choice(options)
    return re.sub(r"\{(\w+)\}", replace, template)


def random_normal_message() -> str:
    return random.choice(TEMPLATES["normal_chat"])


def random_spam_message(attack_type: str = "follower_bot") -> str:
    templates = TEMPLATES.get(attack_type, TEMPLATES["follower_bot"])
    return render_template(random.choice(templates))
