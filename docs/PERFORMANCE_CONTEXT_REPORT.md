# APPLICATION PERFORMANCE CONTEXT REPORT

**Project**: TwitchIDS — Twitch Chat Intrusion Detection System
**Generated**: 2026-03-10
**Repository Root**: `s:\Twitch Chat Bot Detection`

---

## 1. Project Overview

**What it does**: TwitchIDS is a standalone Windows desktop application that monitors a streamer's Twitch chat in real time, detects bot farms and coordinated spam campaigns, and automatically triggers moderation actions (timeouts, bans, message deletion).

**Primary use case**: A Twitch streamer runs it locally on their PC during streams. The app connects to their channel's chat via Twitch EventSub WebSocket, continuously scores incoming messages for botlike behavior, and surfaces threats on a live dashboard — with optional automated moderation.

**Intended users**: Twitch streamers (primarily larger channels vulnerable to botting) who want local, privacy-preserving bot detection without a cloud subscription.

**Core functionality**:
- Ingest every chat message via TwitchIO 3.x EventSub WebSocket
- Run fast-path detectors (O(1) per message): duplicate ratio, temporal sync, MinHash LSH clustering, message rate, burst anomaly, username entropy, known-bot registry, Aho-Corasick spam patterns, inter-arrival timing
- Every 1 second: compute channel-level health score, run anomaly/drift detection, evaluate per-user threat scores, emit results to the React dashboard via WebSocket
- Every 10 seconds: run batch semantic clustering (MiniLM ONNX embeddings + DBSCAN) and cooccurrence/bot-network detection (igraph Infomap)
- Optionally execute Twitch Helix API moderation actions (timeout/ban) with dual-signal safety gates

---

## 2. Technology Stack

**Language(s)**: Python 3.12 (backend), TypeScript (frontend)

**Framework(s)**: FastAPI 0.115.5 (backend REST + WebSocket), React 18.3.1 (UI), Electron 33.2.1 (desktop shell)

**Runtime**: CPython 3.12, Node.js (Electron), uvicorn 0.32.1

**Key libraries**:
- TwitchIO 3.2.0 — EventSub WebSocket client
- aiosqlite 0.20.0 — async SQLite driver
- sentence-transformers 3.3.1 + onnxruntime 1.20.1 — MiniLM embeddings (ONNX export for 2-3x speedup)
- scikit-learn 1.6.0 — DBSCAN, Isolation Forest
- datasketch 1.6.5 — MinHash + LSH
- river 0.21.2 — online HalfSpaceTrees anomaly detector
- igraph 0.11.8 — Infomap community detection for bot network graphs
- fastembed 0.4.2 — BAAI/bge-small-en-v1.5 fast ONNX embedding (primary; sentence-transformers as fallback)
- pyahocorasick 2.1.1 — Aho-Corasick spam pattern matching
- keyring 25.5.0 — Windows Credential Manager (DPAPI) token storage
- httpx 0.28.1 — async HTTP client for Twitch Helix API
- structlog — structured logging
- pydantic 2.10.3 — settings + schema models
- duckdb 1.1.3 — analytics queries
- pywin32 — Windows process priority + Credential Manager

**Package manager**: pip (requirements.txt with exact pinned versions)

**Database(s)**:
- SQLite (WAL mode, aiosqlite) — operational data (messages, flagged users, moderation actions, health history, reputation, whitelist)
- DuckDB 1.1.3 — analytics queries

**Frontend technologies**: React 18, Tailwind CSS 3.4, Vite 7.3.1, Zustand 4.5.5, react-window 1.8.10, sigma 3.0.2 + graphology 0.26.0 (WebGL bot network graph), Recharts 2.13.3

**Target operating system**: Windows (Windows Credential Manager dependency; process priority via ctypes Win32 API)

**Deployment environment**: Local desktop (single PC), no cloud dependency. Packaged via PyInstaller + electron-builder NSIS installer.

---

### `backend/requirements.txt` (key entries)

```
fastapi==0.115.5
uvicorn==0.32.1
pydantic==2.10.3
pydantic-settings==2.7.0
twitchio==3.2.0
httpx==0.28.1
aiosqlite==0.20.0
duckdb==1.1.3
keyring==25.5.0
datasketch==1.6.5
sentence-transformers==3.3.1
onnxruntime==1.20.1
scikit-learn==1.6.0
networkx==3.4.2
pyahocorasick==2.1.1
river==0.21.2
fastembed==0.4.2
igraph==0.11.8
python-dotenv==1.0.1
structlog==24.4.0
pywin32==308
psutil==6.1.1
```

### `frontend/package.json` (key entries)

```json
{
  "electron": "33.2.1",
  "react": "18.3.1",
  "tailwindcss": "3.4.15",
  "sigma": "3.0.2",
  "graphology": "0.26.0",
  "recharts": "2.13.3",
  "react-window": "1.8.10",
  "zustand": "4.5.5",
  "vite": "7.3.1",
  "typescript": "5.6.3",
  "electron-builder": "25.1.8",
  "rollup-plugin-visualizer": "5.12.0"
}
```

---

## 3. Repository Structure

```
s:\Twitch Chat Bot Detection\
├── CLAUDE.md                          # Project instructions + safety rules
├── DEV_SETUP.md                       # Development setup guide
├── docs/
│   ├── ARCHITECTURE.md
│   ├── TECH_STACK.md
│   ├── DETECTION_ALGORITHMS.md
│   ├── CHAT_HEALTH_SCORE.md
│   ├── ROADMAP.md
│   ├── SIMULATOR.md
│   ├── SECURITY.md
│   ├── AUDIT.md
│   ├── PERFORMANCE_AUDIT.md
│   └── PERFORMANCE_CONTEXT_REPORT.md  # This file
├── backend/
│   ├── main.py                        # FastAPI app factory + process priority
│   ├── startup.py                     # Pipeline singleton init + lifecycle hooks
│   ├── tasks.py                       # 5 background asyncio loops
│   ├── requirements.txt
│   ├── .env / .env.example
│   ├── core/
│   │   ├── config.py                  # Pydantic Settings (all configuration)
│   │   ├── ipc.py                     # stdout JSON protocol helpers
│   │   ├── logging.py                 # Structured logger + SensitiveFilter
│   │   └── telemetry.py               # Rolling perf metrics singleton
│   ├── twitch/
│   │   ├── client.py                  # TwitchIO 3.x EventSub client wrapper
│   │   ├── manager.py                 # Client singleton + callback routing
│   │   ├── token_store.py             # Windows Credential Manager token storage
│   │   └── auth.py                    # OAuth flow initialization
│   ├── pipeline/
│   │   ├── queue.py                   # Bounded asyncio.Queue (10K cap, drop-oldest)
│   │   ├── buffer.py                  # Multi-resolution ring buffers (5s–300s windows)
│   │   ├── builder.py                 # ChatMessage factory from raw Twitch payload
│   │   ├── models.py                  # ChatMessage dataclass
│   │   ├── normalizer.py              # Text normalization + content_hash + feature extraction
│   │   └── account_cache.py           # TTLCache for Twitch account age
│   ├── detection/
│   │   ├── engine.py                  # DetectionEngine (TickMixin + AlertingMixin)
│   │   ├── tick.py                    # TickMixin: 1s coordination + batch clustering
│   │   ├── alerting.py                # AlertingMixin: per-user threat eval
│   │   ├── aggregator.py              # Signal weights + compute_user_threat_score()
│   │   ├── known_bots.py              # KnownBotRegistry (GitHub list, 24h refresh)
│   │   ├── alerts.py                  # Alert event builders
│   │   ├── protection.py              # ProtectedAccountChecker
│   │   ├── suppressor.py              # DetectionSuppressor (raid/hype/gift events)
│   │   ├── fast/
│   │   │   ├── burst.py               # BurstAnomalyDetector
│   │   │   ├── duplicate.py           # IncrementalDuplicateTracker
│   │   │   ├── minhash.py             # MinHashDuplicateDetector (LSH)
│   │   │   ├── pattern_match.py       # SpamPatternMatcher (Aho-Corasick)
│   │   │   ├── rate.py                # UserRateDetector
│   │   │   ├── temporal.py            # TemporalSyncDetector
│   │   │   ├── timing.py              # IATScorer
│   │   │   ├── username.py            # score_single_username() entropy
│   │   │   └── username_family.py     # UsernameFamilyDetector
│   │   ├── batch/
│   │   │   ├── clustering.py          # SemanticClusterer (ONNX MiniLM + DBSCAN)
│   │   │   ├── cooccurrence.py        # CooccurrenceDetector (igraph Infomap)
│   │   │   ├── isolation.py           # Isolation Forest wrapper
│   │   │   └── river_anomaly.py       # RiverAnomalyScorer (HalfSpaceTrees online)
│   │   └── scoring/
│   │       ├── health_score.py        # HealthScoreEngine (weighted metric combo)
│   │       ├── baseline.py            # AdaptiveBaseline (channel calibration)
│   │       ├── anomaly.py             # AnomalyDetector (2-cycle state machine)
│   │       └── drift.py               # HealthDriftDetector (EWMA + ADWIN)
│   ├── moderation/
│   │   ├── engine.py                  # ModerationEngine (threat → action dispatcher)
│   │   ├── actions.py                 # ModerationAction dataclass + escalation table
│   │   ├── executor.py                # Helix API execution + transactional DB update
│   │   ├── helix.py                   # RefreshingHTTPClient for Twitch Helix API
│   │   └── rate_limiter.py            # TokenBucketRateLimiter (80 actions/60s)
│   ├── storage/
│   │   ├── db.py                      # SQLite schema init (WAL, indexes)
│   │   ├── writer.py                  # MessageWriter (batch insert, 100 msgs/100ms)
│   │   ├── reputation.py              # ReputationStore (cross-session scoring)
│   │   └── analytics.py               # DuckDB analytics query layer
│   ├── api/
│   │   ├── router.py                  # Route dispatcher
│   │   ├── middleware.py              # IPCAuthMiddleware (X-IPC-Secret)
│   │   ├── schemas.py                 # Pydantic request/response models
│   │   ├── websocket.py               # ConnectionManager (WS broadcast)
│   │   └── routes/
│   │       ├── chat.py / config.py / history.py / moderation.py
│   │       ├── reputation.py / simulator.py / stats.py / users.py / whitelist.py
│   ├── data/
│   │   └── spam_patterns.json         # Aho-Corasick pattern corpus
│   └── models/
│       └── minilm/                    # all-MiniLM-L6-v2 exported to ONNX
├── frontend/
│   ├── package.json
│   ├── electron/                      # Electron main process (windowing, tray, IPC)
│   └── src/                           # React components + Zustand stores
├── simulator/
│   ├── evaluate.py                    # Precision/recall/F1 evaluator
│   ├── scenarios/                     # YAML scenario definitions
│   └── generators/                    # Message generators (Markov, bot patterns)
└── packaging/
    ├── build.ps1                      # Build orchestration script
    └── ...                            # PyInstaller spec, NSIS config
```

**Major directory purposes**:
- `backend/core/` — cross-cutting concerns: config, logging, IPC, telemetry
- `backend/twitch/` — Twitch EventSub connection lifecycle and secure token management
- `backend/pipeline/` — message ingestion: bounded queue, ring buffers, normalization
- `backend/detection/fast/` — sub-millisecond per-message detectors (no ML)
- `backend/detection/batch/` — ML detectors run on windowed message sets every 5–10s
- `backend/detection/scoring/` — health score computation and drift detection
- `backend/moderation/` — action dispatch, safety gates, Helix API execution
- `backend/storage/` — SQLite operational DB, DuckDB analytics, reputation
- `backend/api/` — FastAPI REST routes + WebSocket manager
- `frontend/src/` — React dashboard (chat feed, threat panel, health gauge, bot network graph)
- `simulator/` — synthetic attack scenarios for F1/FP evaluation

---

## 4. Application Entry Points

### Python backend

**Main file**: `backend/main.py`
- `create_app()` — constructs FastAPI app, attaches middleware, routes, lifespan hooks
- `main()` — `uvicorn.run(create_app(), host="127.0.0.1", port=7842)`

**Startup hooks** (`backend/startup.py`):
- `on_startup()` — initializes DB, caches, detection engine, moderation engine, Twitch connection
- `on_shutdown()` — graceful teardown

**Background tasks** (`backend/tasks.py`) — 5 coroutines launched from `on_startup`:
- `heartbeat_loop()` — every 5s: stdout JSON heartbeat to Electron
- `detection_tick_loop()` — every 1s: `detection_engine.tick()`
- `pipeline_metrics_loop()` — every 30s: log msg/min, queue depth, active users
- `retention_loop()` — daily: purge old messages and health history
- `wal_checkpoint_loop()` — every 5 min: SQLite passive WAL checkpoint

**Launch command**:
```bash
cd backend
.venv/Scripts/python.exe main.py
```

**Electron launch**: Electron main process spawns `backend/main.py` as a child process, reads stdout JSON for the IPC secret, then opens the React window pointed at `http://127.0.0.1:7842`.

### Environment variables

All variables use `TWITCHIDS_` prefix, read by `core/config.py` via Pydantic Settings:

| Variable | Default | Purpose |
|---|---|---|
| `TWITCHIDS_HOST` | `127.0.0.1` | FastAPI bind address (never 0.0.0.0) |
| `TWITCHIDS_PORT` | `7842` | FastAPI port |
| `TWITCHIDS_DEV_MODE` | `false` | Skips Electron IPC checks; enables Swagger |
| `TWITCHIDS_SIMULATOR_ACTIVE` | `false` | Enables `/ws/inject` endpoint |
| `TWITCHIDS_DRY_RUN` | `true` | All moderation logged but not executed |
| `TWITCHIDS_AUTO_TIMEOUT_ENABLED` | `false` | Enables automated timeouts |
| `TWITCHIDS_AUTO_BAN_ENABLED` | `false` | Enables automated bans |
| `TWITCHIDS_TIMEOUT_THRESHOLD` | `75` | Threat score floor for timeouts |
| `TWITCHIDS_BAN_THRESHOLD` | `95` | Threat score floor for bans |
| `TWITCHIDS_ALERT_THRESHOLD` | `60` | Threat score floor for dashboard alerts |
| `TWITCHIDS_CLIENT_ID` | (required) | Twitch app client ID |
| `TWITCHIDS_MESSAGE_RETENTION_DAYS` | `7` | SQLite message retention |
| `IPC_SECRET` | (generated) | Shared HMAC secret for Electron↔Python auth |

---

## 5. Runtime Architecture

### Major subsystems

```
┌──────────────────────────────────────────────────────────┐
│                    Electron (main process)                │
│  - Python child process lifecycle                        │
│  - System tray + notifications                           │
│  - Reads stdout JSON (IPC secret, heartbeat, health)     │
└────────────────────┬─────────────────────────────────────┘
                     │ child_process spawn
┌────────────────────▼─────────────────────────────────────┐
│              Python FastAPI (uvicorn, port 7842)          │
│                                                          │
│  ┌──────────┐    ┌─────────┐    ┌────────────────────┐   │
│  │  Twitch  │───▶│Pipeline │───▶│  DetectionEngine   │   │
│  │  Client  │    │ Queue   │    │  (fast-path O(1))  │   │
│  │TwitchIO 3│    │ Buffer  │    │  (tick loop 1s)    │   │
│  └──────────┘    └────┬────┘    └────────┬───────────┘   │
│                       │                  │               │
│                  ┌────▼────┐    ┌────────▼───────────┐   │
│                  │ SQLite  │    │  ModerationEngine  │   │
│                  │ Writer  │    │  (Helix API calls) │   │
│                  └─────────┘    └────────────────────┘   │
│                                                          │
│  WebSocket /ws ──────────────────────────────────────────┼──▶ React UI
│  REST /api/* ────────────────────────────────────────────┼──▶ React UI
└──────────────────────────────────────────────────────────┘
                     │ EventSub WebSocket
┌────────────────────▼─────────────────────────────────────┐
│              Twitch Platform (external)                   │
│  - chat.message events                                   │
│  - raid, hype_train, subscription_gift events            │
└──────────────────────────────────────────────────────────┘
```

### Step-by-step event flow (chat message)

1. **Twitch EventSub** delivers `channel.chat.message` over WebSocket to `TwitchClient`
2. `TwitchClient.event_message()` fires `_message_callbacks`
3. `twitch/manager.py` `on_chat_message()` — extracts fields, broadcasts `chat_message` WS event to React, calls `_message_handler` (registered in startup.py)
4. `startup._enqueue_twitch_message()` — calls `pipeline/builder.py` to build `ChatMessage` (normalizes text, computes content_hash, extracts features), then calls `message_queue.enqueue(msg)` (non-blocking)
5. `pipeline/queue.py` consumer loop — dequeues message, adds to `ChatBuffer` (all windows), dispatches to registered processors:
   - `storage/writer.py` — buffers for batch SQLite insert (≤100ms / 100 messages)
   - `detection/engine.py` `process_message()` — fast-path O(1) detectors
6. **Fast-path detectors** (in `process_message()`):
   - `IncrementalDuplicateTracker.add()` — per-user duplicate ratio
   - `TemporalSyncDetector.add()` — coordinated burst score (cluster membership only)
   - `MinHashDuplicateDetector.add()` — LSH cluster membership
   - `UserRateDetector.add()` — per-user message rate
   - `BurstAnomalyDetector.add()` — channel-level velocity spike (not attributed to users)
   - `score_single_username()` — entropy heuristic, cached in `_user_signals`
   - `UsernameFamilyDetector.add()` — similar username patterns
   - `account_age_cache.get()` — TTL-cached account age lookup
   - `KnownBotRegistry.is_known_bot()` — O(1) frozenset membership test
   - `SpamPatternMatcher.match()` — Aho-Corasick O(n) scan
   - `IATScorer.add()` — inter-arrival time coefficient of variation
7. **Detection tick** (every 1s, `tick.py`):
   - Single-pass deque scan for 30s + 60s stats
   - Fire-and-forget `_run_clustering()` to thread pool (every 10s) → SemanticClusterer (MiniLM ONNX + DBSCAN) → CooccurrenceDetector (igraph Infomap)
   - `_update_isolation_forest()` every 5 ticks → RiverAnomalyScorer (HalfSpaceTrees)
   - Reset channel-level scores; decay per-user temporal_sync/minhash_cluster (−2.0/tick)
   - Compute HealthScore (weighted 7-metric combination via `health_score.py`)
   - Run `AnomalyDetector` (2-cycle state machine) and `HealthDriftDetector` (EWMA + ADWIN)
   - Broadcast `health_update` WebSocket event to all React clients
   - Every 2 ticks: `_evaluate_user_alerts()` (AlertingMixin)
8. **Per-user alert evaluation** (`alerting.py`):
   - Skip if: under 60s cooldown, protected (mod/VIP/60d+ sub/whitelist/known-good-bot)
   - Require ≥2 signals > 0.2 normalized
   - Compute threat score via `aggregator.compute_user_threat_score()`
   - If score > 55: write to `flagged_users` DB, broadcast `threat_alert` WS event, call `ModerationEngine.on_threat()`
9. **Moderation dispatch** (`moderation/engine.py`):
   - Lookup escalation table (score → action type + duration)
   - Ban gate: require 2 independent signals both > 90 confidence
   - Check dry_run + enabled flags
   - Rate limit: 80 actions/60s token bucket
   - Write `status='pending'` to `moderation_actions` DB
   - Call Twitch Helix API via `RefreshingHTTPClient`
   - Update `status='completed'/'failed'`
   - Broadcast `moderation_action` WS event

---

## 6. Concurrency Model

The backend is **fully async** on a single asyncio event loop.

| Component | Concurrency mechanism |
|---|---|
| All FastAPI routes | `async def` coroutines on event loop |
| WebSocket handlers | `async def` on event loop |
| Message queue consumer | `asyncio.Queue` consumer, single task |
| Detection tick loop | `asyncio.sleep(1)` task |
| Heartbeat loop | `asyncio.sleep(5)` task |
| Retention/WAL loops | `asyncio.sleep(86400 / 300)` tasks |
| Stdin listener | `asyncio.StreamReader` task |
| **Semantic clustering** | `asyncio.get_event_loop().run_in_executor(None, ...)` — offloaded to default `ThreadPoolExecutor` to avoid blocking tick loop |
| SQLite writes | `aiosqlite` async driver (non-blocking) |
| Twitch Helix API calls | `httpx.AsyncClient` |
| TwitchIO EventSub WS | Internal TwitchIO event loop integration |
| River HalfSpaceTrees training | Synchronous (called from async context via rate-limiting to every 5 ticks) |
| Known-bot registry refresh | `asyncio` task, 24h interval |
| `TTLCache` (account age) | `cachetools.TTLCache` — not thread-safe; accessed from event loop only |

**Thread usage**: Only the thread pool executor for semantic clustering (ONNX inference + DBSCAN). Everything else is single-threaded on the event loop.

**No multiprocessing** — Python backend is a single process, below-normal Windows priority.

---

## 7. Critical Code Paths

### Per-message fast path (called for every chat message)

| File | Function | Notes |
|---|---|---|
| `pipeline/queue.py` | consumer loop | Dequeues, dispatches to processors |
| `pipeline/buffer.py` | `add()`, `prune()` | O(1) append to all window deques |
| `pipeline/normalizer.py` | `normalize_message()`, `content_hash()`, `extract_features()` | Text processing, hash computation |
| `detection/engine.py` | `process_message()` | Orchestrates all fast-path detectors |
| `detection/fast/duplicate.py` | `IncrementalDuplicateTracker.add()` | 30s sliding window duplicate check |
| `detection/fast/temporal.py` | `TemporalSyncDetector.add()` | Coordinated burst detection |
| `detection/fast/minhash.py` | `MinHashDuplicateDetector.add()` | MinHash + LSH |
| `detection/fast/pattern_match.py` | `SpamPatternMatcher.match()` | Aho-Corasick O(n) scan |
| `detection/fast/timing.py` | `IATScorer.add()` | Inter-arrival time CV |
| `detection/known_bots.py` | `is_known_bot()` | O(1) frozenset lookup |
| `storage/writer.py` | `write()` | Buffer for batch SQLite insert |

### 1-second tick loop (latency budget: < 50ms)

| File | Function | Notes |
|---|---|---|
| `detection/tick.py` | `tick()` | Master coordination |
| `detection/tick.py` | `_run_clustering()` | Offloaded to thread pool every 10s |
| `detection/batch/clustering.py` | `SemanticClusterer.analyze()` | ONNX inference + DBSCAN (thread pool) |
| `detection/batch/cooccurrence.py` | `CooccurrenceDetector.detect()` | igraph Infomap (thread pool) |
| `detection/tick.py` | `_update_isolation_forest()` | River HalfSpaceTrees, every 5 ticks |
| `detection/scoring/health_score.py` | `HealthScoreEngine.compute()` | Weighted 7-metric combination |
| `detection/scoring/anomaly.py` | `AnomalyDetector.update()` | 2-cycle state machine |
| `detection/scoring/drift.py` | `HealthDriftDetector.update()` | EWMA + ADWIN |
| `detection/tick.py` | `_build_health_payload()` | Serialize WS payload |
| `api/websocket.py` | `ConnectionManager.broadcast()` | Broadcast to all WS clients |

### Per-user alert evaluation (every 2 ticks)

| File | Function | Notes |
|---|---|---|
| `detection/alerting.py` | `_evaluate_user_alerts()` | Iterates all active users in 30s window |
| `detection/aggregator.py` | `compute_user_threat_score()` | Weighted signal normalization |
| `detection/protection.py` | `is_protected()` | Badge + whitelist check |
| `storage/reputation.py` | `get_reputation()` | Cross-session score modifier |
| `storage/db.py` | INSERT into `flagged_users` | aiosqlite write |
| `moderation/engine.py` | `on_threat()` | Escalation + safety gates + Helix API |

### WebSocket broadcast

| File | Function | Notes |
|---|---|---|
| `api/websocket.py` | `ConnectionManager.broadcast()` | JSON serialize + send to all connected React clients |
| `twitch/manager.py` | `on_chat_message()` | Also broadcasts `chat_message` event per message |

---

## 8. External Dependencies

### Python libraries (performance-sensitive highlighted)

| Library | Use | Performance-sensitive? |
|---|---|---|
| **twitchio 3.2.0** | EventSub WebSocket client | Yes — message ingestion |
| **fastapi 0.115.5** | REST + WebSocket framework | Yes — all API + WS |
| **uvicorn 0.32.1** | ASGI server | Yes — event loop host |
| **aiosqlite 0.20.0** | Async SQLite writes | Yes — batch message storage |
| **datasketch 1.6.5** | MinHash + LSH per message | **Yes — per-message O(n)** |
| **sentence-transformers 3.3.1** | MiniLM embeddings (fallback) | Yes — batch every 10s |
| **onnxruntime 1.20.1** | ONNX inference (primary embeddings) | **Yes — batch every 10s** |
| **fastembed 0.4.2** | BAAI/bge-small-en-v1.5 (primary) | **Yes — batch every 10s** |
| **scikit-learn 1.6.0** | DBSCAN clustering | Yes — batch every 10s |
| **igraph 0.11.8** | Infomap community detection | Yes — batch every 10s |
| **river 0.21.2** | HalfSpaceTrees online anomaly | Yes — every 5 ticks |
| **pyahocorasick 2.1.1** | Aho-Corasick spam patterns | **Yes — per-message O(n)** |
| **networkx 3.4.2** | Graph data structures | Moderate — batch path |
| **httpx 0.28.1** | Twitch Helix API calls | Moderate — moderation actions |
| **keyring 25.5.0** | Windows Credential Manager | Low — startup only |
| **cachetools** | TTLCache (account age, 50K entries) | Moderate — per-message lookup |
| **psutil 6.1.1** | RSS memory check in heartbeat | Low — every 5s |
| **duckdb 1.1.3** | Analytics queries | Low — on-demand |
| **structlog** | Structured logging | Low |

### External services

| Service | Purpose | Dependency |
|---|---|---|
| **Twitch EventSub WebSocket** | Live chat message delivery | Required (graceful degradation if disconnected) |
| **Twitch Helix API** | Moderation actions (ban/timeout/delete) | Optional (dry-run mode default) |
| **GitHub (raw.githubusercontent.com)** | Known-bot username lists (CommanderRoot + TwitchInsights) | Optional; 24h refresh; graceful fallback if unavailable |

---

## 9. Logging System

**Framework**: `structlog` + Python standard `logging`

**Configuration**: `backend/core/logging.py`
- `SensitiveFilter` registered on root logger — redacts OAuth tokens from all log records
- Structured JSON output (for machine parsing), human-readable in dev mode

**Log levels**:
- `DEBUG` — message processing details, detector scores (dev mode only)
- `INFO` — alerts issued, moderation actions, connection events, startup
- `WARNING` — tick loop exceeded 40ms, drift detected, queue drops
- `ERROR` — API failures, DB errors, token refresh failures, clustering timeouts

**Where logs are written**:
- stdout (always — for Electron parent process)
- `{APPDATA}\TwitchIDS\logs\app.log` (rolling file, path from `core/config.py`)

**High-frequency areas**:
- `detection/tick.py` — logs tick duration if > 40ms (triggered at most once per tick)
- `storage/writer.py` — logs DB errors on flush failure (not per message)
- `tasks.py` `pipeline_metrics_loop()` — logs msg/min, queue depth every 30s
- `twitch/manager.py` — logs each moderation action broadcast (INFO)

**Sample log statement** (from `tasks.py`):
```python
logger.info(
    "pipeline_metrics",
    msg_per_min=telemetry.msg_per_min,
    queue_depth=startup.message_queue.depth,
    active_users=len(active_users),
    dropped=startup.message_queue.dropped,
)
```

---

## 10. Database and Storage

**Database**: SQLite (WAL journal mode, NORMAL synchronous, temp store in memory)

**Driver**: `aiosqlite 0.20.0` (async, non-blocking on event loop)

**Schema location**: `backend/storage/db.py` — `init_db(conn)` function, all `CREATE TABLE IF NOT EXISTS`

**Tables**:

| Table | Purpose | Write frequency |
|---|---|---|
| `messages` | All chat messages | High — batch 100 msgs/100ms |
| `flagged_users` | Threat alerts | Low — on detection trigger |
| `moderation_actions` | All moderation events | Low — on action dispatch |
| `health_history` | Health score snapshots | Every 5 ticks (5s) |
| `user_reputation` | Cross-session user scoring | Low — on flag/action |
| `whitelist` | Manual whitelist | Very low — user-managed |

**Indexes**:
- `messages`: `received_at`, `user_id`, `content_hash`, `channel`
- `flagged_users`: `user_id`, `flagged_at`
- `moderation_actions`: `status`, `user_id`, `created_at`
- `health_history`: `recorded_at`
- `user_reputation`: `reputation`

**Write patterns**:
- `storage/writer.py` — accumulates up to 100 messages, flushes in a single transaction every 100ms (or when batch full)
- `tasks.py` `wal_checkpoint_loop()` — passive checkpoint every 5 minutes

**Analytics**: DuckDB (`analytics.py`) for aggregation queries on the `messages` table — not in the hot path, only on API request.

**Caching layers**:
- `cachetools.TTLCache(maxsize=50_000, ttl=7200)` — Twitch account age (2h TTL, 50K users)
- `detection/known_bots.py` — `frozenset` of ~12M bot usernames (in-memory, 24h refresh)
- Per-user feature deques — `deque(maxlen=200)` per user in `engine.py`
- Telemetry — `deque(maxlen=6000)` for message timestamps, `deque(maxlen=120)` for tick durations

---

## 11. Potential Performance Risk Areas

Listed without fixes:

1. **`datasketch` MinHash per message** (`detection/fast/minhash.py`): MinHash computation involves multiple hash permutations (typically 128). At 5,000 msg/min (~83 msg/s), this runs continuously in the event loop.

2. **`_evaluate_user_alerts()` iterates all active users** (`detection/alerting.py`): Runs every 2 ticks. If 500+ users are active in a 30s window, this is a large synchronous Python loop executed in the event loop — no yielding between iterations.

3. **`KnownBotRegistry._refresh()`** (`detection/known_bots.py`): Fetches CommanderRoot list (~11.8M usernames) from GitHub every 24h, then builds a new `frozenset`. Memory allocation for 11.8M strings is non-trivial and briefly doubles memory.

4. **River `HalfSpaceTrees.learn_one()`** (`detection/batch/river_anomaly.py`): Called once per unique user per invocation (every 5 ticks). At high chat volume with many unique users, this synchronous Python loop runs on the event loop (not offloaded to thread pool).

5. **`SemanticClusterer` with large message sets** (`detection/batch/clustering.py`): DBSCAN is capped at 2,000 messages. ONNX inference batched. However, the 8-second `asyncio.wait_for` timeout means the thread-pool task can occupy a worker thread for up to 8s — potentially starving other thread-pool users if another clustering run is queued while one is in progress.

6. **`ConnectionManager.broadcast()` serializes once, sends to N clients** (`api/websocket.py`): WebSocket broadcast to all connected clients is done sequentially. If a client has a slow connection, it could cause the tick loop to block on socket sends (depending on implementation).

7. **`chat_message` WS broadcast per message** (`twitch/manager.py`): Every single incoming chat message triggers a WebSocket broadcast to the React frontend. At 5,000 msg/min (83/s), this is 83 sequential JSON serializations + sends per second.

8. **Account age cache miss** (`pipeline/account_cache.py`): On cache miss, a Twitch Helix API call is made. If many new users join simultaneously (e.g., raid), this could cause a burst of outbound HTTP requests.

9. **`_user_signals` dict growth** (`detection/engine.py`): Per-user signal dicts accumulate for all users seen in a session. There is per-user deque capping (`maxlen=200`), but the top-level `_user_signals` dict itself is not TTL-limited — relies on external pruning.

10. **`content_hash` computation** (`pipeline/normalizer.py`): Called for every message. Depends on implementation (MD5/SHA vs. simhash) — if using cryptographic hash, this is non-negligible CPU at high volume.

11. **DuckDB analytics queries** (`storage/analytics.py`): DuckDB scans SQLite data on-demand. At 7 days of messages at 5,000 msg/min, the `messages` table could contain ~50M rows. Ad-hoc aggregations without materialized views could be slow.

12. **`TemporalSyncDetector`** (`detection/fast/temporal.py`): Coordinated burst detection likely requires comparing current message timestamps to a sliding window of recent hashes across all users — could be O(active users × window) per message in naive implementation.

13. **Reputation store reads inside alert loop** (`detection/alerting.py`): `reputation_store.get_reputation()` is called inside the per-user alert loop. If the reputation store hits SQLite synchronously (not cached in-memory), this adds one DB read per active user per 2-tick evaluation.

---

## 12. Resource Usage Indicators

**Memory-heavy structures**:

| Structure | Location | Max size |
|---|---|---|
| Known-bot frozenset | `detection/known_bots.py` | ~12M strings (~600MB peak during rebuild) |
| TTLCache (account age) | `pipeline/account_cache.py` | 50,000 entries × 2h TTL |
| Per-user signal dicts | `detection/engine.py` `_user_signals` | Unbounded (no TTL on dict keys) |
| Per-user feature deques | `detection/engine.py` | `deque(maxlen=200)` per user |
| Ring buffers (5 windows) | `pipeline/buffer.py` | 5 × up to 300s of messages |
| Telemetry deques | `core/telemetry.py` | `deque(maxlen=6000)` + `deque(maxlen=120)` |
| ONNX model in memory | `detection/batch/clustering.py` | ~22MB (MiniLM) |

**Long-running loops**:

| Loop | File | Frequency |
|---|---|---|
| Message consumer | `pipeline/queue.py` | Continuous (event-driven) |
| Detection tick | `tasks.py` + `detection/tick.py` | Every 1s |
| Heartbeat | `tasks.py` | Every 5s |
| Pipeline metrics | `tasks.py` | Every 30s |
| WAL checkpoint | `tasks.py` | Every 5 min |
| Retention purge | `tasks.py` | Daily |
| Known-bot refresh | `detection/known_bots.py` | Every 24h |

**High-frequency events**:

| Event | Rate at 5K msg/min |
|---|---|
| `process_message()` | ~83/s |
| SQLite batch write | ~10 batches/s (100 msgs per batch) |
| `chat_message` WS broadcast | ~83/s |
| `health_update` WS broadcast | 1/s |
| Per-user alert evaluation | Every 2s |

**Background tasks**: 6 long-running asyncio tasks + thread pool for clustering.

---

## 13. Configuration and Environment

**System**: Pydantic `BaseSettings` in `backend/core/config.py`

**Source**: `.env` file with `TWITCHIDS_` prefix (loaded by `python-dotenv`)

**Settings class** (key fields):

```python
class Settings(BaseSettings):
    # Server
    host: str = "127.0.0.1"
    port: int = 7842
    dev_mode: bool = False
    simulator_active: bool = False

    # Safety (critical defaults)
    dry_run: bool = True
    auto_timeout_enabled: bool = False
    auto_ban_enabled: bool = False

    # Thresholds
    timeout_threshold: float = 75.0
    ban_threshold: float = 95.0
    alert_threshold: float = 60.0

    # Retention
    message_retention_days: int = 7
    health_history_retention_days: int = 30

    # Paths (computed from APPDATA)
    app_data_dir: Path = ...
    db_path: Path = ...
    log_path: Path = ...
    models_dir: Path = ...

    model_config = SettingsConfigDict(
        env_prefix="TWITCHIDS_",
        env_file=".env"
    )
```

**Runtime `.env`** (dev environment):
```
TWITCHIDS_SIMULATOR_ACTIVE=true
TWITCHIDS_DEV_MODE=true
IPC_SECRET=<generated-at-startup>
```

**Frontend env**: `startup.py` writes `VITE_API_PORT`, `VITE_IPC_SECRET`, etc. to `frontend/.env.local` on startup.

---

## 14. Known Monitoring or Profiling Tools

### Built-in telemetry (`backend/core/telemetry.py`)

- `TelemetrySingleton.snapshot()` returns:
  - `msg_per_min` — rolling count from `deque(maxlen=6000)` timestamps
  - `tick_p50`, `tick_p95`, `tick_p99` — tick duration percentiles from `deque(maxlen=120)`
  - `queue_depth` — current message queue depth
  - `ws_clients` — active WebSocket connections
  - `memory_rss_mb` — process RSS via psutil (if available)
- Published in `health_update` WebSocket payload under `perf` key every 1s → visible in React `PerfPanel` (right sidebar, collapsed by default)

### In-process performance logging

- `detection/tick.py` logs a `WARNING` if tick duration > 40ms: `"tick_slow", duration_ms=X`
- `tasks.py` `pipeline_metrics_loop()` logs msg/min, queue depth, dropped count every 30s
- `tasks.py` `heartbeat_loop()` includes `memory_rss_mb` in stdout JSON every 5s

### Frontend bundle analysis

- `rollup-plugin-visualizer` in devDependencies — generates bundle treemap for chunk analysis

### No external profiling tools

- No APM integration (Datadog, Sentry, etc.)
- No Python cProfiler or py-spy hooks
- No distributed tracing

---

## 15. How to Run the Application

### Prerequisites

- Windows 10/11
- Python 3.12 (install from python.org; use `py -3.12`)
- Node.js 20+ + npm
- A Twitch developer application (client ID + client secret from dev.twitch.tv)

### Backend setup

```bash
# 1. Create virtualenv with Python 3.12 (critical — 3.13+ not supported)
cd "s:\Twitch Chat Bot Detection\backend"
py -3.12 -m venv .venv

# 2. Install dependencies
.venv/Scripts/pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env — set TWITCHIDS_CLIENT_ID, TWITCHIDS_DEV_MODE=true

# 4. Run backend (dev mode)
.venv/Scripts/python.exe main.py
```

### Frontend setup

```bash
cd "s:\Twitch Chat Bot Detection\frontend"
npm install
npm run dev   # Vite dev server; OR use Electron:
npm run electron:dev
```

### Running with simulator

```bash
# In backend/.env:
#   TWITCHIDS_SIMULATOR_ACTIVE=true
#   TWITCHIDS_DEV_MODE=true

# Start backend, capture IPC secret from stdout JSON:
# {"type":"ready","ipc_secret":"..."}

# Run evaluation from project root:
backend/.venv/Scripts/python.exe simulator/evaluate.py \
  --scenario simulator/scenarios/bot_raid.yaml \
  --port 7842 \
  --secret <IPC_SECRET_FROM_STDOUT>
```

### Environment variables for first run

```
TWITCHIDS_CLIENT_ID=<your-twitch-app-client-id>
TWITCHIDS_DEV_MODE=true
TWITCHIDS_SIMULATOR_ACTIVE=false
TWITCHIDS_DRY_RUN=true      # default — no real moderation actions
```

**Note**: OAuth tokens (access + refresh) are stored automatically in Windows Credential Manager after first sign-in via the app's settings drawer. They are never written to `.env`.
