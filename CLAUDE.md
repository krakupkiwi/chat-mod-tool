# Twitch Chat Intrusion Detection System — Project Instructions

## Project Summary

A standalone Windows desktop application that monitors Twitch chat in real time, detects bot farms and coordinated spam campaigns, and automatically triggers moderation actions. Runs fully locally on a streamer's PC with no cloud dependency.

## Architecture

Three-process stack on Windows:

```
Electron (main + renderer)  ←→  Python FastAPI backend  ←→  Twitch EventSub
```

- **Electron** handles the UI (React + Tailwind), process lifecycle management, system tray, native notifications, and auto-updates.
- **Python FastAPI** is the detection engine, Twitch client, moderation dispatcher, and data store. Runs as a child process of Electron, bound to localhost only.
- **Communication**: stdout JSON protocol for lifecycle signals; WebSocket for live event push; REST for config and commands.

Full architecture detail: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

## Repository Layout

```
twitch-ids/
├── CLAUDE.md
├── docs/                    # All design documentation (start here)
│   ├── ARCHITECTURE.md      # System design, IPC, process model
│   ├── TECH_STACK.md        # All libraries, versions, justifications
│   ├── DETECTION_ALGORITHMS.md  # Every detection algorithm with code
│   ├── CHAT_HEALTH_SCORE.md # Health scoring system design
│   ├── ROADMAP.md           # Phase-by-phase task breakdown
│   ├── SIMULATOR.md         # Bot attack simulator design
│   └── SECURITY.md          # Security model, token storage, IPC auth
├── backend/                 # Python detection engine
│   ├── main.py
│   ├── core/
│   ├── twitch/
│   ├── pipeline/
│   ├── detection/
│   ├── moderation/
│   ├── storage/
│   └── api/
├── frontend/                # Electron + React dashboard
│   ├── electron/
│   └── src/
├── simulator/               # Synthetic bot attack simulator
└── packaging/               # Build scripts and installer config
```

## Technology Decisions (Do Not Re-litigate)

- **Twitch integration**: TwitchIO 3.x with EventSub WebSocket. IRC is deprecated. Do not use IRC.
- **UI framework**: Electron + React 18. Tauri was evaluated and rejected (Rust overhead for the team). PySide6 rejected (inferior charting ecosystem).
- **Embeddings**: `all-MiniLM-L6-v2` via sentence-transformers, exported to ONNX for 2-3x CPU speedup. No GPU required.
- **Clustering**: DBSCAN for semantic clusters, Isolation Forest for account anomaly scoring. Both from scikit-learn.
- **Fast deduplication**: MinHash + LSH via `datasketch`. Runs on every message, < 2ms.
- **Database**: SQLite (aiosqlite) for operational data. DuckDB for analytics queries.
- **Token storage**: Windows Credential Manager via `keyring` library. Never flat files.
- **Packaging**: PyInstaller for Python backend. electron-builder with NSIS for the installer.

## Critical Safety Rules

These rules must be enforced in code, not just configuration:

1. **Dry-run mode is ON by default** on fresh installs. All automated actions are logged but not executed until the user explicitly enables live mode in settings.
2. **Bans require two independent detection signals** both scoring > 90 confidence. A single algorithm cannot trigger a permanent ban under any circumstances.
3. **Protected accounts are never actioned**: channel moderators, VIPs, subscribers of 60+ days, accounts on the manual whitelist, and known bot accounts (Nightbot, StreamElements, etc.).
4. **Detection suspends automatically** when EventSub fires raid, hype train, or mass gift sub events. Windows are cleared on reconnect after a gap > 30 seconds.
5. **Moderation actions are transactional**: write `status='pending'` to DB before the API call, update to `completed` or `failed` after. Scan for stuck pending actions on startup.

## Development Conventions

- Python 3.12+. Use `asyncio` throughout. No sync I/O on the event loop.
- All FastAPI routes are async. Database writes use `aiosqlite`.
- Type hints everywhere in Python. Use `pydantic` models for all API request/response schemas.
- The FastAPI server binds to `127.0.0.1` only, never `0.0.0.0`.
- Every API request must include `X-IPC-Secret` header. The secret is generated at Python startup and passed to Electron via stdout.
- Never log OAuth tokens. The `SensitiveFilter` must be registered on the root logger in `main.py`.
- Rolling windows use `collections.deque` — O(1) append and popleft. Never rebuild from scratch on every tick.
- Account state dict must be a `TTLCache(maxsize=50_000, ttl=7200)` — never a plain dict.
- The asyncio.Queue for incoming messages must be bounded: `Queue(maxsize=10_000)`. Drop oldest on overflow.
- Python process sets itself to Windows Below Normal priority on startup.

## Key Files to Read First

When starting work on any component, read these first:

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — process model and IPC design
- [docs/DETECTION_ALGORITHMS.md](docs/DETECTION_ALGORITHMS.md) — algorithm implementations
- [docs/CHAT_HEALTH_SCORE.md](docs/CHAT_HEALTH_SCORE.md) — scoring pipeline
- [docs/SECURITY.md](docs/SECURITY.md) — security rules and token handling
- [docs/ROADMAP.md](docs/ROADMAP.md) — current phase and next tasks

## Performance Targets

| Chat Volume | Max Fast Path Latency | Max Memory |
|---|---|---|
| 100 msg/min | < 5ms/msg | < 200MB total |
| 1,000 msg/min | < 10ms/msg | < 300MB total |
| 5,000 msg/min | < 15ms/msg | < 450MB total |

The 1-second health score tick loop must always complete in < 50ms regardless of volume.

## Detection Response Targets

- Time to first alert (bot raid): < 5 seconds from raid start
- False positive rate target: < 3% on legitimate traffic
- Auto-ban threshold: dual-signal, both > 90 confidence, user not protected
