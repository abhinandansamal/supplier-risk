"""FastAPI application factory."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import auth, investigations, web
from app.api.health import DEFAULT_TIMEOUT_SECONDS, ReadinessRegistry
from app.api.health import router as health_router
from app.api.middleware import (
    BodyLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from app.core.errors import install_exception_handlers
from app.core.settings import Settings, SettingsError, load_settings
from app.security.auth import LoginThrottle, SessionTokens, UserDirectory
from app.services.investigations import InvestigationService, InvestigatorPort
from app.store.db import Store
from app.tools.cache import SnapshotCache

API_PREFIX = "/api/v1"


def create_app(  # noqa: PLR0913 - the factory wires every long-lived dependency
    settings: Settings | None = None,
    readiness_timeout: float = DEFAULT_TIMEOUT_SECONDS,
    snapshot_cache: SnapshotCache | None = None,
    *,
    store: Store | None = None,
    investigator: InvestigatorPort | None = None,
    closers: Sequence[Callable[[], Awaitable[None]]] = (),
) -> FastAPI:
    """Build the FastAPI app.

    Args:
        settings: Settings to use; loaded from the environment when omitted.
        readiness_timeout: Seconds a readiness check may take.
        snapshot_cache: Zetaris snapshot cache; warmed in the background at startup (the app
            serves at once) and closed at shutdown. None means no warm-up (tests).
        store: App database; an in-memory SQLite database when omitted (tests).
        investigator: Runs investigations in the worker; None means POST answers 503.
        closers: Async close functions of other clients, called at shutdown.

    Returns:
        The configured app. /docs, /redoc and /openapi.json exist only when APP_ENV=dev.

    Raises:
        SettingsError: If JWT_SECRET is not set.
    """
    settings = settings or load_settings()
    if settings.jwt_secret is None:
        raise SettingsError("Invalid or missing settings: JWT_SECRET")
    dev = settings.app_env == "dev"
    store = store or Store("sqlite://")
    service = InvestigationService(settings, store, investigator)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        service.start()
        if snapshot_cache is not None:
            application.state.warmup = snapshot_cache.start_warmup()
        yield
        await service.stop()
        if snapshot_cache is not None:
            await snapshot_cache.aclose()
        for close in closers:
            await close()

    app = FastAPI(
        title="Supplier Risk Investigator",
        lifespan=lifespan,
        docs_url="/docs" if dev else None,
        redoc_url="/redoc" if dev else None,
        openapi_url="/openapi.json" if dev else None,
    )
    app.state.settings = settings
    app.state.snapshot_cache = snapshot_cache
    app.state.warmup = None
    app.state.readiness = ReadinessRegistry(timeout=readiness_timeout)
    app.state.investigations = service
    app.state.directory = UserDirectory.from_settings(settings)
    app.state.tokens = SessionTokens(settings.jwt_secret.get_secret_value())
    app.state.login_throttle = LoginThrottle()

    async def app_db() -> bool:
        return await asyncio.to_thread(store.ping)

    app.state.readiness.register("app_db", app_db)
    install_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(auth.router, prefix=API_PREFIX)
    app.include_router(investigations.router, prefix=API_PREFIX)
    app.include_router(web.router)
    app.mount("/static", web.static_files(), name="static")
    # The last middleware added is the outermost: request context wraps the others.
    app.add_middleware(BodyLimitMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestContextMiddleware)
    return app
