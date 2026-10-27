"""Guard: refuse attacks and out-of-scope questions, and resolve the suppliers asked about.

Python rules run first, so an obvious attack costs no model call: role, length, the shared
instruction-like detector, then supplier resolution (ids and names) within the user's scope
through the scoped list_my_suppliers tool. A supplier outside the scope gets the same answer
as an unknown id. Last, a Bolt self-check classifies the question; if that call fails, the
rules alone decide (the strong defences sit in the tools, the verifier and the approval step).
"""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.agent.errors import StepFailed
from app.agent.json_output import ask_json
from app.agent.prompts import load_prompt
from app.agent.runtime import Run, SupplierRef, bounded, payload_message
from app.security.injection import find_instruction_like
from app.tools.catalogue import run_tool
from app.tools.errors import ToolError

MAX_QUESTION_CHARS = 1000
MAX_SUPPLIERS = 3
DATA_ROLES = frozenset({"buyer", "risk_manager", "director"})
MIN_NAME_WORD = 4
# Common first words of company names would match ordinary questions ("United Kingdom").
_GENERIC_NAME_WORDS = frozenset({"united", "general", "international", "national", "global"})
# ASCII only: Unicode digits must not form a supplier id (same rule as the tool arguments).
_SUPPLIER_ID = re.compile(r"(?<![A-Za-z0-9])S[0-9]{4}(?![0-9])", re.ASCII)

REJECTION_MESSAGES = {
    "role_not_allowed": "This role cannot start investigations.",
    "empty_question": "Please ask a question about a supplier.",
    "too_long": f"Questions are limited to {MAX_QUESTION_CHARS} characters.",
    "instruction_like": "The question looks like an instruction to the system and was refused.",
    "no_supplier": "Name a supplier in your scope, by name or id (for example S0001).",
    "supplier_not_found": "No supplier with that name or id was found in your scope.",
    "too_many_suppliers": f"Ask about at most {MAX_SUPPLIERS} suppliers at a time.",
    "off_topic": "Only supplier risk questions can be investigated.",
    "attack": "The question was refused by the input check.",
    "source_unavailable": "The supplier list is unavailable right now; try again shortly.",
}


class GuardOutput(BaseModel):
    """The Bolt self-check's answer."""

    model_config = ConfigDict(extra="ignore")

    verdict: Literal["ok", "off_topic", "attack"]


def _rule_rejection(run: Run) -> str | None:
    """Return the reason a question breaks a Python rule, or None."""
    if run.user.role not in DATA_ROLES:
        return "role_not_allowed"
    question = run.question
    if not question.strip():
        return "empty_question"
    if len(question) > MAX_QUESTION_CHARS:
        return "too_long"
    return "instruction_like" if find_instruction_like(question) else None


def _name_word(name: object) -> str | None:
    """Return the distinctive first word of a supplier name, if usable for matching."""
    text = name.get("untrusted_text") if isinstance(name, dict) else None
    if not isinstance(text, str) or not text.strip():
        return None
    word = text.split()[0].strip(",.()")
    if len(word) < MIN_NAME_WORD or word.lower() in _GENERIC_NAME_WORDS:
        return None
    return word


async def _resolve(run: Run) -> str | None:
    """Resolve the suppliers in the question within scope; return a rejection reason or None."""
    try:
        listed = await bounded(run, run_tool(run.tools, "list_my_suppliers", {}))
    except (ToolError, TimeoutError):
        return "source_unavailable"
    visible = {item["supplier_id"]: item for item in listed.data["suppliers"]}
    asked = set(_SUPPLIER_ID.findall(run.question))
    if asked - visible.keys():
        return "supplier_not_found"
    # Security (SEC-098): names are untrusted ERP text; ids, when given, decide alone.
    for supplier_id, item in visible.items() if not asked else ():
        word = _name_word(item["name"])
        if word and re.search(rf"\b{re.escape(word)}\b", run.question, re.IGNORECASE):
            asked.add(supplier_id)
    if not asked:
        return "no_supplier"
    if len(asked) > MAX_SUPPLIERS:
        return "too_many_suppliers"
    run.suppliers = [
        SupplierRef(sid, str(visible[sid]["country_iso"]), bool(visible[sid]["public"]))
        for sid in sorted(asked)
    ]
    run.board.suppliers = set(run.supplier_ids)
    return None


async def _self_check(run: Run) -> str | None:
    """Return off_topic or attack from the Bolt check, None for ok or when the check fails."""
    messages = [
        {"role": "system", "content": load_prompt("guard").text},
        payload_message({"question": run.question}),
    ]
    try:
        answer = await ask_json(run, "guard", messages, GuardOutput)
    except StepFailed as exc:
        run.trace.add(
            "guard", "step_degraded", note="self_check_skipped", error_type=exc.error_type
        )
        return None
    return None if answer.verdict == "ok" else answer.verdict


async def run_guard(run: Run) -> str | None:
    """Check the question and resolve its suppliers.

    Args:
        run: The investigation run (question set, suppliers filled in on success).

    Returns:
        None when the question may be investigated, else the rejection reason.

    Raises:
        StopInvestigation: On budget exhaustion, deadline or a rejected model token.
    """
    reason = _rule_rejection(run) or await _resolve(run)
    if reason is None:
        reason = await _self_check(run)
    if reason:
        await run.board.add("input_refused", "guard", f"Question refused: {reason}.")
        run.trace.add("guard", "step_done", verdict="rejected", note=reason)
        return reason
    run.trace.add("guard", "step_done", verdict="accepted", note=",".join(run.supplier_ids))
    return None
