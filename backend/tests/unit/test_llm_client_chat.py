"""Tests for MeterlessClient.chat(): request shape, parsing, retries, fallback and budget."""

import asyncio
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from typing import Any, Protocol

import httpx
import pytest
import respx

from app.core.settings import Settings
from app.llm import client as client_module
from app.llm.budget import CallBudget
from app.llm.client import CHAT_TIMEOUT_SECONDS, MAX_RESPONSE_BYTES, MAX_TOOLS, MeterlessClient
from app.llm.errors import (
    BudgetExhausted,
    LLMBadResponse,
    LLMRateLimited,
    LLMRequestError,
    LLMUnauthorized,
    LLMUnavailable,
    ToolCallParseError,
)
from app.llm.rate_limiter import AsyncRateLimiter


class Delays(Protocol):
    """The recording sleep fixture from conftest."""

    delays: list[float]


MESSAGES: list[dict[str, Any]] = [{"role": "user", "content": "Say hello"}]
DEFAULT_MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"
DEEP_MODEL = "nvidia/nemotron-3.5-ultra"
RUN_SQL_TOOL = {
    "type": "function",
    "function": {
        "name": "run_sql",
        "description": "Run a fixed SQL template.",
        "parameters": {"type": "object", "properties": {"template_id": {"type": "string"}}},
    },
}


def completion(
    content: str | None = "Hello",
    *,
    model: str = DEFAULT_MODEL,
    tool_calls: list[dict[str, Any]] | None = None,
    receipt: object = True,
    completion_tokens: int = 7,
    cache: object = None,
) -> dict[str, Any]:
    """Return an OpenAI-style chat completion body with a Meterless receipt."""
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    body: dict[str, Any] = {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": 11,
            "completion_tokens": completion_tokens,
            "total_tokens": 11 + completion_tokens,
        },
    }
    if receipt is True:
        body["meterless"] = {
            "receipt_id": "rcpt_req_abc",
            "requested_model": model,
            "routed_model": model,
            "winning_route": {"model": model, "provider": "nvidia", "kind": "cloud"},
        }
        if cache is not None:
            body["meterless"]["cache"] = cache
    elif receipt is not False:
        body["meterless"] = receipt
    return body


def sent_json(route: respx.Route, index: int = -1) -> dict[str, Any]:
    return json.loads(route.calls[index].request.content)  # type: ignore[no-any-return]


@pytest.fixture
def chat_route(meterless: respx.MockRouter) -> respx.Route:
    return meterless.post("/chat/completions")


async def test_plain_text_returns_string_with_receipt(
    chat_route: respx.Route, make_client: Callable[..., MeterlessClient]
) -> None:
    chat_route.respond(200, json=completion("Hello there"))
    result = await make_client().chat(MESSAGES)
    assert result.content == "Hello there"
    assert isinstance(result.content, str)
    assert result.tool_calls == []
    assert result.requested_model == DEFAULT_MODEL
    assert result.routed_model == DEFAULT_MODEL
    assert result.provider == "nvidia"
    assert result.receipt_id == "rcpt_req_abc"
    assert result.usage.prompt_tokens == 11
    assert result.usage.completion_tokens == 7
    assert result.usage.total_tokens == 18
    assert result.retries == 0
    assert result.fallback_used is False
    assert result.latency_ms >= 0


async def test_request_keeps_plain_string_content_and_omits_unset_options(
    chat_route: respx.Route, make_client: Callable[..., MeterlessClient]
) -> None:
    chat_route.respond(200, json=completion())
    await make_client().chat(MESSAGES)
    sent = sent_json(chat_route)
    assert sent["model"] == DEFAULT_MODEL
    assert sent["messages"] == MESSAGES
    assert isinstance(sent["messages"][0]["content"], str)
    for key in ("tools", "tool_choice", "response_format"):
        assert key not in sent
    assert not sent.get("stream", False)


async def test_sends_auth_app_and_user_scope_headers(
    chat_route: respx.Route, make_client: Callable[..., MeterlessClient]
) -> None:
    chat_route.respond(200, json=completion())
    await make_client().chat(MESSAGES)
    headers = chat_route.calls.last.request.headers
    assert headers["authorization"] == "Bearer mt-secret-VALUE-2222"
    assert headers["meterless-app"] == "supplier-risk"
    assert headers["meterless-scope"] == "user"


async def test_uses_the_180_second_timeout(
    chat_route: respx.Route, make_client: Callable[..., MeterlessClient]
) -> None:
    chat_route.respond(200, json=completion())
    await make_client().chat(MESSAGES)
    assert CHAT_TIMEOUT_SECONDS == 180.0
    assert chat_route.calls.last.request.extensions["timeout"]["read"] == 180.0


@pytest.mark.parametrize(
    ("role", "field"),
    [
        ("default", "meterless_model"),
        ("deep", "meterless_deep_model"),
        ("fast", "meterless_fast_model"),
    ],
)
async def test_role_selects_the_configured_model(
    chat_route: respx.Route,
    make_client: Callable[..., MeterlessClient],
    settings: Settings,
    role: str,
    field: str,
) -> None:
    model = getattr(settings, field)
    chat_route.respond(200, json=completion(model=model))
    result = await make_client().chat(MESSAGES, role=role)  # type: ignore[arg-type]
    assert sent_json(chat_route)["model"] == model
    assert result.requested_model == model


async def test_rejects_unknown_role_without_a_request(
    chat_route: respx.Route, make_client: Callable[..., MeterlessClient]
) -> None:
    with pytest.raises(ValueError, match="role"):
        await make_client().chat(MESSAGES, role="auto")  # type: ignore[arg-type]
    assert chat_route.call_count == 0


@pytest.mark.parametrize("messages", [[], [{"role": "user", "content": "x"}] * 101])
async def test_rejects_empty_or_oversized_message_lists(
    chat_route: respx.Route,
    make_client: Callable[..., MeterlessClient],
    messages: list[dict[str, Any]],
) -> None:
    with pytest.raises(ValueError, match="messages"):
        await make_client().chat(messages)
    assert chat_route.call_count == 0


async def test_passes_tools_tool_choice_and_response_format(
    chat_route: respx.Route, make_client: Callable[..., MeterlessClient]
) -> None:
    chat_route.respond(200, json=completion())
    choice = {"type": "function", "function": {"name": "run_sql"}}
    fmt = {"type": "json_object"}
    await make_client().chat(
        MESSAGES, tools=[RUN_SQL_TOOL], tool_choice=choice, response_format=fmt
    )
    sent = sent_json(chat_route)
    assert sent["tools"] == [RUN_SQL_TOOL]
    assert sent["tool_choice"] == choice
    assert sent["response_format"] == fmt


@pytest.mark.parametrize(
    "content",
    ["<think>secret plan</think>Final", "no opener, still reasoning</think>\nFinal"],
)
async def test_strips_reasoning_up_to_think_end(
    chat_route: respx.Route, make_client: Callable[..., MeterlessClient], content: str
) -> None:
    chat_route.respond(200, json=completion(content))
    assert (await make_client().chat(MESSAGES)).content == "Final"


async def test_parses_tool_calls_and_tolerates_null_content(
    chat_route: respx.Route, make_client: Callable[..., MeterlessClient]
) -> None:
    calls = [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "run_sql", "arguments": '{"template_id": "fin"}'},
        }
    ]
    chat_route.respond(200, json=completion(None, tool_calls=calls))
    result = await make_client().chat(MESSAGES, tools=[RUN_SQL_TOOL])
    assert result.content == ""
    assert result.tool_calls[0].name == "run_sql"
    assert result.tool_calls[0].arguments == {"template_id": "fin"}


async def test_invalid_tool_arguments_raise_typed_error(
    chat_route: respx.Route, make_client: Callable[..., MeterlessClient]
) -> None:
    calls = [{"id": "c", "type": "function", "function": {"name": "run_sql", "arguments": "{"}}]
    chat_route.respond(200, json=completion(None, tool_calls=calls))
    with pytest.raises(ToolCallParseError):
        await make_client().chat(MESSAGES, tools=[RUN_SQL_TOOL])


async def test_missing_receipt_is_tolerated(
    chat_route: respx.Route, make_client: Callable[..., MeterlessClient]
) -> None:
    chat_route.respond(200, json=completion(receipt=False))
    result = await make_client().chat(MESSAGES)
    assert result.receipt_id is None
    assert result.provider is None
    assert result.routed_model == DEFAULT_MODEL


@pytest.mark.parametrize(
    "receipt",
    [
        "oops",
        [],
        {"receipt_id": 42, "routed_model": None, "winning_route": "nvidia"},
        {"receipt_id": "rcpt_1", "winning_route": {"provider": ["nvidia"]}},
    ],
)
async def test_malformed_receipt_is_tolerated(
    chat_route: respx.Route, make_client: Callable[..., MeterlessClient], receipt: object
) -> None:
    chat_route.respond(200, json=completion(receipt=receipt))
    result = await make_client().chat(MESSAGES)
    assert result.content == "Hello"
    assert result.provider is None
    assert result.routed_model == DEFAULT_MODEL


async def test_routed_model_comes_from_the_receipt(
    chat_route: respx.Route, make_client: Callable[..., MeterlessClient]
) -> None:
    body = completion(model="echo-of-request")
    body["meterless"]["routed_model"] = "nvidia/nemotron-3.5-bolt-e4b"
    chat_route.respond(200, json=body)
    assert (await make_client().chat(MESSAGES)).routed_model == "nvidia/nemotron-3.5-bolt-e4b"


async def test_invalid_usage_falls_back_to_zero(
    chat_route: respx.Route, make_client: Callable[..., MeterlessClient]
) -> None:
    body = completion()
    body["usage"] = {"prompt_tokens": "many"}
    chat_route.respond(200, json=body)
    assert (await make_client().chat(MESSAGES)).usage.total_tokens == 0


@pytest.mark.parametrize(
    "content",
    [
        b"not json",
        b"[]",
        b'{"choices": []}',
        b'{"choices": [{"message": "x"}]}',
        b'{"choices": [{"message": {"content": [{"type": "text"}]}}]}',
    ],
)
async def test_bad_response_shape_raises(
    chat_route: respx.Route, make_client: Callable[..., MeterlessClient], content: bytes
) -> None:
    chat_route.respond(200, content=content)
    with pytest.raises(LLMBadResponse):
        await make_client().chat(MESSAGES)


@pytest.mark.parametrize("first", [429, 500, 502, 503, 504])
async def test_retries_once_then_succeeds(
    chat_route: respx.Route,
    make_client: Callable[..., MeterlessClient],
    sleeper: Delays,
    first: int,
) -> None:
    chat_route.side_effect = [httpx.Response(first), httpx.Response(200, json=completion())]
    result = await make_client().chat(MESSAGES)
    assert result.retries == 1
    assert sleeper.delays == [1.0]


@pytest.mark.parametrize(
    "error",
    [httpx.ReadTimeout("slow"), httpx.ConnectError("refused"), httpx.RemoteProtocolError("x")],
)
async def test_retries_on_timeouts_and_transport_errors(
    chat_route: respx.Route, make_client: Callable[..., MeterlessClient], error: Exception
) -> None:
    chat_route.side_effect = [error, httpx.Response(200, json=completion())]
    assert (await make_client().chat(MESSAGES)).retries == 1


async def test_backoff_doubles_between_attempts(
    chat_route: respx.Route, make_client: Callable[..., MeterlessClient], sleeper: Delays
) -> None:
    chat_route.side_effect = [httpx.Response(500)] * 3 + [httpx.Response(200, json=completion())]
    result = await make_client().chat(MESSAGES)
    assert result.retries == 3
    assert sleeper.delays == [1.0, 2.0, 4.0]


async def test_backoff_adds_jitter(
    chat_route: respx.Route, make_client: Callable[..., MeterlessClient], sleeper: Delays
) -> None:
    chat_route.side_effect = [httpx.Response(500), httpx.Response(200, json=completion())]
    await make_client(rand=lambda: 0.5).chat(MESSAGES)
    assert sleeper.delays == [1.5]


async def test_backoff_is_capped(
    chat_route: respx.Route, make_client: Callable[..., MeterlessClient], sleeper: Delays
) -> None:
    chat_route.side_effect = [httpx.Response(500)] * 7 + [httpx.Response(200, json=completion())]
    await make_client(max_attempts=8).chat(MESSAGES)
    assert sleeper.delays == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0]


@pytest.mark.parametrize(("header", "expected"), [("7", 7.0), ("0", 0.0), ("3600", 60.0)])
async def test_honours_retry_after_seconds_with_a_cap(
    chat_route: respx.Route,
    make_client: Callable[..., MeterlessClient],
    sleeper: Delays,
    header: str,
    expected: float,
) -> None:
    chat_route.side_effect = [
        httpx.Response(429, headers={"Retry-After": header}),
        httpx.Response(200, json=completion()),
    ]
    await make_client().chat(MESSAGES)
    assert sleeper.delays == [expected]


async def test_honours_retry_after_http_date(
    chat_route: respx.Route, make_client: Callable[..., MeterlessClient], sleeper: Delays
) -> None:
    when = format_datetime(datetime.now(UTC) + timedelta(seconds=10), usegmt=True)
    chat_route.side_effect = [
        httpx.Response(429, headers={"Retry-After": when}),
        httpx.Response(200, json=completion()),
    ]
    await make_client().chat(MESSAGES)
    assert 8.0 <= sleeper.delays[0] <= 10.0


@pytest.mark.parametrize("header", ["soon", "-5", ""])
async def test_unusable_retry_after_falls_back_to_backoff(
    chat_route: respx.Route,
    make_client: Callable[..., MeterlessClient],
    sleeper: Delays,
    header: str,
) -> None:
    chat_route.side_effect = [
        httpx.Response(429, headers={"Retry-After": header}),
        httpx.Response(200, json=completion()),
    ]
    await make_client().chat(MESSAGES)
    assert sleeper.delays == [1.0]


async def test_gives_up_after_four_attempts_on_5xx(
    chat_route: respx.Route, make_client: Callable[..., MeterlessClient], sleeper: Delays
) -> None:
    chat_route.respond(503)
    with pytest.raises(LLMUnavailable):
        await make_client().chat(MESSAGES)
    assert chat_route.call_count == 4
    assert len(sleeper.delays) == 3


async def test_gives_up_after_four_attempts_on_timeouts(
    chat_route: respx.Route, make_client: Callable[..., MeterlessClient]
) -> None:
    chat_route.mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(LLMUnavailable):
        await make_client().chat(MESSAGES)
    assert chat_route.call_count == 4


async def test_gives_up_after_four_attempts_on_429(
    chat_route: respx.Route, make_client: Callable[..., MeterlessClient]
) -> None:
    chat_route.respond(429)
    with pytest.raises(LLMRateLimited):
        await make_client().chat(MESSAGES)
    assert chat_route.call_count == 4


@pytest.mark.parametrize("code", [401, 403])
async def test_unauthorized_is_not_retried(
    chat_route: respx.Route, make_client: Callable[..., MeterlessClient], code: int
) -> None:
    chat_route.respond(code)
    with pytest.raises(LLMUnauthorized):
        await make_client().chat(MESSAGES)
    assert chat_route.call_count == 1


@pytest.mark.parametrize("code", [302, 400, 404, 422])
async def test_other_non_success_codes_are_not_retried(
    chat_route: respx.Route, make_client: Callable[..., MeterlessClient], code: int
) -> None:
    chat_route.respond(code, headers={"Location": "http://elsewhere.example/"})
    with pytest.raises(LLMRequestError) as info:
        await make_client().chat(MESSAGES)
    assert info.value.status_code == code
    assert chat_route.call_count == 1


def _by_model(deep: httpx.Response | None = None) -> Callable[[httpx.Request], httpx.Response]:
    """Answer 429 (or `deep`) for the deep model and success for the default model."""

    def handler(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        if model == DEEP_MODEL:
            return deep or httpx.Response(429)
        return httpx.Response(200, json=completion(model=model))

    return handler


async def test_deep_falls_back_to_default_once_on_persistent_429(
    chat_route: respx.Route, make_client: Callable[..., MeterlessClient]
) -> None:
    chat_route.mock(side_effect=_by_model())
    result = await make_client().chat(MESSAGES, role="deep")
    assert result.fallback_used is True
    assert result.requested_model == DEFAULT_MODEL
    models = [json.loads(c.request.content)["model"] for c in chat_route.calls]
    assert models == [DEEP_MODEL] * 4 + [DEFAULT_MODEL]


async def test_deep_does_not_fall_back_on_5xx(
    chat_route: respx.Route, make_client: Callable[..., MeterlessClient]
) -> None:
    chat_route.mock(side_effect=_by_model(httpx.Response(500)))
    with pytest.raises(LLMUnavailable):
        await make_client().chat(MESSAGES, role="deep")
    assert chat_route.call_count == 4


async def test_fast_does_not_fall_back_on_429(
    chat_route: respx.Route, make_client: Callable[..., MeterlessClient]
) -> None:
    chat_route.respond(429)
    with pytest.raises(LLMRateLimited):
        await make_client().chat(MESSAGES, role="fast")
    assert chat_route.call_count == 4


async def test_fallback_happens_only_once(
    chat_route: respx.Route, make_client: Callable[..., MeterlessClient]
) -> None:
    chat_route.respond(429)
    with pytest.raises(LLMRateLimited):
        await make_client().chat(MESSAGES, role="deep")
    assert chat_route.call_count == 8


async def test_budget_counts_one_per_call_even_with_retries_and_fallback(
    chat_route: respx.Route, make_client: Callable[..., MeterlessClient]
) -> None:
    chat_route.mock(side_effect=_by_model())
    budget = CallBudget(limit=5)
    await make_client().chat(MESSAGES, role="deep", budget=budget)
    assert budget.used == 1


async def test_exhausted_budget_blocks_the_request(
    chat_route: respx.Route, make_client: Callable[..., MeterlessClient]
) -> None:
    chat_route.respond(200, json=completion())
    budget = CallBudget(limit=1)
    client = make_client()
    await client.chat(MESSAGES, budget=budget)
    with pytest.raises(BudgetExhausted):
        await client.chat(MESSAGES, budget=budget)
    assert chat_route.call_count == 1


async def test_every_attempt_goes_through_the_rate_limiter(
    chat_route: respx.Route, make_client: Callable[..., MeterlessClient]
) -> None:
    class CountingLimiter(AsyncRateLimiter):
        def __init__(self) -> None:
            super().__init__(max_calls=1000)
            self.entered = 0

        def slot(self) -> Any:  # noqa: ANN401
            self.entered += 1
            return super().slot()

    limiter = CountingLimiter()
    chat_route.side_effect = [httpx.Response(429), httpx.Response(200, json=completion())]
    await make_client(limiter=limiter).chat(MESSAGES)
    assert limiter.entered == 2


async def test_truncated_answer_raises_bad_response(
    chat_route: respx.Route, make_client: Callable[..., MeterlessClient]
) -> None:
    body = completion("<think>still reasoning when the limit hit")
    body["choices"][0]["finish_reason"] = "length"
    chat_route.respond(200, json=body)
    with pytest.raises(LLMBadResponse, match="truncated"):
        await make_client().chat(MESSAGES)


async def test_oversized_response_body_raises_bad_response(
    chat_route: respx.Route, make_client: Callable[..., MeterlessClient]
) -> None:
    chat_route.respond(200, content=b" " * (MAX_RESPONSE_BYTES + 1))
    with pytest.raises(LLMBadResponse):
        await make_client().chat(MESSAGES)


async def test_rejects_too_many_tools_without_a_request(
    chat_route: respx.Route, make_client: Callable[..., MeterlessClient]
) -> None:
    with pytest.raises(ValueError, match="tools"):
        await make_client().chat(MESSAGES, tools=[RUN_SQL_TOOL] * (MAX_TOOLS + 1))
    assert chat_route.call_count == 0


async def test_stops_retrying_when_the_wait_would_pass_the_deadline(
    chat_route: respx.Route,
    make_client: Callable[..., MeterlessClient],
    sleeper: Delays,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client_module, "TOTAL_DEADLINE_SECONDS", 5.0)
    chat_route.respond(429, headers={"Retry-After": "30"})
    with pytest.raises(LLMRateLimited):
        await make_client().chat(MESSAGES)
    assert chat_route.call_count == 1
    assert sleeper.delays == []


async def test_attempt_timeout_is_retried(
    chat_route: respx.Route,
    make_client: Callable[..., MeterlessClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client_module, "CHAT_TIMEOUT_SECONDS", 0.05)
    calls = 0

    async def slow_then_ok(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            await asyncio.sleep(1.0)
        return httpx.Response(200, json=completion())

    chat_route.mock(side_effect=slow_then_ok)
    result = await make_client().chat(MESSAGES)
    assert result.retries == 1


TOOL_CALL = {
    "id": "call_1",
    "type": "function",
    "function": {"name": "run_sql", "arguments": '{"template_id": "fin"}'},
}
NONCE = re.compile(
    r"request-id: [0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)


@pytest.mark.parametrize(
    ("cache", "status", "from_cache"),
    [
        ({"status": "exact", "source": "winner"}, "exact", True),
        ({"status": "miss"}, "miss", False),
        (None, None, False),
        ("exact", None, False),
        ({"status": "exact\x1b[2K"}, None, False),
    ],
)
async def test_records_cache_status_from_the_receipt(
    chat_route: respx.Route,
    make_client: Callable[..., MeterlessClient],
    cache: object,
    status: str | None,
    from_cache: bool,
) -> None:
    chat_route.respond(200, json=completion(cache=cache))
    result = await make_client().chat(MESSAGES)
    assert result.cache_status == status
    assert result.from_cache is from_cache


async def test_routed_model_falls_back_to_the_winning_route(
    chat_route: respx.Route, make_client: Callable[..., MeterlessClient]
) -> None:
    body = completion(model="echo-of-request")
    del body["meterless"]["routed_model"]
    body["meterless"]["winning_route"]["model"] = "nvidia/nemotron-3.5-bolt-e4b"
    chat_route.respond(200, json=body)
    assert (await make_client().chat(MESSAGES)).routed_model == "nvidia/nemotron-3.5-bolt-e4b"


@pytest.mark.parametrize(
    ("empty", "tools"),
    [
        (completion("", completion_tokens=0, cache={"status": "exact"}), [RUN_SQL_TOOL]),
        (completion(None, completion_tokens=3), [RUN_SQL_TOOL]),
        (completion("Hello", completion_tokens=0), None),
    ],
)
async def test_empty_reply_is_retried_once_with_a_fresh_request_id(
    chat_route: respx.Route,
    make_client: Callable[..., MeterlessClient],
    empty: dict[str, Any],
    tools: list[dict[str, Any]] | None,
) -> None:
    good = completion(None if tools else "Hello", tool_calls=[TOOL_CALL] if tools else None)
    chat_route.side_effect = [httpx.Response(200, json=empty), httpx.Response(200, json=good)]
    budget = CallBudget(limit=5)
    result = await make_client().chat(MESSAGES, tools=tools, budget=budget)
    assert result.retries == 1
    assert budget.used == 1
    first, second = sent_json(chat_route, 0), sent_json(chat_route, 1)
    assert first["messages"] == MESSAGES
    assert second["messages"][0]["role"] == "system"
    assert NONCE.fullmatch(second["messages"][0]["content"])
    assert second["messages"][1:] == MESSAGES
    assert second["model"] == first["model"]


async def test_each_empty_retry_uses_a_new_request_id(
    chat_route: respx.Route, make_client: Callable[..., MeterlessClient]
) -> None:
    good = completion("Hello")
    empty = completion("", completion_tokens=0)
    chat_route.side_effect = [httpx.Response(200, json=empty), httpx.Response(200, json=good)] * 2
    client = make_client()
    await client.chat(MESSAGES)
    await client.chat(MESSAGES)
    nonces = [sent_json(chat_route, i)["messages"][0]["content"] for i in (1, 3)]
    assert nonces[0] != nonces[1]


async def test_empty_reply_twice_raises_bad_response(
    chat_route: respx.Route, make_client: Callable[..., MeterlessClient]
) -> None:
    chat_route.respond(200, json=completion("", completion_tokens=0, cache={"status": "exact"}))
    with pytest.raises(LLMBadResponse, match="empty"):
        await make_client().chat(MESSAGES, tools=[RUN_SQL_TOOL])
    assert chat_route.call_count == 2


@pytest.mark.parametrize(
    "body",
    [
        completion("", completion_tokens=5),
        {"choices": [{"message": {"content": "ok"}}]},
    ],
)
async def test_text_reply_without_tools_is_not_treated_as_empty(
    chat_route: respx.Route, make_client: Callable[..., MeterlessClient], body: dict[str, Any]
) -> None:
    """Empty means: tools sent but nothing back, or the runtime reports 0 completion tokens."""
    chat_route.respond(200, json=body)
    await make_client().chat(MESSAGES)
    assert chat_route.call_count == 1
