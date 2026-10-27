"""Recommender: propose actions with the propose_action tool (Ultra). Nothing is executed.

propose_action only creates a pending ActionProposal with an idempotency key; approval is a
human step (Feature 7b). Python checks every call: only propose_action, strict arguments,
a supplier of the question, evidence ids of this investigation, no instruction-like
rationale, no duplicates, at most MAX_PROPOSALS. Roles that may not propose (director,
architecture 6.3) skip the model call.
"""

import hashlib
import logging
import uuid
from collections.abc import Mapping
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from app.agent.errors import StepFailed
from app.agent.models import ActionKind, ActionProposal, EvidenceIdText, Impact, SupplierIdText
from app.agent.prompts import load_prompt
from app.agent.report import score_missing, score_payload
from app.agent.runtime import Run, call_model, payload_message
from app.agent.verifier import check_text, supplier_problems
from app.security.injection import find_instruction_like

logger = logging.getLogger(__name__)

PROPOSER_ROLES = frozenset({"buyer", "risk_manager"})
MAX_PROPOSALS = 3
MAX_PROPOSAL_CALLS = 8
TOOL_NAME = "propose_action"


class ProposeActionArgs(BaseModel):
    """Arguments of propose_action (model output, strictly validated).

    Attributes:
        kind: What to do.
        supplier_id: Supplier of the question.
        impact: Impact level.
        evidence_ids: Evidence the proposal rests on.
        rationale: One short reason.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    kind: ActionKind
    supplier_id: SupplierIdText
    impact: Impact
    evidence_ids: Annotated[list[EvidenceIdText], Field(min_length=1, max_length=10)]
    rationale: Annotated[str, StringConstraints(min_length=1, max_length=300)]


PROPOSE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": "Propose an action for a human to approve (never executes anything).",
        "parameters": ProposeActionArgs.model_json_schema(),
    },
}


def idempotency_key(investigation_id: str, kind: str, supplier_id: str) -> str:
    """Return the idempotency key of a proposal (one per investigation, kind and supplier)."""
    return hashlib.sha256(f"{investigation_id}:{kind}:{supplier_id}".encode()).hexdigest()[:32]


def _check(run: Run, name: str, arguments: Mapping[str, Any]) -> ProposeActionArgs | str:
    """Return validated arguments or the reason the call is rejected."""
    if name != TOOL_NAME:
        return "tool_not_offered"
    try:
        args = ProposeActionArgs.model_validate(dict(arguments))
    except (ValidationError, TypeError, ValueError):
        return "invalid_arguments"
    if args.supplier_id not in run.supplier_ids:
        return "supplier_not_in_question"
    known = {r.evidence_id: r for r in run.tools.evidence.all()}
    if not set(args.evidence_ids) <= known.keys():
        return "unknown_evidence"
    if find_instruction_like(args.rationale):
        return "instruction_like"
    cited = [known[e] for e in args.evidence_ids]
    # Security (SEC-089, SEC-094): the evidence is about this supplier, and the rationale an
    # approver reads passes the same number, rating and link checks as a claim.
    if supplier_problems([args.supplier_id], cited, where="rationale"):
        return "wrong_supplier"
    ratings = {s.supplier_id: s.rating for s in run.scores}
    if check_text(args.rationale, cited, ratings, where="rationale"):
        return "unsupported_rationale"
    key = idempotency_key(run.investigation_id, args.kind, args.supplier_id)
    if any(p.idempotency_key == key for p in run.proposals):
        return "duplicate"
    if len(run.proposals) >= MAX_PROPOSALS:
        return "too_many_proposals"
    return args


async def _propose(run: Run, args: ProposeActionArgs) -> None:
    """Create one pending proposal and record action_proposed."""
    proposal = ActionProposal(
        action_id=f"act-{uuid.uuid4().hex[:12]}",
        kind=args.kind,
        supplier_id=args.supplier_id,
        impact=args.impact,
        evidence_ids=list(args.evidence_ids),
        rationale=args.rationale,
        idempotency_key=idempotency_key(run.investigation_id, args.kind, args.supplier_id),
        proposed_by=run.user.user_id,
        role=run.user.role,
    )
    run.proposals.append(proposal)
    await run.board.add(
        "action_proposed",
        "recommender",
        f"Proposed {args.kind} (impact {args.impact}); waiting for approval.",
        args.evidence_ids,
        args.supplier_id,
        {"kind": args.kind, "impact": args.impact, "action_id": proposal.action_id},
    )


async def run_recommender(run: Run) -> list[ActionProposal]:
    """Ask for action proposals and keep the valid ones as pending.

    Args:
        run: The investigation run (scores computed).

    Returns:
        The pending proposals (also on run.proposals).

    Raises:
        StopInvestigation: On budget exhaustion, deadline or a rejected model token.
    """
    if run.user.role not in PROPOSER_ROLES or not run.scores:
        run.trace.add(
            "recommender", "step_skipped", note="role_may_not_propose" if run.scores else "no_score"
        )
        return run.proposals
    messages = [
        {"role": "system", "content": load_prompt("recommender").text},
        payload_message({"scores": score_payload(run), "missing": score_missing(run)}),
    ]
    try:
        answer = await call_model(run, "recommender", messages, tools=[PROPOSE_TOOL])
    except StepFailed as exc:
        run.trace.add("recommender", "step_degraded", error_type=exc.error_type)
        return run.proposals
    for call in answer.tool_calls[:MAX_PROPOSAL_CALLS]:
        checked = _check(run, call.name, call.arguments)
        if isinstance(checked, str):
            run.trace.add("recommender", "proposal_rejected", tools=[call.name], note=checked)
            logger.warning(
                "action proposal rejected", extra={"event": "proposal_rejected", "reason": checked}
            )
            continue
        await _propose(run, checked)
    run.trace.add(
        "recommender", "step_done", verdict=f"{len(run.proposals)}_proposed", tools=[TOOL_NAME]
    )
    return run.proposals
