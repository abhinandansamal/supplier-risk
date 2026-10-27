"""Session dependencies: the verified user from the cookie, and the same-origin check.

Security (T3): the role and category come from the server-side user directory, looked up by
the signed token's subject; nothing in a request body or the chat can change them.
"""

from urllib.parse import urlsplit

from fastapi import Request

from app.core.errors import AppError
from app.security.auth import InvalidSession, Session, SessionTokens, UserDirectory
from app.security.identity import UserContext

COOKIE_NAME = "sr_session"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def check_origin(request: Request) -> None:
    """Refuse a state-changing request from another site (defence in depth to SameSite).

    Raises:
        AppError: 403 when Origin is present and names another host.
    """
    if request.method in SAFE_METHODS:
        return
    origin = request.headers.get("origin")
    if origin is None:
        return
    if urlsplit(origin).netloc != request.headers.get("host"):
        raise AppError(403, "forbidden", "Cross-site requests are not allowed.")


def current_session(request: Request) -> tuple[Session, UserContext]:
    """Return the verified session and identity.

    Raises:
        AppError: 401 without a valid session for a known user.
    """
    tokens: SessionTokens = request.app.state.tokens
    directory: UserDirectory = request.app.state.directory
    try:
        session = tokens.verify(request.cookies.get(COOKIE_NAME, ""))
    except InvalidSession:
        raise AppError(401, "unauthorized", "Please log in.") from None
    user = directory.get(session.subject)
    if user is None:
        raise AppError(401, "unauthorized", "Please log in.")
    return session, user.context


def current_user(request: Request) -> UserContext:
    """Return the verified identity of the caller (see current_session)."""
    return current_session(request)[1]
