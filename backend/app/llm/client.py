"""Meterless Nightshift client: the single path from the app to the Nemotron models.

Implements the "Add Meterless as a provider" contract (Meterless Runtime 0.90.0) with httpx so
the raw JSON, including the "meterless" receipt, stays available. Every HTTP attempt goes
through the shared rate limiter; 429, 5xx and timeouts are retried with backoff; a deep call
that stays rate limited falls back once to the default model.

Responses are untrusted: bodies are read with a byte cap, each attempt has a hard deadline
(httpx timeouts are per phase, so a slow drip would never trip them), and the whole call has
a total deadline across retries and the fallback.
"""

import asyncio
import json
import logging
import math
import random
import re
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from app.core.settings import Settings
from app.llm.budget import CallBudget
from app.llm.errors import (
    LLMBadResponse,
    LLMError,
    LLMRateLimited,
    LLMRequestError,
    LLMUnauthorized,
    LLMUnavailable,
)
from app.llm.models import ChatResult, ModelRole, RuntimeStatus, Usage
from app.llm.rate_limiter import AsyncRateLimiter, get_rate_limiter
from app.llm.text import parse_tool_calls, strip_think

logger = logging.getLogger(__name__)

# A model load on the runtime can take minutes (contract), so each attempt gets 180 s.
CHAT_TIMEOUT_SECONDS = 180.0
STATUS_TIMEOUT_SECONDS = 10.0
# Upper bound for one logical call, including retries, backoff and the deep fallback.
TOTAL_DEADLINE_SECONDS = 600.0
MAX_ATTEMPTS = 4
BACKOFF_BASE_SECONDS = 1.0
BACKOFF_CAP_SECONDS = 30.0
RETRY_AFTER_CAP_SECONDS = 60.0
DEFAULT_EMBED_BATCH_SIZE = 32
# Security: input bounds, so one caller cannot send unbounded payloads upstream.
MAX_MESSAGES = 100
MAX_TOOLS = 32
MAX_REQUEST_CHARS = 500_000
MAX_EMBED_TEXTS = 1024
MAX_EMBED_TEXT_CHARS = 8192
# Security: output bounds; upstream responses are untrusted.
MAX_RESPONSE_BYTES = 8_000_000
MAX_CONTENT_CHARS = 400_000
MAX_EMBED_DIMENSIONS = 8192
MAX_MODEL_IDS = 500
# Security: receipt fields are upstream text that ends up in logs, the UI and the audit trail.
MAX_RECEIPT_FIELD_CHARS = 200

_UNAUTHORIZED = frozenset({401, 403})
_ASCII_SECONDS = re.compile(r"[0-9]{1,6}")


@dataclass(frozen=True)
class _Failure:
    """A retryable attempt failure: an HTTP status or a transport error type."""

    status_code: int | None
    error_type: str | None
    retry_after: float | None = None


@dataclass(frozen=True)
class _Reply:
    """Status, headers and (for success only) the capped body of one response."""

    status_code: int
    headers: httpx.Headers
    content: bytes


def _bounded_str(value: object) -> str | None:
    """Return a short printable string, or None for anything else (incl. control characters)."""
    if not isinstance(value, str) or not value or not value.isprintable():
        return None
    return value[:MAX_RECEIPT_FIELD_CHARS]


def _retry_after_seconds(header: str | None) -> float | None:
    """Return the Retry-After delay in seconds (delta or HTTP date), or None if unusable."""
    if not header or not header.strip():
        return None
    text = header.strip()
    # Security: ASCII digits only; str.isdigit() also accepts characters float() rejects.
    if _ASCII_SECONDS.fullmatch(text):
        return float(text)
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


def _decode(content: bytes, status_code: int) -> Any:  # noqa: ANN401
    """Return the decoded JSON body or raise LLMBadResponse (no body in the message)."""
    try:
        return json.loads(content)
    except (ValueError, RecursionError):
        raise LLMBadResponse("response is not usable JSON", status_code) from None


async def _read_capped(response: httpx.Response) -> bytes:
    """Read a streamed body, stopping as soon as it passes MAX_RESPONSE_BYTES."""
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > MAX_RESPONSE_BYTES:
            raise LLMBadResponse("response too large", response.status_code)
        chunks.append(chunk)
    return b"".join(chunks)


def _usage(value: object) -> Usage:
    """Return token usage, or zeros when the runtime sent none or garbage."""
    if not isinstance(value, dict):
        return Usage()
    try:
        return Usage.model_validate(value)
    except ValueError:
        return Usage()


@dataclass(frozen=True)
class _Receipt:
    """Fields of the optional "meterless" receipt object; None when missing or unusable."""

    receipt_id: str | None
    routed_model: str | None
    provider: str | None
    cache_status: str | None

    @property
    def from_cache(self) -> bool:
        """Return True when Nightshift answered from its cache."""
        return self.cache_status not in (None, "miss")


def _receipt(body: Mapping[str, Any]) -> _Receipt:
    """Return the receipt fields of a chat response.

    The receipt's requested mode can read meterless/auto even for pinned ids, so the served
    model and provider come from routed_model and winning_route, never from the request echo.
    """
    receipt = body.get("meterless")
    receipt = receipt if isinstance(receipt, dict) else {}
    route = receipt.get("winning_route")
    route = route if isinstance(route, dict) else {}
    cache = receipt.get("cache")
    return _Receipt(
        receipt_id=_bounded_str(receipt.get("receipt_id")),
        routed_model=_bounded_str(receipt.get("routed_model"))
        or _bounded_str(route.get("model"))
        or _bounded_str(body.get("model")),
        provider=_bounded_str(route.get("provider")),
        cache_status=_bounded_str(cache.get("status")) if isinstance(cache, dict) else None,
    )


@dataclass(frozen=True)
class _EmptyReply:
    """Marker for a reply with nothing in it (usually a cache hit that kept no tool calls)."""

    cache_status: str | None


def _zero_completion_tokens(body: Mapping[str, Any]) -> bool:
    """Return True when the runtime reports exactly 0 completion tokens (not when unreported)."""
    usage = body.get("usage")
    tokens = usage.get("completion_tokens") if isinstance(usage, dict) else None
    return isinstance(tokens, int) and not isinstance(tokens, bool) and tokens == 0


def _with_request_id(options: dict[str, Any]) -> dict[str, Any]:
    """Return options with a fresh "request-id" system message first, to bypass the cache."""
    nonce = {"role": "system", "content": f"request-id: {uuid.uuid4()}"}
    return {**options, "messages": [nonce, *options["messages"]]}


def _message(body: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return choices[0].message of a complete chat completion, or raise LLMBadResponse."""
    choices = body.get("choices")
    first = choices[0] if isinstance(choices, list) and choices else None
    message = first.get("message") if isinstance(first, dict) else None
    if not isinstance(message, dict):
        raise LLMBadResponse("chat response has no message")
    content = message.get("content")
    if content is not None and not isinstance(content, str):
        raise LLMBadResponse("chat content is not a string")
    if content is not None and len(content) > MAX_CONTENT_CHARS:
        raise LLMBadResponse("chat content too long")
    # A cut-off answer may be all reasoning, or half a claim; never pass it on as an answer.
    if first.get("finish_reason") == "length":  # type: ignore[union-attr]
        raise LLMBadResponse("answer truncated (finish_reason=length)")
    return message


def _model_ids(content: bytes) -> list[str]:
    """Return the model ids listed by GET /v1/models, or [] if the body is not usable."""
    try:
        body = json.loads(content)
    except (ValueError, RecursionError):
        return []
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list):
        return []
    ids = [_bounded_str(item.get("id")) for item in data[:MAX_MODEL_IDS] if isinstance(item, dict)]
    return [model_id for model_id in ids if model_id]


def _vector(item: object) -> tuple[int, list[float]]:
    """Return (index, vector) of one embeddings item, or raise LLMBadResponse."""
    index = item.get("index") if isinstance(item, dict) else None
    vector = item.get("embedding") if isinstance(item, dict) else None
    if not isinstance(index, int) or not isinstance(vector, list):
        raise LLMBadResponse("embeddings item is malformed")
    if not 0 < len(vector) <= MAX_EMBED_DIMENSIONS:
        raise LLMBadResponse("embedding has an unusable dimension")
    # Security: NaN/Infinity would poison similarity ranking in H-MEM.
    if not all(isinstance(x, int | float) and math.isfinite(x) for x in vector):
        raise LLMBadResponse("embedding has non-numeric or non-finite values")
    return index, [float(x) for x in vector]


def _vectors(body: Mapping[str, Any], expected: int, dimension: int | None) -> list[list[float]]:
    """Return the vectors of one embeddings response in input order, or raise.

    Args:
        body: Decoded response body.
        expected: Number of texts sent.
        dimension: Dimension seen in earlier batches of the same call, if any.
    """
    data = body.get("data")
    if not isinstance(data, list) or len(data) != expected:
        raise LLMBadResponse("embeddings response has the wrong number of items")
    by_index = dict(_vector(item) for item in data)
    if sorted(by_index) != list(range(expected)):
        raise LLMBadResponse("embeddings indexes do not match the input")
    vectors = [by_index[i] for i in range(expected)]
    sizes = {len(vector) for vector in vectors} | ({dimension} if dimension else set())
    if len(sizes) != 1:
        raise LLMBadResponse("embeddings have different dimensions")
    return vectors


def _check_chat_input(
    messages: Sequence[Mapping[str, Any]], tools: Sequence[Mapping[str, Any]] | None
) -> None:
    """Raise ValueError when the request is empty or larger than the input bounds."""
    if not messages or len(messages) > MAX_MESSAGES:
        raise ValueError(f"messages must hold 1 to {MAX_MESSAGES} items")
    if tools is not None and len(tools) > MAX_TOOLS:
        raise ValueError(f"at most {MAX_TOOLS} tools per call")
    size = len(json.dumps([list(messages), list(tools or [])], default=str))
    if size > MAX_REQUEST_CHARS:
        raise ValueError(f"messages and tools exceed {MAX_REQUEST_CHARS} characters")


class MeterlessClient:
    """Async client for the Meterless runtime (status, chat, embeddings).

    One instance per process is enough; it holds a pooled httpx.AsyncClient. Close it with
    aclose() (or use it as an async context manager).
    """

    def __init__(
        self,
        settings: Settings,
        *,
        limiter: AsyncRateLimiter | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        max_attempts: int = MAX_ATTEMPTS,
        embed_batch_size: int = DEFAULT_EMBED_BATCH_SIZE,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rand: Callable[[], float] = random.random,
    ) -> None:
        """Create the client.

        Args:
            settings: Provides the base URL, token, app id and model ids.
            limiter: Rate limiter; defaults to the process-wide shared one.
            transport: Optional httpx transport (tests, load tests).
            max_attempts: Attempts per request, including the first (at least 1).
            embed_batch_size: Texts per embeddings request (at least 1).
            sleep: Async sleep used for backoff; injectable for tests.
            rand: Source of jitter in [0, 1); injectable for tests.

        Raises:
            ValueError: If max_attempts or embed_batch_size is below 1.
        """
        if max_attempts < 1 or embed_batch_size < 1:
            raise ValueError("max_attempts and embed_batch_size must be at least 1")
        self._settings = settings
        self._limiter = limiter or get_rate_limiter()
        self._max_attempts = max_attempts
        self._embed_batch_size = embed_batch_size
        self._sleep = sleep
        self._rand = rand
        self._models: dict[str, str] = {
            "default": settings.meterless_model,
            "deep": settings.meterless_deep_model,
            "fast": settings.meterless_fast_model,
        }
        # Security: redirects are not followed, so the bearer token never leaves the runtime host.
        self._http = httpx.AsyncClient(
            base_url=settings.meterless_base_url,
            timeout=CHAT_TIMEOUT_SECONDS,
            transport=transport,
            follow_redirects=False,
            headers={
                "Authorization": f"Bearer {settings.meterless_runtime_token.get_secret_value()}",
                "meterless-app": settings.meterless_app_id,
            },
        )

    def __repr__(self) -> str:
        """Return a representation without the token."""
        return f"MeterlessClient(base_url={self._settings.meterless_base_url!r})"

    async def aclose(self) -> None:
        """Close the underlying HTTP connection pool."""
        await self._http.aclose()

    async def __aenter__(self) -> "MeterlessClient":
        """Return the client for use in `async with`."""
        return self

    async def __aexit__(self, *_: object) -> None:
        """Close the client at the end of `async with`."""
        await self.aclose()

    async def status(self) -> RuntimeStatus:
        """Return whether the runtime is online, unauthorized or offline.

        GET /v1/models decides: 401/403 is unauthorized, 200 with model ids is online, and
        anything else (including no connection) is offline. /healthz is not used because it
        only proves the process is up.

        Returns:
            The state and every listed model id.
        """
        error_type: str | None = None
        try:
            reply = await self._send("GET", "/models", timeout=STATUS_TIMEOUT_SECONDS)
        except (httpx.HTTPError, TimeoutError, LLMBadResponse) as exc:
            status = RuntimeStatus(state="offline")
            error_type = type(exc).__name__
        else:
            ids = _model_ids(reply.content) if reply.status_code == 200 else []
            if reply.status_code in _UNAUTHORIZED:
                status = RuntimeStatus(state="unauthorized")
            elif ids:
                status = RuntimeStatus(state="online", model_ids=ids)
            else:
                status = RuntimeStatus(state="offline")
        logger.info(
            "model runtime status",
            extra={
                "event": "model_status",
                "state": status.state,
                "models": len(status.model_ids),
                "error_type": error_type,
            },
        )
        return status

    async def chat(
        self,
        messages: Sequence[Mapping[str, Any]],
        role: ModelRole = "default",
        tools: Sequence[Mapping[str, Any]] | None = None,
        tool_choice: str | Mapping[str, Any] | None = None,
        response_format: Mapping[str, Any] | None = None,
        budget: CallBudget | None = None,
    ) -> ChatResult:
        """Send one chat completion and return the cleaned answer with its receipt.

        Args:
            messages: OpenAI-style messages; plain text content stays a string.
            role: default (Lightning), deep (Ultra) or fast (Bolt).
            tools: OpenAI function tool definitions.
            tool_choice: OpenAI tool_choice value.
            response_format: OpenAI response_format value.
            budget: Investigation budget; one unit is consumed before the request.

        Returns:
            The answer, tool calls, models, receipt id, usage, latency and retries.

        Raises:
            ValueError: If role is unknown or the messages or tools are empty or too large.
            BudgetExhausted: If the budget is used up (no request is sent).
            LLMUnauthorized: On 401/403.
            LLMRateLimited: If 429 persists (after the deep -> default fallback, if any).
            LLMUnavailable: If 5xx, timeouts or connection errors persist, or the deadline passes.
            LLMRequestError: On another non-success status.
            LLMBadResponse: If the response is malformed, too large or truncated
                (ToolCallParseError for tool-call arguments).
        """
        if role not in self._models:
            raise ValueError(f"unknown role {role!r}; use default, deep or fast")
        _check_chat_input(messages, tools)
        if budget is not None:
            budget.consume()
        options: dict[str, Any] = {"messages": list(messages)}
        if tools is not None:
            options["tools"] = list(tools)
        if tool_choice is not None:
            options["tool_choice"] = tool_choice
        if response_format is not None:
            options["response_format"] = response_format

        model = self._models[role]
        try:
            result = await self._chat_with_fallback(model, role, options)
        except LLMError as exc:
            logger.error(
                "model call failed",
                extra={
                    "event": "model_call_failed",
                    "role": role,
                    "requested_model": model,
                    "error_type": type(exc).__name__,
                    "status_code": exc.status_code,
                },
            )
            raise
        self._log_call(role, result)
        return result

    async def _chat_with_fallback(
        self, model: str, role: str, options: dict[str, Any]
    ) -> ChatResult:
        """Run the chat on the role's model; on persistent 429, run a deep call once on default."""
        started = time.monotonic()
        deadline = started + TOTAL_DEADLINE_SECONDS
        try:
            return await self._chat_once(model, options, started, deadline)
        except LLMRateLimited:
            fallback = self._models["default"]
            if role != "deep" or fallback == model:
                raise
            logger.warning(
                "deep model rate limited, falling back",
                extra={"event": "model_fallback", "from_model": model, "to_model": fallback},
            )
            result = await self._chat_once(
                fallback, options, started, deadline, prior_retries=self._max_attempts
            )
            return result.model_copy(update={"fallback_used": True})

    async def _chat_once(
        self,
        model: str,
        options: dict[str, Any],
        started: float,
        deadline: float,
        prior_retries: int = 0,
    ) -> ChatResult:
        """Run one chat request (with retries) on one model and parse the answer.

        An empty reply (tools sent but no tool calls and no content, or 0 completion tokens)
        is typically a Nightshift exact-cache hit that kept no tool calls. It is retried once
        with a fresh request-id system message, which misses the cache; a second empty reply
        raises LLMBadResponse.
        """
        result, retries = await self._complete(model, options, started, deadline)
        if isinstance(result, _EmptyReply):
            logger.warning(
                "empty model reply, retrying with a fresh request id",
                extra={
                    "event": "model_empty_reply",
                    "requested_model": model,
                    "cache_status": result.cache_status,
                },
            )
            result, more = await self._complete(model, _with_request_id(options), started, deadline)
            retries += more + 1
            if isinstance(result, _EmptyReply):
                raise LLMBadResponse("empty reply from the model runtime, twice")
        return result.model_copy(update={"retries": retries + prior_retries})

    async def _complete(
        self, model: str, options: dict[str, Any], started: float, deadline: float
    ) -> tuple[ChatResult | _EmptyReply, int]:
        """Send one chat request with retries; return (result or empty marker, retries)."""
        body, retries = await self._post(
            "/chat/completions",
            {"model": model, **options},
            model,
            scope_user=True,
            deadline=deadline,
        )
        message = _message(body)
        tool_calls = parse_tool_calls(message.get("tool_calls"))
        content = strip_think(message.get("content"))
        receipt = _receipt(body)
        empty = "tools" in options and not tool_calls and not content
        if empty or _zero_completion_tokens(body):
            return _EmptyReply(receipt.cache_status), retries
        result = ChatResult(
            content=content,
            tool_calls=tool_calls,
            requested_model=model,
            routed_model=receipt.routed_model,
            provider=receipt.provider,
            receipt_id=receipt.receipt_id,
            usage=_usage(body.get("usage")),
            latency_ms=round((time.monotonic() - started) * 1000),
            retries=retries,
            cache_status=receipt.cache_status,
            from_cache=receipt.from_cache,
        )
        return result, retries

    @staticmethod
    def _log_call(role: str, result: ChatResult) -> None:
        """Write the one INFO line per model call (ids and counts only, never content)."""
        logger.info(
            "model call done",
            extra={
                "event": "model_call",
                "role": role,
                "requested_model": result.requested_model,
                "routed_model": result.routed_model,
                "provider": result.provider,
                "receipt_id": result.receipt_id,
                "cache_status": result.cache_status,
                "from_cache": result.from_cache,
                "prompt_tokens": result.usage.prompt_tokens,
                "completion_tokens": result.usage.completion_tokens,
                "total_tokens": result.usage.total_tokens,
                "tool_calls": len(result.tool_calls),
                "latency_ms": result.latency_ms,
                "retries": result.retries,
            },
        )

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one embedding per text from POST /v1/embeddings, in input order.

        Texts are sent in batches; each batch is one rate-limited, retried request. All
        vectors of one call have the same dimension.

        Args:
            texts: Texts to embed (at most MAX_EMBED_TEXTS, each at most MAX_EMBED_TEXT_CHARS).

        Returns:
            Vectors in the same order as texts.

        Raises:
            TypeError: If texts is a single string instead of a sequence of strings.
            ValueError: If there are too many texts or one is too long.
            LLMError: If a batch fails or returns unusable vectors (see chat for subclasses).
        """
        if isinstance(texts, str):
            raise TypeError("texts must be a sequence of strings, not one string")
        if len(texts) > MAX_EMBED_TEXTS:
            raise ValueError(f"at most {MAX_EMBED_TEXTS} texts per call")
        if any(len(text) > MAX_EMBED_TEXT_CHARS for text in texts):
            raise ValueError(f"each text must be at most {MAX_EMBED_TEXT_CHARS} characters")
        model = self._settings.meterless_embed_model
        started = time.monotonic()
        deadline = started + TOTAL_DEADLINE_SECONDS
        vectors: list[list[float]] = []
        retries = 0
        for start in range(0, len(texts), self._embed_batch_size):
            batch = list(texts[start : start + self._embed_batch_size])
            body, batch_retries = await self._post(
                "/embeddings",
                {"model": model, "input": batch},
                model,
                scope_user=False,
                deadline=deadline,
            )
            dimension = len(vectors[0]) if vectors else None
            vectors.extend(_vectors(body, len(batch), dimension))
            retries += batch_retries
        if texts:
            logger.info(
                "embedding call done",
                extra={
                    "event": "embed_call",
                    "requested_model": model,
                    "texts": len(texts),
                    "latency_ms": round((time.monotonic() - started) * 1000),
                    "retries": retries,
                },
            )
        return vectors

    async def _send(
        self,
        method: str,
        path: str,
        *,
        timeout: float,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> _Reply:
        """Send one request under a hard deadline; read the body (capped) only on success."""
        async with asyncio.timeout(timeout):
            async with self._http.stream(
                method, path, json=payload, headers=headers, timeout=timeout
            ) as response:
                content = await _read_capped(response) if response.is_success else b""
                return _Reply(response.status_code, response.headers, content)

    async def _post(
        self,
        path: str,
        payload: dict[str, Any],
        model: str,
        *,
        scope_user: bool,
        deadline: float,
    ) -> tuple[dict[str, Any], int]:
        """POST with rate limiting, retries and a deadline; return (JSON body, retries used).

        Raises:
            LLMUnauthorized, LLMRequestError, LLMRateLimited, LLMUnavailable, LLMBadResponse.
        """
        headers = {"meterless-scope": "user"} if scope_user else None
        outcome: _Failure = _Failure(None, "DeadlineExceeded")
        for attempt in range(1, self._max_attempts + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            result = await self._attempt(
                path, payload, headers, min(CHAT_TIMEOUT_SECONDS, remaining)
            )
            if isinstance(result, _Reply):
                body = _decode(result.content, result.status_code)
                if not isinstance(body, dict):
                    raise LLMBadResponse("response is not a JSON object", result.status_code)
                return body, attempt - 1
            outcome = result
            delay = self._delay(attempt, outcome)
            if attempt == self._max_attempts or time.monotonic() + delay >= deadline:
                break
            logger.warning(
                "model request failed, retrying",
                extra={
                    "event": "model_retry",
                    "model": model,
                    "path": path,
                    "attempt": attempt,
                    "status_code": outcome.status_code,
                    "error_type": outcome.error_type,
                    "delay_ms": round(delay * 1000),
                },
            )
            await self._sleep(delay)
        if outcome.status_code == 429:
            raise LLMRateLimited("model runtime still rate limiting (429)", 429)
        raise LLMUnavailable("model runtime unavailable", outcome.status_code)

    async def _attempt(
        self, path: str, payload: dict[str, Any], headers: dict[str, str] | None, timeout: float
    ) -> _Reply | _Failure:
        """Send one request; return the success reply or a retryable failure."""
        try:
            async with self._limiter.slot():
                reply = await self._send(
                    "POST", path, timeout=timeout, payload=payload, headers=headers
                )
        except (httpx.TransportError, TimeoutError) as exc:
            # Timeouts, refused connections and broken streams are all worth another try.
            return _Failure(status_code=None, error_type=type(exc).__name__)
        code = reply.status_code
        if code in _UNAUTHORIZED:
            raise LLMUnauthorized("model runtime rejected the token", code)
        if code == 429 or code >= 500:
            return _Failure(code, None, _retry_after_seconds(reply.headers.get("Retry-After")))
        if not 200 <= code < 300:
            raise LLMRequestError(f"model runtime refused the request ({code})", code)
        return reply

    def _delay(self, attempt: int, failure: _Failure) -> float:
        """Return the wait before the next attempt: Retry-After if given, else backoff."""
        if failure.retry_after is not None:
            return min(failure.retry_after, RETRY_AFTER_CAP_SECONDS)
        backoff = min(BACKOFF_CAP_SECONDS, BACKOFF_BASE_SECONDS * 2.0 ** (attempt - 1))
        return backoff + self._rand() * BACKOFF_BASE_SECONDS
