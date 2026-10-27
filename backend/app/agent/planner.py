"""Planner: choose the risk dimensions to check (Ultra, Lightning fallback; JSON plan).

The plan is limited to the dimensions the user's role may check (buyers never get the
financial dimension). If the model fails or answers with an invalid plan twice, the
deterministic plan (every allowed dimension) is used. The model's rationale is never written
to the case board.
"""

from app.agent.errors import StepFailed
from app.agent.json_output import ask_json
from app.agent.models import DIMENSIONS, Dimension, PlanOutput
from app.agent.prompts import load_prompt
from app.agent.runtime import Run, payload_message

# Security: financial_trend is for risk roles only (architecture 6.3).
ROLE_DIMENSIONS: dict[str, tuple[Dimension, ...]] = {
    "buyer": tuple(d for d in DIMENSIONS if d != "financial"),
    "risk_manager": DIMENSIONS,
    "director": DIMENSIONS,
}


def allowed_dimensions(role: str) -> tuple[Dimension, ...]:
    """Return the dimensions a role may check, in the fixed order."""
    return ROLE_DIMENSIONS.get(role, ())


async def run_planner(run: Run) -> list[Dimension]:
    """Plan the investigation and record plan_created.

    Args:
        run: The investigation run (suppliers resolved).

    Returns:
        The planned dimensions (also stored on run.plan).

    Raises:
        StopInvestigation: On budget exhaustion, deadline or a rejected model token.
    """
    allowed = allowed_dimensions(run.user.role)
    messages = [
        {"role": "system", "content": load_prompt("planner").text},
        payload_message(
            {
                "question": run.question,
                "suppliers": [
                    {"supplier_id": s.supplier_id, "country_iso": s.country_iso, "public": s.public}
                    for s in run.suppliers
                ],
                "allowed_dimensions": list(allowed),
            }
        ),
    ]
    note = None
    try:
        answer = await ask_json(run, "planner", messages, PlanOutput)
        plan = [d for d in allowed if d in answer.dimensions]
    except StepFailed as exc:
        run.trace.add(
            "planner", "step_degraded", note="deterministic_plan", error_type=exc.error_type
        )
        plan = []
    if not plan:
        plan, note = list(allowed), "deterministic_plan"
    run.plan = plan
    await run.board.add(
        "plan_created",
        "planner",
        f"Plan: check {', '.join(plan)}.",
        data={"dimensions": len(plan)},
    )
    run.trace.add("planner", "step_done", verdict="planned", note=note or ",".join(plan))
    return plan
