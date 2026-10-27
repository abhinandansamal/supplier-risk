"""Client of the Meterless World Model case board (context-service/, internal HTTP API).

The case board is the agents' shared working memory: an append-only event log per
investigation (evidence found, gaps, contradictions, claims, decisions). Appends are idempotent
by event id, so a retried call never duplicates an event.

The case board is optional for an investigation: when the service is down, record_event() logs
memory_unavailable and the agent goes on without it. Responses are untrusted: bodies are read
with a byte cap under a hard deadline, validated with Pydantic, and never copied into errors.
"""

import asyncio
import json
import logging
import re
import time
import unicodedata
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

import httpx
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    StringConstraints,
    ValidationError,
    field_serializer,
    field_validator,
)

from app.core.logging import request_id_var
from app.core.settings import Settings

logger = logging.getLogger(__name__)

# Security: output bound. The service caps a case at 1 MB on disk by default (at most 8 MB when
# configured); 500 maximum-size events are about 6 MB of JSON, so 10 MB covers every case.
MAX_RESPONSE_BYTES = 10_000_000
# SEC-080: JSON numbers are doubles; larger integers lose precision on the way.
MAX_SAFE_INTEGER = 2**53 - 1
# Rejections that are not a bug in our code: the board is full or the token is wrong.
_DEGRADE_CODES = frozenset({"case_full", "store_full"})
_DEGRADE_STATUSES = frozenset({401, 403})
# Security: path parameters are checked before they go into a URL (same rules as the service).
CASE_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}")
ENTITY_ID_PATTERN = re.compile(
    r"(case:board|supplier:erp|part:erp|site:erp|country:iso):[A-Za-z0-9_-]{1,64}"
)
_REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}")
_ERROR_CODE_PATTERN = re.compile(r"[a-z_]{1,40}")
_DATA_KEY_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,39}")
MAX_DATA_KEYS = 20

# Same list as context-service/src/schema.ts (injection_detected: instruction-like tool data).
EventKind = Literal[
    "input_refused",
    "injection_detected",
    "plan_created",
    "plan_revised",
    "task_dispatched",
    "task_failed",
    "task_recovered",
    "evidence_found",
    "no_data",
    "gap_found",
    "contradiction_found",
    "verdict",
    "action_proposed",
    "claim_made",
    "escalated",
    "decision_made",
]
AgentName = Literal[
    "guard",
    "planner",
    "executor",
    "erp",
    "financial",
    "country",
    "weather",
    "evaluator",
    "recommender",
    "writer",
    "verifier",
    "escalation",
    "approval",
]
DataValue = Annotated[str, StringConstraints(max_length=200)] | bool | int | FiniteFloat | None


def _check_text(value: str) -> str:
    """Reject malformed Unicode and control/format characters (SEC-079, as in the service)."""
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("malformed text") from None
    if any(unicodedata.category(ch) in ("Cc", "Cf") for ch in value):
        raise ValueError("control or format characters")
    return value


EvidenceId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_:.-]{1,64}$")]


class WorldModelError(Exception):
    """Base class for case board client errors.

    Attributes:
        status_code: HTTP status of the response, when there was one.
        code: The service's error code (e.g. case_full), when it sent a valid one.
    """

    def __init__(
        self, message: str, status_code: int | None = None, code: str | None = None
    ) -> None:
        """Create the error.

        Args:
            message: Short description without upstream content.
            status_code: HTTP status of the response, if any.
            code: Error code from the service, if any.
        """
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class WorldModelUnavailable(WorldModelError):  # noqa: N818
    """The service is down, slow, answered 5xx, or no token is configured."""


class WorldModelRejected(WorldModelError):  # noqa: N818
    """The service refused the request (4xx: validation, auth, conflict, limits)."""


class WorldModelBadResponse(WorldModelError):  # noqa: N818
    """The service answered with something unusable (not JSON, wrong shape, too large)."""


class Subjects(BaseModel):
    """Entities an event is about; at most one per kind, each becomes a graph entity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    supplier: Annotated[str, StringConstraints(pattern=r"^S[0-9]{4}$")] | None = None
    part: Annotated[str, StringConstraints(pattern=r"^P[0-9]{4}$")] | None = None
    site: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,32}$")] | None = None
    country: Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")] | None = None


class CaseEvent(BaseModel):
    """One case board event, validated with the same rules as the service.

    Attributes:
        event_id: Idempotency key (a new UUID by default).
        kind: Event kind (architecture §6.1).
        agent: The agent (or approval step) that writes it.
        at: Observation time, timezone-aware (sent as UTC with Z).
        summary: Short text (1-500 characters); never raw model output with instructions.
        evidence_ids: Evidence record ids backing the event (at most 50).
        subjects: Supplier, part, site or country the event is about.
        data: Small flat details (at most 20 keys, scalar values).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,64}$")] = Field(
        default_factory=lambda: str(uuid.uuid4())
    )
    kind: EventKind
    agent: AgentName
    at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    summary: Annotated[str, StringConstraints(min_length=1, max_length=500)]
    evidence_ids: Annotated[list[EvidenceId], Field(max_length=50)] = Field(default_factory=list)
    subjects: Subjects = Field(default_factory=Subjects)
    data: dict[str, DataValue] = Field(default_factory=dict)

    @field_validator("summary")
    @classmethod
    def _safe_summary(cls, value: str) -> str:
        return _check_text(value)

    @field_validator("data")
    @classmethod
    def _flat_small_data(cls, value: dict[str, DataValue]) -> dict[str, DataValue]:
        if len(value) > MAX_DATA_KEYS:
            raise ValueError(f"at most {MAX_DATA_KEYS} keys")
        if not all(_DATA_KEY_PATTERN.fullmatch(key) for key in value):
            raise ValueError("keys must be snake_case (max 40 characters)")
        for item in value.values():
            if isinstance(item, str):
                _check_text(item)
            elif isinstance(item, int) and not isinstance(item, bool):
                if abs(item) > MAX_SAFE_INTEGER:
                    raise ValueError("integer out of the safe range")
        return value

    @field_serializer("at")
    def _utc_z(self, value: datetime) -> str:
        utc = value.astimezone(UTC)
        return f"{utc:%Y-%m-%dT%H:%M:%S}.{utc.microsecond // 1000:03d}Z"

    def to_wire(self) -> dict[str, Any]:
        """Return the JSON body the service expects (empty subjects are left out).

        Returns:
            The event as a JSON-ready dict.
        """
        body = self.model_dump(mode="json")
        body["subjects"] = self.subjects.model_dump(exclude_none=True)
        return body


class StoredCaseEvent(CaseEvent):
    """A case event as returned by the timeline, with its position in the case.

    Attributes:
        seq: 1-based position in the case (append order).
    """

    seq: Annotated[int, Field(ge=1)]


class AppendResult(BaseModel):
    """Answer to an append.

    Attributes:
        case_id: The investigation id.
        event_id: The event id.
        seq: Position of the event in the case.
        duplicate: True when the event was already stored (nothing written).
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    case_id: str
    event_id: str
    seq: Annotated[int, Field(ge=1)]
    duplicate: bool


class RecordOutcome(BaseModel):
    """What happened to an event the agent recorded.

    Attributes:
        status: stored, duplicate, or memory_unavailable (the service was not reachable).
        seq: Position in the case, unless memory_unavailable.
    """

    model_config = ConfigDict(frozen=True)

    status: Literal["stored", "duplicate", "memory_unavailable"]
    seq: int | None = None


class EntityReference(BaseModel):
    """A case event that points at an entity."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    case_id: str
    event_id: str
    kind: str


class EntityView(BaseModel):
    """Graph view of a case or subject entity.

    Attributes:
        id: Entity id, e.g. supplier:erp:S0001.
        type: Entity type (case, supplier, part, site, country).
        attrs: Entity attributes (untrusted data).
        references: Case events about the entity (capped by the service).
        truncated: True when the service left references out.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    id: str
    type: str
    attrs: dict[str, Any]
    created_at: str
    updated_at: str
    references: list[EntityReference]
    truncated: bool


class _Timeline(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # Events are validated one by one, so one bad event does not hide the case (SEC-078).
    events: Annotated[list[Any], Field(max_length=10_000)]


def _check(pattern: re.Pattern[str], value: str, what: str) -> str:
    if not pattern.fullmatch(value):
        raise ValueError(f"invalid {what}")
    return value


def _request_id() -> str:
    current = request_id_var.get()
    if current and _REQUEST_ID_PATTERN.fullmatch(current):
        return current
    return str(uuid.uuid4())


def _error_code(content: bytes) -> str | None:
    """Return the service's error code if the body carries a well-formed one."""
    try:
        body = json.loads(content)
    except (ValueError, RecursionError):
        return None
    code = body.get("code") if isinstance(body, dict) else None
    return code if isinstance(code, str) and _ERROR_CODE_PATTERN.fullmatch(code) else None


class WorldModelClient:
    """Async client for the case board (one instance per process; close with aclose()).

    Example:
        async with WorldModelClient(settings) as board:
            await board.record_event(investigation_id, CaseEvent(...))
    """

    def __init__(
        self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        """Create the client.

        Args:
            settings: Provides the service URL, token and timeout.
            transport: Optional httpx transport (tests, load tests).
        """
        self._url = settings.context_service_url
        self._token = settings.context_service_token
        self._deadline = settings.context_service_timeout_seconds
        # Security: redirects are not followed, so the token never leaves the service host.
        self._http = httpx.AsyncClient(
            base_url=settings.context_service_url,
            timeout=settings.context_service_timeout_seconds,
            transport=transport,
            follow_redirects=False,
        )

    def __repr__(self) -> str:
        """Return a representation without the token."""
        return f"WorldModelClient(base_url={self._url!r})"

    async def aclose(self) -> None:
        """Close the HTTP connection pool."""
        await self._http.aclose()

    async def __aenter__(self) -> "WorldModelClient":
        """Return the client for use in async with."""
        return self

    async def __aexit__(self, *_: object) -> None:
        """Close the client."""
        await self.aclose()

    async def append_event(self, case_id: str, event: CaseEvent) -> AppendResult:
        """Append an event to a case (idempotent by event.event_id).

        Args:
            case_id: The investigation id.
            event: The event.

        Returns:
            The event's position and whether it was already stored.

        Raises:
            ValueError: If case_id is not a valid case id.
            WorldModelUnavailable: If the service is down, slow, 5xx, or no token is set.
            WorldModelRejected: If the service refused the event (4xx).
            WorldModelBadResponse: If the answer is unusable.
        """
        _check(CASE_ID_PATTERN, case_id, "case id")
        body = await self._call("POST", f"/cases/{case_id}/events", "append", event.to_wire())
        result = self._parse(AppendResult, body)
        # Security: SEC-082. An answer about another event must not count as stored.
        if (result.case_id, result.event_id) != (case_id, event.event_id):
            raise WorldModelBadResponse("answer is about another event")
        return result

    async def record_event(self, case_id: str, event: CaseEvent) -> RecordOutcome:
        """Append an event; if the service is unavailable, log memory_unavailable and go on.

        Args:
            case_id: The investigation id.
            event: The event.

        Returns:
            stored or duplicate with the seq, or memory_unavailable.

        A full board, a rejected token or an unusable answer also degrade (logged at ERROR, since
        an operator has to act); only invalid events and event id conflicts raise, because those
        are bugs in our code (SEC-077).

        Raises:
            ValueError: If case_id is not a valid case id.
            WorldModelRejected: If the event is invalid or its id conflicts (a bug, not hidden).
        """
        try:
            result = await self.append_event(case_id, event)
        except WorldModelUnavailable as err:
            self._log_degraded(logging.WARNING, case_id, err)
            return RecordOutcome(status="memory_unavailable")
        except WorldModelRejected as err:
            if err.code not in _DEGRADE_CODES and err.status_code not in _DEGRADE_STATUSES:
                raise
            self._log_degraded(logging.ERROR, case_id, err)
            return RecordOutcome(status="memory_unavailable")
        except WorldModelBadResponse as err:
            self._log_degraded(logging.ERROR, case_id, err)
            return RecordOutcome(status="memory_unavailable")
        return RecordOutcome(status="duplicate" if result.duplicate else "stored", seq=result.seq)

    @staticmethod
    def _log_degraded(level: int, case_id: str, err: WorldModelError) -> None:
        logger.log(
            level,
            "Case board unavailable; continuing without it",
            extra={
                "event": "memory_unavailable",
                "investigation_id": case_id,
                "error_type": err.code or type(err.__cause__ or err).__name__,
                "status_code": err.status_code,
            },
        )

    async def timeline(self, case_id: str) -> list[StoredCaseEvent]:
        """Return the events of a case in append order (empty for an unknown case).

        Args:
            case_id: The investigation id.

        Returns:
            The events, oldest first.

        Raises:
            ValueError: If case_id is not a valid case id.
            WorldModelError: As for append_event.
        """
        _check(CASE_ID_PATTERN, case_id, "case id")
        body = await self._call(
            "GET", f"/cases/{case_id}/timeline", "timeline", not_found_code="case_not_found"
        )
        if body is None:
            return []
        events: list[StoredCaseEvent] = []
        skipped = 0
        for item in self._parse(_Timeline, body).events:
            try:
                events.append(StoredCaseEvent.model_validate(item))
            except ValidationError:
                skipped += 1
        if skipped:
            logger.warning(
                "Unreadable case events skipped",
                extra={
                    "event": "timeline_events_skipped",
                    "investigation_id": case_id,
                    "count": skipped,
                },
            )
        return events

    async def entity(self, entity_id: str) -> EntityView | None:
        """Return the graph view of a case or subject entity, or None when unknown.

        The service does not filter references by role; callers apply the user's data scope.

        Args:
            entity_id: For example supplier:erp:S0001 or case:board:<investigation id>.

        Returns:
            The entity view, or None.

        Raises:
            ValueError: If entity_id is not an id the read API serves.
            WorldModelError: As for append_event.
        """
        _check(ENTITY_ID_PATTERN, entity_id, "entity id")
        body = await self._call(
            "GET", f"/entities/{entity_id}", "entity", not_found_code="entity_not_found"
        )
        return None if body is None else self._parse(EntityView, body)

    async def health(self) -> bool:
        """Return True when the service answers GET /health with 200."""
        try:
            async with asyncio.timeout(self._deadline):
                response = await self._http.get("/health")
        except (httpx.HTTPError, TimeoutError):
            return False
        return response.status_code == httpx.codes.OK

    async def _call(
        self,
        method: str,
        path: str,
        operation: str,
        payload: dict[str, Any] | None = None,
        *,
        not_found_code: str | None = None,
    ) -> Any | None:  # noqa: ANN401
        """Send one request and return the decoded JSON body (None for the expected 404)."""
        if self._token is None:
            raise WorldModelUnavailable("context service token is not set")
        headers = {
            "X-Internal-Token": self._token.get_secret_value(),
            "X-Request-ID": _request_id(),
        }
        started = time.perf_counter()
        request = self._http.build_request(method, path, json=payload, headers=headers)
        try:
            # httpx timeouts are per phase; this bounds the whole call, including a slow drip.
            async with asyncio.timeout(self._deadline):
                response = await self._http.send(request, stream=True)
                try:
                    content = await self._read_capped(response)
                finally:
                    await response.aclose()
        except TimeoutError as err:
            raise WorldModelUnavailable("context service timed out") from err
        except httpx.HTTPError as err:
            raise WorldModelUnavailable("context service not reachable") from err
        status = response.status_code
        logger.info(
            "Case board call done",
            extra={
                "event": "world_model_call_done",
                "step": operation,
                "status_code": status,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            },
        )
        return self._decode_status(status, content, not_found_code=not_found_code)

    @staticmethod
    def _decode_status(status: int, content: bytes, *, not_found_code: str | None) -> Any | None:  # noqa: ANN401
        """Map the HTTP status to a body or a typed error (no upstream text in messages)."""
        # Security: SEC-082. Only the service's own "unknown case/entity" answer means "empty";
        # a router 404 (wrong base path) is an error.
        if (
            status == httpx.codes.NOT_FOUND
            and not_found_code is not None
            and _error_code(content) == not_found_code
        ):
            return None
        if status >= httpx.codes.INTERNAL_SERVER_ERROR:
            raise WorldModelUnavailable(f"context service answered {status}", status)
        if status >= httpx.codes.BAD_REQUEST:
            code = _error_code(content)
            raise WorldModelRejected(
                f"context service refused the request ({status})", status, code
            )
        if status >= httpx.codes.MULTIPLE_CHOICES or status < httpx.codes.OK:
            raise WorldModelBadResponse(f"unexpected status {status}", status)
        try:
            return json.loads(content)
        except (ValueError, RecursionError):
            raise WorldModelBadResponse("response is not JSON", status) from None

    @staticmethod
    async def _read_capped(response: httpx.Response) -> bytes:
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                raise WorldModelBadResponse("response too large", response.status_code)
            chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    def _parse[M: BaseModel](model: type[M], body: object) -> M:
        try:
            return model.model_validate(body)
        except ValidationError:
            raise WorldModelBadResponse("response has an unexpected shape") from None
