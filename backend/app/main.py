"""Uvicorn entry point: uvicorn --factory --app-dir backend app.main:create."""

import logging

from fastapi import FastAPI

from app.api.app import create_app
from app.core.logging import setup_logging
from app.core.settings import Settings, SettingsError, load_settings

logger = logging.getLogger(__name__)


def create() -> FastAPI:
    """Load settings, set up logging once and return the app.

    Raises:
        SettingsError: If required settings are missing (logged as CRITICAL first).
    """
    try:
        settings: Settings = load_settings()
    except SettingsError as exc:
        logger.critical(str(exc), extra={"event": "startup_failed"})
        raise
    setup_logging(settings)
    logger.info("application starting", extra={"event": "startup", "app_env": settings.app_env})
    return create_app(settings)
