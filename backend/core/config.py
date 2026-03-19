from __future__ import annotations

import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TWITCHIDS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Server
    host: str = "127.0.0.1"
    port: int = 7842
    dev_mode: bool = False  # Set TWITCHIDS_DEV_MODE=true to skip Electron IPC protocol
    simulator_active: bool = False  # True while a simulation is running — enables /ws/inject

    # Twitch application
    # Register at https://dev.twitch.tv/console/apps
    # The OAuth callback uses an OS-assigned port — register http://localhost
    # as a redirect URI prefix in your Twitch app (not a specific port).
    client_id: str = Field(default="", description="Twitch application Client ID")

    # Channel to monitor (can be overridden via API later)
    default_channel: str = ""

    # Moderation safety
    dry_run: bool = True  # Default ON — no automated actions until explicitly disabled
    auto_timeout_enabled: bool = False
    auto_ban_enabled: bool = False

    # Detection thresholds (can be tuned via API)
    timeout_threshold: float = 75.0    # Confidence score to trigger timeout
    ban_threshold: float = 95.0         # Confidence score to trigger ban (also requires dual signal)
    alert_threshold: float = 60.0       # Confidence score for dashboard alert

    # Data retention — 0 means keep forever
    message_retention_days: int = 7
    health_history_retention_days: int = 30
    flagged_users_retention_days: int = 0      # default: keep indefinitely
    moderation_actions_retention_days: int = 0  # default: keep indefinitely

    # Paths
    @property
    def app_data_dir(self) -> str:
        return os.path.join(os.environ.get("APPDATA", "."), "TwitchIDS")

    @property
    def db_path(self) -> str:
        return os.path.join(self.app_data_dir, "data.db")

    @property
    def log_path(self) -> str:
        return os.path.join(self.app_data_dir, "twitchids.log")

    @property
    def models_dir(self) -> str:
        return os.path.join(os.path.dirname(os.path.dirname(__file__)), "models")


# Module-level singleton — import this everywhere
settings = Settings()
