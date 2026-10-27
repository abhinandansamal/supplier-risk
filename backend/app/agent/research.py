"""Research: one Lightning step per planned dimension, run one after another.

Each step offers the model only that dimension's tools the role may use and runs the tool
calls it returns. Python checks every call before it runs: the tool must be offered, a
supplier id or country must belong to the question's suppliers, and at most
MAX_TOOL_CALLS_PER_STEP calls run. Tool results never go back to the model; they become
evidence records and case board events. Weather needs no model (one tool, always "not
assessed" until NOAA is onboarded), so it runs directly.

Untrusted ERP text in a result is scanned with the guard's detector; a hit is recorded as
injection_detected (field name and evidence id only, never the text).
"""

import logging
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from app.agent.errors import StepFailed
from app.agent.models import Dimension, Gap, GapIssue
from app.agent.prompts import load_prompt
from app.agent.runtime import Finding, Run, bounded, call_model, payload_message
from app.memory.world_model import AgentName
from app.security.injection import scan_untrusted
from app.tools.catalogue import TOOLS, ToolResult, run_tool
from app.tools.errors import SourceUnavailable, ToolError, ToolForbidden

logger = logging.getLogger(__name__)

MAX_TOOL_CALLS_PER_STEP = 8
DIMENSION_TOOLS: dict[Dimension, tuple[str, ...]] = {
    "delivery": ("delivery_history", "open_orders_at_risk"),
    "concentration": ("single_source_parts", "open_orders_at_risk", "supplier_profile"),
    "financial": ("financial_trend",),
    "country": ("country_indicators",),
    "weather": ("weather_extremes",),
}
AGENT_OF: dict[Dimension, AgentName] = {
    "delivery": "erp",
    "concentration": "erp",
    "financial": "financial",
    "country": "country",
    "weather": "weather",
}
# Gaps another round may close; the others are known and reported as missing.
RETRYABLE: frozenset[GapIssue] = frozenset(
    {"missing_evidence", "needs_detail", "tool_failed", "no_tool_call", "contradiction"}
)


def offered_tools(dimension: Dimension, role: str) -> list[str]:
    """Return the tools of a dimension that a role may call."""
    return [name for name in DIMENSION_TOOLS[dimension] if role in TOOLS[name].roles]


def tool_definition(name: str) -> dict[str, Any]:
    """Return the OpenAI function definition of a catalogue tool."""
    spec = TOOLS[name]
    doc = (spec.run.__doc__ or name).strip().splitlines()[0]
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": doc,
            "parameters": spec.args.model_json_schema(),
        },
    }


def _issue(finding: Finding, issue: GapIssue) -> None:
    """Add a gap issue to a finding."""
    if issue not in finding.issues:
        finding.issues.append(issue)


async def _record_result(
    run: Run, dimension: Dimension, finding: Finding, result: ToolResult, supplier: str | None
) -> None:
    """Turn one tool result into evidence, events and injection checks."""
    agent = AGENT_OF[dimension]
    run.trace.add(
        "research",
        "tool_call",
        step=dimension,
        tools=[result.tool],
        evidence_ids=list(result.evidence_ids),
        verdict=result.status,
    )
    for hit in scan_untrusted(result.data):
        run.injections[hit.field] = run.injections.get(hit.field, 0) + 1
        run.trace.add(
            "research",
            "injection_detected",
            step=dimension,
            tools=[result.tool],
            evidence_ids=list(result.evidence_ids),
            note=hit.field,
            verdict=hit.rule,
        )
        logger.warning(
            "instruction-like text in tool data ignored",
            extra={"event": "injection_detected", "tool": result.tool, "field": hit.field},
        )
        await run.board.add(
            "injection_detected",
            agent,
            f"Instruction-like text in {hit.field} was ignored.",
            result.evidence_ids,
            supplier,
            {"field": hit.field, "rule": hit.rule, "tool": result.tool},
        )
    data = {"tool": result.tool, "status": result.status}
    if result.status == "ok":
        finding.evidence_ids.extend(result.evidence_ids)
        await run.board.add(
            "evidence_found",
            agent,
            f"{result.tool} found evidence.",
            result.evidence_ids,
            supplier,
            data,
        )
        return
    finding.gap_evidence_ids.extend(result.evidence_ids)
    _issue(finding, _gap_issue(result))
    await run.board.add(
        "no_data",
        agent,
        f"{result.tool} returned {result.status}.",
        result.evidence_ids,
        supplier,
        data,
    )


def _gap_issue(result: ToolResult) -> GapIssue:
    """Return the gap a non-ok result shows (architecture 8.6).

    An empty delivery window is worth one wider query; other empty results (a private
    supplier's financials, a country without the indicator) are known and reported as missing.
    """
    if result.status == "not_assessed":
        return "not_assessed"
    return "missing_evidence" if result.tool == "delivery_history" else "no_data"


def _rejection(run: Run, offered: list[str], name: str, args: object) -> str | None:
    """Return why a tool call must not run, or None.

    The arguments are validated with the tool's strict model first (SEC-095), so the scope
    checks only ever see well-typed values.
    """
    if name not in offered:
        return "tool_not_offered"
    if not isinstance(args, Mapping):
        return "invalid_arguments"
    try:
        validated = TOOLS[name].args.model_validate(dict(args))
    except ValidationError:
        return "invalid_arguments"
    supplier_id = getattr(validated, "supplier_id", None)
    if supplier_id is not None and supplier_id not in run.supplier_ids:
        # Security: the model may only reach the suppliers the guard resolved (T2).
        return "supplier_not_in_question"
    country = getattr(validated, "country_iso", None)
    if country is not None and country not in {s.country_iso for s in run.suppliers}:
        return "country_not_in_question"
    return None


async def _execute(
    run: Run,
    dimension: Dimension,
    finding: Finding,
    offered: list[str],
    name: str,
    args: Mapping[str, Any],
) -> None:
    """Check and run one tool call."""
    reason = _rejection(run, offered, name, args)
    if reason is None:
        try:
            result = await bounded(run, run_tool(run.tools, name, args))
        except ValidationError:
            reason = "invalid_arguments"
        except ToolForbidden:
            reason = "tool_not_offered"
        except SourceUnavailable:
            _issue(finding, "source_unavailable")
            run.trace.add(
                "research", "tool_failed", step=dimension, tools=[name], note="source_unavailable"
            )
            return
        except (ToolError, TimeoutError) as exc:
            _issue(finding, "tool_failed")
            run.trace.add(
                "research",
                "tool_failed",
                step=dimension,
                tools=[name],
                error_type=type(exc).__name__,
            )
            await run.board.add(
                "task_failed", AGENT_OF[dimension], f"{name} failed.", data={"tool": name}
            )
            return
        else:
            supplier = args.get("supplier_id")
            if supplier is None and len(run.suppliers) == 1:
                supplier = run.suppliers[0].supplier_id
            await _record_result(run, dimension, finding, result, supplier)
            return
    run.trace.add("research", "tool_call_rejected", step=dimension, tools=[name[:64]], note=reason)
    logger.warning(
        "model tool call rejected",
        extra={"event": "tool_call_rejected", "step": dimension, "reason": reason},
    )
    _issue(finding, "tool_failed")


def _payload(run: Run, dimension: Dimension, round_: int, gaps: list[Gap]) -> dict[str, Any]:
    """Return the research step's input."""
    return {
        "dimension": dimension,
        "round": round_,
        "suppliers": [
            {"supplier_id": s.supplier_id, "country_iso": s.country_iso} for s in run.suppliers
        ],
        "gaps": [
            {"dimension": g.dimension, "issue": g.issue} for g in gaps if g.dimension == dimension
        ],
    }


async def _model_step(
    run: Run,
    dimension: Dimension,
    finding: Finding,
    offered: list[str],
    round_: int,
    gaps: list[Gap],
) -> None:
    """Ask the model which tools to call for one dimension and run them."""
    messages = [
        {"role": "system", "content": load_prompt("research").text},
        payload_message(_payload(run, dimension, round_, gaps)),
    ]
    try:
        answer = await call_model(
            run, "research", messages, tools=[tool_definition(n) for n in offered]
        )
    except StepFailed as exc:
        _issue(finding, "tool_failed")
        run.trace.add("research", "step_degraded", step=dimension, error_type=exc.error_type)
        await run.board.add("task_failed", AGENT_OF[dimension], f"Research on {dimension} failed.")
        return
    calls = answer.tool_calls
    if not calls:
        _issue(finding, "no_tool_call")
        return
    if len(calls) > MAX_TOOL_CALLS_PER_STEP:
        run.trace.add("research", "tool_calls_capped", step=dimension, note="too_many_tool_calls")
        calls = calls[:MAX_TOOL_CALLS_PER_STEP]
    for call in calls:
        await _execute(run, dimension, finding, offered, call.name, call.arguments)


async def research_dimension(
    run: Run, dimension: Dimension, round_: int, gaps: list[Gap]
) -> Finding:
    """Research one dimension and update run.findings.

    Args:
        run: The investigation run.
        dimension: Dimension to research.
        round_: 0 for the first round, 1-2 for extra rounds.
        gaps: The evaluator's gaps (only this dimension's are passed on).

    Returns:
        The dimension's finding (evidence from earlier rounds is kept).

    Raises:
        StopInvestigation: On budget exhaustion, deadline or a rejected model token.
    """
    finding = run.findings.setdefault(dimension, Finding())
    finding.issues = []
    offered = offered_tools(dimension, run.user.role)
    if not offered:
        _issue(finding, "hidden_from_role")
    elif dimension == "weather":
        for supplier in run.suppliers:
            await _execute(
                run,
                dimension,
                finding,
                offered,
                "weather_extremes",
                {"supplier_id": supplier.supplier_id},
            )
    else:
        await _model_step(run, dimension, finding, offered, round_, gaps)
    finding.retryable = not finding.covered and any(i in RETRYABLE for i in finding.issues)
    if not finding.covered:
        issue = finding.issues[0] if finding.issues else "missing_evidence"
        await run.board.add(
            "gap_found",
            AGENT_OF[dimension],
            f"No evidence for {dimension} yet ({issue}).",
            finding.gap_evidence_ids,
            data={"dimension": dimension, "issue": issue, "retryable": finding.retryable},
        )
    run.trace.add(
        "research",
        "step_done",
        step=dimension,
        evidence_ids=list(finding.evidence_ids),
        verdict="covered" if finding.covered else "gap",
        note=",".join(finding.issues) or None,
    )
    return finding


async def run_research(run: Run, dimensions: list[Dimension], round_: int, gaps: list[Gap]) -> None:
    """Research the given dimensions one after another.

    Args:
        run: The investigation run.
        dimensions: Dimensions to research this round.
        round_: Round number.
        gaps: The evaluator's gaps from the previous round.
    """
    for dimension in dimensions:
        await research_dimension(run, dimension, round_, gaps)
