# Development Setup Guide

This guide gets a new developer from zero to a fully running local environment.

---

## Prerequisites

| Tool | Version | Notes |
|------|---------|-------|
| Python | **3.12** | 3.13+ not supported — TwitchIO and aiosqlite have compatibility issues |
| Node.js | 20 LTS | npm 10+ included |
| Git | any | |
| Windows 10/11 | 64-bit | Windows Credential Manager required for token storage |

> **Python version is critical.** Use `py -3.12` (Python Launcher) to ensure the right version.
> Run `py -3.12 --version` to verify before creating the venv.

---

## Quick Start (both processes)

```powershell
# 1. Clone
git clone <repo-url> twitch-ids
cd twitch-ids

# 2. Backend
cd backend
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# 3. Frontend
cd ../frontend
npm install

# 4. Create backend .env (copy from example)
cd ../backend
copy .env.example .env
# Edit .env and fill in TWITCHIDS_CLIENT_ID and TWITCHIDS_CLIENT_SECRET

# 5. Run backend
.venv\Scripts\python.exe main.py

# 6. Run frontend (new terminal)
cd frontend
npm run dev:electron
```

---

## Backend Setup (detailed)

### Virtual environment

```powershell
cd backend
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

To use the pinned lockfile (recommended for reproducible installs):

```powershell
pip install -r requirements.lock
```

To regenerate the lockfile after updating `requirements.txt`:

```powershell
pip install pip-tools
pip-compile requirements.txt --output-file requirements.lock
```

### Environment variables

Copy `.env.example` to `.env` and fill in:

```
TWITCHIDS_CLIENT_ID=<your Twitch app client ID>
TWITCHIDS_CLIENT_SECRET=<your Twitch app client secret>
```

All other settings have sensible defaults. Notable optional variables:

| Variable | Default | Purpose |
|----------|---------|---------|
| `TWITCHIDS_DRY_RUN` | `true` | If `true`, all moderation actions are logged but not executed |
| `TWITCHIDS_DEV` | `false` | Enables dev-only endpoints (required for simulator) |
| `TWITCHIDS_SIMULATOR_ACTIVE` | `false` | Enables `/ws/inject` WebSocket endpoint (requires `TWITCHIDS_DEV=true`) |
| `TWITCHIDS_PORT` | `7842` | Port the FastAPI server listens on |

> **Warning**: Never set `TWITCHIDS_SIMULATOR_ACTIVE=true` in production. The backend enforces that it also requires `TWITCHIDS_DEV=true`.

### Register a Twitch application

1. Go to [https://dev.twitch.tv/console/apps](https://dev.twitch.tv/console/apps)
2. Create a new application
3. Set OAuth redirect URL to `http://localhost` (the app uses a dynamic port)
4. Copy Client ID and Client Secret into your `.env`

### Running the backend

```powershell
cd backend
.venv\Scripts\python.exe main.py
```

On first run the backend opens a browser window for Twitch OAuth. After authorising, tokens are stored in Windows Credential Manager and reused on subsequent starts.

The backend emits a JSON line to stdout when ready:

```json
{"type": "ready", "ipc_secret": "abc123...", "port": 7842}
```

The `ipc_secret` is required for all REST API calls (`X-IPC-Secret` header).

---

## Frontend Setup (detailed)

```powershell
cd frontend
npm install
```

### Dev mode (Vite + Electron, hot reload)

```powershell
npm run dev:electron
```

This starts both the Vite dev server (React HMR) and Electron together. Changes to React components reload instantly.

### Production build

```powershell
npm run build:electron
```

Output goes to `../dist/`. Requires the Python backend to be PyInstaller-packaged first (see `docs/ARCHITECTURE.md` §Packaging).

---

## Running Tests

All tests are in `backend/tests/`.

```powershell
cd backend
.venv\Scripts\python.exe -m pytest
```

Flags from `pytest.ini`:
- `-v` verbose output
- `--tb=short` compact tracebacks

### Test structure

```
backend/tests/
├── conftest.py          # make_message() factory, shared fixtures
└── unit/
    ├── test_normalizer.py       # Text normalisation (homoglyphs, unicode)
    ├── test_buffer.py           # ChatBuffer ring buffers and window stats
    ├── test_temporal.py         # TemporalSyncDetector
    ├── test_aggregator.py       # compute_user_threat_score, SIGNAL_WEIGHTS
    ├── test_engine_signals.py   # DetectionEngine signal logic
    ├── test_moderation.py       # ModerationEngine dual-signal gate, dry-run
    └── test_reputation.py       # ReputationStore score bounds and modifiers
```

Expected result: **119 passed, 0 failed, 0 warnings**.

---

## Running the Simulator

The simulator injects synthetic chat traffic into a running backend to measure detection accuracy.

### Prerequisites

1. Backend must be running with simulator mode enabled:

```
# backend/.env
TWITCHIDS_DEV=true
TWITCHIDS_SIMULATOR_ACTIVE=true
```

2. Capture the IPC secret from backend stdout on startup.

### Running a scenario

```powershell
# From project root
backend\.venv\Scripts\python.exe simulator\evaluate.py `
    --scenario simulator\scenarios\bot_raid.yaml `
    --port 7842 `
    --secret <IPC_SECRET>
```

Available scenarios: `normal_chat.yaml`, `spam_flood.yaml`, `bot_raid.yaml`, `5000_mpm_mixed.yaml`.

### Expected results

| Scenario | FP Rate | Target |
|----------|---------|--------|
| normal_chat | 0% | ✅ |
| spam_flood | 0% | ✅ |
| bot_raid | 0% | ✅ |
| 5000_mpm_mixed | ~7% | ❌ (simulator artifact — see AUDIT.md) |

---

## Project Structure

```
twitch-ids/
├── backend/
│   ├── main.py                  # FastAPI app entrypoint
│   ├── core/                    # Config, logging, IPC secret
│   ├── twitch/                  # TwitchIO EventSub client, OAuth
│   ├── pipeline/                # Message ingestion, normaliser, buffer
│   ├── detection/               # Detection algorithms and engine
│   │   ├── engine.py            # Main detection loop
│   │   ├── aggregator.py        # Signal → threat score
│   │   ├── detectors/           # Individual signal detectors
│   │   └── scoring/             # Health score pipeline
│   ├── moderation/              # Action engine, escalation table, executor
│   ├── storage/                 # SQLite schemas, reputation store
│   ├── api/                     # FastAPI routes and Pydantic schemas
│   ├── requirements.txt         # Direct dependencies
│   ├── requirements.lock        # Pinned full dependency tree
│   ├── pytest.ini
│   └── tests/
├── frontend/
│   ├── electron/                # Electron main process
│   └── src/                     # React + Tailwind dashboard
├── simulator/
│   ├── evaluate.py              # CLI evaluation harness
│   ├── scenarios/               # YAML scenario definitions
│   └── generators/              # Normal user and bot traffic generators
├── docs/                        # All design documentation
└── packaging/                   # Build scripts (Phase 8 — not yet started)
```

---

## Common Issues

### `OSError: [WinError 6] The handle is invalid`

Benign. This occurs in asyncio's stdin pipe when the backend is started directly from a terminal rather than from Electron. It does not affect functionality.

### `ModuleNotFoundError` when running tests

Make sure you're running pytest from the `backend/` directory, and that `pytest.ini` is present (sets `pythonpath = .`).

```powershell
cd backend
.venv\Scripts\python.exe -m pytest
```

### Backend fails to start: `TWITCHIDS_SIMULATOR_ACTIVE requires TWITCHIDS_DEV`

The simulator endpoint is dev-only. Set both in `.env`:

```
TWITCHIDS_DEV=true
TWITCHIDS_SIMULATOR_ACTIVE=true
```

### Twitch OAuth doesn't redirect back

Ensure `http://localhost` (without a path) is registered as a redirect URI in your Twitch app console. The backend uses a dynamic port — the full URI is constructed at runtime.

### `keyring` errors on first run

The Windows Credential Manager must be accessible. This fails in some CI/containerised environments. For headless testing, set `TWITCHIDS_DEV=true` and provide tokens via environment variables instead.

---

## Key Design Constraints (do not change without reading docs first)

- FastAPI binds to `127.0.0.1` only — never `0.0.0.0`
- `asyncio.Queue(maxsize=10_000)` — drops oldest on overflow, never unbounded
- Account state uses `TTLCache(maxsize=50_000, ttl=7200)` — never a plain dict
- Rolling windows use `collections.deque` — never rebuild on each tick
- **Bans require two independent signals both > 90 confidence** — this is a hard rule, not configuration
- Dry-run is ON by default — users must explicitly enable live mode

See [ARCHITECTURE.md](ARCHITECTURE.md) and [CLAUDE.md](../CLAUDE.md) for full constraints.
