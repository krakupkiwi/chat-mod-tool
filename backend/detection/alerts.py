"""
Alert persistence and WebSocket push.

write_flagged_user() writes a row to the flagged_users table with
status='active'. The caller is responsible for pushing the threat_alert
event via WebSocket.
"""

from __future__ import annotations

import json
import logging
import time

import aiosqlite

logger = logging.getLogger(__name__)

_INSERT_SQL = """
INSERT INTO flagged_users (flagged_at, user_id, username, channel, threat_score, signals, status)
VALUES (?, ?, ?, ?, ?, ?, 'active')
"""


async def write_flagged_user(
    db_path: str,
    user_id: str,
    username: str,
    channel: str,
    threat_score: float,
    signals: list[str],
) -> int | None:
    """
    Insert a flagged_users row. Returns the new row ID, or None on failure.
    """
    try:
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute(
                _INSERT_SQL,
                (
                    time.time(),
                    user_id,
                    username,
                    channel,
                    round(threat_score, 2),
                    json.dumps(signals),
                ),
            )
            await db.commit()
            return cursor.lastrowid
    except Exception:
        logger.exception("Failed to write flagged user %s", user_id)
        return None


def build_threat_alert_event(
    alert_id: str,
    user_id: str,
    username: str,
    threat_score: float,
    signals: list[str],
    channel: str,
    explanation: list[dict] | None = None,
) -> dict:
    """Build the WebSocket payload for a threat_alert event."""
    severity = _score_to_severity(threat_score)
    return {
        "type": "threat_alert",
        "alert_id": alert_id,
        "severity": severity,
        "signal": signals[0] if signals else "unknown",
        "description": _describe(signals, threat_score),
        "affected_users": [username],
        "user_id": user_id,
        "username": username,
        "channel": channel,
        "confidence": round(threat_score, 1),
        "timestamp": time.time(),
        "explanation": explanation or [],
    }


def _score_to_severity(score: float) -> str:
    if score >= 85:
        return "critical"
    if score >= 70:
        return "high"
    if score >= 50:
        return "medium"
    return "low"


_SIGNAL_PRETTY: dict[str, str] = {
    "temporal_sync":     "coordinated timing",
    "minhash_cluster":   "near-duplicate messages",
    "rate_anomaly":      "machine-speed messaging",
    "burst_anomaly":     "volume spike",
    "duplicate_ratio":   "duplicate flood",
    "username_entropy":  "bot-pattern username",
    "new_account":       "new account",
    "known_bot":         "known bot account",
    "pattern_match":     "spam pattern matched",
    "timing_regularity": "machine-regular timing",
}


def _describe(signals: list[str], score: float) -> str:
    if not signals:
        return f"Threat score {score:.0f}"
    parts = [_SIGNAL_PRETTY.get(s, s) for s in signals[:3]]
    return f"Flagged for {', '.join(parts)} (score {score:.0f})"
