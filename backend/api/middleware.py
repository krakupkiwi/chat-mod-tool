"""
IPC authentication middleware.

Every request to the FastAPI server must include the X-IPC-Secret header.
The secret is generated at Python startup and sent to Electron via stdout.
This prevents other local processes from using the moderation API.

Exempt paths: /health (used for startup polling before secret is known)
              /ws    (secret passed as query param instead, checked in handler)

Implemented as a pure ASGI middleware (not BaseHTTPMiddleware) so that
WebSocket upgrade connections are never intercepted by the HTTP dispatch
path — BaseHTTPMiddleware's call_next wrapper does not pass through WebSocket
connections correctly in all Starlette versions.
"""

from __future__ import annotations

import sys

from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send


class IPCAuthMiddleware:
    EXEMPT_PATHS = {"/health"}

    def __init__(self, app: ASGIApp, ipc_secret: str) -> None:
        self.app = app
        self.ipc_secret = ipc_secret

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        scope_type = scope.get("type", "unknown")
        # Only apply auth logic to HTTP requests.
        # WebSocket (scope["type"] == "websocket") and lifespan events pass through
        # untouched — the WebSocket handler does its own query-param auth check.
        if scope_type != "http":
            print(f"[MIDDLEWARE] non-http scope_type={scope_type!r} passing through", file=sys.stderr, flush=True)
            await self.app(scope, receive, send)
            return

        request = Request(scope)

        # CORS preflight — browsers send OPTIONS without custom headers
        if request.method == "OPTIONS":
            await self.app(scope, receive, send)
            return

        # Exempt paths (/health for startup polling; /ws uses its own auth)
        path = request.url.path
        if path in self.EXEMPT_PATHS or path.startswith("/ws"):
            await self.app(scope, receive, send)
            return

        # All other HTTP requests require the shared IPC secret
        secret = request.headers.get("x-ipc-secret")
        if secret != self.ipc_secret:
            response = Response("Forbidden", status_code=403)
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
