"""Evaluator: read the case board, find gaps and contradictions, decide whether to go on.

Python decides coverage from the research findings; Lightning reads the World Model timeline
and may add gaps or contradictions. Its output is checked: dimensions must be planned, cited
evidence ids must exist, and gaps on dimensions that are known to be missing (not assessed,
hidden from the role, source unavailable) are ignored. The verdict is sufficient when no
retryable gap and no contradiction is left. If the model step fails, the Python verdict stands.
"""

from typing import Any

from app.agent.errors import StepFailed
from app.agent.json_output import ask_json
from app.agent.models import Dimension, EvaluatorOutput, Gap, Verdict
from app.agent.prompts import load_prompt
from app.agent.research import RETRYABLE
from app.agent.runtime import Finding, Run, payload_message

MAX_EXTRA_ROUNDS = 2
MAX_TIMELINE_EVENTS = 200


def _python_gaps(run: Run) -> list[Gap]:
    """Return a gap for every planned dimension without evidence."""
    gaps = []
    for dimension in run.plan:
        finding = run.findings.get(dimension, Finding())
        if finding.covered:
            continue
        issue = finding.issues[0] if finding.issues else "missing_evidence"
        gaps.append(
            Gap(
                dimension=dimension,
                issue=issue,
                retryable=issue in RETRYABLE,
                evidence_ids=finding.gap_evidence_ids,
            )
        )
    return gaps


def _coverage(run: Run) -> dict[str, str]:
    """Return "covered" or the first gap issue per planned dimension."""
    out: dict[str, str] = {}
    for dimension in run.plan:
        finding = run.findings.get(dimension, Finding())
        out[dimension] = (
            "covered" if finding.covered else (finding.issues or ["missing_evidence"])[0]
        )
    return out


async def _timeline_payload(run: Run) -> list[dict[str, Any]]:
    """Return the case board timeline as model input (summaries are Python templates)."""
    events = await run.board.timeline()
    return [
        {
            "kind": e.kind,
            "agent": e.agent,
            "summary": e.summary,
            "evidence_ids": e.evidence_ids,
            "supplier": e.subjects.supplier,
            "data": e.data,
        }
        for e in events[-MAX_TIMELINE_EVENTS:]
    ]


async def _model_findings(run: Run, gaps: list[Gap]) -> tuple[list[Gap], list[Dimension]]:
    """Return extra gaps and contradicted dimensions named by the model (validated)."""
    messages = [
        {"role": "system", "content": load_prompt("evaluator").text},
        payload_message(
            {"plan": run.plan, "coverage": _coverage(run), "timeline": await _timeline_payload(run)}
        ),
    ]
    try:
        answer = await ask_json(run, "evaluator", messages, EvaluatorOutput)
    except StepFailed as exc:
        run.trace.add(
            "evaluator", "step_degraded", note="python_verdict_only", error_type=exc.error_type
        )
        return [], []
    known = {g.dimension for g in gaps}
    extra = [
        Gap(dimension=g.dimension, issue=g.issue, retryable=True)
        for g in answer.gaps
        if g.dimension in run.plan and g.dimension not in known
    ]
    evidence_ids = {r.evidence_id for r in run.tools.evidence.all()}
    contradicted: list[Dimension] = []
    for item in answer.contradictions:
        if item.dimension not in run.plan or not set(item.evidence_ids) <= evidence_ids:
            run.trace.add(
                "evaluator",
                "output_ignored",
                step=item.dimension,
                note="contradiction_not_grounded",
            )
            continue
        if item.dimension not in contradicted:
            contradicted.append(item.dimension)
            await run.board.add(
                "contradiction_found",
                "evaluator",
                f"Evidence on {item.dimension} disagrees.",
                item.evidence_ids,
                data={"dimension": item.dimension},
            )
    return extra, contradicted


async def run_evaluator(run: Run, round_: int) -> Verdict:
    """Judge the evidence after a research round and record the verdict.

    Args:
        run: The investigation run.
        round_: The round just finished (0 is the first).

    Returns:
        The verdict.

    Raises:
        StopInvestigation: On budget exhaustion, deadline or a rejected model token.
    """
    gaps = _python_gaps(run)
    extra, contradicted = await _model_findings(run, gaps)
    gaps += extra
    gaps += [
        Gap(dimension=d, issue="contradiction", retryable=True)
        for d in contradicted
        if d not in {g.dimension for g in gaps}
    ]
    for gap in extra:
        await run.board.add(
            "gap_found",
            "evaluator",
            f"Gap in {gap.dimension}: {gap.issue}.",
            data={"dimension": gap.dimension, "issue": gap.issue},
        )
    verdict = Verdict(
        sufficient=not any(g.retryable for g in gaps),
        covered={d: run.findings.get(d, Finding()).covered for d in run.plan},
        gaps=gaps,
        contradictions=contradicted,
    )
    label = "sufficient" if verdict.sufficient else "insufficient"
    await run.board.add(
        "verdict",
        "evaluator",
        f"Round {round_ + 1}: evidence {label}.",
        data={"round": round_ + 1, "sufficient": verdict.sufficient, "gaps": len(gaps)},
    )
    run.trace.add(
        "evaluator",
        "step_done",
        step=f"round_{round_ + 1}",
        verdict=label,
        note=",".join(f"{g.dimension}:{g.issue}" for g in gaps) or None,
    )
    return verdict
