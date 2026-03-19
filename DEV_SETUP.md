# Development Setup

## Prerequisites

- Python 3.12 (required — 3.13+ lacks pre-built wheels for pydantic-core/twitchio)
- Node 20 LTS
- Git

## Backend Setup

```powershell
cd backend

# Create virtual environment (must use Python 3.12)
py -3.12 -m venv .venv
.venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Copy env template
copy .env.example .env
# Edit .env and set TWITCHIDS_CLIENT_ID

# Run backend standalone (dev mode)
python main.py --port 7842 --dev
```

Backend is ready when you see:
```
{"type": "ready", "port": 7842, "ipc_secret": "..."}
```

In dev mode (`--dev`), the `ipc_secret` is also printed to the console.

## Frontend Setup

```powershell
cd frontend
npm install
```

### Option A: Run with Electron (full app)

```powershell
# Terminal 1 — backend
cd backend
.venv\Scripts\activate
python main.py --port 7842 --dev

# Terminal 2 — Electron
cd frontend
npm run dev:electron
```

### Option B: Run just the React UI in browser (no Electron)

Set the IPC secret from the backend output in a `.env.local` file:

```
# frontend/.env.local
VITE_BACKEND_PORT=7842
VITE_IPC_SECRET=<secret from backend stdout>
```

Then:
```powershell
cd frontend
npm run dev
# Open http://localhost:5173
```

## Twitch App Registration

1. Go to https://dev.twitch.tv/console/apps
2. Create a new application
3. Set OAuth Redirect URL to: `http://localhost:3000/callback`
4. Copy the Client ID into `backend/.env` as `TWITCHIDS_CLIENT_ID`
5. On first run, the setup screen will open the browser for authorization

## Dev Workflow

1. Start backend: `python main.py --dev`
2. Start Electron: `npm run dev:electron`
3. The setup screen appears on first run
4. Enter Client ID → authorize → enter channel name → monitoring starts

## Running Tests

```powershell
cd backend
.venv\Scripts\activate
pytest tests/ -v
```

## Folder Structure

```
twitch-ids/
├── backend/           Python FastAPI detection engine
│   ├── main.py        Entrypoint
│   ├── core/          Config, logging, IPC protocol
│   ├── twitch/        TwitchIO client, OAuth, token store
│   ├── api/           FastAPI routes, WebSocket, middleware
│   ├── pipeline/      Message queue, normalizer (Phase 2)
│   ├── detection/     Detection algorithms (Phase 3-4)
│   ├── moderation/    Action dispatcher (Phase 5)
│   └── storage/       SQLite, DuckDB (Phase 2)
├── frontend/          Electron + React dashboard
│   ├── electron/      Main process, PythonManager, preload
│   └── src/           React components, hooks, store
├── simulator/         Bot attack simulator (Phase 3+)
├── docs/              Architecture documentation
└── DEV_SETUP.md       This file
```

## Phase 1 Milestone Checklist

- [x] `python main.py --dev` starts without errors
- [x] `/health` endpoint returns `{"status": "ok"}`
- [ ] OAuth flow opens browser and stores tokens in Windows Credential Manager
- [ ] Electron app opens, PythonManager starts Python, ready signal received
- [ ] WebSocket connects from renderer to backend
- [ ] Chat messages appear in the UI in real time
- [ ] Connection status indicators update correctly

> Note: `OSError: [WinError 6]` on stdin pipe is **expected and harmless** when running
> from a PowerShell/cmd terminal. It does not occur when Electron spawns the process.
