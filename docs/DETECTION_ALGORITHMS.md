# Detection Algorithms

All detection algorithms are implemented in `backend/detection/`. This document defines every algorithm: what it detects, how it works, implementation notes, thresholds, and known evasion vectors.

---

## Architecture Overview

Detection has two tiers:

**Fast path** — runs on every message, must complete in < 5ms. These are deterministic, no-ML algorithms.

**Batch path** — runs on a timer (every 10 seconds). CPU-intensive ML-based analysis. Runs in a thread pool executor so it never blocks the event loop.

All detectors produce a `signal score` from 0–100. Scores are fed into the Health Score Engine which combines them into a single weighted metric. See [CHAT_HEALTH_SCORE.md](CHAT_HEALTH_SCORE.md) for the scoring model.

---

## Message Preprocessing

**File:** `backend/pipeline/normalizer.py`

Every message passes through normalization before any detection runs. This step is critical for defeating evasion techniques.

```python
import unicodedata
import re

# Cyrillic, Greek, and other script lookalikes mapped to ASCII equivalents
HOMOGLYPH_MAP = {
    # Cyrillic
    '\u0430': 'a', '\u0435': 'e', '\u043e': 'o', '\u0441': 'c',
    '\u0440': 'p', '\u0445': 'x', '\u0432': 'b', '\u043c': 'm',
    '\u043d': 'h', '\u0442': 't', '\u0443': 'y', '\u0456': 'i',
    # Greek
    '\u03b1': 'a', '\u03b5': 'e', '\u03bf': 'o', '\u03c5': 'u',
    # Mathematical/bold lookalikes (Unicode block)
    '\uff41': 'a', '\uff42': 'b', '\uff43': 'c', '\uff44': 'd',
    '\uff45': 'e', '\uff4f': 'o', '\uff53': 's', '\uff54': 't',
    # Zero-width and invisible characters
    '\u200b': '', '\u200c': '', '\u200d': '', '\ufeff': '',
    '\u00ad': '',  # Soft hyphen
}

def normalize_message(text: str) -> str:
    """
    Full normalization pipeline. Applied before any detection.
    1. NFKC Unicode normalization (catches many lookalikes automatically)
    2. Explicit homoglyph substitution
    3. Strip zero-width and invisible characters
    4. Lowercase
    5. Collapse multiple spaces
    6. Truncate to 500 characters
    """
    # Step 1: NFKC normalization
    text = unicodedata.normalize('NFKC', text)

    # Step 2: Homoglyph substitution
    text = ''.join(HOMOGLYPH_MAP.get(c, c) for c in text)

    # Step 3+4: Strip non-printable, lowercase
    text = re.sub(r'[^\x20-\x7E\U0001F300-\U0001F9FF]', '', text)
    text = text.lower().strip()

    # Step 5: Collapse whitespace
    text = re.sub(r'\s+', ' ', text)

    # Step 6: Truncate
    return text[:500]
```

After normalization, compute the content hash:

```python
import hashlib

def content_hash(normalized_text: str) -> str:
    return hashlib.md5(normalized_text.encode()).hexdigest()
```

---

## Fast Path Detectors

### 1. Temporal Synchronization Detector

**File:** `backend/detection/fast/temporal.py`

**Detects:** Multiple distinct accounts sending the same or similar message within a short time window. This is the clearest signal of coordinated bot activity.

**Why multi-window:** Sophisticated bots add random delays to evade fixed-window detection. Checking 1s, 3s, 5s, and 15s windows simultaneously catches both naive (tight) and evasive (spread-out) coordination.

**Implementation:**

```python
from collections import defaultdict, deque
import time

SYNC_WINDOWS = [
    (1,  2),   # (window_seconds, min_accounts_to_trigger)
    (3,  3),
    (5,  4),
    (15, 8),
    (30, 15),
]

class TemporalSyncDetector:
    def __init__(self):
        # hash → deque of (timestamp, user_id)
        self.hash_buckets: dict[str, deque] = defaultdict(deque)
        self.last_prune = time.monotonic()

    def add(self, content_hash: str, user_id: str, timestamp: float) -> float:
        """
        Add message. Returns risk score 0–25.
        """
        self.hash_buckets[content_hash].append((timestamp, user_id))

        # Prune every 5 seconds to avoid memory growth
        if timestamp - self.last_prune > 5:
            self._prune_all(timestamp)
            self.last_prune = timestamp

        return self._compute_score(content_hash, timestamp)

    def _compute_score(self, content_hash: str, now: float) -> float:
        bucket = self.hash_buckets[content_hash]
        scores = []

        for window_s, threshold in SYNC_WINDOWS:
            cutoff = now - window_s
            # Count distinct users within this window
            users_in_window = set(
                uid for ts, uid in bucket if ts >= cutoff
            )
            count = len(users_in_window)

            if count >= threshold:
                # Score scales with how far above threshold we are
                # Shorter windows weighted more heavily (clearer coordination)
                window_weight = 1.0 / window_s
                score = min(count / threshold, 3.0) * window_weight * 10
                scores.append(score)

        return min(sum(scores), 25.0) if scores else 0.0

    def _prune_all(self, now: float):
        max_window = max(w for w, _ in SYNC_WINDOWS)
        cutoff = now - max_window

        empty_keys = []
        for key, bucket in self.hash_buckets.items():
            while bucket and bucket[0][0] < cutoff:
                bucket.popleft()
            if not bucket:
                empty_keys.append(key)

        for key in empty_keys:
            del self.hash_buckets[key]
```

**Thresholds:**

| Window | Min accounts | Signal strength |
|---|---|---|
| 1s | 2 | Very high (impossible organically) |
| 3s | 3 | High |
| 5s | 4 | Moderate-high |
| 15s | 8 | Moderate |
| 30s | 15 | Moderate (baseline check) |

**Evasion:** Bots spread messages across a wider time range (e.g., 60s). Mitigation: the 30s window with threshold=15 catches spread-out floods. The semantic clustering slow path catches varied-message coordination entirely.

---

### 2. MinHash Duplicate Detector (Near-Duplicate Fast Path)

**File:** `backend/detection/fast/duplicate.py`

**Detects:** Messages that are near-identical (same spam template with minor variations) across multiple accounts. Catches the case where bots vary punctuation, spacing, or a single word to defeat exact-hash detection.

**Implementation:**

```python
from datasketch import MinHash, MinHashLSH
from collections import deque
import time

class MinHashDuplicateDetector:
    def __init__(self, similarity_threshold=0.70, num_perm=64, window_seconds=30):
        self.lsh = MinHashLSH(threshold=similarity_threshold, num_perm=num_perm)
        self.window = window_seconds
        # Ordered eviction: deque of (timestamp, key)
        self.time_index: deque = deque()
        self.key_to_meta: dict = {}  # key → {user_id, timestamp, content_hash}

    def add(self, message_id: str, content: str, user_id: str,
            timestamp: float) -> list[dict] | None:
        """
        Returns list of similar message metadata if cluster found, else None.
        Cluster = 3+ distinct accounts with similarity >= threshold.
        """
        mh = self._make_minhash(content)

        # Query before inserting (don't match self)
        similar_keys = self.lsh.query(mh)

        # Insert
        try:
            self.lsh.insert(message_id, mh)
            self.time_index.append((timestamp, message_id))
            self.key_to_meta[message_id] = {
                'user_id': user_id,
                'timestamp': timestamp,
            }
        except ValueError:
            pass  # Duplicate key, skip

        self._evict_old(timestamp)

        if similar_keys:
            # Filter to distinct users
            similar_users = {
                self.key_to_meta[k]['user_id']
                for k in similar_keys
                if k in self.key_to_meta
            }
            similar_users.add(user_id)

            if len(similar_users) >= 3:
                return [self.key_to_meta[k] for k in similar_keys
                        if k in self.key_to_meta]

        return None

    def _make_minhash(self, text: str) -> MinHash:
        mh = MinHash(num_perm=64)
        # Character trigrams — robust to word substitution
        for i in range(len(text) - 2):
            mh.update(text[i:i+3].encode('utf8'))
        return mh

    def _evict_old(self, now: float):
        cutoff = now - self.window
        while self.time_index and self.time_index[0][0] < cutoff:
            _, old_key = self.time_index.popleft()
            try:
                self.lsh.remove(old_key)
            except KeyError:
                pass
            self.key_to_meta.pop(old_key, None)
```

**Parameters:**
- Similarity threshold: 0.70 (70% Jaccard similarity on character trigrams)
- Number of permutations: 64 (good accuracy/speed tradeoff)
- Window: 30 seconds

**Evasion:** Deep paraphrasing ("Follow X for free subs" → "X is giving away free subscriptions"). Mitigation: MiniLM slow path catches semantic equivalence that MinHash misses.

---

### 3. Per-User Rate Anomaly Detector

**File:** `backend/detection/fast/rate.py`

**Detects:** Individual accounts sending at machine-speed rates (too many messages per minute, or perfectly regular timing).

```python
from collections import deque, defaultdict
import time
import statistics

class UserRateDetector:
    def __init__(self, window_seconds=60):
        # user_id → deque of timestamps
        self.user_windows: dict[str, deque] = defaultdict(
            lambda: deque(maxlen=200)
        )
        self.window = window_seconds

    def add(self, user_id: str, timestamp: float) -> float:
        """Returns risk score 0–20."""
        window = self.user_windows[user_id]
        window.append(timestamp)
        return self._compute_score(window, timestamp)

    def _compute_score(self, window: deque, now: float) -> float:
        # Prune to window
        cutoff = now - self.window
        timestamps = [t for t in window if t >= cutoff]

        if len(timestamps) < 3:
            return 0.0

        msg_per_minute = len(timestamps) * (60.0 / self.window)

        # Signal 1: Volume
        volume_score = 0.0
        if msg_per_minute > 30:
            volume_score = min((msg_per_minute - 30) / 10, 1.0) * 15
        elif msg_per_minute > 15:
            volume_score = min((msg_per_minute - 15) / 15, 1.0) * 8

        # Signal 2: Regularity (bots have very consistent intervals)
        intervals = [timestamps[i+1] - timestamps[i]
                     for i in range(len(timestamps)-1)]
        if len(intervals) >= 4:
            mean_interval = statistics.mean(intervals)
            stdev_interval = statistics.stdev(intervals) if len(intervals) > 1 else 0

            # Coefficient of variation: low = suspiciously regular
            cv = stdev_interval / max(mean_interval, 0.001)
            if cv < 0.05 and mean_interval < 5:  # < 5% variation, < 5s interval
                volume_score += 10  # Additional regularity bonus

        return min(volume_score, 20.0)
```

**Thresholds:**
- > 15 msg/min from single account: mild concern
- > 30 msg/min: strong concern
- CV < 0.05 on intervals (< 5% timing variation): bot timing pattern

---

### 4. Username Entropy Scorer

**File:** `backend/detection/fast/username.py`

**Detects:** Bot-generated usernames with low entropy (random characters + digits), sequential digit suffixes, or coordinated username family patterns.

```python
import math
import re
import statistics
from collections import defaultdict

def shannon_entropy(text: str) -> float:
    counts = {}
    for c in text:
        counts[c] = counts.get(c, 0) + 1
    n = len(text)
    return -sum((c/n) * math.log2(c/n) for c in counts.values())

def score_single_username(username: str) -> float:
    """Returns risk contribution 0–1.0 for a single username."""
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

    return min(bot_signals / 3.0, 1.0)


class UsernameFamilyDetector:
    """
    Detects when many accounts in a session share the same structural pattern.
    Catches bots with organic-looking names generated from the same template.
    Example: CosmicTurtle91, CosmicWave42, CosmicRain77 — same pattern family.
    """

    PATTERNS = {
        'word_word_digits':    re.compile(r'^[A-Z][a-z]+[A-Z][a-z]+\d{2,4}$'),
        'lower_digits_suffix': re.compile(r'^[a-z]{5,15}\d{3,4}$'),
        'underscore_digits':   re.compile(r'^[a-z]+_[a-z]+\d{2,4}$'),
        'xx_word_xx':          re.compile(r'^x+_?\w+_?x+$', re.IGNORECASE),
        'prefix_sequential':   re.compile(r'^([a-z]+)(\d+)$'),
    }

    def __init__(self, session_window_seconds: int = 600):
        self.window = session_window_seconds
        # pattern → deque of (timestamp, username)
        self.pattern_buckets: dict[str, deque] = defaultdict(deque)

    def add(self, username: str, timestamp: float) -> float:
        """Returns risk score 0–20."""
        matched_patterns = self._classify(username)
        for pattern in matched_patterns:
            self.pattern_buckets[pattern].append((timestamp, username))

        self._prune(timestamp)
        return self._compute_score(timestamp)

    def _classify(self, username: str) -> list[str]:
        return [name for name, pattern in self.PATTERNS.items()
                if pattern.match(username)]

    def _compute_score(self, now: float) -> float:
        for pattern, bucket in self.pattern_buckets.items():
            recent = [u for t, u in bucket if t >= now - self.window]
            if len(set(recent)) >= 10:  # 10+ distinct accounts, same pattern
                return min(len(set(recent)) / 20.0, 1.0) * 20
        return 0.0

    def _prune(self, now: float):
        cutoff = now - self.window
        for bucket in self.pattern_buckets.values():
            while bucket and bucket[0][0] < cutoff:
                bucket.popleft()
```

**Important:** Username entropy is a supplementary signal only. Maximum contribution to risk score: 15 points. It cannot alone trigger any moderation action.

---

### 5. Burst Anomaly Detector (Z-Score)

**File:** `backend/detection/fast/burst.py`

**Detects:** Sudden spikes in message volume that are statistically anomalous relative to the channel's own baseline. This is channel-adaptive — a normally busy channel requires a larger spike to trigger than a quiet channel.

```python
from collections import deque
import statistics
import time

class BurstAnomalyDetector:
    def __init__(self, baseline_window_seconds: int = 300, sample_interval: float = 5.0):
        self.baseline_window = baseline_window_seconds
        self.sample_interval = sample_interval

        # Rolling history of (timestamp, count_in_interval)
        self.history: deque = deque()

        # Current interval counter
        self.current_interval_start = time.monotonic()
        self.current_interval_count = 0

    def add_message(self, timestamp: float) -> float:
        """Call for each message. Returns risk score 0–25."""
        self.current_interval_count += 1

        now = timestamp
        if now - self.current_interval_start >= self.sample_interval:
            # Commit current interval to history
            self.history.append((self.current_interval_start,
                                   self.current_interval_count))
            self.current_interval_start = now
            self.current_interval_count = 0
            self._prune(now)

        return self._compute_score()

    def _compute_score(self) -> float:
        if len(self.history) < 10:
            return 0.0  # Insufficient baseline

        counts = [c for _, c in self.history]
        mean = statistics.mean(counts)
        stdev = statistics.stdev(counts) if len(counts) > 1 else 1.0
        stdev = max(stdev, 0.5)  # Minimum stdev to avoid division by zero

        z = (self.current_interval_count - mean) / stdev

        # z < 1.5: normal
        # z = 2:   mild concern
        # z = 3:   moderate
        # z = 5:   severe
        if z < 1.5:
            return 0.0
        return min((z - 1.5) * 8, 25.0)

    def _prune(self, now: float):
        cutoff = now - self.baseline_window
        while self.history and self.history[0][0] < cutoff:
            self.history.popleft()
```

---

## Batch Path Detectors

### 6. Semantic Cluster Detector (MiniLM + DBSCAN)

**File:** `backend/detection/batch/clustering.py`

**Detects:** Groups of messages that are semantically similar but not textually identical. Catches paraphrasing bots that vary their template to defeat exact-match and MinHash detection.

**Runs:** Every 10 seconds on the last 30 seconds of messages. Executes in a thread pool executor — never blocks the event loop.

```python
import asyncio
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict
from dataclasses import dataclass
import numpy as np

@dataclass
class ClusterResult:
    cluster_count: int
    clustered_ratio: float
    clusters: list[dict]  # [{cluster_id, user_ids, size, sample_message}]
    risk_score: float     # 0–25

class SemanticClusterer:
    def __init__(self):
        self._model = None  # Lazy load
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='minilm')
        self.last_result = ClusterResult(0, 0.0, [], 0.0)

        # Adaptive sampling threshold
        self.FULL_EMBED_THRESHOLD = 200    # Below this: embed all
        self.SAMPLE_RATIO_HIGH = 0.20      # Above 500 messages: embed 20%

    def _load_model(self):
        """Load ONNX-exported MiniLM model. Called once on first use."""
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            # Use ONNX backend if available
            try:
                self._model = SentenceTransformer(
                    'backend/models/minilm',
                    backend='onnx'
                )
            except Exception:
                self._model = SentenceTransformer('all-MiniLM-L6-v2')

    async def analyze(self, messages: list) -> ClusterResult:
        """
        messages: list of ChatMessage objects (last 30s window)
        Non-blocking: runs in thread pool executor.
        """
        if len(messages) < 5:
            return ClusterResult(0, 0.0, [], 0.0)

        sample = self._get_sample(messages)

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            self._executor,
            self._run_clustering,
            sample
        )
        self.last_result = result
        return result

    def _get_sample(self, messages: list) -> list:
        """Apply adaptive sampling for high-volume channels."""
        if len(messages) <= self.FULL_EMBED_THRESHOLD:
            return messages

        # Always include messages that already hit MinHash clusters
        flagged = [m for m in messages if getattr(m, 'minhash_flagged', False)]
        unflagged = [m for m in messages if not getattr(m, 'minhash_flagged', False)]

        sample_size = max(50, int(len(unflagged) * self.SAMPLE_RATIO_HIGH))
        import random
        sample = random.sample(unflagged, min(sample_size, len(unflagged)))

        return flagged + sample

    def _run_clustering(self, messages: list) -> ClusterResult:
        """CPU-bound work. Runs in thread pool."""
        from sklearn.cluster import DBSCAN

        self._load_model()

        contents = [m.content_normalized for m in messages]
        user_ids = [m.user_id for m in messages]

        # Encode with normalization for cosine similarity
        embeddings = self._model.encode(
            contents,
            normalize_embeddings=True,
            batch_size=64,
            show_progress_bar=False
        )

        # DBSCAN with cosine distance
        # eps=0.20 means clusters have cosine similarity >= 0.80
        labels = DBSCAN(
            eps=0.20,
            min_samples=3,
            metric='cosine',
            algorithm='brute'  # Required for cosine metric
        ).fit_predict(embeddings)

        # Group results by cluster
        cluster_users = defaultdict(list)
        cluster_messages = defaultdict(list)

        for idx, label in enumerate(labels):
            if label >= 0:
                cluster_users[int(label)].append(user_ids[idx])
                cluster_messages[int(label)].append(contents[idx])

        # Build result: only clusters with 3+ distinct accounts
        clusters = []
        for cluster_id, users in cluster_users.items():
            distinct_users = list(set(users))
            if len(distinct_users) >= 3:
                clusters.append({
                    'cluster_id': f'sem_{cluster_id}',
                    'user_ids': distinct_users,
                    'size': len(distinct_users),
                    'sample_message': cluster_messages[cluster_id][0]
                })

        clustered_count = sum(len(c['user_ids']) for c in clusters)
        total = len(messages)
        clustered_ratio = clustered_count / total if total > 0 else 0.0

        # Risk score: scales with concentration and cluster count
        risk = min(clustered_ratio * 40 + len(clusters) * 3, 25.0)

        return ClusterResult(
            cluster_count=len(clusters),
            clustered_ratio=clustered_ratio,
            clusters=clusters,
            risk_score=risk
        )
```

**DBSCAN parameters:**
- `eps=0.20`: Maximum cosine distance for two messages to be considered neighbors (similarity >= 0.80)
- `min_samples=3`: Minimum messages to form a cluster
- Adjust `eps` if too many false clusters on organic chat: increase to 0.15 (similarity >= 0.85)

---

### 7. Isolation Forest Account Scorer

**File:** `backend/detection/batch/isolation.py`

**Detects:** Individual accounts whose behavior patterns are statistically anomalous compared to the observed normal user population. Session-level outlier detection.

**Feature vector per account:**

```python
@dataclass
class AccountFeatureVector:
    account_age_days: float          # 0 if unknown
    messages_this_session: int
    unique_words_ratio: float        # unique_words / total_words
    avg_message_length: float
    emoji_frequency: float           # emojis per message
    url_frequency: float             # messages with URLs / total
    mention_frequency: float         # @mentions per message
    reply_frequency: float           # replies / total messages
    messages_per_minute_peak: float
    username_entropy_score: float    # from UsernameEntropyScorer
    first_message_offset_seconds: float  # seconds after stream start
```

```python
from sklearn.ensemble import IsolationForest
import numpy as np

class IsolationForestScorer:
    def __init__(self):
        self.model = IsolationForest(
            contamination=0.05,  # Expect 5% outliers
            random_state=42,
            n_estimators=100
        )
        self.fitted = False
        self.training_vectors: list = []
        self.MIN_TRAINING_SAMPLES = 30

    def add_account(self, features: AccountFeatureVector):
        """Add account features to training pool."""
        self.training_vectors.append(self._to_array(features))

        # Refit when we have enough samples
        if len(self.training_vectors) >= self.MIN_TRAINING_SAMPLES:
            if not self.fitted or len(self.training_vectors) % 20 == 0:
                self._fit()

    def score_account(self, features: AccountFeatureVector) -> float:
        """
        Returns anomaly risk score 0–20.
        Higher = more anomalous relative to session baseline.
        """
        if not self.fitted:
            return 0.0

        vec = self._to_array(features).reshape(1, -1)

        # decision_function: negative = anomalous, positive = normal
        score = self.model.decision_function(vec)[0]

        # Convert: -0.5 → 0 risk, -0.2 → 10 risk, 0.0+ → 0 risk
        if score >= -0.1:
            return 0.0
        return min(abs(score + 0.1) * 40, 20.0)

    def _fit(self):
        X = np.array(self.training_vectors)
        self.model.fit(X)
        self.fitted = True

    def _to_array(self, f: AccountFeatureVector) -> np.ndarray:
        return np.array([
            min(f.account_age_days / 365, 10),  # Cap at 10 years
            min(f.messages_this_session / 100, 1),
            f.unique_words_ratio,
            min(f.avg_message_length / 200, 1),
            min(f.emoji_frequency, 1),
            min(f.url_frequency, 1),
            min(f.mention_frequency, 1),
            min(f.reply_frequency, 1),
            min(f.messages_per_minute_peak / 30, 1),
            f.username_entropy_score,
        ])
```

---

### 8. Account Co-occurrence Graph

**File:** `backend/detection/batch/graph.py`

**Detects:** Bot networks where the bots vary their messages enough to evade clustering but still exhibit a coordinated network structure — many accounts all appearing in the same clusters across multiple windows.

**Runs:** Every 60 seconds (less frequent — higher computational cost).

```python
import networkx as nx
from collections import defaultdict

class CooccurrenceGraphDetector:
    def __init__(self):
        self.graph = nx.Graph()
        self.cluster_history: list[list[str]] = []  # list of user_id lists

    def add_cluster(self, user_ids: list[str]):
        """Called each time SemanticClusterer or MinHash finds a cluster."""
        self.cluster_history.append(user_ids)

        # Add edges between all pairs in this cluster
        for i in range(len(user_ids)):
            for j in range(i+1, len(user_ids)):
                u, v = user_ids[i], user_ids[j]
                if self.graph.has_edge(u, v):
                    self.graph[u][v]['weight'] += 1
                else:
                    self.graph.add_edge(u, v, weight=1)

    def detect_communities(self) -> list[set[str]]:
        """
        Returns list of suspected bot communities.
        A community is a dense subgraph where accounts
        appear together across many clusters.
        """
        if len(self.graph.nodes) < 5:
            return []

        # Filter to edges with weight >= 2 (appeared together at least twice)
        subgraph = nx.Graph([
            (u, v) for u, v, d in self.graph.edges(data=True)
            if d['weight'] >= 2
        ])

        if not subgraph.nodes:
            return []

        # Louvain community detection
        communities = nx.community.louvain_communities(subgraph, seed=42)

        # Return communities with >= 5 members
        return [c for c in communities if len(c) >= 5]

    def risk_score(self) -> float:
        communities = self.detect_communities()
        if not communities:
            return 0.0
        largest = max(len(c) for c in communities)
        return min(largest / 10.0, 1.0) * 20.0

    def reset(self):
        """Clear graph between sessions."""
        self.graph.clear()
        self.cluster_history.clear()
```

---

## Confidence Score Aggregation

**File:** `backend/detection/aggregator.py`

Combines all detector scores into a per-user threat score and per-cluster confidence score.

### Per-User Threat Score

```python
SIGNAL_WEIGHTS = {
    'temporal_sync':   30,  # Strongest: direct evidence of coordination
    'minhash_cluster': 25,  # Strong: near-identical messages
    'semantic_cluster': 20, # Strong: semantically similar
    'rate_anomaly':    15,  # Moderate: machine-speed messaging
    'burst_anomaly':   10,  # Moderate: statistically abnormal volume
    'isolation_forest': 10, # Moderate: behavioral outlier
    'username_entropy': 8,  # Weak: supplementary only
    'username_family':  7,  # Weak: supplementary only
    'new_account':     5,   # Very weak: alone means nothing
}

def compute_user_threat_score(signals: dict[str, float]) -> float:
    """
    signals: dict of signal_name → raw detector score (0–1.0)
    Returns composite threat score 0–100.
    """
    weighted_sum = sum(
        signals.get(name, 0.0) * weight
        for name, weight in SIGNAL_WEIGHTS.items()
    )
    # Normalize to 0–100 range
    max_possible = sum(SIGNAL_WEIGHTS.values())
    return min((weighted_sum / max_possible) * 100, 100.0)
```

### Reputation Modifier

Applies a multiplier based on the user's history in the system:

```python
def apply_reputation_modifier(base_score: float, reputation) -> float:
    if reputation is None:
        return base_score

    flag_rate = reputation.times_flagged / max(reputation.total_sessions, 1)

    if flag_rate > 0.5:
        modifier = 1.5   # Frequently flagged: amplify score
    elif flag_rate < 0.05 and reputation.total_sessions > 5:
        modifier = 0.6   # Long-standing clean user: dampen score
    else:
        modifier = 1.0

    return min(base_score * modifier, 100.0)
```

### Action Threshold Table

| Score | Action | Additional Requirements |
|---|---|---|
| 0–39 | Log only | None |
| 40–59 | Dashboard flag (orange) | None |
| 60–74 | Dashboard alert (red) + notify streamer | None |
| 60–74 | Delete specific message | None |
| 75–84 | Auto-timeout 60s | User not protected |
| 85–94 | Auto-timeout 600s | User not protected; second signal in last 30s |
| 95–100 | Auto-ban | Two independent signals both > 90; user not protected; dry-run OFF |

---

## Detection Suppression Events

The following Twitch EventSub events trigger automatic suppression of detection. During suppression, all detector scores are zeroed and no automated actions fire.

```python
class DetectionSuppressor:
    SUPPRESSION_RULES = {
        'channel.raid':                     90,   # seconds
        'channel.hype_train.begin':         120,
        'channel.hype_train.end':           30,   # 30s extra cooldown after end
        'channel.subscription.gift':        60,   # If total >= 10
        'channel.chat.notification':        0,    # System messages filtered, no suppression
    }
```

Additionally, when the Twitch connection drops and reconnects, detection is suppressed for 15 seconds after reconnect (warmup period).

---

## Known Evasion Techniques and Mitigations

| Evasion Technique | What It Defeats | Mitigation |
|---|---|---|
| Unicode homoglyphs | Exact hash, basic similarity | NFKC normalization + homoglyph map in preprocessor |
| Random timing delays | Fixed-window sync detection | Multi-window detection (1s, 3s, 5s, 15s, 30s) |
| Message paraphrasing | MinHash similarity | MiniLM semantic embeddings catch semantic equivalence |
| Word padding/noise | Some embedding models | Truncate to 100 chars before embedding (payload is always early) |
| Organic-looking usernames | Entropy scoring | Username family pattern detection + behavioral signals take priority |
| Mixed legitimate + bot messages | Temporal detection | Isolation Forest scores individual anomaly against session baseline |
| Aged accounts | New account filter | Account age is a weak signal; behavioral clustering is primary |
| Slow drip spam (1 msg/10min) | Rate detection | Reputation scoring accumulates across sessions |

---

## Algorithm Rollout Schedule

**Phase 3 — Implement first (no ML required):**
- Temporal synchronization detector
- MinHash duplicate detector
- Per-user rate anomaly detector
- Username entropy scorer
- Burst anomaly detector (z-score)

**Phase 4 — Implement second (ML required):**
- Semantic cluster detector (MiniLM + DBSCAN)
- Isolation Forest account scorer
- Account co-occurrence graph

**Phase 4+ — Later refinement:**
- Username family pattern detector (adds value incrementally)
- Reputation system (requires accumulated session data)
