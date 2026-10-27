"""Score step: the deterministic risk score of each supplier (Python, never a model).

Each score gets one computed evidence record holding its numbers (total, coverage, every
sub-score and weight), so claims about the score can cite it and the verifier can check them.
"""

from app.agent.runtime import Run, bounded
from app.evidence.models import FactValue
from app.score.model import RiskScore
from app.tools.context import record_computed
from app.tools.errors import SourceUnavailable, ToolError
from app.tools.scoring import compute_score

SCORE_TOOL = "risk_score"


def score_facts(score: RiskScore) -> dict[str, FactValue]:
    """Return the numbers of a score as evidence facts."""
    facts: dict[str, FactValue] = {
        "score_total": score.total,
        "coverage_pct": score.coverage_pct,
        "rating": score.rating,
        "critical_signals": len(score.critical_signals),
    }
    for component in score.components:
        facts[f"{component.name}_score"] = component.value
        facts[f"{component.name}_weight"] = component.weight
    return facts


async def run_score(run: Run) -> list[RiskScore]:
    """Score every supplier of the question and record the score evidence.

    Args:
        run: The investigation run.

    Returns:
        The scores (also stored on run.scores); a supplier whose ERP data is unavailable is
        left out and traced.

    Raises:
        StopInvestigation: If the investigation deadline passes.
    """
    for supplier_id in run.supplier_ids:
        if supplier_id in run.score_evidence:
            continue
        try:
            score = await bounded(run, compute_score(run.tools, supplier_id))
        except (ToolError, TimeoutError) as exc:
            unavailable = isinstance(exc, SourceUnavailable)
            run.unscored[supplier_id] = "source unavailable" if unavailable else "tool failed"
            run.trace.add("score", "score_failed", step=supplier_id, error_type=type(exc).__name__)
            continue
        cited = sorted({e for c in score.components for e in c.evidence_ids})
        evidence_id = record_computed(
            run.tools,
            SCORE_TOOL,
            {"supplier_id": supplier_id},
            score_facts(score),
            cited,
            note=f"weights {score.weights_version}, score rules {score.score_rules_version}",
        )
        run.scores.append(score)
        run.score_evidence[supplier_id] = evidence_id
        await run.board.add(
            "evidence_found",
            "executor",
            f"Risk score computed: {score.rating}.",
            [evidence_id],
            supplier_id,
            {"total": score.total, "coverage_pct": score.coverage_pct, "rating": score.rating},
        )
        run.trace.add(
            "score",
            "step_done",
            step=supplier_id,
            evidence_ids=[evidence_id],
            verdict=score.rating,
            note=",".join(s.component for s in score.critical_signals) or None,
        )
    return run.scores
