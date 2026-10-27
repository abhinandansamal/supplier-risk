"""One error model {code, message, request_id} and the handlers that produce it.

Security: handlers never echo exception text, upstream bodies or input values to clients.
"""

import logging
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import request_id_var

logger = logging.getLogger(__name__)

_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    422: "validation_error",
    429: "rate_limited",
}


class ErrorResponse(BaseModel):
    """The only error shape clients ever see."""

    code: str
    message: str
    request_id: str


class AppError(Exception):
    """An expected, client-safe error.

    Attributes:
        status_code: HTTP status to return.
        code: Stable machine-readable code.
        message: Client-safe text (written by us, never copied from upstream).
    """

    def __init__(self, status_code: int, code: str, message: str) -> None:
        """Create the error."""
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def current_request_id(request: Request | None = None) -> str:
    """Return the id of the current request, or 'unknown' outside one."""
    if request is not None:
        state = request.scope.get("state") or {}
        found = state.get("request_id") if isinstance(state, dict) else None
        if found:
            return str(found)
    return request_id_var.get() or "unknown"


def error_response(status_code: int, code: str, message: str, request_id: str) -> JSONResponse:
    """Build the JSON error response."""
    body = ErrorResponse(code=code, message=message, request_id=request_id)
    return JSONResponse(status_code=status_code, content=body.model_dump())


async def _app_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)  # noqa: S101 - narrows the type for mypy
    return error_response(exc.status_code, exc.code, exc.message, current_request_id(request))


async def _http_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)  # noqa: S101
    # The detail text may come from deeper code; use the standard phrase for the status.
    try:
        phrase = HTTPStatus(exc.status_code).phrase
    except ValueError:
        phrase = "Error"
    code = _CODES.get(exc.status_code, "http_error")
    return error_response(exc.status_code, code, f"{phrase}.", current_request_id(request))


async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)  # noqa: S101
    # Field names only (never values), capped so a hostile key cannot bloat the response.
    fields = sorted({".".join(str(p) for p in err["loc"]) for err in exc.errors()})
    message = ("Request validation failed: " + ", ".join(fields))[:200]
    return error_response(422, "validation_error", message, current_request_id(request))


def install_exception_handlers(app: FastAPI) -> None:
    """Register the handlers for expected errors on the app."""
    app.add_exception_handler(AppError, _app_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
