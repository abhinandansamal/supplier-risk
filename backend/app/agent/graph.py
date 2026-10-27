"""The investigation graph (LangGraph) and its entry point, Investigator.run_investigation.

guard -> planner -> research -> evaluator -> (research again | score) -> recommender -> writer
-> verifier -> (writer once more | end | escalation). The evaluator allows at most
MAX_EXTRA_ROUNDS extra research rounds; still unsatisfied, the case is scored and escalated
with the Python fallback report and the gap list. Verification failing twice escalates too.

The graph state only routes. Evidence, findings, scores, proposals, the report and the trace
live on the Run, so a stop at any point (budget, deadline, rejected token) still returns
"incomplete" with what was checked.
"""

import asyncio
import logging
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agent.assess import run_score
from app.agent.board import BoardClient
from app.agent.errors import StopInvestigation, StopReason
from app.agent.evaluator import MAX_EXTRA_ROUNDS, run_evaluator
from app.agent.guard import REJECTION_MESSAGES, run_guard
from app.agent.models import Dimension, Gap, InvestigationResult, Status
from app.agent.planner import run_planner
from app.agent.recommender import run_recommender
from app.agent.report import fallback_report
from app.agent.research import run_research
from app.agent.runtime import ChatModel, Run, new_investigation_id, new_run
from app.agent.verifier import Problem
from app.agent.writer import run_verifier, run_writer
from app.core.logging import investigation_id_var
from app.core.settings import Settings
from app.evidence.models import EvidenceLog
from app.security.identity import UserContext
from app.tools.cache import SnapshotCache
from app.tools.context import ToolContext

logger = logging.getLogger(__name__)

# Security: a hard bound on graph steps (T8), above the longest legal path.
RECURSION_LIMIT = 40
# The backstop deadline fires only if a step ignores the per-call deadlines.
BACKSTOP_GRACE_SECONDS = 5.0
STOP_MESSAGES: dict[StopReason, str] = {
    "budget_exhausted": "Stopped early: the model call budget is used up (rate limited).",
    "deadline": "Stopped early: the time limit for one investigation was reached.",
    "model_unauthorized": "Stopped early: the model runtime rejected the app's token.",
    "internal_error": "Stopped early: an internal error occurred; it has been logged.",
}
EVALUATOR_ESCALATION = "Escalated: evidence still incomplete or contradictory after 3 rounds"
NO_SCORE_ESCALATION = "Escalated: no supplier could be scored, so no claim can be made"
VERIFIER_ESCALATION = "Escalated: claims could not be verified after one rewrite"


class GraphState(TypedDict, total=False):
    """Routing state of the graph (the records live on the Run)."""

    rejected: str
    round: int
    todo: list[Dimension]
    gaps: list[Gap]
    escalate: str
    problems: list[Problem]
    rewrites_left: int
    route: Literal["research", "score", "rewrite", "escalate", "done"]


def _build(run: Run) -> Any:  # noqa: ANN401, C901 - LangGraph's compiled type is generic
    """Return the compiled graph for one run."""

    async def guard(state: GraphState) -> GraphState:
        reason = await run_guard(run)
        return {"rejected": reason} if reason else {}

    async def planner(state: GraphState) -> GraphState:
        return {"todo": await run_planner(run), "round": 0, "gaps": []}

    async def research(state: GraphState) -> GraphState:
        await run_research(run, state["todo"], state["round"], state.get("gaps", []))
        return {}

    async def evaluator(state: GraphState) -> GraphState:
        round_ = state["round"]
        verdict = await run_evaluator(run, round_)
        retry = sorted({g.dimension for g in verdict.gaps if g.retryable}, key=run.plan.index)
        if verdict.sufficient:
            return {"route": "score", "gaps": verdict.gaps}
        if round_ < MAX_EXTRA_ROUNDS:
            logger.warning(
                "evaluator asked for another research round",
                extra={"event": "evaluator_round", "round": round_ + 1, "gaps": len(retry)},
            )
            return {"route": "research", "todo": retry, "round": round_ + 1, "gaps": verdict.gaps}
        return {"route": "score", "escalate": "evaluator_unsatisfied", "gaps": verdict.gaps}

    async def score(state: GraphState) -> GraphState:
        if not await run_score(run):
            return {"escalate": "no_score"}
        return {}

    async def recommender(state: GraphState) -> GraphState:
        await run_recommender(run)
        return {"rewrites_left": 1, "problems": []}

    async def writer(state: GraphState) -> GraphState:
        await run_writer(run, state.get("problems", []))
        return {}

    async def verifier(state: GraphState) -> GraphState:
        rewrites_left = state.get("rewrites_left", 0)
        problems = await run_verifier(run, rewrites_left)
        if not problems:
            return {"route": "done"}
        if rewrites_left and run.report and run.report.source == "writer":
            return {"route": "rewrite", "problems": problems, "rewrites_left": 0}
        return {"route": "escalate", "escalate": "verification_failed"}

    async def escalation(state: GraphState) -> GraphState:
        reason = state.get("escalate", "evaluator_unsatisfied")
        gaps = [g for g in state.get("gaps", []) if g.retryable]
        if reason == "evaluator_unsatisfied":
            missing = [f"{g.dimension.capitalize()}: {g.issue.replace('_', ' ')}" for g in gaps]
            run.report = fallback_report(run, missing=missing, note=EVALUATOR_ESCALATION)
        elif reason == "no_score":
            run.report = fallback_report(run, note=NO_SCORE_ESCALATION)
        elif run.report is not None:
            run.report = run.report.model_copy(
                update={"notes": [*run.report.notes, VERIFIER_ESCALATION]}
            )
        await run.board.add(
            "escalated",
            "escalation",
            f"Handed to a human: {reason}.",
            data={"reason": reason, "gaps": len(gaps)},
        )
        run.trace.add("escalation", "step_done", verdict="escalated", note=reason)
        return {}

    graph = StateGraph(GraphState)
    for name, node in (
        ("guard", guard),
        ("planner", planner),
        ("research", research),
        ("evaluator", evaluator),
        ("score", score),
        ("recommender", recommender),
        ("writer", writer),
        ("verifier", verifier),
        ("escalation", escalation),
    ):
        graph.add_node(name, node)
    graph.add_edge(START, "guard")
    graph.add_conditional_edges(
        "guard", lambda s: END if s.get("rejected") else "planner", ["planner", END]
    )
    graph.add_edge("planner", "research")
    graph.add_edge("research", "evaluator")
    graph.add_conditional_edges("evaluator", lambda s: s["route"], ["research", "score"])
    graph.add_conditional_edges(
        "score",
        lambda s: "escalation" if s.get("escalate") else "recommender",
        ["escalation", "recommender"],
    )
    graph.add_edge("recommender", "writer")
    graph.add_edge("writer", "verifier")
    graph.add_conditional_edges(
        "verifier",
        lambda s: {"rewrite": "writer", "escalate": "escalation"}.get(s["route"], END),
        ["writer", "escalation", END],
    )
    graph.add_edge("escalation", END)
    return graph.compile()


def _checked(run: Run) -> list[Dimension]:
    """Return the planned dimensions with evidence."""
    return [d for d in run.plan if (f := run.findings.get(d)) is not None and f.covered]


def _result(
    run: Run,
    status: Status,
    started: float,
    reason: str | None = None,
    **fields: Any,  # noqa: ANN401
) -> InvestigationResult:
    """Return the result of a run."""
    return InvestigationResult(
        investigation_id=run.investigation_id,
        status=status,
        reason=reason,
        role=run.user.role,
        suppliers=run.supplier_ids,
        plan=run.plan,
        checked=_checked(run),
        report=run.report,
        scores=run.scores,
        evidence=run.tools.evidence.all(),
        proposals=run.proposals,
        trace=run.trace.events,
        model_calls=run.budget.used,
        memory=run.board.memory,
        duration_ms=round((time.monotonic() - started) * 1000),
        **fields,
    )


async def _incomplete(run: Run, reason: StopReason, started: float) -> InvestigationResult:
    """Finish a stopped investigation with what was checked (scores need no model)."""
    run.trace.add("executor", "investigation_stopped", note=reason)
    if reason in ("budget_exhausted", "model_unauthorized") and run.suppliers and not run.scores:
        try:
            await run_score(run)
        except StopInvestigation:
            pass
    checked = _checked(run)
    missing = [
        f"{STOP_MESSAGES[reason]} Checked: {', '.join(checked) or 'nothing'}.",
        *(f"{d.capitalize()}: not checked" for d in run.plan if d not in checked),
    ]
    run.report = fallback_report(run, missing=missing, note=STOP_MESSAGES[reason])
    gaps = [
        Gap(dimension=d, issue="missing_evidence", retryable=True)
        for d in run.plan
        if d not in checked
    ]
    return _result(run, "incomplete", started, reason, message=STOP_MESSAGES[reason], gaps=gaps)


class Investigator:
    """Runs investigations: one per call, sharing the long-lived clients and the cache."""

    def __init__(
        self,
        settings: Settings,
        *,
        llm: ChatModel,
        cache: SnapshotCache,
        board_client: BoardClient | None,
        today: date | None = None,
        trace_dir: Path | None = None,
    ) -> None:
        """Create the investigator.

        Args:
            settings: Budget, deadlines and step routes.
            llm: Model client (MeterlessClient).
            cache: Snapshot cache shared by the app.
            board_client: World Model client, or None for an in-process board.
            today: Reference date for the tools (UTC today by default).
            trace_dir: Directory for runs/<investigation_id>.jsonl, if any.
        """
        self._settings = settings
        self._llm = llm
        self._cache = cache
        self._board_client = board_client
        self._today = today
        self._trace_dir = trace_dir

    async def run_investigation(self, user: UserContext, question: str) -> InvestigationResult:
        """Investigate one question for one verified user.

        Args:
            user: Verified session identity (role and category scope).
            question: The user's question (untrusted).

        Returns:
            The result: completed, incomplete, escalated or rejected, with report, scores,
            claims, evidence, proposals and trace.
        """
        investigation_id = new_investigation_id()
        token = investigation_id_var.set(investigation_id)
        started = time.monotonic()
        tools = ToolContext(
            user=user,
            cache=self._cache,
            evidence=EvidenceLog(),
            today=self._today or datetime.now(UTC).date(),
        )
        trace_path = self._trace_dir / f"{investigation_id}.jsonl" if self._trace_dir else None
        run = new_run(
            self._settings,
            tools=tools,
            llm=self._llm,
            board_client=self._board_client,
            investigation_id=investigation_id,
            trace_path=trace_path,
        )
        run.question = question
        logger.info(
            "investigation started",
            extra={"event": "investigation_started", "role": user.role, "chars": len(question)},
        )
        try:
            result = await self._invoke(run, started)
        finally:
            investigation_id_var.reset(token)
        logger.info(
            "investigation finished",
            extra={
                "event": "investigation_finished",
                "investigation_id": investigation_id,
                "status": result.status,
                "reason": result.reason,
                "model_calls": result.model_calls,
                "duration_ms": result.duration_ms,
            },
        )
        return result

    async def _invoke(self, run: Run, started: float) -> InvestigationResult:
        """Run the graph and turn its outcome into a result."""
        backstop = self._settings.investigation_deadline_seconds + BACKSTOP_GRACE_SECONDS
        try:
            async with asyncio.timeout(backstop):
                state = await _build(run).ainvoke({}, {"recursion_limit": RECURSION_LIMIT})
        except StopInvestigation as stop:
            return await _incomplete(run, stop.reason, started)
        except TimeoutError:
            return await _incomplete(run, "deadline", started)
        except Exception:  # noqa: BLE001 - last resort (SEC-095): never crash the caller
            logger.exception(
                "investigation failed", extra={"event": "investigation_failed", "agent": "graph"}
            )
            return await _incomplete(run, "internal_error", started)
        if state.get("rejected"):
            reason = state["rejected"]
            return _result(run, "rejected", started, reason, message=REJECTION_MESSAGES[reason])
        if state.get("escalate"):
            gaps = [g for g in state.get("gaps", []) if g.retryable]
            return _result(run, "escalated", started, state["escalate"], gaps=gaps)
        return _result(run, "completed", started)
