"""Liveness (/health) and readiness (/ready) endpoints plus the readiness check registry."""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

ReadinessCheck = Callable[[], Awaitable[bool]]
DEFAULT_TIMEOUT_SECONDS = 5.0

router = APIRouter()


class ReadinessRegistry:
    """Named async checks that decide whether the app can serve (the model is never one)."""

    def __init__(self, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        """Create an empty registry.

        Args:
            timeout: Seconds a single check may take before it counts as failed.
        """
        self.timeout = timeout
        self._checks: dict[str, ReadinessCheck] = {}

    def register(self, name: str, check: ReadinessCheck) -> None:
        """Add or replace a check."""
        self._checks[name] = check

    @property
    def names(self) -> list[str]:
        """Return the registered check names in registration order."""
        return list(self._checks)

    async def run(self) -> dict[str, bool]:
        """Run all checks concurrently and return name -> passed."""
        results = await asyncio.gather(*(self._run_one(n, c) for n, c in self._checks.items()))
        return dict(zip(self._checks, results, strict=True))

    async def _run_one(self, name: str, check: ReadinessCheck) -> bool:
        try:
            return bool(await asyncio.wait_for(check(), timeout=self.timeout))
        except Exception as exc:
            # Only the error type is logged: messages may contain connection strings.
            logger.warning(
                "readiness check failed",
                extra={
                    "event": "readiness_check_failed",
                    "check": name,
                    "error_type": type(exc).__name__,
                },
            )
            return False


def register_readiness_check(app: FastAPI, name: str, check: ReadinessCheck) -> None:
    """Register a readiness check on the app (Zetaris, DB, context service later)."""
    app.state.readiness.register(name, check)


@router.get("/health")
async def health() -> dict[str, str]:
    """Return ok while the process is alive."""
    return {"status": "ok"}


@router.get("/ready", response_model=None)
async def ready(request: Request) -> JSONResponse:
    """Return 200 when every registered check passes, otherwise 503 naming the failed checks."""
    registry: ReadinessRegistry = request.app.state.readiness
    results = await registry.run()
    failed = [name for name, passed in results.items() if not passed]
    if failed:
        return JSONResponse(status_code=503, content={"status": "not_ready", "failed": failed})
    return JSONResponse(content={"status": "ready", "checked": registry.names})
