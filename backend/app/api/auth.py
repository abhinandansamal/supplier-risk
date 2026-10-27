"""Demo login: POST /auth/login, POST /auth/logout, GET /auth/me.

The session is an HS256 token in an httpOnly, SameSite=Strict cookie that lasts 30 minutes.
The token is never in a response body, so page scripts cannot read it.
"""

import asyncio
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.session import COOKIE_NAME, check_origin, current_session
from app.core.errors import AppError
from app.security.auth import SESSION_SECONDS, LoginThrottle, Session, SessionTokens, UserDirectory
from app.security.identity import UserContext

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", dependencies=[Depends(check_origin)])

UNKNOWN_USER_BUCKET = "<unknown>"


class LoginBody(BaseModel):
    """Login form."""

    model_config = ConfigDict(extra="forbid")

    username: Annotated[str, Field(min_length=1, max_length=32, pattern=r"^[a-z_]+$")]
    password: Annotated[str, Field(min_length=1, max_length=256)]


def _identity(user: UserContext, expires_at: int | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "username": user.user_id,
        "role": user.role,
        "category": user.category,
    }
    if expires_at is not None:
        body["expires_at"] = expires_at
    return body


@router.post("/login")
async def login(body: LoginBody, request: Request, response: Response) -> dict[str, Any]:
    """Check the password and set the session cookie.

    Raises:
        AppError: 401 for a wrong username or password (one message for both), 429 after
            repeated failures.
    """
    throttle: LoginThrottle = request.app.state.login_throttle
    directory: UserDirectory = request.app.state.directory
    tokens: SessionTokens = request.app.state.tokens
    known = directory.get(body.username) is not None
    # Security (SEC-101): unknown names share one bucket, so they cannot evict a real user's
    # failures from the throttle.
    bucket = body.username if known else UNKNOWN_USER_BUCKET
    if throttle.locked(bucket):
        logger.warning("login locked", extra={"event": "login_locked"})
        raise AppError(429, "too_many_attempts", "Too many failed logins. Try again later.")
    # Security (SEC-100): count the attempt before the check, so parallel requests cannot all
    # pass the lock while the first checks are still running; a success clears it again.
    throttle.failed(bucket)
    # scrypt takes tens of milliseconds; keep it off the event loop.
    user = await asyncio.to_thread(directory.authenticate, body.username, body.password)
    if user is None:
        logger.warning("login failed", extra={"event": "login_failed", "known_user": known})
        raise AppError(401, "invalid_credentials", "Wrong username or password.")
    throttle.reset(bucket)
    token, expires_at = tokens.issue(user.username)
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_SECONDS,
        httponly=True,
        samesite="strict",
        secure=request.app.state.settings.session_cookie_secure,
        path="/",
    )
    logger.info("login succeeded", extra={"event": "login_succeeded", "role": user.role})
    return _identity(user.context, expires_at)


@router.post("/logout", status_code=204)
async def logout(
    request: Request, session: Annotated[tuple[Session, UserContext], Depends(current_session)]
) -> Response:
    """Revoke the session token and clear the cookie."""
    tokens: SessionTokens = request.app.state.tokens
    tokens.revoke(session[0])
    response = Response(status_code=204)
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="strict")
    logger.info("logout", extra={"event": "logout", "role": session[1].role})
    return response


@router.get("/me")
async def me(
    session: Annotated[tuple[Session, UserContext], Depends(current_session)],
) -> dict[str, Any]:
    """Return the logged-in user and when the session ends."""
    return _identity(session[1], session[0].expires_at)
