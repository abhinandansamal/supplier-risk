"""Scripted fake of the Meterless client for agent tests (no network, deterministic).

Each agent is recognised by the first line of its system prompt ("# <agent> prompt v1"). By
default every agent gives a sensible answer built from the JSON payload it was sent; tests
override an agent with a list of scripted replies, used in order, after which the default
applies again. A reply is a content string, a list of tool calls (name, arguments), an
exception to raise, or a callable (sync or async) that receives the call and returns one.
"""

import inspect
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.llm.budget import CallBudget
from app.llm.models import ChatResult, ToolCall, Usage

_AGENT = re.compile(r"^# ([a-z_]+) prompt v[0-9]+")
DEFAULT_ARGS: dict[str, list[tuple[str, dict[str, Any]]]] = {
    "delivery": [("delivery_history", {"months": 12}), ("open_orders_at_risk", {})],
    "concentration": [("single_source_parts", {}), ("supplier_profile", {})],
    "financial": [("financial_trend", {"years": 3})],
}


@dataclass
class FakeCall:
    """One chat call the fake received.

    Attributes:
        agent: Agent read from the system prompt.
        role: Model route (default, deep, fast).
        messages: Messages as sent.
        tools: Tool definitions offered.
    """

    agent: str
    role: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] | None

    @property
    def payload(self) -> dict[str, Any]:
        """Return the JSON payload of the first user message."""
        user = next(m for m in self.messages if m["role"] == "user")
        return json.loads(user["content"])  # type: ignore[no-any-return]

    @property
    def last_user(self) -> str:
        """Return the text of the last user message (re-asks and rewrites)."""
        return [m for m in self.messages if m["role"] == "user"][-1]["content"]  # type: ignore[no-any-return]

    @property
    def tool_names(self) -> list[str]:
        """Return the names of the offered tools."""
        return [t["function"]["name"] for t in self.tools or []]


def tool_calls(*calls: tuple[str, dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    """Return a scripted reply made of tool calls."""
    return list(calls)


def default_reply(call: FakeCall) -> Any:  # noqa: ANN401, C901 - one branch per agent
    """Return the default reply of an agent."""
    payload = call.payload
    if call.agent == "guard":
        return json.dumps({"verdict": "ok"})
    if call.agent == "planner":
        return json.dumps({"dimensions": payload["allowed_dimensions"], "rationale": "all"})
    if call.agent == "research":
        calls = []
        for supplier in payload["suppliers"]:
            sid = supplier["supplier_id"]
            if payload["dimension"] == "country":
                for indicator in ("unemployment_rate", "hicp_annual_rate"):
                    args = {"country_iso": supplier["country_iso"], "indicator": indicator}
                    calls.append(("country_indicators", args))
                continue
            for name, args in DEFAULT_ARGS[payload["dimension"]]:
                calls.append((name, {"supplier_id": sid, **args}))
        return calls
    if call.agent == "evaluator":
        return json.dumps({"gaps": [], "contradictions": []})
    if call.agent == "recommender":
        score = payload["scores"][0]
        cited = next(c["evidence_ids"] for c in score["components"] if c["assessed"])
        args = {
            "kind": "supplier_review",
            "supplier_id": score["supplier_id"],
            "impact": "medium",
            "evidence_ids": cited[:2],
            "rationale": "Delivery performance needs a review.",
        }
        return [("propose_action", args)]
    if call.agent == "writer":
        claims = [
            {"text": c["reason"], "evidence_ids": c["evidence_ids"], "kind": "fact"}
            for s in payload["scores"]
            for c in s["components"]
            if c["assessed"]
        ]
        return json.dumps({"claims": claims, "missing": []})
    raise AssertionError(f"no default reply for agent {call.agent!r}")


class FakeLLM:
    """Scripted stand-in for MeterlessClient.chat (consumes the budget like the real one).

    Attributes:
        calls: Every call received, in order.
    """

    def __init__(self, script: Mapping[str, Sequence[Any]] | None = None) -> None:
        """Create the fake.

        Args:
            script: Replies per agent, used in order before the defaults.
        """
        self._script = {agent: list(replies) for agent, replies in (script or {}).items()}
        self.calls: list[FakeCall] = []

    def calls_of(self, agent: str) -> list[FakeCall]:
        """Return the calls one agent made."""
        return [c for c in self.calls if c.agent == agent]

    async def chat(
        self,
        messages: Sequence[Mapping[str, Any]],
        role: str = "default",
        tools: Sequence[Mapping[str, Any]] | None = None,
        tool_choice: object = None,
        response_format: object = None,
        budget: CallBudget | None = None,
    ) -> ChatResult:
        """Return the next scripted (or default) reply for the calling agent."""
        if budget is not None:
            budget.consume()
        first = str(messages[0]["content"]).splitlines()[0]
        match = _AGENT.match(first)
        assert match, "system prompt must start with '# <agent> prompt vN'"
        call = FakeCall(
            match.group(1),
            role,
            [dict(m) for m in messages],
            [dict(t) for t in tools] if tools else None,
        )
        self.calls.append(call)
        queue = self._script.get(call.agent) or []
        reply = queue.pop(0) if queue else default_reply
        if callable(reply) and not isinstance(reply, type):
            reply = reply(call)
            if inspect.isawaitable(reply):
                reply = await reply
        if isinstance(reply, BaseException):
            raise reply
        n = len(self.calls)
        parsed = [
            ToolCall(id=f"call_{n}_{i}", name=name, arguments=args)
            for i, (name, args) in enumerate(reply if isinstance(reply, list) else [])
        ]
        return ChatResult(
            content=reply if isinstance(reply, str) else "",
            tool_calls=parsed,
            requested_model=f"fake/{role}",
            routed_model=f"fake/{role}",
            provider="fake",
            receipt_id=f"rcpt-{n:04d}",
            usage=Usage(prompt_tokens=100, completion_tokens=20, total_tokens=120),
            latency_ms=5,
            retries=0,
            cache_status="miss",
        )
