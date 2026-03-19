# Security Model

---

## Threat Model

The application runs on a streamer's personal Windows PC. The relevant threats are:

| Threat | Vector | Risk |
|---|---|---|
| Token theft | Another local process reads OAuth tokens from disk | Medium |
| Local API abuse | Another process issues moderation commands via localhost API | Medium |
| XSS in chat messages | Malicious message content executed in renderer | Medium |
| Credential leak in logs | OAuth tokens written to log files | Low-Medium |
| Renderer sandbox escape | Malicious content accesses Node.js APIs | Medium |
| Supply chain attack | Malicious PyPI or npm package | Low |

This is not a public-facing service. There is no remote attack surface beyond the Twitch OAuth callback (handled by OS browser, not the app).

---

## OAuth Token Storage

**All tokens are stored in Windows Credential Manager (DPAPI-backed).**

Tokens are never written to:
- Config files
- Log files
- Environment variables on disk
- The SQLite database
- Any file on the filesystem in plaintext

### Implementation

**File:** `backend/core/auth/token_store.py`

```python
import keyring
import base64
import os
from pathlib import Path
from cryptography.fernet import Fernet

SERVICE_NAME = "TwitchIDS_v1"
MAX_DIRECT_LENGTH = 400  # Windows Credential Manager limit is 512 chars

class SecureTokenStore:

    def store(self, token_type: str, token: str) -> None:
        """
        Store token securely.
        If token fits in WCM directly: store directly.
        If token is too long: encrypt with random key, store key in WCM,
        store encrypted token in AppData.
        """
        if len(token) <= MAX_DIRECT_LENGTH:
            keyring.set_password(SERVICE_NAME, token_type, token)
        else:
            key = Fernet.generate_key()
            encrypted = Fernet(key).encrypt(token.encode())
            keyring.set_password(SERVICE_NAME, f"{token_type}_key",
                                  base64.b64encode(key).decode())
            self._token_file(token_type).write_bytes(encrypted)

    def retrieve(self, token_type: str) -> str | None:
        # Try direct storage first
        direct = keyring.get_password(SERVICE_NAME, token_type)
        if direct:
            return direct

        # Try encrypted file storage
        key_b64 = keyring.get_password(SERVICE_NAME, f"{token_type}_key")
        if not key_b64:
            return None

        token_file = self._token_file(token_type)
        if not token_file.exists():
            return None

        key = base64.b64decode(key_b64)
        return Fernet(key).decrypt(token_file.read_bytes()).decode()

    def delete(self, token_type: str) -> None:
        for key in [token_type, f"{token_type}_key"]:
            try:
                keyring.delete_password(SERVICE_NAME, key)
            except keyring.errors.PasswordDeleteError:
                pass
        self._token_file(token_type).unlink(missing_ok=True)

    def _token_file(self, token_type: str) -> Path:
        app_data = Path(os.environ['APPDATA']) / 'TwitchIDS'
        app_data.mkdir(exist_ok=True)
        return app_data / f".{token_type}.enc"


# Token types used by the application
TOKEN_ACCESS = "access_token"
TOKEN_REFRESH = "refresh_token"
TOKEN_CLIENT_ID = "client_id"
```

### PKCE OAuth Flow

The application uses the Authorization Code + PKCE flow. No client secret is stored or distributed in the application binary.

```python
# backend/core/auth/oauth.py

import secrets
import hashlib
import base64
import webbrowser
from aiohttp import web

class PKCEOAuthFlow:
    """
    Opens the system browser for Twitch OAuth.
    Starts a temporary local HTTP server to receive the callback.
    """

    SCOPES = [
        'user:read:chat',
        'user:write:chat',
        'user:bot',
        'moderator:manage:banned_users',
        'moderator:manage:chat_messages',
        'moderator:manage:chat_settings',
        'moderator:read:chatters',
    ]

    def __init__(self, client_id: str):
        self.client_id = client_id
        self.redirect_uri = 'http://localhost:3000/callback'

    async def run(self) -> dict:
        """Execute PKCE flow. Returns {access_token, refresh_token}."""
        code_verifier = secrets.token_urlsafe(64)
        code_challenge = self._make_challenge(code_verifier)
        state = secrets.token_urlsafe(16)

        auth_url = self._build_auth_url(code_challenge, state)
        webbrowser.open(auth_url)

        code = await self._wait_for_callback(state)
        tokens = await self._exchange_code(code, code_verifier)
        return tokens

    def _make_challenge(self, verifier: str) -> str:
        digest = hashlib.sha256(verifier.encode()).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b'=').decode()

    def _build_auth_url(self, challenge: str, state: str) -> str:
        params = {
            'client_id': self.client_id,
            'redirect_uri': self.redirect_uri,
            'response_type': 'code',
            'scope': ' '.join(self.SCOPES),
            'code_challenge': challenge,
            'code_challenge_method': 'S256',
            'state': state,
        }
        query = '&'.join(f'{k}={v}' for k, v in params.items())
        return f'https://id.twitch.tv/oauth2/authorize?{query}'
```

---

## IPC Authentication

The FastAPI server only binds to `127.0.0.1`. All requests must include a shared secret generated at Python startup.

**This prevents:**
- Other applications on the machine from issuing moderation commands
- Browser tabs (via fetch) from accessing the API
- Scripts running in background from abusing the moderation endpoint

### Implementation

```python
# Generated once at startup
import secrets
IPC_SECRET = secrets.token_urlsafe(32)

# Emitted to Electron main process via stdout (Channel 1)
emit_status("ready", port=PORT, ipc_secret=IPC_SECRET)
```

```python
# FastAPI middleware — validates on every request except /health
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

class IPCAuthMiddleware(BaseHTTPMiddleware):
    EXEMPT_PATHS = {'/health', '/ws'}

    async def dispatch(self, request, call_next):
        if request.url.path in self.EXEMPT_PATHS:
            return await call_next(request)

        secret = request.headers.get('X-IPC-Secret')
        if secret != IPC_SECRET:
            return Response('Forbidden', status_code=403)

        return await call_next(request)
```

The Electron main process receives the secret via stdout and stores it in memory. It passes the secret to the renderer via `contextBridge` after the backend is ready. The renderer includes the secret in every API request header.

---

## Electron Security Configuration

### Required Settings

```javascript
// electron/main.js

const mainWindow = new BrowserWindow({
  webPreferences: {
    nodeIntegration: false,           // NEVER true — prevents Node.js access from renderer
    contextIsolation: true,           // REQUIRED — isolates preload from renderer
    sandbox: true,                    // REQUIRED — Chromium sandbox
    webSecurity: true,                // NEVER false
    allowRunningInsecureContent: false,
    preload: path.join(__dirname, 'preload.js'),
  }
});
```

### Content Security Policy

```javascript
session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
  callback({
    responseHeaders: {
      ...details.responseHeaders,
      'Content-Security-Policy': [
        [
          "default-src 'self'",
          "script-src 'self'",
          // Only allow WebSocket to localhost backend
          "connect-src 'self' ws://127.0.0.1:* http://127.0.0.1:*",
          // Twitch CDN for user avatars only
          "img-src 'self' data: https://static-cdn.jtvnw.net",
          // Tailwind requires unsafe-inline (acceptable for local-only app)
          "style-src 'self' 'unsafe-inline'",
          "font-src 'self'",
        ].join('; ')
      ]
    }
  });
});
```

### Context Bridge — Minimal Surface

```javascript
// electron/preload.js
// Expose ONLY what the renderer legitimately needs.
// No arbitrary ipcRenderer access. No require(). No fs access.

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  // Receive backend connection details
  onBackendReady: (cb) => ipcRenderer.on('backend-ready', (_, data) => cb(data)),

  // Request backend config (port + secret)
  getBackendConfig: () => ipcRenderer.invoke('get-backend-config'),

  // Trigger native Windows notification
  showNotification: (title, body) =>
    ipcRenderer.send('show-notification', { title, body }),

  // Open external URLs in default browser (not in app)
  openExternal: (url) => ipcRenderer.send('open-external', url),

  // App version
  getVersion: () => ipcRenderer.invoke('get-version'),
});
```

---

## Sensitive Data in Logs

**File:** `backend/core/logging.py`

All loggers must have `SensitiveFilter` installed. This must be registered before any other code runs in `main.py`.

```python
import logging
import re

class SensitiveFilter(logging.Filter):
    """Redact OAuth tokens and secrets from all log output."""

    PATTERNS = [
        re.compile(r'oauth:[a-zA-Z0-9]{20,}', re.IGNORECASE),
        re.compile(r'"access_token"\s*:\s*"[^"]{20,}"', re.IGNORECASE),
        re.compile(r'"refresh_token"\s*:\s*"[^"]{20,}"', re.IGNORECASE),
        re.compile(r'client_secret[=:]\s*["\']?[a-zA-Z0-9]{20,}', re.IGNORECASE),
        re.compile(r'X-IPC-Secret[:\s]+[a-zA-Z0-9_-]{20,}', re.IGNORECASE),
        re.compile(r'ipc_secret["\s:]+[a-zA-Z0-9_-]{20,}', re.IGNORECASE),
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        msg = str(record.msg)
        for pattern in self.PATTERNS:
            msg = pattern.sub('[REDACTED]', msg)
        record.msg = msg

        # Also clean args (used by % formatting)
        if record.args:
            args = record.args
            if isinstance(args, tuple):
                args = tuple(
                    pattern.sub('[REDACTED]', str(a)) if isinstance(a, str) else a
                    for a in args
                    for pattern in self.PATTERNS
                )
            record.args = args

        return True


def configure_logging():
    """Call this as the first thing in main.py."""
    root = logging.getLogger()
    root.addFilter(SensitiveFilter())

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        '%(asctime)s %(levelname)s %(name)s %(message)s'
    ))
    root.addHandler(handler)
    root.setLevel(logging.INFO)
```

---

## Configuration File Security

Application config is stored in SQLite at `%APPDATA%\TwitchIDS\data.db`.

The config table stores settings as JSON values. It must not contain:
- OAuth tokens (use SecureTokenStore)
- Passwords
- Raw client secrets

File permissions: The `%APPDATA%\TwitchIDS\` directory is created with default Windows user-scoped permissions. Only the current user can read it. No additional ACL changes are needed.

---

## Token Refresh

TwitchIO 3.x handles token refresh automatically. When implementing manual Helix API calls via httpx, add a refresh interceptor:

```python
class RefreshingHTTPClient:
    def __init__(self, token_store: SecureTokenStore, client_id: str):
        self.token_store = token_store
        self.client_id = client_id
        self._lock = asyncio.Lock()

    async def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        token = self.token_store.retrieve(TOKEN_ACCESS)
        kwargs.setdefault('headers', {})
        kwargs['headers']['Authorization'] = f'Bearer {token}'
        kwargs['headers']['Client-Id'] = self.client_id

        async with httpx.AsyncClient() as client:
            response = await client.request(method, url, **kwargs)

        if response.status_code == 401:
            # Token expired — refresh and retry once
            async with self._lock:  # Prevent concurrent refreshes
                new_token = await self._refresh()
            if new_token:
                kwargs['headers']['Authorization'] = f'Bearer {new_token}'
                async with httpx.AsyncClient() as client:
                    response = await client.request(method, url, **kwargs)

        return response

    async def _refresh(self) -> str | None:
        refresh_token = self.token_store.retrieve(TOKEN_REFRESH)
        if not refresh_token:
            return None

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                'https://id.twitch.tv/oauth2/token',
                data={
                    'grant_type': 'refresh_token',
                    'refresh_token': refresh_token,
                    'client_id': self.client_id,
                }
            )

        if resp.status_code == 200:
            data = resp.json()
            self.token_store.store(TOKEN_ACCESS, data['access_token'])
            if 'refresh_token' in data:
                self.token_store.store(TOKEN_REFRESH, data['refresh_token'])
            return data['access_token']

        return None
```

---

## Production Build Security

### Code Signing

The final Windows installer must be signed with a code signing certificate. Without signing:
- Windows SmartScreen will warn users on first run
- Windows Defender may flag the PyInstaller bundle as a false positive
- Enterprise machines may block execution

For initial development builds, sign with a self-signed certificate and document that users must click through SmartScreen. For public release, obtain an EV code signing certificate from a CA.

### Dependency Integrity

Pin all Python dependencies with pip hash verification:

```
# requirements.txt (generated by pip-compile --generate-hashes)
fastapi==0.115.0 \
    --hash=sha256:abc123... \
    --hash=sha256:def456...
```

Pin Node packages via `package-lock.json`. Never use `npm install --no-package-lock`.

### PyInstaller Binary

The PyInstaller bundle includes the Python interpreter and all dependencies. It does not include source code in a human-readable form, but it is not obfuscated — determined attackers can extract bytecode. This is acceptable since:
- No secrets are embedded in the binary (all stored in Credential Manager)
- The client_id is not secret (it is visible in the OAuth URL the user visits)
- Detection algorithm details are not security-sensitive

---

## Security Checklist (Pre-Release)

- [ ] OAuth tokens stored only in Windows Credential Manager
- [ ] FastAPI bound to 127.0.0.1 only in production builds
- [ ] IPC shared secret middleware active on all non-health endpoints
- [ ] Electron contextIsolation and sandbox enabled
- [ ] Content Security Policy installed and tested
- [ ] SensitiveFilter registered on root logger
- [ ] No hardcoded credentials anywhere in codebase (verify with: `grep -r "access_token\|client_secret" backend/ --include="*.py"`)
- [ ] All dependencies pinned with hashes
- [ ] Code signing certificate applied to installer
- [ ] Test: verify tokens do not appear in `%APPDATA%\TwitchIDS\` directory as plaintext files
- [ ] Test: verify another process cannot call localhost API without the secret
