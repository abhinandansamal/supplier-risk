"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.health import DEFAULT_TIMEOUT_SECONDS, ReadinessRegistry
from app.api.health import router as health_router
from app.api.middleware import BodyLimitMiddleware, RequestContextMiddleware
from app.core.errors import install_exception_handlers
from app.core.settings import Settings, load_settings
from app.tools.cache import SnapshotCache


def create_app(
    settings: Settings | None = None,
    readiness_timeout: float = DEFAULT_TIMEOUT_SECONDS,
    snapshot_cache: SnapshotCache | None = None,
) -> FastAPI:
    """Build the FastAPI app.

    Args:
        settings: Settings to use; loaded from the environment when omitted.
        readiness_timeout: Seconds a readiness check may take.
        snapshot_cache: Zetaris snapshot cache; warmed in the background at startup (the app
            serves at once) and closed at shutdown. None means no warm-up (tests).

    Returns:
        The configured app. /docs, /redoc and /openapi.json exist only when APP_ENV=dev.
    """
    settings = settings or load_settings()
    dev = settings.app_env == "dev"

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if snapshot_cache is not None:
            snapshot_cache.start_warmup()
        yield
        if snapshot_cache is not None:
            await snapshot_cache.aclose()

    app = FastAPI(
        title="Supplier Risk Investigator",
        lifespan=lifespan,
        docs_url="/docs" if dev else None,
        redoc_url="/redoc" if dev else None,
        openapi_url="/openapi.json" if dev else None,
    )
    app.state.settings = settings
    app.state.snapshot_cache = snapshot_cache
    app.state.readiness = ReadinessRegistry(timeout=readiness_timeout)
    install_exception_handlers(app)
    app.include_router(health_router)
    # The last middleware added is the outermost: request context wraps the body limit.
    app.add_middleware(BodyLimitMiddleware)
    app.add_middleware(RequestContextMiddleware)
    return app
