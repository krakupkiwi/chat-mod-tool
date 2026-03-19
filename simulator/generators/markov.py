"""
Markov chain text generator for organic-looking Twitch chat messages.

Trains a bigram Markov chain on a built-in corpus of ~500 Twitch-style
messages. The chain is re-entrant and thread-safe for reads (no mutation
after training). Call generate() to produce a new message each time.

Usage:
    from simulator.generators.markov import MarkovGenerator, TWITCH_CORPUS
    gen = MarkovGenerator(order=2)
    gen.train(TWITCH_CORPUS)
    print(gen.generate())  # "chat this is insane pog"
"""

from __future__ import annotations

import random
from collections import defaultdict

# ---------------------------------------------------------------------------
# Built-in seed corpus — Twitch chat style
# ---------------------------------------------------------------------------

TWITCH_CORPUS: list[str] = [
    # Reactions
    "Pog",
    "PogChamp",
    "POGGERS",
    "PauseChamp",
    "monkaS",
    "monkaW",
    "monkaGIGA",
    "KEKW",
    "OMEGALUL",
    "LUL",
    "LULW",
    "Sadge",
    "peepoSad",
    "Clap",
    "W",
    "L",
    "gg",
    "GGs",
    "rip",
    "F",
    "F in chat",
    # Hype
    "lets go",
    "LETS GO",
    "LFG",
    "let's gooo",
    "lets freaking go",
    "hype hype hype",
    "this is insane",
    "actually insane",
    "bro what",
    "no way",
    "no way that just happened",
    "chat did you see that",
    "CLIP IT",
    "clip that",
    "clipclipclip someone clip",
    "that needs to be clipped",
    "that was clean",
    "so clean",
    "nasty play",
    "that play was nuts",
    "absolutely mental",
    # Streamer reactions
    "W streamer",
    "W streamer W chat",
    "banger stream as always",
    "best streamer on twitch",
    "he's so good at this",
    "she just does not miss",
    "he's actually cracked",
    "this guy is built different",
    "goat behavior",
    "GOAT",
    "cooking right now",
    "he's cooking",
    "she is cooking",
    "chat is he cooking",
    "chat is she cooked",
    # Chat commentary
    "chat is cooked today",
    "chat is wild today",
    "chat eating good tonight",
    "chat we won",
    "chat we are so back",
    "we are so back chat",
    "chat this is the run",
    "this has to be the run",
    "chat it's over",
    "chat we need to talk",
    "real chat real",
    "based",
    "ngl kinda based",
    "ngl that was based",
    "this is actually peak",
    "peak content",
    "peak cinema",
    "peak right here",
    "we are witnessing peak",
    "I am not okay",
    "I can't stop laughing",
    "I am crying rn",
    "I'm dead KEKW",
    "I'm actually dying",
    "this is sending me",
    "sent me to the moon",
    # Skill
    "skill issue",
    "massive skill issue",
    "diff",
    "skill diff",
    "literally outplayed",
    "get outplayed",
    "that was clean though",
    "that was disgusting actually",
    "how did that work",
    "how is that possible",
    "that should not have worked",
    "lucky",
    "so lucky",
    "favored by rng",
    # Questions and chat talk
    "first time watching already a fan",
    "been here since day one",
    "long time viewer first time chatter",
    "what did I miss",
    "wait what happened",
    "wait how did that happen",
    "did he just",
    "did she just",
    "bro just",
    "imagine",
    "lmao imagine",
    "copium",
    "massive copium",
    "hopium",
    "pure hopium right now",
    "it's joever",
    "we are so back",
    "same honestly",
    "honestly same",
    "relatable",
    "very relatable content",
    "real",
    "very real",
    "true",
    "tru",
    "respectfully",
    # Misc positive
    "this stream is so good",
    "this content is gold",
    "loving this game",
    "this game goes hard",
    "this game is actually hard",
    "this game is insane",
    "genuinely impressive",
    "actually impressive ngl",
    "that made me go Pog out loud",
    "first time watching this is great",
    "new viewer this is amazing",
    "I can't believe this is free",
    "the best part of my day fr",
    "okay that was actually funny",
    "that was hilarious ngl",
    "I spit out my drink KEKW",
    # Multi-word phrases that chain nicely
    "bro this is actually insane chat",
    "chat bro this is crazy",
    "chat we are actually witnessing history",
    "I have been watching for years and this is new",
    "we go again",
    "here we go again chat",
    "round two let's go",
    "okay okay okay",
    "wait wait wait",
    "hold on hold on",
    "no no no no",
    "yes yes yes",
    "alright alright alright",
    "let him cook let him cook",
    "do not interrupt the cook",
    "the man is cooking do not touch him",
    "she is in the zone do not disturb",
    "absolute unit of a play",
    "what a play what a play",
    "simply cannot be stopped",
    "genuinely unstoppable right now",
    "chat this streamer woke up and chose violence",
    "chose violence today",
    "no thoughts head empty just vibes",
    "vibes only in this chat",
    # Gaming-specific reactions
    "that aim is insane",
    "the movement on this guy",
    "mechanical god",
    "no scope no way",
    "did he just no scope that",
    "one shot one kill clean",
    "clutch time",
    "clutch or kick",
    "he clutched it chat",
    "she clutched the whole round",
    "1v5 incoming",
    "he's in 1v3 chat",
    "pray for him chat",
    "it's not looking good",
    "actually cooked this time",
    "he's too good for this",
    "we are watching a god",
    "different breed",
    "different species entirely",
    "not humanly possible",
    "how did he see that",
    "the gamesense is unreal",
    "IQ play right there",
    "galaxy brain",
    "galaxy brained it",
    "200 IQ or 0 IQ no in between",
    "that was either genius or luck",
    "lucky or good who knows",
    "I'd like to think skill",
    "definitely skill don't at me",
    "that was raw skill",
    "raw mechanics on display",
    "tutorial mode is on",
    "turning on aimbot live on stream",
    "jk jk but also seriously how",
    "genuinely cannot explain that",
    "physics don't apply",
    "laws of physics suspended",
    "game is broken he's broken",
    "nerf incoming",
    "they're going to patch this",
    "enjoy it while it lasts",
    "devs are watching chat",
    "this will be nerfed tomorrow",
    # Emote combos and hype trains
    "Pog Pog Pog Pog",
    "POGGERS in the chat",
    "KEKW KEKW KEKW",
    "LUL got em",
    "OMEGALUL that was awful",
    "monkaS he's going in",
    "monkaW bro",
    "PauseChamp what is he doing",
    "Sadge that was rough",
    "HYPERS lets go",
    "Clap Clap Clap",
    "5Head play right there",
    "pepega play but it worked",
    "actually pepega strat that worked",
    # Subscriber / community chat
    "lurking since the start",
    "day one lurker finally typing",
    "been lurking for months hello",
    "just woke up catching up on vod",
    "watching at work rn shhh",
    "my boss thinks I'm working",
    "sneaky stream break at work",
    "on my lunch break let's go",
    "been here all night worth it",
    "went to sleep came back still going",
    "how long has this stream been going",
    "is this still the same session",
    "marathon stream energy",
    "twelve hours and still cooking",
    "longest stream ever maybe",
    "mod check in the chat",
    "mods are asleep post good content",
    "where are the mods",
    "chat behave please",
    "banning everyone who types bad",
    "this chat is unironically wholesome",
    "the chat is so fun today",
    "chat is popping off",
    # Advice and callouts
    "look left look left",
    "behind you behind you",
    "inventory check",
    "you forgot to reload",
    "no ammo no ammo",
    "health is low heal up",
    "pick up the better gun",
    "why didn't you take that loot",
    "chat calm down he knows",
    "backseat gaming is real today",
    "let him play his game chat",
    "trust the process",
    "he has a plan trust",
    "four dimensional chess right now",
    "this is all part of it",
    "orchestrated chaos",
    "controlled panic mode",
    # Reactions to fails
    "bro noooo",
    "oh noooo",
    "that hurts to watch",
    "painful",
    "physically painful",
    "my soul left my body",
    "I felt that one",
    "that was a mistake",
    "rookie mistake",
    "we don't talk about that",
    "delete the vod",
    "editors deleting that scene",
    "cut that part out",
    "pretend that didn't happen",
    "moving on swiftly",
    "next question",
    "we learn and move on",
    "lesson learned hopefully",
    "improvement speedrun any percent",
    "character development",
    "that's character development right there",
    # General conversational filler
    "honestly fair",
    "fair enough",
    "can't argue with that",
    "hard to disagree",
    "solid point ngl",
    "you make a fair case",
    "I see your point",
    "counterpoint though",
    "okay but hear me out",
    "hot take incoming",
    "unpopular opinion maybe",
    "controversial but I agree",
    "this might be a bad take but",
    "take of the century right there",
    "hall of fame take",
    "legendary take",
    "that aged well",
    "that did not age well",
    "aged like fine wine",
    "aged like milk",
    "I called it chat",
    "called it earlier",
    "told you all told you",
    "prediction locked in",
    "manifesting right now",
    "manifesting the win",
    "good vibes only",
    "positive energy in the chat",
    "wholesome moment",
    "this is why I watch",
    "my favorite streamer for a reason",
    "never disappoints",
    "always delivers",
    "consistent excellence",
    "standard stuff at this point",
    "just a regular day for him",
    "Tuesday for her",
    "casual for these guys",
    "warmup mode still",
    "this is just warmup chat",
    "wait until he gets serious",
    "final form incoming",
    "we haven't seen the final form yet",
    "ultra instinct mode activating",
    "unlocked something different today",
    "something clicked",
    "today is the day I can feel it",
    "this is the one chat believe",
    "we believe",
    "we ride together",
    "team effort this is",
    "community win",
    "chat carried honestly",
    "couldn't have done it without chat",
    "we did it chat",
    "together we achieved this",
    "what a moment to be alive",
    "historic",
    "we are witnessing something special",
    "years from now we'll remember this",
    # Timing and pacing
    "the timing on that",
    "perfect timing every time",
    "impeccable timing as always",
    "the reaction speed is insane",
    "how do you react that fast",
    "hands of a surgeon",
    "surgeon hands live on stream",
    "precision is unmatched",
    "frame perfect honestly",
    "pixel perfect play",
    "sub pixel accuracy",
    "the consistency though",
    "so consistent it's scary",
    "never tilts just plays",
    "tilt proof build",
    "iron mental fortitude",
    "mental game is elite",
    "emotional regulation speedrun",
    "unbothered and winning",
    "calm and collected always",
    "ice in the veins",
    "cold blooded move",
    # Community inside jokes style
    "donkey of the year candidate",
    "best donkey on the platform",
    "goat of all time no debate",
    "undisputed champion of my watch list",
    "certified banger moment",
    "hall of fame clip incoming",
    "bookmark this timestamp",
    "timestamp this now",
    "this goes in the clip vault",
    "adding to the collection",
    "the collection grows",
    "another one for the highlight reel",
    "highlight reel is getting long",
    "season two episode one",
    "we are in the good timeline",
    "timeline check this is good",
    "simulation is generous today",
    "the algorithm is fed",
    "content machine never stops",
    "content factory open for business",
    "manufacturing bangers live",
    # Viewer to viewer chat
    "hello fellow watchers",
    "how is everyone doing today",
    "good stream today right",
    "enjoying the stream so far",
    "this is a great stream so far",
    "been here three hours no regrets",
    "came for ten minutes stayed for the whole thing",
    "just stopped by and now I can't leave",
    "hooked immediately",
    "one more minute for the past hour",
    "productive evening this is not",
    "my sleep schedule is gone",
    "worth every minute of lost sleep",
    "sleep is for the weak",
    "streaming until sun up apparently",
    "breakfast stream at this rate",
    "good morning to those just waking up",
    "night crew represent",
    "eu hours checking in",
    "hello from across the ocean",
    "watching from the other side of the world",
    "international viewer represent",
    "different timezone same hype",
    # Reactions to big moments
    "YOOOO",
    "YOOOOOOO",
    "what on earth was that",
    "earth shattering play",
    "ground breaking moment",
    "everything changed right then",
    "the shift just happened",
    "I feel the momentum changing",
    "momentum swing incoming",
    "biggest play of the stream so far",
    "could be the play of the year",
    "putting that on the resume",
    "hiring this player immediately",
    "scout him please someone",
    "professional play on a casual stream",
    "pro players watching and taking notes",
    "this is content creation peak form",
    "peak entertainment honestly",
    "better than anything on tv",
    "twitch is the best platform",
    "nothing better to do on a Thursday",
    "Friday is cancelled watching this instead",
    "weekend plans abandoned",
    "everything else can wait",
    # Calm and chill chat
    "just vibing rn",
    "vibe check everyone passes",
    "comfy stream energy",
    "cozy stream tonight",
    "perfect background stream",
    "great stream to eat dinner to",
    "dinner and stream combo",
    "snack time and stream time",
    "grabbed my snacks and settled in",
    "got my tea ready for this",
    "hot drink and good stream",
    "this is the life",
    "living the dream right here",
    "small joys in life",
    "simple pleasures",
    "grateful for these streams honestly",
    "genuinely appreciate this content",
    "real talk this streamer is special",
    "parasocial love is real",
    "found my comfort streamer",
    "comfort stream unlocked",
    # Extra variety — short punchy reactions
    "nah this is crazy",
    "this can't be real",
    "absolutely not",
    "no shot",
    "no shot that worked",
    "there is no way",
    "it is not possible",
    "genuinely cannot",
    "I refuse to believe that",
    "that defies everything",
    "unbelievable scenes",
    "unreal content",
    "unreal human being",
    "not a normal person",
    "certified freak",
    "main character behavior",
    "main character moment",
    "protagonist energy",
    "final boss energy today",
    "villain arc incoming",
    "hero arc confirmed",
    "redemption arc complete",
    "character development arc done",
    "glow up speedrun",
    "from zero to hero chat",
    "rock bottom to the top",
    "came back stronger",
    "what a turnaround",
    "biggest comeback I've seen",
    "never count them out",
    "always believe",
    "faith rewarded",
    "patience is a virtue and it paid off",
    "the wait was worth it",
    "worth the hype",
    "the hype was justified",
    "expectations exceeded",
    "delivered beyond expectations",
    "went above and beyond",
    "overdelivered as usual",
    "undersold honestly",
    "underselling this moment",
    "this deserves more recognition",
    "more people need to see this",
    "why does nobody talk about this streamer",
    "criminally underrated content",
    "hidden gem right here",
    "best kept secret on the platform",
    "not hidden for long",
    "numbers going up watch",
    "growth unlocked",
    "blowing up soon mark my words",
]


# ---------------------------------------------------------------------------
# Markov chain
# ---------------------------------------------------------------------------

class MarkovGenerator:
    """
    Bigram (order=1) or trigram (order=2) Markov chain text generator.

    After training on the corpus the generator has no mutable state, so
    generate() is safe to call from multiple coroutines without locking.
    """

    _START = "__START__"
    _END = "__END__"

    def __init__(self, order: int = 2) -> None:
        if order < 1:
            raise ValueError("order must be >= 1")
        self._order = order
        # state (tuple of words) → list of possible next words
        self._chain: dict[tuple[str, ...], list[str]] = defaultdict(list)
        # valid start states
        self._starts: list[tuple[str, ...]] = []
        self._trained = False

    def train(self, messages: list[str]) -> None:
        """Build transition table from a list of messages."""
        for msg in messages:
            words = msg.strip().split()
            if not words:
                continue

            # Pad with start/end sentinels
            padded = [self._START] * self._order + words + [self._END]

            # Record the first real n-gram as a valid start
            start_state = tuple(padded[self._order : self._order * 2])
            if all(w != self._END for w in start_state):
                self._starts.append(start_state)

            # Build transitions
            for i in range(len(padded) - self._order):
                state = tuple(padded[i : i + self._order])
                next_word = padded[i + self._order]
                self._chain[state].append(next_word)

        self._trained = True

    def generate(self, min_words: int = 1, max_words: int = 14) -> str:
        """
        Walk the chain from a random start state until END or max_words.
        Falls back to a random corpus word if the chain is empty.
        """
        if not self._trained or not self._starts:
            return random.choice(["Pog", "nice", "lets go", "W", "KEKW"])

        state = random.choice(self._starts)
        result: list[str] = list(state)

        for _ in range(max_words - self._order):
            next_words = self._chain.get(state)
            if not next_words:
                break
            next_word = random.choice(next_words)
            if next_word == self._END:
                break
            result.append(next_word)
            state = tuple(result[-self._order :])

        # Strip any leftover sentinel tokens (shouldn't happen but guard anyway)
        result = [w for w in result if w not in (self._START, self._END)]

        if len(result) < min_words:
            return random.choice(["Pog", "W", "lol", "nice", "KEKW"])

        msg = " ".join(result)
        # 20% chance: capitalise first letter (organic variation)
        if random.random() < 0.2:
            msg = msg[0].upper() + msg[1:]
        return msg


# ---------------------------------------------------------------------------
# Module-level singleton — trained once on import
# ---------------------------------------------------------------------------

_default_generator: MarkovGenerator | None = None


def get_default_generator() -> MarkovGenerator:
    """Return the module-level generator, training it on first call."""
    global _default_generator
    if _default_generator is None:
        _default_generator = MarkovGenerator(order=2)
        _default_generator.train(TWITCH_CORPUS)
    return _default_generator


def random_markov_message() -> str:
    """Convenience wrapper — drop-in replacement for random_normal_message()."""
    return get_default_generator().generate()
