"""Per-investigation runtime: deadlines, the activity trace and the one path to the model.

Every model call of every agent goes through call_model(). It applies the step's route from
the settings (exact model ids come from METERLESS_*_MODEL), the call budget, a per-call
timeout capped by the time left before the investigation deadline (SEC-061), one Ultra ->
Lightning fallback for deep steps, and records a TraceEvent with the Nightshift receipt.
"""

import asyncio
import inspect
import json
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from app.agent.board import BoardClient, CaseBoard
from app.agent.errors import StepFailed, StopInvestigation
from app.agent.models import (
    ActionProposal,
    Dimension,
    GapIssue,
    Report,
    TraceEvent,
)
from app.agent.prompts import PROMPTS
from app.core.settings import Settings
from app.llm.budget import CallBudget
from app.llm.errors import BudgetExhausted, LLMError, LLMUnauthorized
from app.llm.models import ChatResult, ModelRole
from app.score.model import RiskScore
from app.security.identity import UserContext
from app.tools.context import ToolContext

logger = logging.getLogger(__name__)

Clock = Callable[[], float]
TraceListener = Callable[[TraceEvent], None]
# Security: the id names the trace file and the case, so it is never free text.
INVESTIGATION_ID_PATTERN = re.compile(r"inv-[0-9a-f]{16}")


class ChatModel(Protocol):
    """What the agent needs from the model client (MeterlessClient satisfies it)."""

    async def chat(
        self,
        messages: Sequence[Mapping[str, Any]],
        role: ModelRole = "default",
        tools: Sequence[Mapping[str, Any]] | None = None,
        tool_choice: str | Mapping[str, Any] | None = None,
        response_format: Mapping[str, Any] | None = None,
        budget: CallBudget | None = None,
    ) -> ChatResult:
        """Send one chat completion."""
        ...


class Deadline:
    """Wall-time limit of one investigation (monotonic clock)."""

    def __init__(self, seconds: float, *, clock: Clock = time.monotonic) -> None:
        """Start the deadline now.

        Args:
            seconds: Time allowed.
            clock: Monotonic clock (injectable for tests).
        """
        self._clock = clock
        self._ends = clock() + seconds

    def remaining(self) -> float:
        """Return the seconds left (negative once passed)."""
        return self._ends - self._clock()

    @property
    def expired(self) -> bool:
        """Return True once the deadline has passed."""
        return self.remaining() <= 0


class Trace:
    """Agent activity trace of one investigation, optionally mirrored to a JSONL file.

    Attributes:
        events: Events in order.
        path: File the events are appended to (runs/<investigation_id>.jsonl), if any.
    """

    def __init__(
        self,
        budget: CallBudget,
        path: Path | None = None,
        listener: TraceListener | None = None,
    ) -> None:
        """Create an empty trace.

        Args:
            budget: Budget whose remaining calls each event records.
            path: JSONL file to append events to.
            listener: Called with every event as it happens (the API's live stream).
        """
        self.events: list[TraceEvent] = []
        self.path = path
        self._budget = budget
        self._file_failed = False
        self._listener = listener

    def add(self, agent: str, action: str, **fields: Any) -> TraceEvent:  # noqa: ANN401
        """Append an event and return it.

        Args:
            agent: Agent or step.
            action: What happened.
            **fields: Other TraceEvent fields.

        Returns:
            The event.
        """
        event = TraceEvent(
            seq=len(self.events) + 1,
            agent=agent,
            action=action,
            budget_left=self._budget.remaining,
            **fields,
        )
        self.events.append(event)
        self._write(event)
        self._notify(event)
        return event

    def _notify(self, event: TraceEvent) -> None:
        """Pass the event to the listener; a failing listener never fails the investigation."""
        if self._listener is None:
            return
        try:
            self._listener(event)
        except Exception as exc:  # noqa: BLE001 - the listener is the caller's code
            self._listener = None
            logger.warning(
                "trace listener failed",
                extra={"event": "trace_listener_failed", "error_type": type(exc).__name__},
            )

    def _write(self, event: TraceEvent) -> None:
        """Append one JSON line; a failing file never fails the investigation."""
        if self.path is None or self._file_failed:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(event.model_dump_json() + "\n")
        except OSError as exc:
            self._file_failed = True
            logger.warning(
                "trace file not writable",
                extra={"event": "trace_write_failed", "error_type": type(exc).__name__},
            )


@dataclass(frozen=True)
class SupplierRef:
    """A supplier the question is about (resolved within the user's scope).

    Attributes:
        supplier_id: Supplier id.
        country_iso: Site country.
        public: True when the supplier files with the SEC.
    """

    supplier_id: str
    country_iso: str
    public: bool


@dataclass
class Finding:
    """What research established for one dimension.

    Attributes:
        evidence_ids: Evidence found (status ok).
        gap_evidence_ids: Evidence that shows a gap (no data, not assessed).
        issues: Gap issues seen in the last round.
        retryable: True when another round may close the gap.
    """

    evidence_ids: list[str] = field(default_factory=list)
    gap_evidence_ids: list[str] = field(default_factory=list)
    issues: list[GapIssue] = field(default_factory=list)
    retryable: bool = False

    @property
    def covered(self) -> bool:
        """Return True when evidence was found."""
        return bool(self.evidence_ids)


@dataclass
class Run:
    """Dependencies and working state of one investigation.

    The LangGraph state only routes; the records an incomplete result must still report
    (evidence, findings, scores) live here, so a stop at any point keeps what was checked.
    """

    investigation_id: str
    tools: ToolContext
    llm: ChatModel
    budget: CallBudget
    deadline: Deadline
    board: CaseBoard
    trace: Trace
    step_models: Mapping[str, ModelRole]
    model_call_timeout: float
    tool_call_timeout: float
    question: str = ""
    suppliers: list[SupplierRef] = field(default_factory=list)
    plan: list[Dimension] = field(default_factory=list)
    findings: dict[Dimension, Finding] = field(default_factory=dict)
    scores: list[RiskScore] = field(default_factory=list)
    score_evidence: dict[str, str] = field(default_factory=dict)
    unscored: dict[str, str] = field(default_factory=dict)
    proposals: list[ActionProposal] = field(default_factory=list)
    injections: dict[str, int] = field(default_factory=dict)
    report: Report | None = None

    @property
    def user(self) -> UserContext:
        """Return the verified session identity."""
        return self.tools.user

    @property
    def supplier_ids(self) -> list[str]:
        """Return the resolved supplier ids."""
        return [s.supplier_id for s in self.suppliers]


def new_investigation_id() -> str:
    """Return a new investigation id (also a valid case board id)."""
    return f"inv-{uuid.uuid4().hex[:16]}"


def new_run(
    settings: Settings,
    *,
    tools: ToolContext,
    llm: ChatModel,
    board_client: BoardClient | None,
    investigation_id: str | None = None,
    clock: Clock = time.monotonic,
    trace_path: Path | None = None,
    on_event: TraceListener | None = None,
) -> Run:
    """Return a run with a fresh budget, deadline, case board and trace.

    Args:
        settings: Budget, deadlines and step routes.
        tools: Session-bound tool context (with its own evidence log).
        llm: Model client.
        board_client: World Model client, or None to keep the board in process only.
        investigation_id: Id to use (new by default).
        clock: Monotonic clock for the deadline.
        trace_path: JSONL file for the trace.
        on_event: Called with every trace event as it happens.

    Returns:
        The run.
    """
    investigation_id = investigation_id or new_investigation_id()
    budget = CallBudget(settings.investigation_call_budget)
    return Run(
        investigation_id=investigation_id,
        tools=tools,
        llm=llm,
        budget=budget,
        deadline=Deadline(settings.investigation_deadline_seconds, clock=clock),
        board=CaseBoard(board_client, investigation_id, suppliers=set()),
        trace=Trace(budget, trace_path, on_event),
        step_models=settings.agent_step_models(),
        model_call_timeout=settings.model_call_timeout_seconds,
        tool_call_timeout=settings.tool_call_timeout_seconds,
    )


class _Retryable(Exception):  # noqa: N818
    """A model call failure that a fallback (or the step's own fallback) may handle."""

    def __init__(self, error_type: str) -> None:
        super().__init__(error_type)
        self.error_type = error_type


def _tool_names(tools: Sequence[Mapping[str, Any]] | None) -> list[str]:
    """Return the function names of OpenAI tool definitions."""
    return [str(t.get("function", {}).get("name")) for t in tools or []]


async def _attempt(
    run: Run,
    agent: str,
    route: ModelRole,
    messages: Sequence[Mapping[str, Any]],
    tools: Sequence[Mapping[str, Any]] | None,
) -> ChatResult:
    """Make one model call on one route and trace it."""
    remaining = run.deadline.remaining()
    if remaining <= 0:
        run.trace.add(agent, "deadline_reached", model_route=route)
        raise StopInvestigation("deadline")
    cut_by_deadline = remaining <= run.model_call_timeout
    version = PROMPTS.get(agent)
    started = time.monotonic()

    def failed(error_type: str) -> None:
        run.trace.add(
            agent,
            "model_call_failed",
            model_route=route,
            error_type=error_type,
            latency_ms=round((time.monotonic() - started) * 1000),
            prompt_version=version,
        )

    try:
        async with asyncio.timeout(min(run.model_call_timeout, remaining)):
            result = await run.llm.chat(messages, role=route, tools=tools, budget=run.budget)
    except BudgetExhausted:
        failed("BudgetExhausted")
        raise StopInvestigation("budget_exhausted") from None
    except LLMUnauthorized:
        failed("LLMUnauthorized")
        raise StopInvestigation("model_unauthorized") from None
    except TimeoutError:
        failed("TimeoutError")
        if cut_by_deadline:
            raise StopInvestigation("deadline") from None
        raise _Retryable("TimeoutError") from None
    except (LLMError, ValueError) as exc:
        failed(type(exc).__name__)
        raise _Retryable(type(exc).__name__) from None
    run.trace.add(
        agent,
        "model_call",
        model_route=route,
        model_requested=result.requested_model,
        routed_model=result.routed_model,
        receipt_id=result.receipt_id,
        cache_status=result.cache_status,
        from_cache=result.from_cache,
        tokens=result.usage.total_tokens,
        latency_ms=result.latency_ms,
        retries=result.retries,
        tools=_tool_names(tools),
        prompt_version=version,
        note="client_fallback" if result.fallback_used else None,
    )
    return result


async def call_model(
    run: Run,
    agent: str,
    messages: Sequence[Mapping[str, Any]],
    *,
    tools: Sequence[Mapping[str, Any]] | None = None,
) -> ChatResult:
    """Call the model for one agent step.

    Args:
        run: The investigation run.
        agent: Agent name (its route comes from the settings).
        messages: Messages to send.
        tools: Tool definitions the agent may call.

    Returns:
        The model's answer.

    Raises:
        StopInvestigation: Budget used up, deadline passed, or the token was rejected.
        StepFailed: The call failed (after one fallback to the default route for deep steps).
    """
    route = run.step_models[agent]
    try:
        return await _attempt(run, agent, route, messages, tools)
    except _Retryable as first:
        if route != "deep":
            raise StepFailed(agent, first.error_type) from None
    run.trace.add(agent, "model_fallback", model_route="default", note=f"from_{route}")
    logger.warning(
        "deep model failed, falling back to the default model",
        extra={"event": "model_fallback", "agent": agent},
    )
    try:
        return await _attempt(run, agent, "default", messages, tools)
    except _Retryable as second:
        raise StepFailed(agent, second.error_type) from None


async def bounded[T](run: Run, work: Awaitable[T]) -> T:
    """Await tool work under the tool timeout, capped by the time left (SEC-061).

    The snapshot cache shields its loads, so a caller that times out does not cancel a load
    other callers share.

    Args:
        run: The investigation run.
        work: The tool call or score computation.

    Returns:
        Its result.

    Raises:
        StopInvestigation: If the investigation deadline passes (or has passed).
        TimeoutError: If the tool timeout passes first.
    """
    remaining = run.deadline.remaining()
    if remaining <= 0:
        if inspect.iscoroutine(work):
            work.close()
        raise StopInvestigation("deadline")
    try:
        async with asyncio.timeout(min(run.tool_call_timeout, remaining)):
            return await work
    except TimeoutError:
        if remaining <= run.tool_call_timeout:
            raise StopInvestigation("deadline") from None
        raise


def payload_message(payload: Mapping[str, Any]) -> dict[str, str]:
    """Return a user message holding a JSON payload (data for the model, never instructions)."""
    return {"role": "user", "content": json.dumps(payload, default=str, ensure_ascii=False)}
