"""Pydantic request/response schemas for all API endpoints."""

from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field, model_validator


# --- Health ---

class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "0.1.0"
    connected: bool = False
    channel: str | None = None
    dry_run: bool = True


# --- Auth ---

class AuthStatusResponse(BaseModel):
    authenticated: bool
    username: str | None = None
    client_id_configured: bool


class AuthInitRequest(BaseModel):
    client_id: str = Field(..., min_length=1)
    client_secret: str = Field(..., min_length=1)


class AuthInitResponse(BaseModel):
    status: str  # 'started' | 'already_authenticated'
    message: str


# --- Channel ---

class ChannelConfig(BaseModel):
    name: str = Field(..., min_length=1, max_length=25)


class ChannelResponse(BaseModel):
    name: str
    connected: bool


# --- Config ---

class AppConfig(BaseModel):
    dry_run: bool
    auto_timeout_enabled: bool
    auto_ban_enabled: bool
    timeout_threshold: float
    ban_threshold: float
    alert_threshold: float
    default_channel: str
    message_retention_days: int
    health_history_retention_days: int
    flagged_users_retention_days: int
    moderation_actions_retention_days: int


class UpdateConfigRequest(BaseModel):
    dry_run: bool | None = None
    auto_timeout_enabled: bool | None = None
    auto_ban_enabled: bool | None = None
    timeout_threshold: float | None = Field(default=None, ge=30.0, le=100.0)
    ban_threshold: float | None = Field(default=None, ge=50.0, le=100.0)
    alert_threshold: float | None = Field(default=None, ge=20.0, le=100.0)
    default_channel: str | None = Field(
        default=None,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_]*$",
    )
    message_retention_days: int | None = Field(default=None, ge=1, le=365)
    health_history_retention_days: int | None = Field(default=None, ge=1, le=365)
    flagged_users_retention_days: int | None = Field(default=None, ge=0, le=3650)
    moderation_actions_retention_days: int | None = Field(default=None, ge=0, le=3650)

    @model_validator(mode="after")
    def thresholds_must_be_ordered(self) -> "UpdateConfigRequest":
        """Enforce alert <= timeout <= ban so the pipeline remains coherent."""
        from core.config import settings

        alert = self.alert_threshold if self.alert_threshold is not None else settings.alert_threshold
        timeout = self.timeout_threshold if self.timeout_threshold is not None else settings.timeout_threshold
        ban = self.ban_threshold if self.ban_threshold is not None else settings.ban_threshold

        if not (alert <= timeout <= ban):
            raise ValueError(
                f"Thresholds must satisfy alert ({alert}) <= timeout ({timeout}) <= ban ({ban})"
            )
        return self


# --- WebSocket events (outbound) ---

class WSEvent(BaseModel):
    type: str
    ts: float

    model_config = {"extra": "allow"}


class ChatMessageEvent(WSEvent):
    type: str = "chat_message"
    user_id: str
    username: str
    content: str
    channel: str
    threat_score: float = 0.0
    flags: list[str] = []


class ConnectionStatusEvent(WSEvent):
    type: str = "connection_status"
    connected: bool
    channel: str | None = None
    reason: str | None = None
