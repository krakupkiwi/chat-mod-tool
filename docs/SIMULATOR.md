# Bot Attack Simulator

A standalone Python tool that generates synthetic Twitch chat traffic for testing the detection engine. It runs independently from the main application and feeds messages into the detection pipeline via WebSocket injection or direct function calls.

**Location:** `simulator/`

**Primary uses:**
- Verify detection algorithms fire within the required time window
- Measure false positive rates against normal user traffic
- Stress test pipeline performance at 100/1K/5K messages/minute
- Generate labeled datasets for ML model evaluation

---

## Architecture

```
Orchestrator
├── Loads scenario configuration (YAML or Python dataclass)
├── Creates user pool (NormalUserModels + BotModels)
├── Controls traffic scheduler
└── Runs async event loop

UserPool
├── NormalUserModel (many instances)
└── BotModels (varies by scenario)
    ├── SpamBotModel
    ├── CoordinatedBotNetwork
    ├── LinkBotModel
    └── TemplateBotModel

MessageGenerator
├── TemplateLibrary (Phase 1)
├── MarkovChainGenerator (Phase 2)
└── PersonaGenerator (username + account metadata)

TrafficScheduler
├── Poisson-distributed arrival timing for normal users
├── Burst injection for coordinated bots
└── Phase controller (normal → attack → recovery)

OutputAdapters
├── WebSocketAdapter (inject into running detection engine)
├── JSONLAdapter (export labeled dataset)
└── DirectAdapter (call detection engine functions directly for unit tests)
```

---

## Module Structure

```
simulator/
├── simulator.py          # CLI entrypoint
├── orchestrator.py       # Scenario loading, phase control, main loop
├── config.py             # SimulationConfig and scenario dataclasses
│
├── users/
│   ├── base.py           # User abstract base class
│   ├── normal_user.py    # Realistic user behavior model
│   ├── spam_bot.py       # Identical/near-identical message spam
│   ├── coord_bot.py      # Coordinated synchronized bursts
│   ├── link_bot.py       # URL spam patterns
│   └── template_bot.py   # Template with synonym variation
│
├── generators/
│   ├── message_gen.py    # Message content generation dispatch
│   ├── template_lib.py   # Curated message template banks
│   ├── markov_gen.py     # Markov chain generator (requires corpus)
│   ├── username_gen.py   # Account name generation
│   └── account_gen.py    # Full account metadata generation
│
├── scheduler.py          # Timing and rate control
│
├── scenarios/
│   ├── normal_chat.yaml
│   ├── spam_flood.yaml
│   ├── bot_raid.yaml
│   ├── crypto_scam.yaml
│   ├── follower_bot.yaml
│   ├── mixed_attack.yaml
│   ├── evasion_homoglyph.yaml
│   ├── evasion_paraphrase.yaml
│   └── stress_5000mpm.yaml
│
└── output/
    ├── websocket_adapter.py
    ├── jsonl_adapter.py
    └── direct_adapter.py
```

---

## Normal User Model

**File:** `simulator/users/normal_user.py`

Simulates organic user behavior with Poisson-distributed timing, natural message variety, emote usage, and reply chains.

```python
import asyncio
import random
import math
from dataclasses import dataclass, field

@dataclass
class NormalUserModel:
    user_id: str
    username: str
    account_age_days: int = field(default_factory=lambda: random.randint(30, 2000))
    # Average messages per minute (personal rate, normally distributed)
    base_rate_mpm: float = field(default_factory=lambda: max(0.1, random.gauss(2.0, 1.2)))
    typing_speed_wpm: int = field(default_factory=lambda: random.randint(30, 80))
    emoji_rate: float = field(default_factory=lambda: random.uniform(0.1, 0.6))
    lurk_probability: float = field(default_factory=lambda: random.uniform(0.0, 0.4))

    # Session state
    messages_sent: int = 0
    last_message_time: float = 0.0

    async def next_delay(self) -> float:
        """
        Poisson-distributed inter-message delay.
        Real users don't message at perfectly regular intervals.
        """
        mean_delay = 60.0 / self.base_rate_mpm
        # Poisson delay: exponential distribution
        delay = random.expovariate(1.0 / mean_delay)
        # Add typing simulation: longer messages take longer to type
        return max(2.0, delay)

    def generate_message(self, recent_messages: list[str] | None = None) -> str:
        """Generate a contextually plausible chat message."""
        # Occasionally just emote-spam (organic behavior)
        if random.random() < 0.15:
            emotes = ['Pog', 'PogChamp', 'KEKW', 'LUL', 'monkaS', 'PauseChamp',
                      'OMEGALUL', 'Copium', 'Sadge', 'PogO', 'Clap', 'EZ']
            count = random.randint(1, 4)
            return ' '.join(random.choices(emotes, k=count))

        # Regular messages
        messages = [
            "nice play",
            "lets goooo",
            "clip that",
            "W",
            "that was insane",
            "gg",
            "PogChamp",
            "no way lol",
            "actually cracked",
            "LOL",
            "rip",
            "he's so good at this",
            "I've been watching for an hour and this is wild",
            "what is he doing",
            "chat is he cooking",
            "bro really said that",
            "pls",
        ]

        msg = random.choice(messages)

        # Occasionally add emote suffix
        if random.random() < self.emoji_rate:
            emote = random.choice(['KEKW', 'LUL', 'PogChamp', 'Pog', 'monkaS'])
            msg = f"{msg} {emote}"

        return msg
```

---

## Bot Models

### Spam Bot

**File:** `simulator/users/spam_bot.py`

```python
@dataclass
class SpamBotModel:
    user_id: str
    username: str
    account_age_days: int = field(default_factory=lambda: random.randint(0, 7))
    campaign_message: str = "Follow scambot123 for free subs!"
    variation_rate: float = 0.0   # 0 = identical, 0.15 = slight variation
    message_interval: float = 30.0  # Seconds between messages

    def generate_message(self) -> str:
        if self.variation_rate == 0:
            return self.campaign_message
        return self._apply_variations(self.campaign_message)

    def _apply_variations(self, text: str) -> str:
        """
        Apply minor character variations to defeat exact-hash detection.
        Note: the normalization pipeline will reverse most of these —
        use evasion_homoglyph scenario to test the normalizer specifically.
        """
        # Randomly insert/remove punctuation, change case of single letters
        result = list(text)
        num_changes = max(1, int(len(text) * self.variation_rate))

        for _ in range(num_changes):
            if not result:
                break
            idx = random.randint(0, len(result) - 1)
            char = result[idx]

            if char.isalpha():
                # Random case flip
                result[idx] = char.upper() if char.islower() else char.lower()
            elif char == ' ' and random.random() < 0.3:
                # Insert extra space
                result.insert(idx, ' ')

        return ''.join(result)
```

### Coordinated Bot Network

**File:** `simulator/users/coord_bot.py`

```python
@dataclass
class CoordinatedBotNetwork:
    """
    A network of bots that fire in synchronized bursts.
    Each burst sends from a subset of bots within a short jitter window.
    """
    bots: list[SpamBotModel]
    burst_size: int = 20               # Bots per burst
    burst_interval_seconds: float = 30.0
    sync_jitter_ms: float = 300.0      # Max spread within burst (milliseconds)

    async def run(self, output_queue: asyncio.Queue, stop_event: asyncio.Event):
        while not stop_event.is_set():
            await self._fire_burst(output_queue)
            await asyncio.sleep(self.burst_interval_seconds)

    async def _fire_burst(self, output_queue: asyncio.Queue):
        active = random.sample(self.bots, min(self.burst_size, len(self.bots)))

        async def delayed_send(bot: SpamBotModel):
            jitter = random.uniform(0, self.sync_jitter_ms / 1000.0)
            await asyncio.sleep(jitter)
            msg = SimulatedMessage(
                user_id=bot.user_id,
                username=bot.username,
                account_age_days=bot.account_age_days,
                content=bot.generate_message(),
                label='bot_cluster_message',
                cluster_id='coord_burst'
            )
            await output_queue.put(msg)

        await asyncio.gather(*[delayed_send(bot) for bot in active])
```

### Evasion Bot (Homoglyph)

```python
@dataclass
class HomoglyphEvasionBot:
    """
    Replaces ASCII letters with Cyrillic lookalikes.
    Tests whether the normalizer correctly catches this.
    After normalization, messages should cluster with other bots.
    """
    HOMOGLYPHS = {
        'a': '\u0430',  # Cyrillic а
        'e': '\u0435',  # Cyrillic е
        'o': '\u043e',  # Cyrillic о
        'c': '\u0441',  # Cyrillic с
    }

    base_message: str
    substitution_rate: float = 0.5

    def generate_message(self) -> str:
        result = []
        for char in self.base_message:
            lower = char.lower()
            if lower in self.HOMOGLYPHS and random.random() < self.substitution_rate:
                replacement = self.HOMOGLYPHS[lower]
                result.append(replacement if char.islower() else replacement.upper())
            else:
                result.append(char)
        return ''.join(result)
```

---

## Message Generator

**File:** `simulator/generators/template_lib.py`

Curated templates by attack type. Expand these as new spam patterns are observed.

```python
TEMPLATES = {
    'crypto_scam': [
        "Free BTC giveaway at {url} - first {n} people get {amount}!",
        "Elon Musk is doubling all crypto at {url}!",
        "I just made ${amount} in 10 minutes at {url} check it out!",
        "Limited time: send 0.1 BTC to {wallet} get 0.2 back",
    ],
    'follower_bot': [
        "Follow {account} for a follow back!",
        "I follow everyone back! Check out {account}",
        "{account} - free follows for everyone who follows!",
    ],
    'link_spam': [
        "Check this out {url}",
        "Free {item} at {url} hurry!",
        "This streamer is better: {url}",
    ],
    'viewer_scam': [
        "Get free channel points at {url}",
        "Amazon Prime free sub at {url}",
        "Claim your free {item} at {url} before it expires!",
    ],
    'normal_chat': [
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
    ],
}

FILLER = {
    'url': ['twitch.tv/scam123', 'bit.ly/free123', 't.co/scam99'],
    'amount': ['$500', '1 BTC', '100 subs', '1000 followers'],
    'item': ['subs', 'bits', 'followers', 'channel points'],
    'account': ['scambot99', 'followback42', 'freefollow123'],
    'wallet': ['1A2B3C4D5E...', '0xABCDEF...'],
    'n': ['10', '50', '100'],
}

def render_template(template: str) -> str:
    import re
    def replace(match):
        key = match.group(1)
        return random.choice(FILLER.get(key, [key]))
    return re.sub(r'\{(\w+)\}', replace, template)
```

### Username Generator

```python
# simulator/generators/username_gen.py

import random
import string

def generate_bot_username(style: str = 'random') -> str:
    if style == 'sequential':
        # word + sequential number: bot1, bot2, bot3
        prefix = random.choice(['user', 'chat', 'twitch', 'viewer', 'fan'])
        number = random.randint(1, 9999)
        return f"{prefix}{number}"

    elif style == 'random_chars':
        # Pure random lowercase + digits
        length = random.randint(8, 15)
        chars = string.ascii_lowercase + string.digits
        return ''.join(random.choices(chars, k=length))

    elif style == 'word_word_digits':
        # Looks organic but generated: CosmicTurtle92
        words = ['cosmic', 'purple', 'dark', 'silver', 'ghost', 'neon',
                 'rapid', 'storm', 'wild', 'iron', 'swift', 'blue']
        animals = ['turtle', 'wolf', 'bear', 'shark', 'eagle', 'fox',
                   'hawk', 'panda', 'tiger', 'lion', 'viper', 'cobra']
        word1 = random.choice(words).capitalize()
        word2 = random.choice(animals).capitalize()
        digits = random.randint(10, 99)
        return f"{word1}{word2}{digits}"

    # Default: random trailing digits
    base = random.choice(string.ascii_lowercase) + \
           ''.join(random.choices(string.ascii_lowercase, k=random.randint(4, 10)))
    return base + str(random.randint(1000, 9999))


def generate_normal_username() -> str:
    """Generate an organic-looking username."""
    styles = [
        lambda: f"{''.join(random.choices(string.ascii_lowercase, k=random.randint(5,10)))}",
        lambda: f"{random.choice(['xX_', 'the_', 'real', ''])}"
                f"{''.join(random.choices(string.ascii_lowercase, k=random.randint(4,8)))}"
                f"{random.choice(['_xX', '', str(random.randint(1,999))])}",
    ]
    return random.choice(styles)()
```

---

## Scenario Configurations

### Normal Chat

```yaml
# simulator/scenarios/normal_chat.yaml
name: "Normal Chat Baseline"
duration_seconds: 300

phases:
  - start: 0
    end: 300
    normal_users: 200
    bots: 0
    target_rate_mpm: 200

normal_user_config:
  avg_rate_mpm: 1.5
  rate_stddev: 1.0
  username_style: organic
  account_age_range: [30, 2000]
```

### Spam Flood

```yaml
# simulator/scenarios/spam_flood.yaml
name: "Low-Level Spam Flood"
duration_seconds: 180

phases:
  - start: 0
    end: 60
    normal_users: 100
    bots: 0
    description: "Baseline normal chat"

  - start: 60
    end: 180
    normal_users: 100
    bots: 50
    bot_type: spam_bot
    bot_config:
      campaign_message: "Follow scambot123 for FREE SUBS!"
      variation_rate: 0.0
      message_interval: 20
    description: "Spam flood begins"
```

### Large Bot Raid

```yaml
# simulator/scenarios/bot_raid.yaml
name: "Large Scale Coordinated Bot Raid"
duration_seconds: 300

phases:
  - start: 0
    end: 60
    normal_users: 150
    bots: 0
    description: "Normal chat"

  - start: 60
    end: 240
    normal_users: 150
    bots: 500
    bot_type: coordinated
    bot_config:
      burst_size: 100
      burst_interval_seconds: 15
      sync_jitter_ms: 400
      campaign_message: "Follow {account} for free subs!"
      variation_rate: 0.10
      username_style: word_word_digits
      account_age_range: [0, 7]
    description: "Bot raid active"

  - start: 240
    end: 300
    normal_users: 150
    bots: 0
    description: "Recovery phase"
```

### Evasion Test — Homoglyph

```yaml
# simulator/scenarios/evasion_homoglyph.yaml
name: "Homoglyph Evasion Test"
description: "Bots use Cyrillic lookalike characters. Tests normalizer."
duration_seconds: 120

phases:
  - start: 0
    end: 120
    normal_users: 50
    bots: 80
    bot_type: homoglyph_evasion
    bot_config:
      base_message: "Follow scamaccount for free subs!"
      substitution_rate: 0.5
      burst_size: 20
      burst_interval_seconds: 10
```

### Stress Test

```yaml
# simulator/scenarios/stress_5000mpm.yaml
name: "5000 Messages Per Minute Stress Test"
duration_seconds: 600

phases:
  - start: 0
    end: 600
    normal_users: 500
    bots: 300
    bot_type: mixed
    target_rate_mpm: 5000
    description: "Maximum throughput test"
```

---

## Output Adapters

### WebSocket Adapter

Injects simulated messages directly into the running detection engine. The detection engine must expose a `/ws/inject` endpoint (development builds only, not available in production).

```python
# simulator/output/websocket_adapter.py

import asyncio
import json
import websockets
from dataclasses import asdict

class WebSocketAdapter:
    def __init__(self, url: str, ipc_secret: str):
        self.url = url
        self.ipc_secret = ipc_secret
        self.ws = None

    async def connect(self):
        self.ws = await websockets.connect(
            self.url,
            extra_headers={'X-IPC-Secret': self.ipc_secret}
        )

    async def send(self, message: 'SimulatedMessage'):
        if not self.ws:
            await self.connect()
        payload = {
            'type': 'simulated_message',
            'data': asdict(message)
        }
        await self.ws.send(json.dumps(payload))

    async def close(self):
        if self.ws:
            await self.ws.close()
```

### JSONL Dataset Adapter

```python
# simulator/output/jsonl_adapter.py

import json
import gzip
from pathlib import Path
from dataclasses import asdict

class JSONLAdapter:
    """
    Writes labeled messages to a compressed JSONL file.
    Each line is a JSON object with message content + ground truth label.
    """

    def __init__(self, output_path: str):
        self.path = Path(output_path)
        self._file = None

    def open(self):
        self._file = gzip.open(self.path.with_suffix('.jsonl.gz'), 'wt', encoding='utf-8')

    def write(self, message: 'SimulatedMessage'):
        record = {
            'timestamp': message.timestamp,
            'user_id': message.user_id,
            'username': message.username,
            'account_age_days': message.account_age_days,
            'content': message.content,
            'label': message.label,        # 'normal_user_message', 'bot_cluster_message', etc.
            'cluster_id': message.cluster_id,
            'scenario': message.scenario,
        }
        self._file.write(json.dumps(record) + '\n')

    def close(self):
        if self._file:
            self._file.close()
```

### Simulated Message Schema

```python
from dataclasses import dataclass, field
import time

@dataclass
class SimulatedMessage:
    user_id: str
    username: str
    content: str
    label: str  # 'normal_user_message' | 'spam_message' | 'bot_cluster_message'

    account_age_days: int = 365
    timestamp: float = field(default_factory=time.time)
    cluster_id: str | None = None
    scenario: str = 'unknown'

    # Twitch EventSub-compatible fields for direct injection
    channel_id: str = 'sim_channel'
    message_id: str = field(default_factory=lambda: __import__('uuid').uuid4().hex)
```

---

## CLI Interface

```bash
# Normal chat for 5 minutes
python simulator.py --scenario normal_chat --duration 300

# Bot raid at 1000 msg/min, inject into running detection engine
python simulator.py \
  --scenario bot_raid \
  --rate 1000 \
  --output websocket \
  --ws-url ws://localhost:7842/ws/inject \
  --ipc-secret abc123

# Export labeled training dataset
python simulator.py \
  --scenario mixed_attack \
  --output jsonl \
  --output-file datasets/mixed_attack_01.jsonl

# Stress test at 5K msg/min for 10 minutes
python simulator.py \
  --scenario stress_5000mpm \
  --rate 5000 \
  --duration 600 \
  --output websocket \
  --report

# Evasion test
python simulator.py \
  --scenario evasion_homoglyph \
  --output websocket \
  --ws-url ws://localhost:7842/ws/inject \
  --expect-detection  # Fail if detection doesn't fire
```

---

## Evaluation Harness

**File:** `simulator/evaluate.py`

Runs a scenario and measures detection accuracy.

```python
import asyncio
import json
from dataclasses import dataclass

@dataclass
class EvaluationResult:
    scenario: str
    duration_seconds: float
    total_messages: int
    bot_messages: int
    normal_messages: int

    # Detection timing
    time_to_first_alert_seconds: float | None
    time_to_critical_level_seconds: float | None

    # Accuracy
    true_positive_alerts: int    # Alerts on bot accounts
    false_positive_alerts: int   # Alerts on normal user accounts
    true_positive_rate: float
    false_positive_rate: float

    # Health score behaviour
    min_health_score_during_attack: float
    health_score_at_recovery: float

    passed: bool


async def run_evaluation(scenario_path: str, ws_url: str) -> EvaluationResult:
    """
    1. Start listening for detection engine events
    2. Run simulator scenario
    3. Collect all alerts
    4. Compute metrics against ground truth labels
    """
    # Ground truth: track which user_ids are bots
    bot_user_ids: set[str] = set()
    alert_timestamps: list[tuple[float, str]] = []  # (timestamp, user_id)
    health_snapshots: list[dict] = []

    # ... implementation connects to detection engine WS,
    # runs scenario, collects results, computes metrics
    pass
```

### Target Metrics

| Scenario | Time to Alert | Min Health Score | False Positive Rate |
|---|---|---|---|
| `normal_chat` | No alert | > 80 | 0% |
| `spam_flood` (50 bots) | < 5s | < 40 | < 3% |
| `bot_raid` (500 bots) | < 5s | < 15 | < 3% |
| `evasion_homoglyph` | < 10s | < 30 | < 5% |
| Twitch raid (real users) | No alert | > 60 | 0% (suppressed) |

---

## Integration with Development Workflow

```
Development loop for each new detection algorithm:

1. Implement algorithm in backend/detection/
2. Run simulator: python simulator.py --scenario spam_flood --output websocket
3. Observe detection in dashboard / check alert log
4. Run evaluation: python simulator.py --scenario spam_flood --expect-detection
5. Run all scenarios: python simulator.py --run-all-scenarios --report
6. Commit if: FPR < 5%, TTA < 10s on all attack scenarios
```

The evaluation suite should run on every commit as a CI check (GitHub Actions). It requires the Python backend to be running but does not require Electron.

---

## Corpus for Markov Chain Generator

The Markov chain message generator (Phase S9) requires a training corpus of real Twitch chat messages. Public sources:

- Kaggle: "Twitch Chat Logs" datasets (multiple publicly available)
- `rustlebot` Twitch chat log archive: downloadable JSON logs from major channels
- Your own channel logs (export from the application after Phase 7)

Preprocessing before training:
1. Filter out bot/spam messages (use manual labels or known spam patterns)
2. Remove messages shorter than 3 words
3. Normalize Unicode (NFKC)
4. Strip URLs and @mentions

Target corpus size: 100,000+ messages for a reasonable Markov model. The `markovify` library handles training and generation.
