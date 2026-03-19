# Chat Health Score System

The Chat Health Score (CHS) is a single number from 0–100 emitted every second from the Python detection engine. 100 = perfectly healthy chat. 0 = active bot raid in progress. A well-tuned system should reach a score below 30 within 3–5 seconds of a bot raid starting.

---

## Architecture

```
ChatBuffer (ring buffers)
        │
        ▼
MetricCalculator          ← runs every 1 second
  │  VelocityMetric
  │  DuplicateMetric
  │  SyncMetric
  │  NewAccountMetric
  │  EntropyMetric
  │  BurstMetric
  │  ClusterMetric ←── fed from SemanticClusterer (updates every 10s)
        │
        ▼
HealthScoreEngine
  │  AdaptiveBaseline
  │  WeightedScoreComposer
        │
        ▼
AnomalyDetector
  │  Level classification
  │  State machine (2-cycle confirmation)
  │  Response triggers
        │
        ▼
WebSocketBroadcaster → Electron dashboard (1/second)
```

---

## Ring Buffer System

**File:** `backend/pipeline/chat_buffer.py`

Multi-resolution ring buffers are the data foundation for all metrics. Every window is maintained as a `collections.deque` with O(1) add and O(1) prune operations.

```python
from collections import deque
from dataclasses import dataclass, field
import time

@dataclass
class ChatMessage:
    message_id: str
    channel_id: str
    user_id: str
    username: str
    content: str               # Raw original content
    content_normalized: str    # After normalization pipeline
    content_hash: str          # MD5 of normalized content
    timestamp: float           # Unix timestamp
    account_age_days: int | None = None
    minhash_flagged: bool = False
    threat_score: float = 0.0

class ChatBuffer:
    WINDOWS = {
        '5s':   5,
        '10s':  10,
        '30s':  30,
        '60s':  60,
        '300s': 300,
    }

    def __init__(self):
        self.buffers: dict[str, deque[ChatMessage]] = {
            name: deque() for name in self.WINDOWS
        }
        # Active user set per window
        self.active_users: dict[str, set[str]] = {
            name: set() for name in self.WINDOWS
        }

    def add(self, msg: ChatMessage) -> None:
        now = msg.timestamp
        for name, seconds in self.WINDOWS.items():
            self.buffers[name].append(msg)
            self.active_users[name].add(msg.user_id)
            self._prune(name, now - seconds)

    def _prune(self, name: str, cutoff: float) -> None:
        buf = self.buffers[name]
        # Also remove pruned users from active set
        while buf and buf[0].timestamp < cutoff:
            old_msg = buf.popleft()
            # Only remove from active set if no remaining messages from this user
            if not any(m.user_id == old_msg.user_id for m in buf):
                self.active_users[name].discard(old_msg.user_id)

    def window(self, name: str) -> list[ChatMessage]:
        return list(self.buffers[name])

    def window_count(self, name: str) -> int:
        return len(self.buffers[name])

    def unique_users(self, name: str) -> int:
        return len(self.active_users[name])

    def clear_all(self):
        """Call on reconnect after long gap."""
        for name in self.WINDOWS:
            self.buffers[name].clear()
            self.active_users[name].clear()
```

**Memory at 5K msg/min:**
- 300s window holds up to 25,000 messages
- ~500 bytes per ChatMessage = ~12.5MB max
- All windows combined: ~15MB — well within budget

---

## Individual Metrics

### Velocity Metric

Measures message rate relative to the channel's own baseline.

```python
class VelocityMetric:
    """
    Raw metric: messages per minute over last 5s (projected)
    Risk contribution: 0–30 points
    """

    def compute(self, buffer: ChatBuffer, baseline: 'AdaptiveBaseline') -> float:
        count_5s = buffer.window_count('5s')
        projected_mpm = count_5s * 12  # 5s → per-minute projection

        if not baseline.is_calibrated:
            # Pre-calibration: use absolute thresholds
            if projected_mpm > 500:
                return min((projected_mpm - 500) / 100, 1.0) * 30
            return 0.0

        z = baseline.z_score('velocity', projected_mpm)
        # z < 1.5: normal, z = 3: moderate, z = 5: severe
        return max(0, min((z - 1.5) * 10, 30.0))
```

### Duplicate Message Ratio (Incremental)

Maintained incrementally — O(1) per message, O(1) to read.

```python
from collections import Counter

class IncrementalDuplicateTracker:
    """
    Tracks duplicate ratio with O(1) updates.
    Risk contribution: 0–35 points
    """

    def __init__(self, window_seconds: int = 30):
        self.window = window_seconds
        self.buffer: deque = deque()       # (timestamp, hash) pairs
        self.hash_counts: Counter = Counter()

    def add(self, content_hash: str, timestamp: float) -> None:
        self.buffer.append((timestamp, content_hash))
        self.hash_counts[content_hash] += 1
        self._prune(timestamp)

    def _prune(self, now: float) -> None:
        cutoff = now - self.window
        while self.buffer and self.buffer[0][0] < cutoff:
            _, old_hash = self.buffer.popleft()
            self.hash_counts[old_hash] -= 1
            if self.hash_counts[old_hash] == 0:
                del self.hash_counts[old_hash]

    @property
    def duplicate_ratio(self) -> float:
        total = len(self.buffer)
        if total < 5:
            return 0.0
        unique = len(self.hash_counts)
        return 1.0 - (unique / total)

    @property
    def risk_score(self) -> float:
        ratio = self.duplicate_ratio
        if ratio < 0.05:
            return 0.0
        if ratio < 0.15:
            return ratio * 50  # 0–7.5 range
        return min(ratio * 100, 35.0)
```

### New Account Ratio

Requires account age lookup via Helix API. Results cached per session.

```python
class NewAccountMetric:
    """
    Fraction of active chatters with accounts < 7 days old.
    Risk contribution: 0–20 points
    Account ages fetched lazily from Helix API, cached.
    """

    NEW_ACCOUNT_DAYS = 7

    def __init__(self):
        self.age_cache: dict[str, int | None] = {}  # user_id → age_days
        self.pending_lookups: set[str] = set()

    def get_risk_score(self, active_user_ids: list[str]) -> float:
        if not active_user_ids:
            return 0.0

        # Only count users whose age we know
        known_ages = [
            self.age_cache[uid]
            for uid in active_user_ids
            if uid in self.age_cache and self.age_cache[uid] is not None
        ]

        if len(known_ages) < 3:
            return 0.0  # Not enough data

        new_count = sum(1 for age in known_ages if age < self.NEW_ACCOUNT_DAYS)
        ratio = new_count / len(known_ages)

        # Ratio < 0.10: normal, > 0.25: suspicious
        if ratio < 0.10:
            return 0.0
        return min((ratio - 0.10) * 100, 20.0)

    def enqueue_lookup(self, user_ids: list[str]) -> list[str]:
        """Returns user_ids not yet in cache (caller schedules Helix lookup)."""
        missing = [uid for uid in user_ids
                   if uid not in self.age_cache and uid not in self.pending_lookups]
        self.pending_lookups.update(missing)
        return missing

    def record_age(self, user_id: str, age_days: int | None):
        self.age_cache[user_id] = age_days
        self.pending_lookups.discard(user_id)
```

---

## Health Score Engine

**File:** `backend/detection/scoring/health_score_engine.py`

### Score Weights

```python
METRIC_WEIGHTS = {
    'temporal_sync':    0.25,  # Strongest signal: direct coordination evidence
    'duplicate_ratio':  0.25,  # Strong: identical or near-identical flood
    'semantic_cluster': 0.20,  # Strong: varied but semantically coordinated
    'velocity':         0.15,  # Moderate: volume spike
    'burst_anomaly':    0.08,  # Moderate: z-score spike
    'new_account':      0.04,  # Weak: alone insufficient
    'entropy':          0.03,  # Weak: supplementary only
}

# Maximum risk contribution per metric (maps to 0–100 total scale)
METRIC_MAX = {
    'temporal_sync':    25,
    'duplicate_ratio':  35,
    'semantic_cluster': 25,
    'velocity':         30,
    'burst_anomaly':    25,
    'new_account':      20,
    'entropy':          15,
}
```

### Computation

```python
@dataclass
class HealthSnapshot:
    timestamp: float
    health_score: float       # 0–100
    risk_score: float         # 0–100
    level: str
    level_duration_seconds: int
    trend: str                # 'worsening', 'stable', 'improving'
    metric_scores: dict[str, float]
    active_signals: list[str]
    messages_per_minute: float
    active_users: int
    duplicate_ratio: float
    cluster_data: dict

class HealthScoreEngine:
    def __init__(self):
        self.baseline = AdaptiveBaseline()
        self.prev_score = 100.0

    def compute(self, metrics: dict[str, float], cluster_data: dict,
                chat_stats: dict) -> HealthSnapshot:
        # Weighted risk score
        raw_risk = sum(
            metrics.get(name, 0.0) * weight
            for name, weight in METRIC_WEIGHTS.items()
        )

        # Calibrate against channel baseline
        calibrated_risk = self.baseline.calibrate(raw_risk)
        risk_score = max(0.0, min(100.0, calibrated_risk))
        health_score = 100.0 - risk_score

        # Trend
        delta = health_score - self.prev_score
        if delta < -5:
            trend = 'worsening'
        elif delta > 5:
            trend = 'improving'
        else:
            trend = 'stable'
        self.prev_score = health_score

        return HealthSnapshot(
            timestamp=time.time(),
            health_score=round(health_score, 1),
            risk_score=round(risk_score, 1),
            level=self._classify(health_score),
            level_duration_seconds=0,  # Set by AnomalyDetector
            trend=trend,
            metric_scores=metrics,
            active_signals=self._active_signals(metrics),
            messages_per_minute=chat_stats.get('mpm', 0),
            active_users=chat_stats.get('active_users', 0),
            duplicate_ratio=chat_stats.get('duplicate_ratio', 0),
            cluster_data=cluster_data
        )

    def _classify(self, score: float) -> str:
        if score >= 80: return 'healthy'
        if score >= 65: return 'elevated'
        if score >= 45: return 'suspicious'
        if score >= 25: return 'likely_attack'
        return 'critical'

    def _active_signals(self, metrics: dict) -> list[str]:
        thresholds = {
            'velocity': 10,
            'duplicate_ratio': 12,
            'temporal_sync': 8,
            'semantic_cluster': 8,
            'burst_anomaly': 8,
            'new_account': 6,
            'entropy': 5,
        }
        return [name for name, threshold in thresholds.items()
                if metrics.get(name, 0) >= threshold]
```

---

## Adaptive Baseline

**File:** `backend/detection/scoring/baseline.py`

Calibrates detection sensitivity to the channel's own normal traffic patterns. Prevents false alarms in naturally high-volume channels.

```python
import statistics
from collections import deque

class AdaptiveBaseline:
    """
    Tracks rolling statistics of raw metric values.
    After calibration, expresses current metrics as z-scores
    relative to the channel's own normal range.

    Key property: a channel that normally has 500 msg/min
    should not be alarmed by 600 msg/min. A channel that
    normally has 20 msg/min should be alarmed by 200 msg/min.
    """

    def __init__(self, history_minutes: int = 30):
        self.history_seconds = history_minutes * 60
        # metric_name → deque of (timestamp, value) pairs
        self.histories: dict[str, deque] = {}
        self.MIN_SAMPLES = 30

    def record(self, metrics: dict[str, float], timestamp: float):
        for name, value in metrics.items():
            if name not in self.histories:
                self.histories[name] = deque()
            self.histories[name].append((timestamp, value))
            self._prune(name, timestamp)

    def calibrate(self, raw_risk: float) -> float:
        """
        Returns calibrated risk score.
        Below MIN_SAMPLES: return raw_risk unchanged.
        Above MIN_SAMPLES: express as z-score scaled to 0–100.
        """
        key = 'raw_risk'
        if key not in self.histories or len(self.histories[key]) < self.MIN_SAMPLES:
            return raw_risk

        values = [v for _, v in self.histories[key]]
        mean = statistics.mean(values)
        stdev = statistics.stdev(values) if len(values) > 1 else 1.0
        stdev = max(stdev, 0.5)

        z = (raw_risk - mean) / stdev
        # Scale: z=0 → 20 risk (baseline), z=3 → 80 risk, z=5 → 100 risk
        calibrated = 20 + max(0, z) * 20
        return min(calibrated, 100.0)

    def z_score(self, metric: str, value: float) -> float:
        if metric not in self.histories:
            return 0.0
        history = self.histories[metric]
        if len(history) < self.MIN_SAMPLES:
            return 0.0
        values = [v for _, v in history]
        mean = statistics.mean(values)
        stdev = max(statistics.stdev(values) if len(values) > 1 else 1.0, 0.1)
        return (value - mean) / stdev

    @property
    def is_calibrated(self) -> bool:
        return ('raw_risk' in self.histories and
                len(self.histories['raw_risk']) >= self.MIN_SAMPLES)

    def reset(self):
        """Call after reconnect with long gap."""
        self.histories.clear()

    def _prune(self, name: str, now: float):
        cutoff = now - self.history_seconds
        while self.histories[name] and self.histories[name][0][0] < cutoff:
            self.histories[name].popleft()
```

---

## Anomaly Detector State Machine

**File:** `backend/detection/anomaly/anomaly_detector.py`

Prevents single-cycle spikes from triggering automated responses. Requires 2 consecutive evaluation cycles at the same level before acting.

```python
class AnomalyDetector:
    LEVELS = ['healthy', 'elevated', 'suspicious', 'likely_attack', 'critical']

    def __init__(self, response_manager):
        self.response_manager = response_manager
        self.current_level = 'healthy'
        self.level_duration = 0
        self.prev_snapshot = None

    def evaluate(self, snapshot: HealthSnapshot) -> HealthSnapshot:
        new_level = snapshot.level

        if new_level == self.current_level:
            self.level_duration += 1
        else:
            self.current_level = new_level
            self.level_duration = 1

        # Attach duration to snapshot
        snapshot.level_duration_seconds = self.level_duration

        self._trigger_responses(snapshot)
        self.prev_snapshot = snapshot
        return snapshot

    def _trigger_responses(self, snapshot: HealthSnapshot):
        level = self.current_level
        duration = self.level_duration

        # Alert streamer dashboard after 2 cycles at elevated+
        if level in ('elevated', 'suspicious', 'likely_attack', 'critical'):
            if duration == 2:
                self.response_manager.send_alert(snapshot)

        # Suggest slow mode after 5s suspicious
        if level == 'suspicious' and duration == 5:
            self.response_manager.suggest_action('slow_mode', snapshot)

        # Auto-enable slow mode after 3s likely_attack
        if level == 'likely_attack' and duration == 3:
            self.response_manager.auto_action('enable_slow_mode', snapshot)

        # Auto-enable followers-only + mass timeout after 3s critical
        if level == 'critical' and duration == 3:
            self.response_manager.auto_action('enable_followers_only', snapshot)
            self.response_manager.timeout_detected_clusters(snapshot)

        # Recovery: suggest disabling restrictions after 10s elevated
        # (following a worse period)
        if level == 'elevated' and duration == 10:
            if self.prev_snapshot and self.prev_snapshot.level in ('likely_attack', 'critical'):
                self.response_manager.suggest_action('disable_restrictions', snapshot)
```

---

## Level Definitions

| Level | Score Range | Meaning | Auto-Action |
|---|---|---|---|
| Healthy | 80–100 | Normal chat activity | None |
| Elevated | 65–79 | Unusual but not alarming | Dashboard highlight after 2s |
| Suspicious | 45–64 | Multiple signals active | Dashboard alert + suggestion after 5s |
| Likely Attack | 25–44 | Strong bot evidence | Suggest slow mode; auto-execute if enabled after 3s |
| Critical | 0–24 | Active bot raid | Auto followers-only + mass timeout after 3s |

---

## Dashboard JSON Payload

Emitted every second to all connected WebSocket clients.

```json
{
  "type": "health_update",
  "timestamp": 1710000123.456,

  "health": {
    "score": 67.4,
    "risk_score": 32.6,
    "level": "elevated",
    "level_duration_seconds": 4,
    "trend": "worsening"
  },

  "chat_activity": {
    "messages_per_minute": 342,
    "active_users": 89,
    "messages_in_5s": 28,
    "messages_in_30s": 171,
    "duplicate_ratio": 0.12
  },

  "signals": {
    "velocity": 8.2,
    "duplicate_ratio": 14.6,
    "temporal_sync": 6.0,
    "semantic_cluster": 4.5,
    "burst_anomaly": 3.1,
    "new_account": 0.0,
    "entropy": 0.0,
    "active": ["velocity_spike", "duplicate_flood"]
  },

  "clusters": {
    "active_count": 2,
    "largest_cluster_size": 7,
    "total_flagged_users": 12,
    "clusters": [
      {
        "cluster_id": "c_001",
        "size": 7,
        "user_ids": ["uid1", "uid2", "uid3", "uid4", "uid5", "uid6", "uid7"],
        "sample_message": "Follow scambot for free subs!",
        "first_seen": 1710000120.0
      }
    ]
  },

  "flagged_users": [
    {
      "user_id": "uid1",
      "username": "xX_bot1234_Xx",
      "threat_score": 78.2,
      "signals": ["temporal_sync", "entropy", "new_account"],
      "status": "monitoring"
    }
  ],

  "response_state": {
    "dry_run_mode": false,
    "detection_suppressed": false,
    "suppression_reason": null,
    "slow_mode_active": false,
    "followers_only_active": false,
    "pending_actions": 0
  }
}
```

---

## Evaluation Metrics

When testing with the simulator, measure:

| Metric | Target |
|---|---|
| Time to first alert (bot raid start) | < 5 seconds |
| Time to Critical level (large raid) | < 10 seconds |
| False positive rate (normal chat) | < 3% |
| False positive rate (raid event) | 0% (must be suppressed) |
| Min health score during active raid | < 30 |
| Health score recovery after raid ends | > 70 within 30s |

---

## Performance

The 1-second tick loop must complete in < 50ms regardless of chat volume.

**Fast path (runs every message):**
- Buffer add: O(1) per window × 5 windows = O(5) ≈ O(1)
- Duplicate tracker update: O(1)
- Temporal sync update: O(k) where k = cluster bucket size ≈ < 1ms

**1-second tick (MetricCalculator):**
- Read buffer sizes: O(1) per window
- Compute duplicate ratio: O(1) (incremental)
- Read sync detector scores: O(1)
- Read burst anomaly: O(1)
- Combine into health score: O(n_signals) ≈ O(7) ≈ O(1)
- Serialize JSON: ~1–2ms
- WebSocket push: ~0.5ms

Total tick time: < 10ms at any volume.

**10-second batch (SemanticClusterer):**
- Runs in thread pool, never blocks tick loop
- At 5K msg/min with sampling: ~400ms in background thread
