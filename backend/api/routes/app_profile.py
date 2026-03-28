"""
App-level profile export endpoint.

This is distinct from /api/profiles, which manages lockdown (chat-mode) profiles.
This endpoint exports the entire application profile (DB + config) as a .tidsprofile
archive that can be imported on another machine or shared with a co-moderator.

Export format
─────────────
Unencrypted:  Standard ZIP renamed to .tidsprofile containing:
  manifest.json   – metadata (version, timestamp, profile name)
  meta.json       – {id, name, created_at} — pwd_hash excluded
  data.db         – consistent SQLite snapshot via VACUUM INTO
  config.json     – all settings; default_channel blanked for privacy

Encrypted:    The ZIP bytes are wrapped in a binary envelope:
  Offset  Len  Field
       0    4  Magic: ASCII "TIDS"
       4    1  Version: 0x01
       5    1  Flags: 0x01 = AES-256-GCM password-encrypted
       6    4  Payload length (little-endian uint32)
      10   16  PBKDF2-SHA256 salt (random)
      26   12  AES-256-GCM nonce (random)
      38   16  GCM authentication tag
      54    N  AES-256-GCM ciphertext (encrypted ZIP bytes)

  AAD (authenticated but not encrypted) = bytes 0–53 (the header),
  which binds the header to the ciphertext and prevents tampering.
  Key derivation: PBKDF2-HMAC-SHA256(password, salt, 480_000, 32).
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import struct
import tempfile
import time
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

_MAGIC = b"TIDS"
_VERSION = 1
_HEADER_SIZE = 54  # magic(4) + ver(1) + flags(1) + payload_len(4) + salt(16) + nonce(12) + tag(16)


class ExportRequest(BaseModel):
    dest_path: str
    export_password: str | None = None


@router.post("/app-profile/export")
async def export_app_profile(request: ExportRequest) -> dict:
    """
    Create a .tidsprofile archive of the currently active profile and write it
    to dest_path.  If export_password is provided the archive is AES-256-GCM
    encrypted.

    Tokens (OAuth credentials) are intentionally excluded — the recipient must
    authenticate independently after importing the profile.
    """
    if not settings.profile_dir:
        raise HTTPException(
            status_code=400,
            detail="No active profile — app-profile export requires a profile to be loaded",
        )

    dest = Path(request.dest_path)
    if not dest.parent.exists():
        raise HTTPException(status_code=400, detail=f"Destination directory does not exist: {dest.parent}")

    # Validate destination extension
    if dest.suffix.lower() not in (".tidsprofile", ".zip"):
        dest = dest.with_suffix(".tidsprofile")

    # Build meta.json content (strip pwd_hash for privacy)
    meta_path = Path(settings.profile_dir) / "meta.json"
    if meta_path.exists():
        with open(meta_path) as f:
            meta = json.load(f)
        meta.pop("pwd_hash", None)   # never export the local password hash
        meta.pop("pwd_hash_b64", None)
    else:
        meta = {"id": settings.profile_id or "", "name": "Exported Profile"}

    # Build config.json (blank default_channel for recipient privacy)
    config_data = {
        "dry_run": settings.dry_run,
        "auto_timeout_enabled": settings.auto_timeout_enabled,
        "auto_ban_enabled": settings.auto_ban_enabled,
        "timeout_threshold": settings.timeout_threshold,
        "ban_threshold": settings.ban_threshold,
        "alert_threshold": settings.alert_threshold,
        "emote_filter_sensitivity": settings.emote_filter_sensitivity,
        "default_channel": "",   # intentionally blank — recipient sets their own channel
        "message_retention_days": settings.message_retention_days,
        "health_history_retention_days": settings.health_history_retention_days,
        "flagged_users_retention_days": settings.flagged_users_retention_days,
        "moderation_actions_retention_days": settings.moderation_actions_retention_days,
    }

    manifest = {
        "format_version": 1,
        "exported_at": int(time.time()),
        "profile_name": meta.get("name", "Exported Profile"),
        "encrypted": bool(request.export_password),
    }

    try:
        zip_bytes = await _build_zip(meta, config_data, manifest)
    except Exception as e:
        logger.exception("Failed to build export ZIP")
        raise HTTPException(status_code=500, detail=f"Export failed: {e}") from e

    if request.export_password:
        try:
            final_bytes = _encrypt(zip_bytes, request.export_password)
        except Exception as e:
            logger.exception("Failed to encrypt export archive")
            raise HTTPException(status_code=500, detail=f"Encryption failed: {e}") from e
    else:
        final_bytes = zip_bytes

    try:
        dest.write_bytes(final_bytes)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Could not write to {dest}: {e}") from e

    logger.info(
        "Profile exported to %s (%d bytes, encrypted=%s)",
        dest, len(final_bytes), bool(request.export_password),
    )
    return {"success": True, "path": str(dest), "size": len(final_bytes)}


async def _build_zip(meta: dict, config_data: dict, manifest: dict) -> bytes:
    """Build the ZIP archive in memory and return the raw bytes."""
    with tempfile.TemporaryDirectory() as tmp:
        db_snapshot = os.path.join(tmp, "data.db")
        # VACUUM INTO produces a consistent read-consistent snapshot regardless of WAL state
        import aiosqlite
        async with aiosqlite.connect(settings.db_path) as db:
            await db.execute(f"VACUUM INTO '{db_snapshot}'")

        import io
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", json.dumps(manifest, indent=2))
            zf.writestr("meta.json", json.dumps(meta, indent=2))
            zf.write(db_snapshot, "data.db")
            zf.writestr("config.json", json.dumps(config_data, indent=2))
        return buf.getvalue()


def _encrypt(zip_bytes: bytes, password: str) -> bytes:
    """Encrypt zip_bytes with AES-256-GCM and return the binary envelope."""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        from cryptography.hazmat.primitives import hashes
    except ImportError as e:
        raise RuntimeError(
            "The 'cryptography' package is required for encrypted exports. "
            "Run: pip install cryptography"
        ) from e

    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(12)

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=480_000,
    )
    key = kdf.derive(password.encode())

    aesgcm = AESGCM(key)

    # Build the header first (used as AAD)
    flags = 0x01  # password-encrypted
    payload_len = 0  # placeholder — filled in below
    header_prefix = (
        _MAGIC
        + struct.pack("<B", _VERSION)
        + struct.pack("<B", flags)
        + struct.pack("<I", payload_len)  # 4 bytes LE uint32
        + salt
        + nonce
    )
    # Use the full header (including placeholder tag space) as AAD so that
    # the ciphertext is bound to the header structure.
    # The GCM tag is appended by AESGCM.encrypt() at the end of the ciphertext.
    aad = header_prefix + b"\x00" * 16  # 16-byte tag placeholder as AAD

    ciphertext_with_tag = aesgcm.encrypt(nonce, zip_bytes, aad)
    # cryptography appends the 16-byte GCM tag at the end
    tag = ciphertext_with_tag[-16:]
    ciphertext = ciphertext_with_tag[:-16]

    # Rebuild header with correct payload length and real tag
    header = (
        _MAGIC
        + struct.pack("<B", _VERSION)
        + struct.pack("<B", flags)
        + struct.pack("<I", len(ciphertext))
        + salt
        + nonce
        + tag
    )
    assert len(header) == _HEADER_SIZE

    return header + ciphertext
