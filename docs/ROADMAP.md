# Development Roadmap

Eight phases from working Twitch client to distributable Windows installer.

Each task is sized for a single focused coding session. Complexity ratings: Low (< 1 hour), Medium (1–3 hours), High (3–6 hours).

---

## ✅ Phase 1 — Core Twitch Chat Client *(COMPLETE)*

**Goal:** App opens, connects to a Twitch channel, displays live chat in a window. No detection yet.

**Milestone:** Streamer can see their chat in the application in real time.

### Backend Tasks

| # | Task | Description | Output | Complexity |
|---|---|---|---|---|
| 1.1 | Dev environment | Python 3.12 venv, install core deps, verify imports | Working venv | Low |
| 1.2 | FastAPI skeleton | `main.py`, `/health` endpoint, uvicorn startup, stdout JSON protocol (`emit_status`) | Running server on dynamic port | Low |
| 1.3 | Process priority | Set Windows Below Normal priority on startup | Priority set | Low |
| 1.4 | Logging setup | `configure_logging()` with `SensitiveFilter` on root logger | Structured logs, no token leaks | Low |
| 1.5 | TwitchIO client | Connect to channel via EventSub WebSocket, print messages to console | Console chat stream | Low |
| 1.6 | OAuth PKCE flow | Open browser, local callback server, exchange code for tokens | Token returned | High |
| 1.7 | Token storage | `SecureTokenStore` using keyring/Windows Credential Manager | Tokens in WCM | Medium |
| 1.8 | Token refresh | Auto-refresh before expiry via TwitchIO | Silent re-auth | Low |
| 1.9 | WebSocket endpoint | FastAPI `/ws` endpoint, broadcast messages to connected clients | Messages pushed to WS | Medium |
| 1.10 | IPC auth middleware | `IPCAuthMiddleware` on all non-health routes | Requests without secret rejected | Low |

### Frontend Tasks

| # | Task | Description | Output | Complexity |
|---|---|---|---|---|
| 1.11 | Electron project | Init with Vite + React + TypeScript, configure `webPreferences` | App window opens | Low |
| 1.12 | Python process manager | `PythonManager` class: spawn, stdout parsing, ready handshake, crash restart | Python starts with Electron | High |
| 1.13 | Port forwarding | Main process receives port+secret, passes to renderer via contextBridge | Renderer knows where to connect | Medium |
| 1.14 | Context bridge | `preload.js` with minimal exposed API | Secure IPC | Low |
| 1.15 | WebSocket hook | `useWebSocket.ts`: connect, reconnect, event routing | Live WS connection in React | Medium |
| 1.16 | Basic chat feed | Display incoming messages in a scrolling list | Chat visible in UI | Low |
| 1.17 | Connection status | Show connected/disconnected/reconnecting state | Status indicator | Low |

### Dev Environment Task

| # | Task | Description |
|---|---|---|
| 1.18 | Dev workflow script | `TWITCHIDS_DEV=true` bypasses Electron lifecycle; run Python + Electron separately |

**Done when:** App starts, authenticates, joins a channel, and displays chat in the window.

---

## ✅ Phase 2 — Message Processing Pipeline *(COMPLETE)*

**Goal:** All messages flow through a proper pipeline and are stored. Foundation for all detection.

**Milestone:** Messages stored in SQLite, metrics visible in console logs.

| # | Task | Description | Output | Complexity |
|---|---|---|---|---|
| 2.1 | ChatMessage dataclass | Define `ChatMessage` with all required fields | Data model | Low |
| 2.2 | Message normalizer | `normalize_message()`: NFKC, homoglyph map, strip invisible, lowercase, truncate | Clean normalized text | Medium |
| 2.3 | Async message queue | `asyncio.Queue(maxsize=10_000)`, enqueue with drop-oldest overflow | Bounded queue | Low |
| 2.4 | Feature extractor | Compute `content_hash`, emoji count, URL presence, mention count | Feature dict | Low |
| 2.5 | ChatBuffer | Multi-resolution ring buffers (5s, 10s, 30s, 60s, 300s), O(1) add/prune | Working ring buffers | Medium |
| 2.6 | SQLite initialization | Schema creation, WAL mode pragma, migration runner | DB file created | Medium |
| 2.7 | Async message writer | `aiosqlite` batch writer (flush every 100ms or 100 msgs) | Messages in DB | Medium |
| 2.8 | Account age cache | `NewAccountMetric` skeleton, Helix bulk user lookup, TTL cache | Age lookups working | Medium |
| 2.9 | REST history endpoint | `GET /api/history/messages` with pagination | Paginated message history | Low |
| 2.10 | Pipeline metrics log | Log msg/min, queue depth, write latency every 30s | Console metrics | Low |

**Done when:** Every message flows through normalizer → queue → feature extractor → buffer → SQLite. History endpoint returns data.

---

## ✅ Phase 3 — Basic Spam Detection *(COMPLETE)*

**Goal:** Detect obvious spam floods. No ML required. First working detection.

**Milestone:** App flags spam floods in the dashboard with confidence scores.

| # | Task | Description | Output | Complexity |
|---|---|---|---|---|
| 3.1 | IncrementalDuplicateTracker | O(1) duplicate ratio with Counter + deque | Duplicate ratio metric | Medium |
| 3.2 | TemporalSyncDetector | Multi-window (1s/3s/5s/15s/30s) coordination detection | Sync burst alerts | Medium |
| 3.3 | MinHashDuplicateDetector | MinHash LSH with bounded eviction window | Near-duplicate cluster detection | High |
| 3.4 | UserRateDetector | Per-user msg/min + regularity (CV) scoring | Rate anomaly alerts | Medium |
| 3.5 | UsernameEntropyScorer | Shannon entropy + digit ratio + trailing digit pattern | Entropy scores | Low |
| 3.6 | BurstAnomalyDetector | Z-score on 5-second interval counts vs 5-min baseline | Statistical spike detection | Medium |
| 3.7 | MetricCalculator | Aggregate all fast-path signals into metric dict | Combined metrics | Medium |
| 3.8 | Confidence aggregator | `compute_user_threat_score()` weighted combination | Threat scores | Low |
| 3.9 | Alert model + DB write | Write flagged users and alerts to `flagged_users` table | Persisted alerts | Low |
| 3.10 | WebSocket alert push | Push `threat_alert` events to renderer | Live alerts | Low |
| 3.11 | Threat panel UI | Show active threats with scores, usernames, signals | Visual threat list | Medium |
| 3.12 | Dashboard metrics bar | Messages/min, active users, duplicate ratio | Metrics display | Low |

**Done when:** App detects an identical-message spam flood and shows it in the UI within 5 seconds.

---

## ✅ Phase 4 — Bot Farm Detection Algorithms *(COMPLETE)*

**Goal:** Detect sophisticated coordinated bots that vary their messages. First ML components.

**Milestone:** System detects paraphrasing bot clusters. False positive rate measured with simulator.

| # | Task | Description | Output | Complexity |
|---|---|---|---|---|
| 4.1 | Model setup script | Download MiniLM, export to ONNX, save to `backend/models/` | ONNX model file | Medium |
| 4.2 | SemanticClusterer | MiniLM ONNX encoding + DBSCAN, runs in thread pool | Semantic cluster results | High |
| 4.3 | Adaptive sampling | Sampling strategy for high-volume channels (> 200 msgs/batch) | Bounded embedding cost | Medium |
| 4.4 | Batch accumulator | Trigger SemanticClusterer every 10 seconds | Timed batch dispatch | Low |
| 4.5 | HealthScoreEngine | Weighted combination of all metrics + adaptive baseline | Health snapshots | High |
| 4.6 | AdaptiveBaseline | Rolling mean/stdev per channel, calibrate risk scores | Channel-calibrated scores | Medium |
| 4.7 | AnomalyDetector | Level state machine with 2-cycle confirmation | Level transitions, response triggers | Medium |
| 4.8 | DetectionSuppressor | Suppress on raid/hype train/gift sub EventSub events | No false alarms during events | Medium |
| 4.9 | IsolationForestScorer | Account feature vector + Isolation Forest outlier scoring | Account anomaly scores | High |
| 4.10 | UsernameFamilyDetector | Structural pattern matching across session accounts | Pattern family alerts | Medium |
| 4.11 | ProtectedAccountChecker | Whitelist mods, VIPs, long-term subscribers | Protected set lookup | Medium |
| 4.12 | Health score WebSocket push | Emit `health_update` JSON every 1 second | Live score in UI | Low |
| 4.13 | HealthScoreMeter UI | Large central health score display with trend | Score gauge component | Medium |
| 4.14 | Signal breakdown UI | Show per-signal contribution to risk score | Signal panel | Medium |
| 4.15 | Simulator Phase 1 | Build simulator, normal chat + spam flood scenarios, WebSocket output | Test traffic generator | High |

**Done when:** System detects a paraphrasing bot cluster (semantic clustering). Simulator confirms < 5% false positive rate on normal chat.

---

## ✅ Phase 5 — Moderation Action Engine *(COMPLETE)*

**Goal:** System can take automated moderation actions with full safety gating.

**Milestone:** Automated timeouts fire correctly. Dry-run mode works. Undo works.

| # | Task | Description | Output | Complexity |
|---|---|---|---|---|
| 5.1 | Helix API client | `RefreshingHTTPClient` with 401 auto-refresh for direct API calls | Authenticated Helix calls | Medium |
| 5.2 | Moderation action types | Define all action types: ban, timeout, delete, slow_mode, followers_only | Action dataclasses | Low |
| 5.3 | Transactional executor | Write `status='pending'` before API call, update after | Crash-safe action logging | Medium |
| 5.4 | Token bucket rate limiter | 80 actions/minute, async implementation | Rate-limited dispatcher | Medium |
| 5.5 | Action queue | `asyncio.Queue(maxsize=1000)` feeding rate limiter | Ordered action dispatch | Low |
| 5.6 | Dual-signal ban gate | Require 2 independent signals both > 90 for ban | Ban safety gate | Medium |
| 5.7 | Dry-run mode | Default ON; log all planned actions without executing | Safe default behavior | Low |
| 5.8 | Escalation logic | Map confidence score → action type per threshold table | Correct action selection | Medium |
| 5.9 | Startup action recovery | Scan pending actions on startup, resolve via Helix API | Crash recovery | Medium |
| 5.10 | Manual ban/timeout REST | `POST /api/moderation/ban` and `/timeout` endpoints | Manual moderation API | Low |
| 5.11 | Undo endpoint | `DELETE /api/moderation/ban/{action_id}` — reverse action | Reversible actions | Medium |
| 5.12 | Action log UI | Show moderation history with automated/manual indicator, undo button | Action log panel | Medium |
| 5.13 | Cluster timeout action | Timeout all users in a detected cluster with one trigger | Bulk cluster action | Medium |

**Done when:** Auto-timeout fires on a simulated spam flood (with dry-run off). Undo reverses it. All actions logged with triggering signals.

---

## ✅ Phase 6 — Dashboard UI *(COMPLETE)*

**Goal:** Production-quality real-time dashboard.

**Milestone:** Dashboard is usable as a primary tool during a live streaming session.

| # | Task | Description | Output | Complexity |
|---|---|---|---|---|
| 6.1 | Layout skeleton | Main panel grid: left column, center chat, right panel | Responsive layout | Low |
| 6.2 | Chat feed | Virtualized list (react-window), color-coded by threat score | Performant chat display | High |
| 6.3 | Threat color coding | Message highlight: gray (clean) → yellow → orange → red | Visual threat grading | Low |
| 6.4 | Health timeline chart | 60-minute area chart of health score (Recharts) | Historical score graph | Medium |
| 6.5 | Metrics bar | Real-time msg/min, active users, duplicate ratio, sync score | Live metrics strip | Low |
| 6.6 | Threat panel | Active cluster list with member count, sample message, action buttons | Cluster display | Medium |
| 6.7 | User detail panel | Click user → show message history, threat score breakdown, action controls | User inspector | High |
| 6.8 | Bot network graph | react-force-graph visualization of co-occurrence graph | Network graph | High |
| 6.9 | Settings drawer | Threshold config, whitelist editor, channel list, dry-run toggle | Settings UI | High |
| 6.10 | Auth flow UI | First-run wizard: connect Twitch account, select channel | Onboarding screens | Medium |
| 6.11 | Windows notifications | Native toast via `electron.Notification` for critical level | OS notifications | Low |
| 6.12 | System tray | Minimize to tray, health score in tray icon tooltip, quick actions | Tray integration | Medium |
| 6.13 | Keyboard shortcuts | Global shortcuts: Ctrl+M (mute detection), Ctrl+D (dashboard focus) | Keyboard control | Low |

**Done when:** Dashboard is visually polished and functional during a real streaming session.

---

## ✅ Phase 7 — Data Storage and Analytics *(COMPLETE)*

**Goal:** Historical analysis, session reports, data export.

**Milestone:** Streamer can review past sessions and export flagged user lists.

| # | Task | Description | Output | Complexity |
|---|---|---|---|---|
| 7.1 | DuckDB analytics layer | Analytics queries against SQLite data for aggregations | Analytics DB setup | Medium |
| 7.2 | Stats API endpoints | `/api/stats/summary`, `/api/stats/timeline`, `/api/stats/top_threats` | Analytics REST API | Medium |
| 7.3 | Session summary | Per-stream report: threats detected, actions taken, health trend | Session report data | Medium |
| 7.4 | Data retention job | Background asyncio task: purge messages > 7 days, health_history > 30 days | Auto-purge | Low |
| 7.5 | User reputation system | `UserReputation` model, accumulate across sessions, apply modifier | Persistent reputation | High |
| 7.6 | CSV export | Export flagged_users and moderation_actions to CSV via REST | Data export | Low |
| 7.7 | Stats page UI | Session summary panel with charts, exportable data | Analytics dashboard tab | High |

---

## Phase 8 — Windows Packaging and Distribution

**Goal:** Downloadable installer that works on a clean Windows machine.

**Milestone:** Fresh Windows 11 install → download installer → run → functional app.

| # | Task | Description | Output | Complexity |
|---|---|---|---|---|
| 8.1 | PyInstaller spec | Fine-tuned `.spec` file with all hidden imports and data files | Working EXE | High |
| 8.2 | Model bundling | Include `backend/models/minilm.onnx` in PyInstaller bundle | Models included | Low |
| 8.3 | Bundle test | Test on clean Windows 11 VM, fix any import errors | Validated bundle | Medium |
| 8.4 | Defender submission | Submit bundle to Microsoft for analysis, whitelist if flagged | Clean AV status | Medium |
| 8.5 | electron-builder config | NSIS installer config, embed Python EXE as extraResource | Installer EXE | Medium |
| 8.6 | Build script | `packaging/build.ps1`: full pipeline from source to installer | One-command build | Medium |
| 8.7 | Auto-updater | `electron-updater` pointing to GitHub Releases | Silent auto-update | Medium |
| 8.8 | First-run wizard | Detect fresh install, guide auth → channel selection → threshold config | Onboarding | Medium |
| 8.9 | Installer test | Fresh Windows 10 and Windows 11 VMs, verify all features | Cross-version validation | Low |
| 8.10 | Version management | Semantic versioning, changelog, GitHub Release automation | Release pipeline | Low |

---

## ✅ Phase 9 — Detection Stack Upgrades *(COMPLETE)*

**Goal:** Harden detection with new signals, online learning, explainability, and faster inference.

**Milestone:** Known bot pre-filtering, spam pattern matching, timing regularity, adaptive anomaly detection, drift alerts, and Sigma.js graph rendering all live.

| # | Task | Description | Output | Complexity | Status |
|---|---|---|---|---|---|
| 9-A | KnownBotRegistry | Load CommanderRoot (11.8M) + TwitchInsights (~200k) bot lists at startup into frozenset; refresh every 24h; `known_bot` signal (score 20) | `detection/known_bots.py` | Low | ✅ Done |
| 9-B | SHAP explainability | Signal contribution breakdown on every alert; "why flagged?" collapsible in ThreatPanel with mini bar charts | `alerting.py`, `ThreatPanel.tsx` | Low | ✅ Done |
| 9-C | Spam pattern matcher | Aho-Corasick multi-pattern matching against `data/spam_patterns.json` (crypto, fake giveaway, phishing, follower bot); `pattern_match` signal | `fast/pattern_match.py`, `data/spam_patterns.json` | Low | ✅ Done |
| 9-D | IAT CV signal | Inter-arrival time coefficient of variation — near-zero CV = machine-regular bot timing; `timing_regularity` signal | `fast/timing.py` | Low | ✅ Done |
| 9-E | River HalfSpaceTrees | Online anomaly detection replacing batch IsolationForest; adapts to concept drift; ~0.05ms per sample; graceful fallback to IsolationForest if river not installed | `batch/river_anomaly.py` | Medium | ✅ Done |
| 9-F | EWMA + ADWIN drift | EWMA control chart + River ADWIN on message rate; detects slow-ramp campaigns that stay under per-tick thresholds; `drift` key in health payload | `scoring/drift.py` | Medium | ✅ Done |
| 9-G | fastembed migration | Prefer fastembed (BAAI/bge-small-en-v1.5, pre-quantized ONNX) over manual sentence-transformers ONNX export; fallback preserved | `batch/clustering.py` | Low | ✅ Done |
| 9-H | igraph community detection | Post-DBSCAN co-occurrence graph with Infomap community detection; finds bot networks spanning multiple semantic clusters | `batch/cooccurrence.py` | Medium | ✅ Done |
| 9-I | Sigma.js graph | WebGL BotNetworkGraph via sigma + graphology; handles 1000+ nodes; replaces pure SVG renderer | `BotNetworkGraph.tsx` | Medium | ✅ Done |

**New dependencies added:**
- Python: `pyahocorasick`, `river`, `fastembed`, `igraph`
- Node: `sigma`, `graphology`, `graphology-layout-forceatlas2`

---

## Simulator Development (Parallel to Phase 3–4)

The simulator is developed alongside detection to enable continuous testing.

| # | Task | Description | Output | Complexity | Status |
|---|---|---|---|---|---|
| S1 | Simulator project | `simulator/` directory, config system, scenario loader | Project structure | Low | ✅ Done |
| S2 | NormalUserModel | Poisson-distributed timing, template messages, emoji, replies | Realistic chat simulation | Medium | ✅ Done |
| S3 | SpamBotModel | Identical/near-identical messages, configurable variation rate | Basic bot simulation | Medium | ✅ Done |
| S4 | CoordinatedBotNetwork | Synchronized burst firing with configurable jitter | Bot raid simulation | Medium | ✅ Done |
| S5 | WebSocket output adapter | Stream simulated messages to detection engine ws://localhost/ws/inject | Live injection | Medium | ✅ Done |
| S6 | JSON export adapter | Write labeled JSONL dataset with ground truth labels | Training data export | Low | ✅ Done |
| S7 | Basic scenarios | `normal_chat.yaml`, `spam_flood.yaml`, `bot_raid.yaml` | Runnable scenarios | Low | ✅ Done |
| S8 | CLI interface | `python simulator.py --scenario bot_raid --rate 1000 --duration 300` | Command-line runner | Low | ✅ Done |
| S9 | Markov chain generator | Train on public Twitch chat corpus, generate realistic messages | Organic-looking messages | Medium | ✅ Done |
| S10 | Evasion scenarios | Unicode homoglyph bots, paraphrasing bots, spread-timing bots | Evasion test cases | Medium | ✅ Done (user models) |
| S11 | Evaluation harness | Run scenario → collect alerts → compute FP/FN rate → report | Automated accuracy testing | High | ✅ Done |
| S12 | Stress test scenario | `5000_mpm_mixed.yaml`: 200 normal + 300 bots at 5K msg/min total | Performance baseline | Low | ✅ Done |

---

## Working Prototype Checklist

Minimum criteria for a usable prototype (end of Phase 5):

- [x] App connects to Twitch and displays live chat
- [x] All messages stored in SQLite
- [x] Spam floods detected and shown in UI within 5 seconds
- [x] Semantic clustering catches paraphrasing bots
- [x] Health score updates every 1 second
- [ ] False positive rate < 5% on normal chat (run: `python simulator/evaluate.py --scenario normal_chat.yaml --port PORT --secret SECRET`)
- [x] Automated timeouts fire with correct confidence gating
- [x] Dry-run mode works (actions logged, not executed)
- [x] Undo works for last 50 actions
- [x] Detection suppressed during raid events
- [x] OAuth tokens stored securely (not in files)
- [x] App recovers from Python crash without user action

---

## Suggested Development Order

For a single developer:

**Week 1–2:** Phase 1 (Twitch client + Electron shell)
**Week 3:** Phase 2 (pipeline + storage)
**Week 4–5:** Phase 3 (fast-path detection) + Simulator S1–S8
**Week 6–8:** Phase 4 (ML clustering + health score)
**Week 9:** Phase 5 (moderation engine)
**Week 10–12:** Phase 6 (dashboard UI)
**Week 13:** Phase 7 (analytics)
**Week 14–15:** Phase 8 (packaging)

With AI coding assistance, Phase 1–3 can realistically complete in 2–3 weeks.
