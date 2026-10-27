"""FastAPI application factory."""

from fastapi import FastAPI

from app.api.health import DEFAULT_TIMEOUT_SECONDS, ReadinessRegistry
from app.api.health import router as health_router
from app.api.middleware import BodyLimitMiddleware, RequestContextMiddleware
from app.core.errors import install_exception_handlers
from app.core.settings import Settings, load_settings


def create_app(
    settings: Settings | None = None, readiness_timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> FastAPI:
    """Build the FastAPI app.

    Args:
        settings: Settings to use; loaded from the environment when omitted.
        readiness_timeout: Seconds a readiness check may take.

    Returns:
        The configured app. /docs, /redoc and /openapi.json exist only when APP_ENV=dev.
    """
    settings = settings or load_settings()
    dev = settings.app_env == "dev"
    app = FastAPI(
        title="Supplier Risk Investigator",
        docs_url="/docs" if dev else None,
        redoc_url="/redoc" if dev else None,
        openapi_url="/openapi.json" if dev else None,
    )
    app.state.settings = settings
    app.state.readiness = ReadinessRegistry(timeout=readiness_timeout)
    install_exception_handlers(app)
    app.include_router(health_router)
    # The last middleware added is the outermost: request context wraps the body limit.
    app.add_middleware(BodyLimitMiddleware)
    app.add_middleware(RequestContextMiddleware)
    return app
