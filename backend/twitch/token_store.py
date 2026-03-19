"""
Secure OAuth token storage using Windows Credential Manager (DPAPI-backed).

Tokens are NEVER written to files, databases, environment variables, or logs.
The keyring library uses the Windows Credential Locker on Windows,
which is encrypted per-user via DPAPI.

For tokens that exceed the 512-character WCM limit, a random Fernet key is
generated, the key is stored in WCM, and the encrypted token is stored in
AppData as a binary file.
"""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path

import keyring
import keyring.errors

# cryptography is an optional dependency used only when a token exceeds 400 chars.
# Twitch OAuth tokens are typically 200-350 chars, so this path is rarely hit.
try:
    from cryptography.fernet import Fernet, InvalidToken
    _FERNET_AVAILABLE = True
except ImportError:
    _FERNET_AVAILABLE = False

logger = logging.getLogger(__name__)

SERVICE_NAME = "TwitchIDS_v1"
MAX_DIRECT_LENGTH = 400  # Stay well below the 512-char WCM limit

# Logical token names used throughout the codebase
TOKEN_ACCESS = "access_token"
TOKEN_REFRESH = "refresh_token"
TOKEN_CLIENT_ID = "client_id"
TOKEN_CLIENT_SECRET = "client_secret"
TOKEN_CHANNEL = "channel"
TOKEN_BROADCASTER_ID = "broadcaster_id"   # Twitch numeric user ID for the channel owner


class SecureTokenStore:
    """
    Store and retrieve OAuth tokens via Windows Credential Manager.
    Thread-safe for reads; callers must not write concurrently.
    """

    def store(self, token_type: str, token: str) -> None:
        """Persist token securely. Raises on storage failure."""
        if not token:
            raise ValueError(f"Refusing to store empty token for '{token_type}'")

        if len(token) <= MAX_DIRECT_LENGTH:
            keyring.set_password(SERVICE_NAME, token_type, token)
            logger.debug("Stored '%s' directly in Credential Manager", token_type)
        else:
            # Token too long for direct WCM storage — encrypt it
            if not _FERNET_AVAILABLE:
                raise RuntimeError(
                    f"Token '{token_type}' is {len(token)} chars (exceeds 400-char WCM limit) "
                    "and the 'cryptography' package is not installed. "
                    "Run: pip install cryptography"
                )
            key = Fernet.generate_key()
            encrypted = Fernet(key).encrypt(token.encode())
            keyring.set_password(
                SERVICE_NAME,
                f"{token_type}_key",
                base64.b64encode(key).decode(),
            )
            token_file = self._token_file(token_type)
            token_file.write_bytes(encrypted)
            logger.debug(
                "Stored '%s' as encrypted file (token length %d)", token_type, len(token)
            )

    def retrieve(self, token_type: str) -> str | None:
        """Return stored token or None if not found."""
        # Try direct WCM storage first
        try:
            direct = keyring.get_password(SERVICE_NAME, token_type)
            if direct:
                return direct
        except Exception as e:
            logger.warning("Keyring read error for '%s': %s", token_type, e)

        # Try encrypted file storage
        try:
            key_b64 = keyring.get_password(SERVICE_NAME, f"{token_type}_key")
            if not key_b64:
                return None
            token_file = self._token_file(token_type)
            if not token_file.exists():
                return None
            if not _FERNET_AVAILABLE:
                logger.error("Cannot decrypt token '%s': cryptography package not installed", token_type)
                return None
            key = base64.b64decode(key_b64)
            return Fernet(key).decrypt(token_file.read_bytes()).decode()
        except Exception as e:
            if "InvalidToken" in type(e).__name__:
                logger.error("Encrypted token file for '%s' is corrupted", token_type)
            else:
                logger.warning("Failed to retrieve encrypted token '%s': %s", token_type, e)
            return None

    def delete(self, token_type: str) -> None:
        """Remove token from storage (used during sign-out)."""
        for key_name in [token_type, f"{token_type}_key"]:
            try:
                keyring.delete_password(SERVICE_NAME, key_name)
            except keyring.errors.PasswordDeleteError:
                pass
            except Exception as e:
                logger.warning("Error deleting keyring entry '%s': %s", key_name, e)

        self._token_file(token_type).unlink(missing_ok=True)
        logger.info("Deleted token '%s'", token_type)

    def has_tokens(self) -> bool:
        """Return True if valid access and refresh tokens exist."""
        return (
            self.retrieve(TOKEN_ACCESS) is not None
            and self.retrieve(TOKEN_REFRESH) is not None
        )

    def clear_all(self) -> None:
        """Remove all stored tokens (full sign-out)."""
        for token_type in [TOKEN_ACCESS, TOKEN_REFRESH, TOKEN_CLIENT_ID, TOKEN_CLIENT_SECRET, TOKEN_CHANNEL]:
            self.delete(token_type)

    def _token_file(self, token_type: str) -> Path:
        app_data = Path(os.environ.get("APPDATA", ".")) / "TwitchIDS"
        app_data.mkdir(parents=True, exist_ok=True)
        # Prefix with dot to make less obvious in file browser
        return app_data / f".{token_type}.enc"


# Module-level singleton
token_store = SecureTokenStore()
