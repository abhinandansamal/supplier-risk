"""Pure ASGI middleware: request id and access log, and the request body size limit."""

import logging
import re
import time
import uuid
from typing import Any

from fastapi import HTTPException
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import error_response
from app.core.logging import request_id_var

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "x-request-id"
MAX_BODY_BYTES = 16 * 1024
# Security: only safe characters and a short length, so an id cannot inject into logs.
_VALID_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _resolve_request_id(scope: Scope) -> str:
    for name, value in scope["headers"]:
        if name == REQUEST_ID_HEADER.encode():
            candidate = str(value.decode("latin-1"))
            if _VALID_ID.match(candidate):
                return candidate
            break
    return str(uuid.uuid4())


class RequestContextMiddleware:
    """Set the request id, add it to the response, log one access line, catch crashes."""

    def __init__(self, app: ASGIApp) -> None:
        """Wrap an ASGI app."""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Handle one ASGI call."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = _resolve_request_id(scope)
        scope.setdefault("state", {})["request_id"] = request_id
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        state: dict[str, Any] = {"status": 500, "sent": False}

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                state["status"] = message["status"]
                state["sent"] = True
                message.setdefault("headers", [])
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except Exception:
            # Details go to the log; the client only gets the generic error model.
            logger.exception("Unhandled error", extra={"event": "unhandled_exception"})
            if not state["sent"]:
                response = error_response(
                    500, "internal_error", "Internal server error.", request_id
                )
                await response(scope, receive, send_with_id)
        finally:
            logger.info(
                "request done",
                extra={
                    "event": "http_request",
                    "method": scope["method"],
                    # Path only: a query string may carry tokens.
                    "path": scope["path"],
                    "status_code": state["status"],
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
            request_id_var.reset(token)


class BodyLimitMiddleware:
    """Reject request bodies larger than max_bytes, by header and while streaming."""

    def __init__(self, app: ASGIApp, max_bytes: int = MAX_BODY_BYTES) -> None:
        """Wrap an ASGI app.

        Args:
            app: The inner ASGI app.
            max_bytes: Largest accepted body.
        """
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Handle one ASGI call."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        for name, value in scope["headers"]:
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = -1
                if declared < 0:
                    await self._reject(scope, receive, send, 400, "bad_request", "Bad request.")
                    return
                if declared > self.max_bytes:
                    await self._reject(
                        scope, receive, send, 413, "payload_too_large", "Request body too large."
                    )
                    return
        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    # HTTPException so FastAPI's body parsing re-raises it as is.
                    raise HTTPException(status_code=413)
            return message

        await self.app(scope, limited_receive, send)

    @staticmethod
    async def _reject(
        scope: Scope, receive: Receive, send: Send, status: int, code: str, message: str
    ) -> None:
        response = error_response(status, code, message, current_request_id_from(scope))
        await response(scope, receive, send)


def current_request_id_from(scope: Scope) -> str:
    """Return the request id stored in the ASGI scope, or 'unknown'."""
    state = scope.get("state") or {}
    return str(state.get("request_id", "unknown")) if isinstance(state, dict) else "unknown"
