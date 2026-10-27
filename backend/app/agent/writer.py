"""Writer (Ultra) and the verifier step around it.

The writer gets the scores, the evidence (facts only), the proposals and what is missing, and
answers with JSON claims. The verifier (Python) checks the report; on problems the writer gets
one rewrite with the problem list. If it still fails, the failing claims are marked unverified
and the case is escalated. If the writer cannot produce valid JSON twice, the Python fallback
report built from the score is used instead. Claim texts never go to the case board.
"""

from app.agent.errors import StepFailed
from app.agent.json_output import ask_json
from app.agent.models import Claim, Report, WriterOutput
from app.agent.prompts import load_prompt
from app.agent.report import (
    evidence_payload,
    fallback_report,
    headline,
    missing_items,
    notes,
    score_payload,
)
from app.agent.runtime import Run, payload_message
from app.agent.verifier import Problem, mark_unverified, verify_report

MAX_PROBLEMS_SHOWN = 20
WRITER_FALLBACK_NOTE = "The writer gave no valid answer; the claims were built from the risk score"


def _messages(run: Run, problems: list[Problem]) -> list[dict[str, str]]:
    """Return the writer's messages; a rewrite adds the verifier's problems."""
    messages = [
        {"role": "system", "content": load_prompt("writer").text},
        payload_message(
            {
                "question": run.question,
                "scores": score_payload(run),
                "evidence": evidence_payload(run),
                "proposals": [
                    {"kind": p.kind, "supplier_id": p.supplier_id, "impact": p.impact}
                    for p in run.proposals
                ],
                "missing": missing_items(run),
            }
        ),
    ]
    if problems:
        listed = "; ".join(
            f"{p.where} {p.index + 1}: {p.describe()}" for p in problems[:MAX_PROBLEMS_SHOWN]
        )
        messages.append(
            {
                "role": "user",
                "content": f"The verifier found problems: {listed}. Rewrite the JSON answer.",
            }
        )
    return messages


async def run_writer(run: Run, problems: list[Problem]) -> Report:
    """Write the report (a rewrite when problems are given).

    Args:
        run: The investigation run.
        problems: Verifier problems of the previous draft (empty for the first draft).

    Returns:
        The draft report (also on run.report).

    Raises:
        StopInvestigation: On budget exhaustion, deadline or a rejected model token.
    """
    try:
        answer = await ask_json(run, "writer", _messages(run, problems), WriterOutput)
    except StepFailed as exc:
        run.trace.add("writer", "step_degraded", note="fallback_report", error_type=exc.error_type)
        run.report = fallback_report(run, note=WRITER_FALLBACK_NOTE)
        return run.report
    # Security (SEC-093): "missing" is Python-built; the model's list is free text, not shown.
    missing = missing_items(run)
    run.report = Report(
        headline=headline(run),
        claims=[
            Claim(text=c.text, evidence_ids=c.evidence_ids, kind=c.kind) for c in answer.claims
        ],
        missing=missing,
        notes=notes(run),
        source="writer",
    )
    run.trace.add(
        "writer",
        "step_done",
        step="rewrite" if problems else "draft",
        evidence_ids=sorted({e for c in answer.claims for e in c.evidence_ids}),
        verdict=f"{len(answer.claims)}_claims",
    )
    return run.report


async def run_verifier(run: Run, rewrites_left: int) -> list[Problem]:
    """Verify run.report; on final failure mark failing claims unverified.

    Args:
        run: The investigation run (report drafted).
        rewrites_left: 1 if the writer may still rewrite, else 0.

    Returns:
        The problems (empty when the report verifies). When rewrites_left is 0, the report on
        run.report is already marked.
    """
    report = run.report or Report(headline=headline(run), claims=[])
    evidence = {r.evidence_id: r for r in run.tools.evidence.all()}
    ratings = {s.supplier_id: s.rating for s in run.scores}
    problems = verify_report(report, evidence, ratings)
    final = not problems or rewrites_left == 0 or report.source == "fallback"
    if problems and final:
        report = mark_unverified(report, problems)
    run.report = report
    verdict = "passed" if not problems else "failed"
    await run.board.add(
        "verdict",
        "verifier",
        f"Report verification {verdict}.",
        data={"problems": len(problems), "claims": len(report.claims)},
    )
    run.trace.add(
        "verifier",
        "step_done",
        verdict=verdict,
        note=",".join(sorted({p.code for p in problems})) or None,
    )
    if final:
        for index, claim in enumerate(report.claims, start=1):
            await run.board.add(
                "claim_made",
                "writer",
                f"Claim {index} ({claim.kind}) {'verified' if claim.verified else 'unverified'}.",
                claim.evidence_ids,
                data={"index": index, "kind": claim.kind, "verified": claim.verified},
            )
    return problems
