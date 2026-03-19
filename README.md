# TwitchIDS — Twitch Chat Intrusion Detection System

A standalone Windows desktop app that monitors Twitch chat in real time, detects bot farms and coordinated spam campaigns, and automatically takes moderation action. Runs fully locally — no cloud, no subscription, no data sent anywhere.

---

## Features

### Detection
- **Real-time spam flood detection** — identical and near-identical message floods caught in under 5 seconds
- **Semantic bot clustering** — paraphrasing bots that vary their messages are caught via ONNX sentence embeddings + DBSCAN clustering
- **Known bot pre-filter** — 12M+ usernames from CommanderRoot + TwitchInsights checked on every message via Bloom filter
- **Timing regularity signal** — machine-regular inter-arrival times (near-zero CV) flagged as bot behaviour
- **Online anomaly detection** — River HalfSpaceTrees adapts to your channel's normal traffic patterns
- **Drift detection** — EWMA control chart + ADWIN catches slow-ramp campaigns that stay under per-tick thresholds
- **Account age scoring** — new accounts graded by age (<1d, <7d, <30d, <90d)
- **Username entropy analysis** — random-suffix bot name patterns detected via Shannon entropy
- **Spam pattern matching** — Aho-Corasick multi-pattern scan against crypto/phishing/giveaway/follower-bot corpus
- **Bot network graph** — co-occurrence graph with Infomap community detection, rendered in WebGL via Sigma.js

### Moderation tools
- One-click timeout / ban / warn / delete on any user
- **Cluster mass-action** — timeout or ban all members of a detected bot cluster at once
- **Nuke tool** — bulk-action by phrase or regex with preview
- **AutoMod queue** — approve/deny held messages with keyboard shortcuts (A/D)
- **Unban request panel** — review and action unban requests without leaving the app
- **Follower bot audit** — scan your follower list against the known-bot registry
- **Shared ban list import** — paste a plain-text, JSON, or CommanderRoot export and mass-ban
- **Regex block filters** — persistent custom pattern filters with live test-against-recent-messages
- **Chat mode controls** — emote-only, sub-only, unique-chat, slow-mode, followers-only toggles
- **Lockdown profiles** — save chat mode combinations and apply them in one click; auto-apply on raid
- **User watchlist** — flag accounts for monitoring with personal notes
- **Warning system** — issue Twitch-native warnings with reason tracking
- **Action rollback** — undo the last 50 moderation actions
- **Multi-channel monitoring** — watch multiple channels from a single dashboard

### Safety
- **Dry-run mode is ON by default** — no automated actions until you explicitly enable live moderation
- **Bans require two independent signals**, both scoring > 90 confidence — a single algorithm cannot trigger a permanent ban
- **Protected accounts are never actioned** — mods, VIPs, 60-day subscribers, known-good bots (Nightbot, StreamElements, etc.)
- **Detection suspends automatically** during raids, hype trains, and mass gift sub events

---

## Requirements

- Windows 10 or Windows 11 (x64)
- A Twitch account with moderator permissions on the channel you want to monitor
- A [Twitch Developer application](https://dev.twitch.tv/console/apps) (free — takes ~2 minutes to register)

---

## Installation

Download the latest `TwitchIDS-Setup-x.x.x.exe` from the [Releases](../../releases) page and run it.

The installer will:
1. Install TwitchIDS to `Program Files` (or a directory of your choice)
2. Create Start Menu and Desktop shortcuts
3. Launch the first-run setup wizard

The app auto-updates silently in the background — no manual downloads needed for future versions.

---

## First-run setup

The setup wizard walks you through four steps:

1. **App credentials** — enter your Twitch application Client ID and Client Secret
   Register at [dev.twitch.tv/console/apps](https://dev.twitch.tv/console/apps). Set the redirect URI to `http://localhost`.

2. **Authorize** — a browser window opens; sign in with the Twitch account that has mod permissions

3. **Channel** — enter the channel name to monitor

4. **Sensitivity** — choose a detection preset:
   - **Conservative** — fewer alerts, lowest false-positive rate
   - **Balanced** — default; < 3% false positives on normal traffic
   - **Aggressive** — catches suspicious accounts earlier; best for actively-targeted channels

All settings can be changed later from the Settings drawer (⚙ in the top-right corner).

---

## Building from source

### Prerequisites
- Python 3.12 (not 3.13+)
- Node.js 20+
- Git

### Setup

```powershell
git clone https://github.com/krakupkiwi/chat-mod-tool.git
cd chat-mod-tool

# Python backend
py -3.12 -m venv backend\.venv
backend\.venv\Scripts\pip install -r backend\requirements.txt

# Node frontend
cd frontend && npm install && cd ..
```

### Development (hot-reload)

```powershell
# Terminal 1 — Python backend
cd backend
.venv\Scripts\python.exe main.py --port 7842 --dev

# Terminal 2 — Electron + Vite
cd frontend
npm run dev:electron
```

### Production build

```powershell
.\packaging\build.ps1
# Output: dist\TwitchIDS-Setup-1.0.0.exe
```

Add `-SkipPython` to reuse an existing `dist-python\` bundle (saves ~5 minutes on frontend-only changes).

---

## Architecture

```
Electron (main + renderer)  ←→  Python FastAPI backend  ←→  Twitch EventSub
```

- **Electron** — React 18 + Tailwind dashboard, process lifecycle, system tray, auto-updates
- **Python FastAPI** — detection engine, Twitch client, moderation dispatcher, SQLite/DuckDB storage
- **IPC** — stdout JSON for lifecycle signals; WebSocket for live event push; REST for config and commands
- Binds to `127.0.0.1` only — no network exposure

**Detection pipeline:**

```
EventSub message
  → normalizer (NFKC, homoglyph, invisible-char strip)
  → fast path  (duplicate ratio, temporal sync, MinHash LSH, rate, username entropy,
                timing regularity, pattern match, known-bot lookup, regex filters)
  → batch path (fastembed ONNX → DBSCAN clusters → igraph Infomap bot-network detection,
                River HalfSpaceTrees anomaly, EWMA+ADWIN drift)
  → aggregator (weighted confidence score, 0–100)
  → alert / moderation action
```

---

## Performance

| Chat volume | Latency per message | Memory |
|---|---|---|
| 100 msg/min | < 5 ms | < 200 MB |
| 1,000 msg/min | < 10 ms | < 300 MB |
| 5,000 msg/min | < 15 ms | < 450 MB |

Time to first alert on a bot raid: **< 5 seconds**.

---

## Tech stack

| Layer | Technology |
|---|---|
| UI | Electron 33, React 18, Tailwind CSS, Recharts, Sigma.js |
| Backend | Python 3.12, FastAPI, uvicorn, TwitchIO 3.x (EventSub WebSocket) |
| Detection — fast path | datasketch (MinHash LSH), pyahocorasick, xxhash |
| Detection — ML | fastembed (BAAI/bge-small-en-v1.5 ONNX), scikit-learn (DBSCAN), River (HalfSpaceTrees, ADWIN), igraph |
| Storage | aiosqlite (operational), DuckDB (analytics) |
| Token storage | Windows Credential Manager via `keyring` — never flat files |
| Packaging | PyInstaller (Python), electron-builder NSIS (installer) |

---

## License

Private — all rights reserved.
