# Technology Stack

All library choices are final decisions made during architecture review. Do not substitute without documenting the reason and updating this file.

---

## Python Backend

Python version: **3.12+**

### Core Runtime

| Library | Version | Purpose | Why This Choice |
|---|---|---|---|
| `fastapi` | ^0.115 | Local API server | Best async Python web framework; built-in WebSocket; excellent Pydantic integration |
| `uvicorn` | ^0.30 | ASGI server | Standard FastAPI runtime; supports WebSocket; minimal overhead |
| `pydantic` | ^2.0 | Data validation and schemas | FastAPI's native model layer; fast Rust-backed validation in v2 |
| `pydantic-settings` | ^2.0 | Configuration management | Type-safe config from env vars and files |

### Twitch Integration

| Library | Version | Purpose | Why This Choice |
|---|---|---|---|
| `twitchio` | ^3.2 | Twitch EventSub + Helix API | Only mature async Python library using EventSub WebSocket as core transport; active maintenance; handles token refresh automatically |
| `httpx` | ^0.27 | Async HTTP client | Used for direct Helix API calls not wrapped by TwitchIO; async-first; better than aiohttp for this use case |

TwitchIO 3.x uses EventSub WebSocket by default. IRC is not used — Twitch has deprecated IRC for new integrations.

### Machine Learning

| Library | Version | Purpose | Why This Choice |
|---|---|---|---|
| `sentence-transformers` | ^3.0 | MiniLM embedding model | Best Python interface for transformer models; supports ONNX export; well maintained by HuggingFace |
| `onnxruntime` | ^1.18 | ONNX model inference | 2–3x faster than PyTorch on CPU; no GPU required; optimized for Windows |
| `scikit-learn` | ^1.5 | DBSCAN, Isolation Forest | Battle-tested implementations; no GPU required; fast enough for batch sizes used here |
| `numpy` | ^1.26 | Array operations | Required by scikit-learn and sentence-transformers |

**Primary model:** `sentence-transformers/all-MiniLM-L6-v2`
- Size: 22MB
- Embedding dimensions: 384
- Speed: ~500 sentences/second on CPU (raw PyTorch), ~1,000–1,500 with ONNX
- Use: semantic similarity clustering of chat messages

**Model setup (one-time at install):**

```python
from sentence_transformers import SentenceTransformer

model = SentenceTransformer('all-MiniLM-L6-v2')
model.save('backend/models/minilm')
# Export to ONNX for faster CPU inference
model.save_to_onnx('backend/models/minilm.onnx')
```

### Detection Algorithms

| Library | Version | Purpose | Why This Choice |
|---|---|---|---|
| `datasketch` | ^1.6 | MinHash + LSH | Best Python implementation of locality-sensitive hashing; O(1) per-message similarity lookups |
| `networkx` | ^3.3 | Co-occurrence graph analysis | Standard Python graph library; `louvain_communities` for bot network detection |
| `cachetools` | ^5.3 | TTLCache for account state | Bounded LRU + TTL cache; prevents account state dict growing unbounded |

### Database

| Library | Version | Purpose | Why This Choice |
|---|---|---|---|
| `aiosqlite` | ^0.20 | Async SQLite writes | Non-blocking DB writes on the asyncio event loop; simple API |
| `duckdb` | ^1.0 | Analytics queries | Columnar OLAP queries for aggregations, timelines, and reports; 10–50x faster than SQLite for analytical workloads |

SQLite for operational data (point lookups, transactional writes). DuckDB for read-heavy analytics. Both are embedded — no separate database process.

### Storage and Security

| Library | Version | Purpose | Why This Choice |
|---|---|---|---|
| `keyring` | ^25 | Windows Credential Manager | Native DPAPI-backed secret storage; tokens never written to files in plain text |
| `cryptography` | ^42 | Fernet symmetric encryption | Used when tokens exceed Windows Credential Manager 512-char limit |

### Utilities

| Library | Version | Purpose |
|---|---|---|
| `structlog` | ^24 | Structured JSON logging |
| `pywin32` | ^306 | Windows power events (sleep/wake detection) |
| `python-dotenv` | ^1.0 | Dev environment config |

### Packaging

| Tool | Version | Purpose |
|---|---|---|
| `pyinstaller` | ^6.0 | Bundle Python app to single Windows EXE |

PyInstaller produces a single `twitchids-backend.exe` (~250–350MB with ML models). Known PyInstaller quirks for this stack:

```
--collect-all sentence_transformers
--collect-all twitchio
--add-data "backend/models:models"
--hidden-import sklearn.tree._utils
--hidden-import datasketch
```

Always test the PyInstaller bundle on a clean Windows VM before release. Windows Defender may flag new builds — code signing certificate required for production.

### Development Dependencies

| Library | Purpose |
|---|---|
| `pytest` | Unit and integration tests |
| `pytest-asyncio` | Async test support |
| `pytest-cov` | Coverage reporting |
| `ruff` | Fast Python linter + formatter |
| `mypy` | Static type checking |
| `faker` | Fake data generation for tests |
| `markovify` | Markov chain message generation (simulator) |

---

## JavaScript / TypeScript Frontend

Node version: **20 LTS**

### Electron

| Package | Version | Purpose |
|---|---|---|
| `electron` | ^30 | Desktop shell; ships Chromium + Node |
| `electron-builder` | ^24 | Windows packaging (NSIS installer) |
| `electron-updater` | ^6 | Auto-update via GitHub Releases |

### React

| Package | Version | Purpose |
|---|---|---|
| `react` | ^18 | UI framework |
| `react-dom` | ^18 | DOM rendering |
| `typescript` | ^5.4 | Type safety |
| `vite` | ^5 | Build tool (replaces CRA) |

### State Management

| Package | Version | Purpose | Why |
|---|---|---|---|
| `zustand` | ^4 | Global state | Minimal boilerplate; no context provider overhead; easy to split into slices |
| `@tanstack/react-query` | ^5 | REST data fetching | Caching, refetch, background sync for non-live data (config, history) |

### Visualization

| Package | Version | Purpose |
|---|---|---|
| `recharts` | ^2 | Line charts, area charts, bar charts (health timeline, metrics) |
| `react-force-graph` | ^1 | Bot network graph visualization |
| `react-window` | ^1 | Virtualized list for chat feed (never render > 200 DOM nodes) |

### Styling

| Package | Version | Purpose |
|---|---|---|
| `tailwindcss` | ^3 | Utility-first CSS |
| `@headlessui/react` | ^2 | Accessible UI primitives (modals, dropdowns) |
| `lucide-react` | ^0.400 | Icon library |

### Utilities

| Package | Version | Purpose |
|---|---|---|
| `date-fns` | ^3 | Date formatting |
| `clsx` | ^2 | Conditional className utility |

---

## Build and Tooling

| Tool | Version | Purpose |
|---|---|---|
| `electron-builder` | ^24 | NSIS Windows installer |
| PowerShell | built-in | Build automation scripts |
| GitHub Actions | — | CI/CD pipeline |

### Build Pipeline

```
1. Python: pip install pyinstaller + deps
           pyinstaller backend/main.spec → dist/twitchids-backend.exe

2. Node:   npm ci
           npm run build → frontend/dist/ (Vite bundle)

3. Electron: electron-builder --win
             Embeds dist/twitchids-backend.exe as extraResource
             Produces TwitchIDS-Setup-{version}.exe
```

---

## Environment Setup

### Python Backend

```bash
# Create virtual environment
python -m venv .venv
.venv\Scripts\activate

# Install dependencies
pip install -r backend/requirements.txt

# One-time model download and ONNX export
python backend/scripts/setup_models.py

# Run in dev mode
uvicorn backend.main:app --port 7842 --reload
```

### Frontend

```bash
cd frontend
npm install

# Run Electron in dev mode (expects Python backend running separately)
npm run dev:electron

# Run just the React UI in browser
npm run dev
```

### Dev Workflow

In development, run Python and Electron separately:

```
Terminal 1: uvicorn backend.main:app --port 7842
Terminal 2: cd frontend && npm run dev:electron
```

Set `TWITCHIDS_DEV=true` in the Python environment to skip the Electron lifecycle protocol and just start the server directly on port 7842.

---

## Dependency Pinning

All Python dependencies are pinned with hashes in `requirements.txt` for production builds:

```
pip-compile backend/requirements.in --generate-hashes -o backend/requirements.txt
```

Node dependencies are locked via `package-lock.json`.

---

## Version Compatibility Matrix

| Component | Minimum Windows | Notes |
|---|---|---|
| Electron 30 | Windows 10 | Requires WebView2 (bundled) |
| WebView2 | Windows 10 | Ships with Win 11; auto-installed by Electron on Win 10 |
| Python 3.12 | Windows 10 | Bundled in PyInstaller binary — not required on target machine |
| ONNX Runtime 1.18 | Windows 10 | AVX2 preferred but not required |
| SQLite (bundled) | Windows 10 | Bundled with Python — no separate install |

**Target minimum system:** Windows 10 64-bit, 8GB RAM, no GPU required.
**Recommended system:** Windows 11 64-bit, 16GB RAM.
