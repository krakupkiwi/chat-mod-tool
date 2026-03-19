# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for TwitchIDS Python backend.

Usage (from project root):
    backend\\.venv\\Scripts\\pyinstaller.exe packaging\\twitchids-backend.spec

Output: dist-python\\twitchids-backend\\  (a folder, not a single EXE)
  → electron-builder picks this up as an extraResource via package.json build.win.extraResources

Design decisions:
  - COLLECT mode (folder bundle, not onefile) — faster cold-start, no temp-dir extraction,
    and avoids Windows Defender heuristics that fire on self-extracting single-EXEs.
  - UPX disabled — UPX compression triggers false positives in some AV engines.
  - console=True — required; the Electron PythonManager reads the backend's stdout.
  - optimize=2 — strips docstrings from .pyc; reduces bundle ~5-8%.
"""

import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules, copy_metadata

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# SPECPATH = directory containing this spec file (packaging/)
BACKEND_DIR = Path(SPECPATH).parent / "backend"
PROJECT_ROOT = Path(SPECPATH).parent

# PyInstaller will write the collected bundle here (relative to project root)
DIST_PATH = str(PROJECT_ROOT / "dist-python")
WORK_PATH  = str(PROJECT_ROOT / "build-python")

# ---------------------------------------------------------------------------
# Aggregate datas / binaries / hiddenimports from large packages
# ---------------------------------------------------------------------------

datas_list    = []
binaries_list = []
hidden_list   = []


def _add(pkg):
    """collect_all() for a package and merge into the three lists."""
    d, b, h = collect_all(pkg)
    datas_list.extend(d)
    binaries_list.extend(b)
    hidden_list.extend(h)


# Web framework stack
_add("fastapi")
_add("starlette")
_add("uvicorn")
_add("pydantic")
_add("pydantic_core")
_add("pydantic_settings")

# Twitch client
_add("twitchio")

# ML / detection
_add("sklearn")       # scikit-learn — IsolationForest, DBSCAN
_add("river")         # online anomaly + ADWIN drift
_add("igraph")        # community detection (C-backed)
_add("fastembed")     # BAAI/bge-small-en-v1.5 ONNX embeddings

# sentence-transformers is a fallback for fastembed; include its submodules but
# skip the full transformers stack (adds ~900 MB) — the ONNX model is loaded directly.
hidden_list += collect_submodules("sentence_transformers")
datas_list  += collect_data_files("sentence_transformers")

# ONNX runtime
_add("onnxruntime")

# Tokenizers (Rust extension, used by fastembed / sentence-transformers)
_add("tokenizers")

# Hugging Face hub (needed by fastembed model download at runtime)
_add("huggingface_hub")

# ---------------------------------------------------------------------------
# Project data files
# ---------------------------------------------------------------------------

# Spam pattern corpus
datas_list.append(
    (str(BACKEND_DIR / "data" / "spam_patterns.json"), "data")
)

# MiniLM ONNX model (sentence-transformers fallback)
datas_list.append(
    (str(BACKEND_DIR / "models" / "minilm"), "models/minilm")
)

# fastembed model cache (BAAI/bge-small-en-v1.5-onnx-q)
fastembed_cache = PROJECT_ROOT / "models"
if fastembed_cache.exists():
    datas_list.append((str(fastembed_cache), "models"))

# ---------------------------------------------------------------------------
# importlib.metadata — copy dist-info so packages can call importlib.metadata
# ---------------------------------------------------------------------------

for pkg in [
    "fastapi", "starlette", "uvicorn", "pydantic", "pydantic_settings",
    "twitchio", "httpx", "httpcore", "anyio", "h11",
    "scikit_learn", "river", "igraph", "fastembed", "onnxruntime",
    "sentence_transformers", "tokenizers", "huggingface_hub",
    "aiosqlite", "duckdb", "keyring", "cachetools", "datasketch",
    "pybloom_live", "pyahocorasick", "xxhash", "structlog", "psutil",
    "python_dotenv", "pywin32",
]:
    try:
        datas_list += copy_metadata(pkg)
    except Exception:
        pass  # package not installed or no dist-info; non-fatal

# ---------------------------------------------------------------------------
# Hidden imports
# ---------------------------------------------------------------------------

hidden_list += [
    # uvicorn internals (not auto-discovered)
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",

    # keyring Windows backend
    "keyring.backends",
    "keyring.backends.Windows",
    "keyring.backends._win_crypto",
    "keyring.backends.fail",

    # pywin32 (keyring + process-priority)
    "win32api",
    "win32con",
    "win32cred",
    "win32security",
    "pywintypes",

    # asyncio Windows event loop
    "asyncio",
    "asyncio.windows_events",
    "asyncio.windows_utils",

    # websockets (twitchio EventSub)
    "websockets",
    "websockets.legacy",
    "websockets.legacy.client",
    "websockets.legacy.server",
    "websockets.extensions",
    "websockets.extensions.permessage_deflate",

    # httpx / httpcore transports
    "httpcore",
    "httpcore._sync.http11",
    "httpcore._async.http11",
    "httpx",

    # anyio asyncio backend
    "anyio",
    "anyio._backends._asyncio",
    "anyio.from_thread",

    # email-validator (FastAPI optional dep for EmailStr)
    "email_validator",

    # h11 (uvicorn HTTP/1.1)
    "h11",

    # aiosqlite / duckdb
    "aiosqlite",
    "duckdb",

    # cachetools
    "cachetools",
    "cachetools.ttl",

    # datasketch MinHash + LSH
    "datasketch",
    "datasketch.minhash",
    "datasketch.lsh",

    # Bloom filter
    "pybloom_live",

    # Aho-Corasick (C extension)
    "ahocorasick",

    # xxhash (C extension)
    "xxhash",

    # psutil (C extension)
    "psutil",
    "psutil._pswindows",

    # structlog
    "structlog",
    "structlog.stdlib",

    # python-dotenv
    "dotenv",

    # cryptography (optional keyring dep for long tokens)
    "cryptography",
    "cryptography.fernet",
    "cryptography.hazmat.primitives",
    "cryptography.hazmat.backends",

    # sklearn internals PyInstaller misses
    "sklearn.tree._utils",
    "sklearn.ensemble._iforest",
    "sklearn.neighbors._dist_metrics",
    "sklearn.utils._cython_blas",
    "sklearn.utils._weight_vector",

    # onnxruntime internals
    "onnxruntime",
    "onnxruntime.capi",
    "onnxruntime.capi.onnxruntime_pybind11_state",

    # river internals
    "river.anomaly",
    "river.anomaly.half_space_trees",
    "river.drift",
    "river.drift.adwin",
    "river.stats",
    "river.utils",

    # igraph internals
    "igraph",
    "igraph._igraph",

    # huggingface_hub lazy modules
    "huggingface_hub.utils",
    "huggingface_hub.file_download",
]

# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

a = Analysis(
    [str(BACKEND_DIR / "main.py")],
    pathex=[str(BACKEND_DIR)],
    binaries=binaries_list,
    datas=datas_list,
    hiddenimports=hidden_list,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Dev tools — not needed at runtime
        "pytest", "pytest_asyncio", "ruff",
        # Jupyter / IPython
        "IPython", "jupyter", "notebook",
        # GUI toolkits (not used)
        "tkinter", "_tkinter", "wx", "PyQt5", "PyQt6",
        # matplotlib (not needed — we use Recharts in the frontend)
        "matplotlib", "PIL",
        # Test frameworks
        "unittest", "doctest",
        # Full transformers stack (we use fastembed ONNX directly)
        "transformers",
    ],
    noarchive=False,
    optimize=2,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="twitchids-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,        # UPX disabled — triggers AV false positives
    console=True,     # stdout JSON protocol requires a console handle
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,        # TODO: add backend/assets/icon.ico if desired
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="twitchids-backend",
)
