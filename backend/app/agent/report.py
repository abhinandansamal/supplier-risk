"""Report helpers: what the writer and recommender see, and the Python fallback report.

The model sees evidence as facts, notes and parameters only, never sample rows, so ERP free
text (buyer notes, names, descriptions) does not reach the writer. Headline, notes and the
"missing" items from the score are built in Python. The fallback report quotes the score's
component reasons, whose numbers are all in the cited evidence facts.
"""

from typing import Any

from app.agent.models import Claim, Report
from app.agent.runtime import Run

FIELD_LABELS = {
    "buyer_notes": "buyer note",
    "part_description": "part description",
    "name": "supplier name",
}
MAX_WRITER_EVIDENCE = 60
_GAP_LINES = frozenset({"source_unavailable", "tool_failed", "missing_evidence", "no_tool_call"})


def headline(run: Run) -> str:
    """Return one Python-built line per supplier with its computed rating."""
    labels = {s.supplier_id: s.label for s in run.scores}
    return (
        "; ".join(f"{sid}: {labels.get(sid, 'not scored')}" for sid in run.supplier_ids)
        or "No supplier scored"
    )


def _capitalised(text: str) -> str:
    return text[:1].upper() + text[1:]


def score_missing(run: Run) -> list[str]:
    """Return the score components that could not be assessed, one line each."""
    lines: list[str] = []
    for score in run.scores:
        for component in score.components:
            line = _capitalised(component.reason.rstrip("."))
            if not component.assessed and line not in lines:
                lines.append(line)
    return lines


def gap_missing(run: Run) -> list[str]:
    """Return one line per planned dimension that research could not cover.

    Only reasons the score does not already explain are listed (source down, tool failed,
    no evidence found).
    """
    lines = [f"{sid}: risk score not computed ({why})" for sid, why in run.unscored.items()]
    for dimension in run.plan:
        finding = run.findings.get(dimension)
        if finding is None or finding.covered:
            continue
        for issue in finding.issues:
            if issue in _GAP_LINES:
                lines.append(f"{dimension.capitalize()}: {issue.replace('_', ' ')}")
                break
    return lines


def missing_items(run: Run) -> list[str]:
    """Return research gaps, then score components that could not be assessed."""
    out = gap_missing(run)
    return out + [line for line in score_missing(run) if line not in out]


def notes(run: Run, *, fallback: str | None = None) -> list[str]:
    """Return the Python-built notes of the report."""
    out = []
    for field, count in sorted(run.injections.items()):
        label = FIELD_LABELS.get(field, field.replace("_", " "))
        plural = "s" if count > 1 else ""
        verb = "were" if count > 1 else "was"
        out.append(f"{count} {label}{plural} contained instruction-like text and {verb} ignored")
    if run.board.memory == "unavailable":
        out.append("World Model memory unavailable; the case board was kept in process")
    if fallback:
        out.append(fallback)
    return out


def score_payload(run: Run) -> list[dict[str, Any]]:
    """Return the scores as model input (reasons are Python text with evidence ids)."""
    return [
        {
            "supplier_id": score.supplier_id,
            "rating": score.rating,
            "total": score.total,
            "coverage_pct": score.coverage_pct,
            "label": score.label,
            "score_evidence_id": run.score_evidence.get(score.supplier_id),
            "critical_signals": [s.component for s in score.critical_signals],
            "components": [
                {
                    "name": c.name,
                    "assessed": c.assessed,
                    "value": c.value,
                    "weight": c.weight,
                    "reason": c.reason,
                    "evidence_ids": c.evidence_ids,
                }
                for c in score.components
            ],
        }
        for score in run.scores
    ]


def evidence_payload(run: Run) -> list[dict[str, Any]]:
    """Return the evidence the writer may cite: facts, notes and parameters, never rows."""
    wanted = {e for f in run.findings.values() for e in f.evidence_ids + f.gap_evidence_ids}
    wanted |= {e for s in run.scores for c in s.components for e in c.evidence_ids}
    wanted |= set(run.score_evidence.values())
    records = [r for r in run.tools.evidence.all() if r.evidence_id in wanted]
    return [
        {
            "evidence_id": r.evidence_id,
            "tool": r.tool,
            "source": r.source,
            "params": r.params,
            "facts": r.facts,
            "row_count": r.row_count,
            "note": r.note,
            "snapshot_at": r.snapshot_at.isoformat() if r.snapshot_at else None,
        }
        for r in records[-MAX_WRITER_EVIDENCE:]
    ]


def fallback_claims(run: Run) -> list[Claim]:
    """Return claims built from each score: the total, then every assessed component."""
    claims: list[Claim] = []
    for score in run.scores:
        sid = score.supplier_id
        score_id = run.score_evidence.get(sid)
        if score_id and score.total is not None:
            text = (
                f"{sid} risk score {score.total} ({score.rating}), coverage {score.coverage_pct} %."
            )
            claims.append(Claim(text=text, evidence_ids=[score_id]))
        for component in score.components:
            if component.assessed and component.evidence_ids:
                claims.append(
                    Claim(
                        text=f"{sid} {component.name}: {component.reason}",
                        evidence_ids=list(component.evidence_ids),
                    )
                )
    return claims


def fallback_report(run: Run, *, missing: list[str] | None = None, note: str) -> Report:
    """Return the Python report built from the score component reasons.

    Args:
        run: The investigation run.
        missing: Extra missing items (gaps, an early stop), before the score's own.
        note: Why the fallback was used (shown in the notes).

    Returns:
        The report with source "fallback".
    """
    return Report(
        headline=headline(run),
        claims=fallback_claims(run),
        missing=[*(missing or []), *missing_items(run)],
        notes=notes(run, fallback=note),
        source="fallback",
    )
