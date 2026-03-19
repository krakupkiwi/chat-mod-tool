"""
SQLite database initialization.

Schema uses WAL mode for concurrent read/write without blocking.
All table creation is idempotent (IF NOT EXISTS).
"""

from __future__ import annotations

import logging

import aiosqlite

logger = logging.getLogger(__name__)

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;
PRAGMA temp_store=MEMORY;

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at     REAL    NOT NULL,
    channel         TEXT    NOT NULL,
    user_id         TEXT    NOT NULL,
    username        TEXT    NOT NULL,
    raw_text        TEXT    NOT NULL,
    normalized_text TEXT    NOT NULL,
    content_hash    TEXT    NOT NULL,
    emoji_count     INTEGER NOT NULL DEFAULT 0,
    url_count       INTEGER NOT NULL DEFAULT 0,
    mention_count   INTEGER NOT NULL DEFAULT 0,
    word_count      INTEGER NOT NULL DEFAULT 0,
    char_count      INTEGER NOT NULL DEFAULT 0,
    caps_ratio      REAL    NOT NULL DEFAULT 0.0,
    has_url         INTEGER NOT NULL DEFAULT 0,
    color           TEXT,
    is_subscriber   INTEGER NOT NULL DEFAULT 0,
    is_moderator    INTEGER NOT NULL DEFAULT 0,
    is_vip          INTEGER NOT NULL DEFAULT 0,
    account_age_days INTEGER
);

CREATE INDEX IF NOT EXISTS idx_messages_received_at  ON messages(received_at);
CREATE INDEX IF NOT EXISTS idx_messages_user_id      ON messages(user_id);
CREATE INDEX IF NOT EXISTS idx_messages_content_hash ON messages(content_hash);
CREATE INDEX IF NOT EXISTS idx_messages_channel      ON messages(channel);

CREATE TABLE IF NOT EXISTS flagged_users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    flagged_at      REAL    NOT NULL,
    user_id         TEXT    NOT NULL,
    username        TEXT    NOT NULL,
    channel         TEXT    NOT NULL,
    threat_score    REAL    NOT NULL,
    signals         TEXT    NOT NULL,  -- JSON array of signal names
    status          TEXT    NOT NULL DEFAULT 'active'  -- active | resolved | false_positive
);

CREATE INDEX IF NOT EXISTS idx_flagged_users_user_id   ON flagged_users(user_id);
CREATE INDEX IF NOT EXISTS idx_flagged_users_flagged_at ON flagged_users(flagged_at);

CREATE TABLE IF NOT EXISTS moderation_actions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      REAL    NOT NULL,
    completed_at    REAL,
    user_id         TEXT    NOT NULL,
    username        TEXT    NOT NULL,
    channel         TEXT    NOT NULL,
    action_type     TEXT    NOT NULL,  -- ban | timeout | delete | slow_mode | followers_only
    duration_seconds INTEGER,          -- for timeout actions
    reason          TEXT,
    status          TEXT    NOT NULL DEFAULT 'pending',  -- pending | completed | failed | undone
    triggered_by    TEXT    NOT NULL DEFAULT 'manual',   -- manual | auto:<signal>
    confidence      REAL,
    error_message   TEXT
);

CREATE INDEX IF NOT EXISTS idx_moderation_actions_status    ON moderation_actions(status);
CREATE INDEX IF NOT EXISTS idx_moderation_actions_user_id   ON moderation_actions(user_id);
CREATE INDEX IF NOT EXISTS idx_moderation_actions_created_at ON moderation_actions(created_at);

CREATE TABLE IF NOT EXISTS health_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at     REAL    NOT NULL,
    channel         TEXT    NOT NULL,
    health_score    REAL    NOT NULL,
    msg_per_min     REAL    NOT NULL DEFAULT 0,
    active_users    INTEGER NOT NULL DEFAULT 0,
    duplicate_ratio REAL    NOT NULL DEFAULT 0,
    sync_score      REAL    NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_health_history_recorded_at ON health_history(recorded_at);

CREATE TABLE IF NOT EXISTS user_reputation (
    user_id         TEXT    PRIMARY KEY,
    username        TEXT    NOT NULL,
    reputation      REAL    NOT NULL DEFAULT 100.0,
    total_flags     INTEGER NOT NULL DEFAULT 0,
    total_actions   INTEGER NOT NULL DEFAULT 0,
    false_positives INTEGER NOT NULL DEFAULT 0,
    last_seen       REAL    NOT NULL,
    updated_at      REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_user_reputation_reputation ON user_reputation(reputation);

CREATE TABLE IF NOT EXISTS whitelist (
    username        TEXT    PRIMARY KEY,   -- lowercase username or user_id
    added_at        REAL    NOT NULL,
    note            TEXT    NOT NULL DEFAULT ''  -- optional label (e.g. "streamer's friend")
);

CREATE TABLE IF NOT EXISTS user_watchlist (
    user_id         TEXT    PRIMARY KEY,
    username        TEXT    NOT NULL,
    added_at        REAL    NOT NULL,
    note            TEXT    NOT NULL DEFAULT '',
    priority        TEXT    NOT NULL DEFAULT 'normal'  -- normal | high
);

CREATE INDEX IF NOT EXISTS idx_user_watchlist_added_at ON user_watchlist(added_at);

CREATE TABLE IF NOT EXISTS unban_decisions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    unban_request_id    TEXT    NOT NULL UNIQUE,
    user_id             TEXT    NOT NULL,
    username            TEXT    NOT NULL,
    request_text        TEXT    NOT NULL DEFAULT '',
    decision            TEXT    NOT NULL,   -- approved | denied
    resolution_text     TEXT    NOT NULL DEFAULT '',
    decided_at          REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_unban_decisions_user_id ON unban_decisions(user_id);
CREATE INDEX IF NOT EXISTS idx_unban_decisions_decided_at ON unban_decisions(decided_at);

CREATE TABLE IF NOT EXISTS regex_filters (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern         TEXT    NOT NULL,
    flags           TEXT    NOT NULL DEFAULT 'i',    -- regex flags: i=case-insensitive
    action_type     TEXT    NOT NULL DEFAULT 'delete',  -- delete | timeout | flag
    duration_seconds INTEGER,
    note            TEXT    NOT NULL DEFAULT '',
    enabled         INTEGER NOT NULL DEFAULT 1,
    created_at      REAL    NOT NULL,
    match_count     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS monitored_channels (
    name            TEXT    PRIMARY KEY,   -- lowercase channel name
    broadcaster_id  TEXT    NOT NULL DEFAULT '',
    added_at        REAL    NOT NULL,
    note            TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS lockdown_profiles (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    name                    TEXT    NOT NULL,
    created_at              REAL    NOT NULL,
    auto_on_raid            INTEGER NOT NULL DEFAULT 0,  -- 1 = apply automatically on raid
    -- Per-mode: NULL=don't touch, 1=enable, 0=disable
    emote_only              INTEGER,
    sub_only                INTEGER,
    unique_chat             INTEGER,
    slow_mode               INTEGER,
    slow_mode_wait_time     INTEGER,   -- seconds (3–120); only meaningful if slow_mode=1
    followers_only          INTEGER,
    followers_only_duration INTEGER    -- minutes (0=any follower); only if followers_only=1
);
"""


async def init_db(db_path: str) -> None:
    """
    Create all tables and indexes. Safe to call on every startup —
    all statements use IF NOT EXISTS.
    """
    logger.info("Initializing SQLite database at %s", db_path)
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(SCHEMA)
        await db.commit()
    logger.info("Database initialized")
