"""Tests for the agent runtime: model calls with trace, budget, deadlines and fallback."""

import asyncio
import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from tests.fakes.llm import FakeCall, FakeLLM

from app.agent.errors import StepFailed, StopInvestigation
from app.agent.prompts import load_prompt
from app.agent.runtime import Deadline, Run, bounded, call_model, new_run
from app.core.settings import Settings
from app.llm.errors import LLMBadResponse, LLMRateLimited, LLMUnauthorized, LLMUnavailable
from app.tools.catalogue import ToolContext

MakeRun = Callable[..., Run]


@pytest.fixture
def make_run(
    make_ctx: Callable[..., ToolContext], make_settings: Callable[..., Settings], tmp_path: Path
) -> MakeRun:
    def factory(llm: FakeLLM, clock: Callable[[], float] | None = None, **settings: Any) -> Run:  # noqa: ANN401
        ctx = make_ctx("risk_manager")
        options: dict[str, Any] = {"trace_path": tmp_path / "trace.jsonl"}
        if clock is not None:
            options["clock"] = clock
        return new_run(make_settings(**settings), tools=ctx, llm=llm, board_client=None, **options)

    return factory


def _messages(agent: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": load_prompt(agent).text},
        {"role": "user", "content": json.dumps({"allowed_dimensions": ["delivery"]})},
    ]


async def test_model_call_is_traced_with_receipt_tokens_and_budget(make_run: MakeRun) -> None:
    run = make_run(FakeLLM())
    await call_model(run, "planner", _messages("planner"))
    event = run.trace.events[-1]
    assert (event.agent, event.action, event.model_route) == ("planner", "model_call", "deep")
    assert event.receipt_id == "rcpt-0001"
    assert event.cache_status == "miss"
    assert event.tokens == 120
    assert event.budget_left == 14
    assert event.prompt_version == "v1"
    lines = (run.trace.path or Path()).read_text().splitlines()
    assert json.loads(lines[-1])["receipt_id"] == "rcpt-0001"


async def test_each_step_uses_its_configured_route(make_run: MakeRun) -> None:
    llm = FakeLLM()
    run = make_run(llm, agent_model_planner="default")
    await call_model(run, "planner", _messages("planner"))
    assert llm.calls[0].role == "default"


@pytest.mark.parametrize(
    "error", [LLMUnavailable("down", 503), LLMRateLimited("429", 429), LLMBadResponse("bad")]
)
async def test_deep_step_falls_back_to_lightning_once(
    make_run: MakeRun, error: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    llm = FakeLLM({"planner": [error]})
    run = make_run(llm)
    with caplog.at_level(logging.WARNING):
        result = await call_model(run, "planner", _messages("planner"))
    assert [c.role for c in llm.calls] == ["deep", "default"]
    assert result.requested_model == "fake/default"
    actions = [e.action for e in run.trace.events]
    assert actions == ["model_call_failed", "model_fallback", "model_call"]
    assert any(r.__dict__.get("event") == "model_fallback" for r in caplog.records)


async def test_a_failing_fallback_fails_the_step(make_run: MakeRun) -> None:
    llm = FakeLLM({"planner": [LLMUnavailable("down"), LLMUnavailable("down")]})
    with pytest.raises(StepFailed):
        await call_model(make_run(llm), "planner", _messages("planner"))


async def test_a_lightning_step_does_not_fall_back(make_run: MakeRun) -> None:
    llm = FakeLLM({"evaluator": [LLMUnavailable("down")]})
    with pytest.raises(StepFailed):
        await call_model(make_run(llm), "evaluator", _messages("evaluator"))
    assert len(llm.calls) == 1


async def test_budget_exhaustion_stops_the_investigation(make_run: MakeRun) -> None:
    run = make_run(FakeLLM(), investigation_call_budget=1)
    await call_model(run, "evaluator", _messages("evaluator"))
    with pytest.raises(StopInvestigation) as info:
        await call_model(run, "evaluator", _messages("evaluator"))
    assert info.value.reason == "budget_exhausted"


async def test_unauthorized_stops_the_investigation(make_run: MakeRun) -> None:
    llm = FakeLLM({"planner": [LLMUnauthorized("401", 401)]})
    with pytest.raises(StopInvestigation) as info:
        await call_model(make_run(llm), "planner", _messages("planner"))
    assert info.value.reason == "model_unauthorized"
    assert len(llm.calls) == 1


async def test_a_slow_call_hits_the_per_call_timeout(make_run: MakeRun) -> None:
    async def slow(_: FakeCall) -> str:
        await asyncio.sleep(5)
        return "{}"

    llm = FakeLLM({"evaluator": [slow]})
    with pytest.raises(StepFailed) as info:
        await call_model(
            make_run(llm, model_call_timeout_seconds=0.05), "evaluator", _messages("evaluator")
        )
    assert info.value.error_type == "TimeoutError"


async def test_a_call_after_the_deadline_stops_without_calling(make_run: MakeRun) -> None:
    now = [0.0]
    llm = FakeLLM()
    run = make_run(llm, clock=lambda: now[0], investigation_deadline_seconds=10)
    now[0] = 11.0
    with pytest.raises(StopInvestigation) as info:
        await call_model(run, "evaluator", _messages("evaluator"))
    assert info.value.reason == "deadline"
    assert llm.calls == []


async def test_a_call_cut_by_the_overall_deadline_stops_the_investigation(
    make_run: MakeRun,
) -> None:
    async def slow(_: FakeCall) -> str:
        await asyncio.sleep(5)
        return "{}"

    llm = FakeLLM({"evaluator": [slow]})
    run = make_run(llm, investigation_deadline_seconds=0.05)
    with pytest.raises(StopInvestigation) as info:
        await call_model(run, "evaluator", _messages("evaluator"))
    assert info.value.reason == "deadline"


def test_deadline_reports_remaining_time() -> None:
    now = [100.0]
    deadline = Deadline(30, clock=lambda: now[0])
    now[0] = 110.0
    assert deadline.remaining() == pytest.approx(20)
    assert not deadline.expired
    now[0] = 131.0
    assert deadline.expired


async def test_bounded_tool_work_times_out_before_the_deadline(make_run: MakeRun) -> None:
    run = make_run(FakeLLM(), tool_call_timeout_seconds=0.05)
    with pytest.raises(TimeoutError):
        await bounded(run, asyncio.sleep(5))


async def test_bounded_tool_work_cut_by_the_deadline_stops(make_run: MakeRun) -> None:
    run = make_run(FakeLLM(), investigation_deadline_seconds=0.05)
    with pytest.raises(StopInvestigation) as info:
        await bounded(run, asyncio.sleep(5))
    assert info.value.reason == "deadline"


async def test_bounded_after_the_deadline_does_not_start_the_work(make_run: MakeRun) -> None:
    now = [0.0]
    run = make_run(FakeLLM(), clock=lambda: now[0], investigation_deadline_seconds=1)
    now[0] = 2.0
    work = asyncio.sleep(0)
    with pytest.raises(StopInvestigation):
        await bounded(run, work)
    assert work.cr_frame is None  # closed, never awaited
