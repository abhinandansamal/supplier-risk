"""Uvicorn entry point: uvicorn --factory --app-dir backend app.main:create (scripts/run_app.sh).

Run with exactly one worker (SEC-024): the queue, the live events and the model rate limiter
live in this process.
"""

import logging
import os
from collections.abc import Mapping

from fastapi import FastAPI

from app.agent.graph import Investigator
from app.api.app import create_app
from app.api.health import register_readiness_check
from app.core.logging import setup_logging
from app.core.settings import Settings, SettingsError, load_settings
from app.llm.client import MeterlessClient
from app.memory.world_model import WorldModelClient
from app.store.db import Store
from app.tools.cache import SnapshotCache

logger = logging.getLogger(__name__)

# Security: LangSmith tracing would send prompts and tool results to a third party.
TRACING_VARIABLES = ("LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING", "LANGCHAIN_TRACING")
_TRUE = frozenset({"1", "true", "yes", "on"})


def check_tracing_off(environ: Mapping[str, str] = os.environ) -> None:
    """Refuse to start when LangSmith/LangChain tracing is switched on.

    Raises:
        SettingsError: Naming the variable that turns tracing on.
    """
    for name in TRACING_VARIABLES:
        if environ.get(name, "").strip().lower() in _TRUE:
            raise SettingsError(f"{name} must not be true: LangSmith tracing would send data out")
    logger.info(
        "langchain tracing is off",
        extra={"event": "langchain_tracing_off", "checked": list(TRACING_VARIABLES)},
    )


def _build(settings: Settings) -> FastAPI:
    """Wire the real clients, the store and the investigator into the app."""
    try:
        store = Store(settings.app_database_url.get_secret_value())
    except ValueError as exc:
        raise SettingsError(f"Invalid or missing settings: APP_DATABASE_URL ({exc})") from None
    cache = SnapshotCache(settings)
    llm = MeterlessClient(settings)
    board = WorldModelClient(settings) if settings.context_service_token else None
    investigator = Investigator(
        settings, llm=llm, cache=cache, board_client=board, trace_dir=settings.trace_dir
    )

    async def close_clients() -> None:
        await llm.aclose()
        if board is not None:
            await board.aclose()
        store.close()

    app = create_app(
        settings,
        snapshot_cache=cache,
        store=store,
        investigator=investigator,
        closers=[close_clients],
    )
    if board is not None:
        register_readiness_check(app, "context_service", board.health)
    return app


def create() -> FastAPI:
    """Load settings, set up logging once and return the app with all its clients.

    Raises:
        SettingsError: If required settings are missing or invalid, or tracing is on
            (logged as CRITICAL first).
    """
    try:
        settings: Settings = load_settings()
    except SettingsError as exc:
        logger.critical(str(exc), extra={"event": "startup_failed"})
        raise
    setup_logging(settings)
    try:
        check_tracing_off()
        app = _build(settings)
    except SettingsError as exc:
        logger.critical(str(exc), extra={"event": "startup_failed"})
        raise
    logger.info("application starting", extra={"event": "startup", "app_env": settings.app_env})
    return app
