"""Zetaris REST client: the only path from the app to Zetaris (used by tools/ only).

POST {ZETARIS_REST_URL}/sql-editor/sqls/run with the API key of the caller's role. The
admin key is never used here: the client refuses to start for a role whose key is missing
or equals the admin key, so a missing setup fails loudly instead of running with full rights.

Rows come back as lists of strings without column names, so every SQL template lists its
columns. Responses are untrusted: bodies are read with a byte cap, each attempt has a hard
deadline, and errors never carry the response body, the SQL text or the key.
"""

import asyncio
import json
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr

from app.core.settings import Settings, is_local_host
from app.zetaris.errors import (
    ZetarisBadResponse,
    ZetarisConfigError,
    ZetarisError,
    ZetarisRequestError,
    ZetarisUnauthorized,
    ZetarisUnavailable,
)

logger = logging.getLogger(__name__)

ZetarisRole = Literal["buyer", "risk_manager", "director"]

RUN_PATH = "/sql-editor/sqls/run"
QUERY_TIMEOUT_SECONDS = 20.0
# Only for setup checks and integration tests: live REST views take minutes until they are
# cached (docs/devlog.md). The app keeps QUERY_TIMEOUT_SECONDS.
MAX_QUERY_TIMEOUT_SECONDS = 600.0
# Architecture 8.5: one retry on 429/502/503/504 (and timeouts), then the source is unavailable.
MAX_ATTEMPTS = 2
RETRY_DELAY_SECONDS = 1.0
RETRY_AFTER_CAP_SECONDS = 10.0
MAX_QUERY_ROWS = 1000
# Security: bounds on what we send and what we accept back.
MAX_SQL_CHARS = 20_000
MAX_RESPONSE_BYTES = 5_000_000

# Director decisions need the same data as the risk manager (docs/zetaris-setup.md); the
# auditor reads only the app database and has no Zetaris key at all.
ROLE_KEY_SETTINGS: dict[str, str] = {
    "buyer": "zetaris_key_buyer",
    "risk_manager": "zetaris_key_risk",
    "director": "zetaris_key_risk",
}
_RETRYABLE = frozenset({429, 502, 503, 504})
_UNAUTHORIZED = frozenset({401, 403})
# Security: one plain SELECT (or WITH ... SELECT); no statement separators, no comments that
# could hide a second statement or a trailing clause from review.
_READ_ONLY_SQL = re.compile(r"(?is)(SELECT|WITH)\s.*")
_FORBIDDEN_SQL_TOKENS = (";", "--", "/*")
_ASCII_SECONDS = re.compile(r"[0-9]{1,6}")


@dataclass(frozen=True)
class QueryResult:
    """Rows of one query plus what the evidence record needs.

    Attributes:
        rows: Rows as lists of strings (None for SQL NULL), at most `limit` rows.
        query_id: The queryId sent to Zetaris (stored in the evidence record).
        duration_ms: Wall time of the call, including a retry.
    """

    rows: list[list[str | None]]
    query_id: str
    duration_ms: int

    @property
    def row_count(self) -> int:
        """Return the number of rows returned."""
        return len(self.rows)


def role_api_key(settings: Settings, role: ZetarisRole) -> SecretStr:
    """Return the Zetaris API key for an app role, never the admin key.

    Args:
        settings: Application settings.
        role: Verified app role from the session.

    Returns:
        The role's key.

    Raises:
        ZetarisConfigError: If the role has no Zetaris access, its key is not set, it equals
            the admin key, or the buyer and risk keys are the same.
    """
    field = ROLE_KEY_SETTINGS.get(role)
    if field is None:
        raise ZetarisConfigError(f"role {role!r} has no Zetaris access")
    variable = field.upper()
    key: SecretStr | None = getattr(settings, field)
    if key is None:
        raise ZetarisConfigError(
            f"{variable} is not set; the admin key is never used at runtime "
            "(see docs/zetaris-setup.md)"
        )
    raw = key.get_secret_value()
    admin = settings.zetaris_admin_api_key
    if admin is not None and raw == admin.get_secret_value():
        raise ZetarisConfigError(f"{variable} must not be the admin key")
    buyer, risk = settings.zetaris_key_buyer, settings.zetaris_key_risk
    if (
        buyer is not None
        and risk is not None
        and buyer.get_secret_value() == risk.get_secret_value()
    ):
        raise ZetarisConfigError("ZETARIS_KEY_BUYER and ZETARIS_KEY_RISK must differ")
    return key


def _check_url(url: str) -> None:
    """Refuse plain http to a remote host, so the key never crosses a network in clear text."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ZetarisConfigError("ZETARIS_REST_URL must be an http(s) URL with a host")
    if parts.scheme == "http" and not is_local_host(parts.hostname):
        raise ZetarisConfigError("ZETARIS_REST_URL must use https for a non-local host")


def _check_sql(sql: str) -> str:
    """Return the SQL stripped, or raise ValueError unless it is one plain SELECT."""
    text = sql.strip()
    if len(text) > MAX_SQL_CHARS:
        raise ValueError("sql is too long")
    if not _READ_ONLY_SQL.fullmatch(text) or any(t in text for t in _FORBIDDEN_SQL_TOKENS):
        raise ValueError("sql must be a single SELECT statement without comments")
    return text


def _cell(value: object) -> str | None:
    """Return one cell as a string (or None); nested values are not a valid row cell."""
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    raise ZetarisBadResponse("response row holds a nested value", 200)


def _rows(content: bytes, limit: int) -> list[list[str | None]]:
    """Parse {"data": [[...], ...]} into at most `limit` rows."""
    try:
        body: Any = json.loads(content)
    except (ValueError, RecursionError):
        raise ZetarisBadResponse("response is not usable JSON", 200) from None
    if not isinstance(body, dict):
        raise ZetarisBadResponse("response is not a JSON object", 200)
    data = body.get("data")
    if data is None:
        return []
    if not isinstance(data, list) or not all(isinstance(row, list) for row in data):
        raise ZetarisBadResponse("response data is not a list of rows", 200)
    return [[_cell(value) for value in row] for row in data[:limit]]


async def _read_capped(response: httpx.Response) -> bytes:
    """Read a streamed body, stopping as soon as it passes MAX_RESPONSE_BYTES."""
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > MAX_RESPONSE_BYTES:
            raise ZetarisBadResponse("response too large", response.status_code)
        chunks.append(chunk)
    return b"".join(chunks)


def _retry_after(headers: httpx.Headers) -> float:
    """Return the wait before the retry: Retry-After seconds (capped), else the default."""
    text = (headers.get("Retry-After") or "").strip()
    if _ASCII_SECONDS.fullmatch(text):
        return min(float(text), RETRY_AFTER_CAP_SECONDS)
    return RETRY_DELAY_SECONDS


class ZetarisClient:
    """Async Zetaris REST client bound to one app role (and so to one role key).

    Close it with aclose() (or use it as an async context manager).
    """

    def __init__(
        self,
        settings: Settings,
        role: ZetarisRole,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        query_timeout: float = QUERY_TIMEOUT_SECONDS,
    ) -> None:
        """Create the client for one role.

        Args:
            settings: Provides the REST URL, org id and role keys.
            role: Verified app role from the session; picks the API key.
            transport: Optional httpx transport (tests, load tests).
            sleep: Async sleep used before the retry; injectable for tests.
            query_timeout: Seconds per attempt (0 < t <= MAX_QUERY_TIMEOUT_SECONDS).

        Raises:
            ValueError: If query_timeout is out of range.
            ZetarisConfigError: If the role key is missing or unsafe, or the URL is unsafe.
        """
        if not 0 < query_timeout <= MAX_QUERY_TIMEOUT_SECONDS:
            raise ValueError(f"query_timeout must be in (0, {MAX_QUERY_TIMEOUT_SECONDS:g}] seconds")
        key = role_api_key(settings, role)
        _check_url(settings.zetaris_rest_url)
        self._role = role
        self._base_url = settings.zetaris_rest_url.rstrip("/")
        self._sleep = sleep
        self._timeout = query_timeout
        # Security: redirects are not followed, so the key never leaves the Zetaris host.
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=query_timeout,
            transport=transport,
            follow_redirects=False,
            headers={
                "Authorization": f"Bearer {key.get_secret_value()}",
                "X-Org-ID": settings.zetaris_org_id,
            },
        )

    def __repr__(self) -> str:
        """Return a representation without the key."""
        return f"ZetarisClient(base_url={self._base_url!r}, role={self._role!r})"

    async def aclose(self) -> None:
        """Close the underlying HTTP connection pool."""
        await self._http.aclose()

    async def __aenter__(self) -> "ZetarisClient":
        """Return the client for use in `async with`."""
        return self

    async def __aexit__(self, *_: object) -> None:
        """Close the client at the end of `async with`."""
        await self.aclose()

    async def query(self, sql: str, *, limit: int = 100) -> QueryResult:
        """Run one read-only SQL statement and return its rows.

        Args:
            sql: One SELECT from a fixed template (no semicolon, no comments).
            limit: Maximum rows to return (1 to MAX_QUERY_ROWS).

        Returns:
            The rows, the query id and the duration.

        Raises:
            ValueError: If the SQL is not one plain SELECT or the limit is out of range.
            ZetarisUnauthorized: On 401/403 (wrong key or no grant).
            ZetarisRequestError: On another refused request (for example a SQL error).
            ZetarisUnavailable: If 429, 502-504 or timeouts persist after the retry.
            ZetarisBadResponse: If the response is not the expected JSON.
        """
        if not 1 <= limit <= MAX_QUERY_ROWS:
            raise ValueError(f"limit must be between 1 and {MAX_QUERY_ROWS}")
        text = _check_sql(sql)
        query_id = str(uuid.uuid4())
        payload = {"queryId": query_id, "sql": text, "source": "SqlEditor", "limit": limit}
        started = time.monotonic()
        try:
            content, retries = await self._post(payload)
            rows = _rows(content, limit)
        except ZetarisError as exc:
            logger.error(
                "zetaris query failed",
                extra={
                    "event": "zetaris_query_failed",
                    "role": self._role,
                    "query_id": query_id,
                    "status_code": exc.status_code,
                    "error_type": type(exc).__name__,
                },
            )
            raise
        result = QueryResult(rows, query_id, round((time.monotonic() - started) * 1000))
        logger.info(
            "zetaris query done",
            extra={
                "event": "zetaris_query_done",
                "role": self._role,
                "query_id": query_id,
                "rows": result.row_count,
                "retries": retries,
                "duration_ms": result.duration_ms,
            },
        )
        return result

    async def _post(self, payload: dict[str, Any]) -> tuple[bytes, int]:
        """POST with one retry on 429/502-504/timeouts; return (body, retries used)."""
        status: int | None = None
        error_type: str | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                reply = await self._send(payload)
            except (httpx.TransportError, TimeoutError) as exc:
                status, delay, error_type = None, RETRY_DELAY_SECONDS, type(exc).__name__
            else:
                code, headers, content = reply
                if code in _UNAUTHORIZED:
                    raise ZetarisUnauthorized("Zetaris rejected the key or the grant", code)
                if 200 <= code < 300:
                    return content, attempt - 1
                if code not in _RETRYABLE:
                    raise ZetarisRequestError(f"Zetaris refused the query ({code})", code)
                status, delay, error_type = code, _retry_after(headers), None
            if attempt == MAX_ATTEMPTS:
                break
            logger.warning(
                "zetaris request failed, retrying",
                extra={
                    "event": "zetaris_retry",
                    "role": self._role,
                    "attempt": attempt,
                    "status_code": status,
                    "error_type": error_type,
                    "delay_ms": round(delay * 1000),
                },
            )
            await self._sleep(delay)
        raise ZetarisUnavailable("Zetaris unavailable", status)

    async def _send(self, payload: dict[str, Any]) -> tuple[int, httpx.Headers, bytes]:
        """Send one request under a hard deadline; read the body (capped) only on success."""
        headers = {"X-Request-ID": str(uuid.uuid4())}
        async with asyncio.timeout(self._timeout):
            async with self._http.stream(
                "POST", RUN_PATH, json=payload, headers=headers
            ) as response:
                content = await _read_capped(response) if response.is_success else b""
                return response.status_code, response.headers, content
