"""
TwitchIDS Backend — Main entrypoint.

Startup sequence:
1. Configure logging with SensitiveFilter (MUST be first)
2. Set Windows process priority to Below Normal
3. Parse CLI args (--port, --parent-pid)
4. Register FastAPI lifecycle hooks (startup.on_startup / on_shutdown)
5. Run uvicorn → on_startup fires → pipeline initialises → ready signal emitted

See startup.py for the full initialization sequence.
See tasks.py for background loop implementations.
"""

from __future__ import annotations

# === Step 1: Logging must be configured before any other imports ===
from core.logging import configure_logging

configure_logging()
# ===================================================================

import argparse
import logging
import os
import sys
import time

import uvicorn
from fastapi import FastAPI, Query, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from api.middleware import IPCAuthMiddleware
from api.router import register_routes
from api.websocket import handle_websocket, manager as ws_manager
from core.config import settings
from startup import IPC_SECRET, START_TIME, on_startup, on_shutdown
import startup as _startup

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Windows process priority
# ---------------------------------------------------------------------------

def set_below_normal_priority() -> None:
    """Lower this process's priority so the streamer's game isn't impacted."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
        handle = ctypes.windll.kernel32.OpenProcess(0x1F0FFF, False, os.getpid())
        ctypes.windll.kernel32.SetPriorityClass(handle, BELOW_NORMAL_PRIORITY_CLASS)
        ctypes.windll.kernel32.CloseHandle(handle)
        logger.debug("Process priority set to Below Normal")
    except Exception as e:
        logger.warning("Could not set process priority: %s", e)


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    app = FastAPI(
        title="TwitchIDS Backend",
        version="0.1.0",
        docs_url=None,   # Disable Swagger UI in production
        redoc_url=None,
    )

    # IPC authentication middleware — added first so CORS wraps around it
    app.add_middleware(IPCAuthMiddleware, ipc_secret=IPC_SECRET)

    # CORS: added last = runs first, so CORS headers are present even on 403 responses
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "null"],  # null = file:// origin
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register REST routes
    register_routes(app)

    # Health endpoint — no auth required, used for startup polling by Electron
    @app.get("/health")
    async def health():
        from twitch import manager as twitch_manager
        client = twitch_manager.get_client()
        return {
            "status": "ok",
            "version": "0.1.0",
            "uptime": round(time.time() - START_TIME, 1),
            "connected": client.is_connected if client else False,
            "dry_run": settings.dry_run,
        }

    # WebSocket endpoint — auth via query param ?secret=
    @app.websocket("/ws")
    async def websocket_endpoint(
        websocket: WebSocket,
        secret: str = Query(default=""),
    ):
        import sys
        print(f"[WS ENDPOINT] websocket_endpoint called — secret_len={len(secret)}", file=sys.stderr, flush=True)
        await handle_websocket(websocket, IPC_SECRET, client_secret=secret)

    # Simulator injection endpoint — dev mode only
    @app.websocket("/ws/inject")
    async def inject_endpoint(
        websocket: WebSocket,
        secret: str = Query(default=""),
    ):
        import json as _json
        if secret != IPC_SECRET:
            await websocket.close(code=4003)
            return
        if not settings.dev_mode and not settings.simulator_active:
            await websocket.close(code=4004)
            return
        await websocket.accept()
        logger.info("Simulator inject WebSocket connected")
        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    frame = _json.loads(raw)
                except Exception:
                    continue
                if frame.get("type") != "simulated_message":
                    continue
                d = frame.get("data", {})
                if _startup.message_queue is not None:
                    from pipeline.builder import build_message
                    msg = build_message(
                        user_id=str(d.get("user_id", "")),
                        username=str(d.get("username", "")),
                        channel="__sim__",  # always isolate simulated data
                        raw_text=str(d.get("content", "")),
                        color=None,
                        badges=[],
                    )
                    msg.account_age_days = d.get("account_age_days")
                    _startup.message_queue.enqueue(msg)
        except Exception:
            pass
        finally:
            logger.info("Simulator inject WebSocket disconnected")

    return app


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="TwitchIDS Backend")
    parser.add_argument("--port", type=int, default=7842)
    parser.add_argument("--parent-pid", type=int, default=None)
    parser.add_argument("--dev", action="store_true")
    args = parser.parse_args()

    # Apply CLI args to settings
    settings.port = args.port
    if args.dev:
        settings.dev_mode = True

    # Step 2: Set process priority
    set_below_normal_priority()

    # Create the FastAPI app
    app = create_app()
    app.add_event_handler("startup", on_startup)
    app.add_event_handler("shutdown", on_shutdown)

    if settings.dev_mode:
        logger.info("Running in dev mode — IPC secret: %s", IPC_SECRET)

    # Run uvicorn
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level="warning",   # uvicorn access logs suppressed; we use our own
        access_log=False,
    )


if __name__ == "__main__":
    main()
