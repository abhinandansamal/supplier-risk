"""Tests for scripts/check_llm.py: PASS/FAIL lines against a mocked runtime, no secrets printed."""

import json
from collections.abc import Callable

import httpx
import pytest
import respx

from app.core.settings import Settings
from scripts.check_llm import CHECK_NAMES, CheckResult, format_result, main, run_checks

TOKEN = "mt-secret-VALUE-2222"


def _models(settings: Settings) -> Callable[[httpx.Request], httpx.Response]:
    ids = [
        settings.meterless_model,
        settings.meterless_deep_model,
        settings.meterless_fast_model,
        settings.meterless_embed_model,
        "meterless/auto",
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401)
        return httpx.Response(200, json={"data": [{"id": i} for i in ids]})

    return handler


def _chat(request: httpx.Request) -> httpx.Response:
    sent = json.loads(request.content)
    model = sent["model"]
    message: dict[str, object] = {"role": "assistant", "content": "<think>x</think>Hello"}
    if sent.get("tools"):
        message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "run_sql", "arguments": '{"template_id": "t"}'},
                }
            ],
        }
    return httpx.Response(
        200,
        json={
            "model": model,
            "choices": [{"message": message}],
            "meterless": {
                "receipt_id": "rcpt_req_1",
                "routed_model": model,
                "winning_route": {"provider": "nvidia"},
            },
        },
    )


@pytest.fixture
def healthy(meterless: respx.MockRouter, settings: Settings) -> respx.MockRouter:
    meterless.get("/models").mock(side_effect=_models(settings))
    meterless.post("/chat/completions").mock(side_effect=_chat)
    return meterless


async def test_all_checks_pass_against_a_healthy_runtime(
    healthy: respx.MockRouter, settings: Settings
) -> None:
    results = await run_checks(settings, max_attempts=1)
    assert [r.name for r in results] == list(CHECK_NAMES)
    assert all(r.passed for r in results), results
    assert healthy.routes[1].call_count == 3


async def test_chat_checks_are_skipped_when_runtime_is_offline(
    meterless: respx.MockRouter, settings: Settings
) -> None:
    meterless.get("/models").mock(side_effect=httpx.ConnectError("refused"))
    chat = meterless.post("/chat/completions")
    results = await run_checks(settings, max_attempts=1)
    assert not any(r.passed for r in results)
    assert chat.call_count == 0


async def test_routed_model_mismatch_fails_the_plain_text_check(
    meterless: respx.MockRouter, settings: Settings
) -> None:
    meterless.get("/models").mock(side_effect=_models(settings))

    def rerouted(request: httpx.Request) -> httpx.Response:
        response = _chat(request)
        body = json.loads(response.content)
        body["meterless"]["routed_model"] = "local/other-model"
        return httpx.Response(200, json=body)

    meterless.post("/chat/completions").mock(side_effect=rerouted)
    results = {r.name: r for r in await run_checks(settings, max_attempts=1)}
    assert not results["plain_text"].passed


def test_format_result() -> None:
    assert format_result(CheckResult("status", True, "online")) == "PASS status: online"
    assert format_result(CheckResult("status", False, "offline")) == "FAIL status: offline"


async def test_main_prints_results_without_secrets_and_exits_zero(
    healthy: respx.MockRouter, settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    code = await main(settings=settings, max_attempts=1)
    out = capsys.readouterr().out
    assert code == 0
    assert out.count("PASS") == len(CHECK_NAMES)
    assert TOKEN not in out


async def test_main_exits_one_on_failure(
    meterless: respx.MockRouter, settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    meterless.get("/models").respond(500)
    assert await main(settings=settings, max_attempts=1) == 1
    assert "FAIL" in capsys.readouterr().out


async def test_every_chat_prompt_carries_a_fresh_run_id(
    healthy: respx.MockRouter, settings: Settings
) -> None:
    await run_checks(settings, max_attempts=1)
    chat = healthy.routes[1].calls
    contents = [json.loads(c.request.content)["messages"][-1]["content"] for c in chat]
    assert len(contents) == 3
    assert all("(run " in c for c in contents)
    assert len(set(contents)) == 3
