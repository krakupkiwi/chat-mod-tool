"""Registers all API routers onto the FastAPI app."""

from __future__ import annotations

from fastapi import FastAPI

from .routes.app_profile import router as app_profile_router
from .routes.automod import router as automod_router
from .routes.banlist import router as banlist_router
from .routes.channels import router as channels_router
from .routes.followers import router as followers_router
from .routes.nuke import router as nuke_router
from .routes.profiles import router as profiles_router
from .routes.regex_filters import router as regex_filters_router
from .routes.chat import router as chat_router
from .routes.config import router as config_router
from .routes.history import router as history_router
from .routes.moderation import router as moderation_router
from .routes.reputation import router as reputation_router
from .routes.simulator import router as simulator_router
from .routes.stats import router as stats_router
from .routes.users import router as users_router
from .routes.unban_requests import router as unban_requests_router
from .routes.watchlist_users import router as watchlist_users_router
from .routes.whitelist import router as whitelist_router


def register_routes(app: FastAPI) -> None:
    app.include_router(app_profile_router, prefix="/api")
    app.include_router(automod_router, prefix="/api")
    app.include_router(banlist_router, prefix="/api")
    app.include_router(channels_router, prefix="/api")
    app.include_router(chat_router, prefix="/api")
    app.include_router(config_router, prefix="/api")
    app.include_router(history_router, prefix="/api")
    app.include_router(moderation_router, prefix="/api")
    app.include_router(reputation_router, prefix="/api")
    app.include_router(simulator_router, prefix="/api")
    app.include_router(stats_router, prefix="/api")
    app.include_router(users_router, prefix="/api")
    app.include_router(followers_router, prefix="/api")
    app.include_router(nuke_router, prefix="/api")
    app.include_router(profiles_router, prefix="/api")
    app.include_router(regex_filters_router, prefix="/api")
    app.include_router(unban_requests_router, prefix="/api")
    app.include_router(watchlist_users_router, prefix="/api")
    app.include_router(whitelist_router, prefix="/api")
