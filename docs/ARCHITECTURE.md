# System Architecture

## Overview

The application runs as two OS processes on Windows: an Electron shell and a Python backend. The Electron shell manages the Python process lifecycle and renders the React dashboard. The Python backend handles all Twitch connectivity, detection, and moderation.

```
Windows OS
├── Electron Main Process (PID A)
│   ├── PythonManager — spawns, monitors, restarts Python
│   ├── SystemTray — tray icon, quick actions
│   ├── PowerEventHandler — sleep/wake reconnect
│   └── AutoUpdater — electron-updater
│
├── Electron Renderer Process (PID B) — Chromium/React
│   ├── WebSocket client → ws://127.0.0.1:{PORT}/ws
│   └── REST client → http://127.0.0.1:{PORT}/api/*
│
└── Python Process (PID C) — twitchids-backend.exe
    ├── FastAPI (uvicorn, 127.0.0.1 only)
    ├── TwitchIO EventSub client
    ├── Detection pipeline
    ├── Moderation dispatcher
    └── SQLite + DuckDB data layer
```

---

## IPC Design

Three communication channels exist between the processes.

### Channel 1: stdout JSON Protocol (Lifecycle)

Python writes newline-delimited JSON to stdout. Electron main process reads and parses it. Used exclusively for process lifecycle signals.

**Python → Electron messages:**

```json
{ "type": "ready", "port": 7842, "ipc_secret": "abc123..." }
{ "type": "health", "status": "ok", "uptime": 42.1 }
{ "type": "error", "message": "Twitch connection failed", "code": "CONN_ERR" }
{ "type": "shutdown", "reason": "graceful" }
```

**Electron → Python messages (via stdin):**

```json
{ "type": "shutdown" }
```

All stdout messages must be flushed immediately (`print(..., flush=True)`). Non-JSON lines on stdout are silently ignored by Electron.

### Channel 2: WebSocket (Live Events)

Python pushes real-time events to the renderer. The renderer never polls for live data — all live updates are pushed.

- Endpoint: `ws://127.0.0.1:{PORT}/ws`
- Auth: `X-IPC-Secret` header on the upgrade request
- Format: newline-delimited JSON frames
- Rate: Health score snapshots emitted every 1 second. Alerts emitted immediately.

Event types pushed over WebSocket:
- `health_update` — full health snapshot, 1/second
- `threat_alert` — new threat detected
- `moderation_action` — action executed or queued
- `cluster_update` — semantic cluster formed or dissolved
- `connection_status` — Twitch connect/disconnect events

### Channel 3: REST API (Commands and Queries)

Used for config reads/writes, history queries, manual moderation commands. All requests require `X-IPC-Secret` header.

```
GET  /health                   — no auth, used for startup polling
GET  /api/config               — get current configuration
POST /api/config               — update configuration
GET  /api/channels             — list monitored channels
POST /api/channels             — add a channel
DELETE /api/channels/{id}      — remove a channel
GET  /api/history/messages     — paginated message history
GET  /api/history/actions      — paginated moderation action history
GET  /api/flagged              — currently flagged users
POST /api/moderation/ban       — manual ban
POST /api/moderation/timeout   — manual timeout
DELETE /api/moderation/ban/{id} — unban (undo)
GET  /api/stats/summary        — current session statistics
GET  /api/stats/timeline       — health score history (last N minutes)
```

---

## Startup Sequencing

The startup sequence eliminates the race condition where the renderer tries to connect before Python is ready.

```
1. Electron main process starts
2. PythonManager probes port 7842 (falls back to any free port)
3. PythonManager spawns Python process with --port {PORT} --parent-pid {PID}
4. Electron polls Python stdout for {"type": "ready"} message
5. Timeout: if no ready signal in 20 seconds → show error dialog
6. On ready: Electron stores port + ipc_secret in memory
7. Electron sends backend config to renderer via ipcMain
8. Renderer connects WebSocket using received port + secret
9. Renderer can now make API calls
```

**Python startup sequence:**

```
1. Parse --port and --parent-pid args
2. Set Windows process priority to Below Normal
3. Register SensitiveFilter on root logger
4. Initialize SQLite (run schema migrations if needed)
5. Load MiniLM model (lazy — defer until first clustering request)
6. Start FastAPI/uvicorn on 127.0.0.1:{PORT}
7. Connect to Twitch EventSub
8. Emit {"type": "ready", "port": PORT, "ipc_secret": SECRET} to stdout
9. Start heartbeat loop (emits health every 5s)
10. Start detection engine loops
```

---

## Crash Recovery

### Python Crash

Electron's PythonManager detects process exit via the `exit` event on the child process.

```
On crash:
1. Mark backend as unavailable in renderer (gray status indicator)
2. Increment restart counter
3. Wait: 1s, 2s, 4s, 8s, 16s (exponential backoff by attempt number)
4. After 5 failed restarts: show error dialog, stop retrying
5. On successful restart: renderer reconnects WebSocket automatically
```

On Python restart, the detection engine scans for `status='pending'` moderation actions older than 30 seconds and resolves them via Helix API state check.

### Twitch Disconnection

```
On disconnect:
1. Pause detection (suppress all detectors)
2. Record disconnect timestamp
3. Begin reconnect with exponential backoff (1s, 2s, 5s, 10s, 30s, 60s, 120s)
4. On reconnect:
   a. If gap > 30s: clear all rolling windows and reset baseline
   b. Wait 15 seconds (warmup period — let windows fill with clean data)
   c. Resume detection
```

### Network Outage

The detection engine continues running locally during an outage — it just has no new messages to process. When connectivity returns, TwitchIO reconnects automatically. The 15-second warmup prevents stale window data from triggering false alerts.

### Windows Sleep / Wake

```python
# Python registers for Windows power notifications via win32api
# On sleep:
#   - Disconnect from Twitch cleanly
#   - Suppress detection indefinitely
# On wake:
#   - Wait 5 seconds (network stabilization)
#   - Reconnect Twitch
#   - Clear windows, reset baseline
#   - 15-second warmup, then resume
```

---

## Detection Pipeline

All message processing is async. The fast path runs on every message. The batch path runs on a timer.

```
EventSub message arrives
        │
        ▼
  Normalizer
  - Unicode NFKC normalization
  - Homoglyph substitution (Cyrillic, Greek lookalikes → ASCII)
  - Strip zero-width characters
  - Lowercase
  - Truncate to 500 chars
        │
        ▼
  asyncio.Queue (maxsize=10_000)
  On overflow: drop oldest, not newest
        │
        ▼
  Feature Extractor (~0.5ms)
  Produces ChatMessage with:
  - content_normalized
  - content_hash (MD5 of normalized)
  - message_length
  - emoji_count
  - url_present (bool)
  - mention_count
        │
   ┌────┴──────────────────────────────────┐
   │                                       │
   ▼ Fast Path (every message, < 5ms)      │
IncrementalDuplicateTracker                │
MinHashLSHIndex                            │
TemporalSyncDetector (multi-window)        │
BurstAnomalyDetector (z-score)             │
UsernameEntropyScorer                      │
   │                                       │
   │                                       ▼ Batch Path (every 10s)
   │                              SemanticClusterer
   │                              (MiniLM ONNX + DBSCAN)
   │                                       │
   └─────────────────┬─────────────────────┘
                     │
                     ▼
             MetricCalculator
             (aggregates all signals, 1/sec tick)
                     │
                     ▼
             HealthScoreEngine
             (weighted combination + adaptive baseline)
                     │
                     ▼
             AnomalyDetector
             (level state machine, 2-cycle confirmation)
                     │
                     ▼
             ResponseManager
             (safety checks → ModerationDispatcher)
                     │
              ┌──────┴──────┐
              ▼             ▼
       Helix API      WebSocket push
       (actions)      (UI events)
```

---

## Moderation Action Flow

Every automated action follows this exact sequence:

```
1. Threat score computed ≥ threshold
2. Safety checks:
   a. Is user in protected list? → abort
   b. Is detection suppressed (raid/hype train)? → abort
   c. Is confidence threshold met for this action type? → proceed / downgrade action
   d. For bans: are two independent signals both > 90? → proceed / downgrade to timeout
   e. Is dry-run mode enabled? → log only, no API call
3. Write action to DB with status='pending'
4. Enqueue in ModerationDispatcher (asyncio.Queue, bounded at 1000)
5. TokenBucketRateLimiter (80 actions/min) dequeues and executes
6. Helix API call (ban / timeout / delete message / settings change)
7. Update DB action status to 'completed' or 'failed'
8. Push action event to UI via WebSocket
```

On application startup, scan DB for actions with `status='pending'` older than 30 seconds. For each: check current ban status via Helix API and update DB accordingly.

---

## Data Flow: Message to Dashboard

```
1. Twitch sends message via EventSub WebSocket (TwitchIO)
2. TwitchIO fires event_eventsub_notification callback
3. ChatMessage created, added to ChatBuffer ring buffers
4. Message processed by fast path detectors
5. Every 1 second: MetricCalculator reads all ring buffers, computes metric vector
6. HealthScoreEngine combines metrics → health score + level
7. AnomalyDetector evaluates level change, triggers responses
8. WebSocketBroadcaster serializes HealthSnapshot → JSON
9. JSON pushed to all connected WebSocket clients (renderer)
10. React state updated → components re-render
```

End-to-end latency target (EventSub receipt → UI display): < 200ms.

---

## Port Allocation

```javascript
// electron/python-manager.js
async findFreePort(preferred = 7842) {
  // Try preferred port first
  // If in use: bind to port 0 (OS assigns free port)
  // Store assigned port, pass to Python via --port arg
  // Store in Electron memory, pass to renderer via ipcMain
}
```

The port is dynamic. The renderer never hardcodes `7842`. It always receives the actual port from the main process after Python signals ready.

---

## WebSocket Event Schemas

### health_update (1/second)

```json
{
  "type": "health_update",
  "timestamp": 1710000123.456,
  "health": {
    "score": 67,
    "risk_score": 33,
    "level": "elevated",
    "level_duration_seconds": 4,
    "trend": "worsening"
  },
  "chat_activity": {
    "messages_per_minute": 342,
    "active_users": 89,
    "messages_in_5s": 28,
    "messages_in_30s": 171
  },
  "signals": {
    "velocity_risk": 8.2,
    "duplicate_risk": 14.6,
    "sync_risk": 6.0,
    "cluster_risk": 4.5,
    "new_account_risk": 0.0,
    "entropy_risk": 0.0,
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
        "user_ids": ["uid1", "uid2", "uid3"],
        "sample_message": "Follow scambot for free subs!",
        "first_seen": 1710000120.0
      }
    ]
  },
  "flagged_users": [
    {
      "user_id": "uid1",
      "username": "xX_bot1234_Xx",
      "threat_score": 78,
      "signals": ["temporal_sync", "bot_username", "new_account"]
    }
  ],
  "response_state": {
    "dry_run_mode": false,
    "slow_mode_active": false,
    "followers_only_active": false,
    "detection_suppressed": false,
    "suppression_reason": null,
    "pending_actions": 0
  }
}
```

### threat_alert (on detection)

```json
{
  "type": "threat_alert",
  "alert_id": "alert_001",
  "severity": "high",
  "signal": "temporal_sync",
  "affected_users": ["uid1", "uid2", "uid3", "uid4"],
  "cluster_id": "c_001",
  "confidence": 84,
  "description": "4 accounts sent identical messages within 1.2 seconds",
  "timestamp": 1710000123.456
}
```

### moderation_action (on action)

```json
{
  "type": "moderation_action",
  "action_id": "action_042",
  "action_type": "timeout",
  "user_id": "uid1",
  "username": "xX_bot1234_Xx",
  "duration_seconds": 600,
  "reason": "Bot cluster detection (confidence: 84)",
  "automated": true,
  "dry_run": false,
  "timestamp": 1710000124.0
}
```

---

## Database Schema

All tables live in a single SQLite file at `%APPDATA%\TwitchIDS\data.db`.

```sql
CREATE TABLE messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id      TEXT NOT NULL,
    user_id         TEXT NOT NULL,
    username        TEXT NOT NULL,
    content         TEXT NOT NULL,
    content_hash    TEXT NOT NULL,
    timestamp       REAL NOT NULL,
    threat_score    REAL DEFAULT 0,
    cluster_id      TEXT,
    flags           TEXT  -- JSON array of signal names
);

CREATE TABLE flagged_users (
    user_id         TEXT PRIMARY KEY,
    username        TEXT NOT NULL,
    first_seen      REAL NOT NULL,
    last_seen       REAL NOT NULL,
    threat_score    REAL NOT NULL,
    status          TEXT NOT NULL CHECK(status IN ('monitoring','actioned','cleared')),
    signals         TEXT,  -- JSON array
    actions_taken   TEXT   -- JSON array
);

CREATE TABLE moderation_actions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    username        TEXT NOT NULL,
    action_type     TEXT NOT NULL CHECK(action_type IN ('ban','timeout','delete_message','slow_mode','followers_only','subscribers_only')),
    duration        INTEGER,
    reason          TEXT,
    confidence      REAL,
    signals         TEXT,  -- JSON array of triggering signals
    timestamp       REAL NOT NULL,
    automated       INTEGER NOT NULL DEFAULT 1,
    dry_run         INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','completed','failed','reversed')),
    error_message   TEXT,
    reversed_at     REAL,
    reversed_by     TEXT  -- 'user' or 'auto'
);

CREATE TABLE user_reputation (
    user_id         TEXT PRIMARY KEY,
    username        TEXT NOT NULL,
    first_seen      REAL NOT NULL,
    last_seen       REAL NOT NULL,
    total_sessions  INTEGER DEFAULT 0,
    times_flagged   INTEGER DEFAULT 0,
    times_actioned  INTEGER DEFAULT 0,
    avg_threat_score REAL DEFAULT 0,
    is_whitelisted  INTEGER DEFAULT 0,
    whitelist_reason TEXT
);

CREATE TABLE config (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL,  -- JSON value
    updated_at      REAL NOT NULL
);

CREATE TABLE health_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id      TEXT NOT NULL,
    timestamp       REAL NOT NULL,
    health_score    REAL NOT NULL,
    risk_score      REAL NOT NULL,
    level           TEXT NOT NULL,
    messages_per_min REAL,
    active_users    INTEGER,
    duplicate_ratio REAL,
    active_signals  TEXT  -- JSON array
);

-- Indexes
CREATE INDEX idx_messages_timestamp ON messages(timestamp);
CREATE INDEX idx_messages_user_id ON messages(user_id);
CREATE INDEX idx_messages_content_hash ON messages(content_hash);
CREATE INDEX idx_moderation_actions_user ON moderation_actions(user_id);
CREATE INDEX idx_moderation_actions_status ON moderation_actions(status);
CREATE INDEX idx_health_history_channel_time ON health_history(channel_id, timestamp);
```

**SQLite configuration (set on every connection open):**

```sql
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA cache_size = -64000;  -- 64MB cache
PRAGMA temp_store = MEMORY;
PRAGMA mmap_size = 268435456;  -- 256MB mmap
```

**Retention policy (enforced by background job, runs every hour):**

- `messages`: purge rows older than 7 days
- `health_history`: purge rows older than 30 days
- `moderation_actions`: keep indefinitely (important audit log)
- `flagged_users`: keep indefinitely, status set to 'cleared' after 90 days of inactivity

---

## Process Priority

The Python backend sets itself to Windows Below Normal priority on startup to avoid competing with the streamer's game and OBS:

```python
import ctypes, os

def set_below_normal_priority():
    BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
    handle = ctypes.windll.kernel32.OpenProcess(0x1F0FFF, False, os.getpid())
    ctypes.windll.kernel32.SetPriorityClass(handle, BELOW_NORMAL_PRIORITY_CLASS)
    ctypes.windll.kernel32.CloseHandle(handle)
```

---

## Performance Budgets

| Volume | Fast path/msg | 1s tick | 10s batch | Memory |
|---|---|---|---|---|
| 100 msg/min | < 5ms | < 5ms | < 100ms | < 200MB |
| 1,000 msg/min | < 5ms | < 20ms | < 500ms | < 300MB |
| 5,000 msg/min | < 10ms | < 50ms | < 2,000ms* | < 450MB |

*At 5K msg/min, the semantic batch runs in a thread pool executor and does not block the 1s tick loop. Adaptive sampling kicks in above 3K msg/min.

### Adaptive Detection Mode

```
0–1,000 msg/min:    Full mode — all algorithms at full resolution
1,000–3,000 msg/min: Reduced mode — MinHash + sampled MiniLM (20% of messages)
3,000+ msg/min:     Minimal mode — MinHash + temporal sync only; MiniLM suspended
```

The mode is recalculated every 30 seconds based on observed message rate.

---

## Frontend Component Map

```
App
├── TopBar
│   ├── ChannelSelector
│   ├── ConnectionStatus
│   └── GlobalHealthIndicator (large score display)
├── MainPanel
│   ├── LeftColumn
│   │   ├── MetricsBar (msg/min, users, dup ratio, sync score)
│   │   ├── HealthScoreChart (60-min timeline, Recharts)
│   │   └── SignalBreakdown (contribution of each signal)
│   ├── CenterColumn
│   │   └── ChatFeed (react-window virtualized list, color-coded)
│   └── RightColumn
│       ├── ThreatPanel (active clusters and flagged users)
│       ├── ActionLog (recent moderation actions, undo buttons)
│       └── BotNetworkGraph (react-force-graph)
└── SettingsDrawer
    ├── ChannelConfig
    ├── ThresholdConfig
    ├── WhitelistManager
    ├── ModerationSettings (dry-run toggle, action thresholds)
    └── AuthManager (Twitch account connection)
```

**State management:** Zustand store with two slices:
- `chatStore` — current health snapshot, recent messages, active clusters
- `configStore` — application configuration, channel list

**WebSocket handling:** Custom `useWebSocket` hook manages connection, reconnection, and event routing. All incoming events update the Zustand store.

---

## Build Artifacts

```
dist/
├── twitchids-backend.exe      (PyInstaller single-file, ~300MB with models)
├── TwitchIDS-Setup-1.0.0.exe  (NSIS installer, includes backend.exe)
└── TwitchIDS-1.0.0.zip        (Portable version, no installer)
```

The NSIS installer:
1. Installs Electron app to `%LOCALAPPDATA%\TwitchIDS`
2. Unpacks `twitchids-backend.exe` to the same directory
3. Creates Start Menu shortcut
4. Registers auto-start (optional, user choice during install)
5. Registers uninstaller in Programs and Features
