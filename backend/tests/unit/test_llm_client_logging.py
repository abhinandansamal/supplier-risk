"""Tests for MeterlessClient logging: events, fields, levels and what is never logged."""

import json
import logging
from collections.abc import Callable
from typing import Any

import httpx
import pytest
import respx

from app.llm.client import MeterlessClient
from app.llm.errors import LLMUnavailable

SECRET_PROMPT = "TOP-SECRET-PROMPT"
SECRET_ANSWER = "SECRET-ANSWER"
SECRET_ARG = "SECRET-ARG"
TOKEN = "mt-secret-VALUE-2222"
DEFAULT_MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"
DEEP_MODEL = "nvidia/nemotron-3.5-ultra"


def completion(model: str = DEFAULT_MODEL) -> dict[str, Any]:
    tool_call = {
        "id": "call_1",
        "type": "function",
        "function": {"name": "run_sql", "arguments": json.dumps({"q": SECRET_ARG})},
    }
    return {
        "model": model,
        "choices": [
            {"message": {"role": "assistant", "content": SECRET_ANSWER, "tool_calls": [tool_call]}}
        ],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        "meterless": {
            "receipt_id": "rcpt_req_abc",
            "requested_model": model,
            "routed_model": model,
            "winning_route": {"model": model, "provider": "nvidia", "kind": "cloud"},
        },
    }


def events(caplog: pytest.LogCaptureFixture, event: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if getattr(r, "event", None) == event]


MESSAGES = [{"role": "user", "content": SECRET_PROMPT}]


async def test_one_info_line_per_call_with_receipt_fields(
    meterless: respx.MockRouter,
    make_client: Callable[..., MeterlessClient],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    meterless.post("/chat/completions").respond(200, json=completion())
    await make_client().chat(MESSAGES)
    records = events(caplog, "model_call")
    assert len(records) == 1
    record = records[0]
    assert record.levelno == logging.INFO
    assert record.name == "app.llm.client"
    expected = {
        "role": "default",
        "requested_model": DEFAULT_MODEL,
        "routed_model": DEFAULT_MODEL,
        "provider": "nvidia",
        "receipt_id": "rcpt_req_abc",
        "prompt_tokens": 11,
        "completion_tokens": 7,
        "total_tokens": 18,
        "retries": 0,
        "tool_calls": 1,
    }
    for key, value in expected.items():
        assert getattr(record, key) == value, key
    assert isinstance(record.latency_ms, int)


async def test_retry_is_logged_as_warning(
    meterless: respx.MockRouter,
    make_client: Callable[..., MeterlessClient],
    caplog: pytest.LogCaptureFixture,
) -> None:
    meterless.post("/chat/completions").side_effect = [
        httpx.Response(429),
        httpx.ReadTimeout("slow"),
        httpx.Response(200, json=completion()),
    ]
    await make_client().chat(MESSAGES)
    records = events(caplog, "model_retry")
    assert [r.levelno for r in records] == [logging.WARNING, logging.WARNING]
    assert records[0].attempt == 1
    assert records[0].status_code == 429
    assert records[0].delay_ms == 1000
    assert records[1].error_type == "ReadTimeout"
    assert records[1].model == DEFAULT_MODEL


async def test_fallback_is_logged_as_warning(
    meterless: respx.MockRouter,
    make_client: Callable[..., MeterlessClient],
    caplog: pytest.LogCaptureFixture,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        if model == DEEP_MODEL:
            return httpx.Response(429)
        return httpx.Response(200, json=completion(model))

    meterless.post("/chat/completions").mock(side_effect=handler)
    await make_client().chat(MESSAGES, role="deep")
    records = events(caplog, "model_fallback")
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    assert records[0].from_model == DEEP_MODEL
    assert records[0].to_model == DEFAULT_MODEL


async def test_final_failure_is_logged_as_error(
    meterless: respx.MockRouter,
    make_client: Callable[..., MeterlessClient],
    caplog: pytest.LogCaptureFixture,
) -> None:
    meterless.post("/chat/completions").respond(503, text="UPSTREAM-BODY")
    with pytest.raises(LLMUnavailable):
        await make_client().chat(MESSAGES)
    records = events(caplog, "model_call_failed")
    assert len(records) == 1
    assert records[0].levelno == logging.ERROR
    assert records[0].error_type == "LLMUnavailable"
    assert records[0].requested_model == DEFAULT_MODEL


async def test_status_is_logged(
    meterless: respx.MockRouter,
    make_client: Callable[..., MeterlessClient],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    meterless.get("/models").respond(401)
    await make_client().status()
    records = events(caplog, "model_status")
    assert len(records) == 1
    assert records[0].state == "unauthorized"


async def test_embed_call_is_logged(
    meterless: respx.MockRouter,
    make_client: Callable[..., MeterlessClient],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    meterless.post("/embeddings").respond(200, json={"data": [{"index": 0, "embedding": [0.1]}]})
    await make_client().embed([SECRET_PROMPT])
    records = events(caplog, "embed_call")
    assert len(records) == 1
    assert records[0].texts == 1


async def test_never_logs_prompts_contents_tool_arguments_or_token(
    meterless: respx.MockRouter,
    make_client: Callable[..., MeterlessClient],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    meterless.post("/chat/completions").side_effect = [
        httpx.Response(429, text=SECRET_ANSWER),
        httpx.Response(200, json=completion()),
    ]
    meterless.post("/embeddings").respond(200, json={"data": [{"index": 0, "embedding": [0.1]}]})
    client = make_client()
    await client.chat(MESSAGES)
    await client.embed([SECRET_PROMPT])
    assert caplog.records
    for record in caplog.records:
        text = record.getMessage() + " " + " ".join(str(v) for v in record.__dict__.values())
        for secret in (SECRET_PROMPT, SECRET_ANSWER, SECRET_ARG, TOKEN):
            assert secret not in text, (record.__dict__.get("event"), secret)


async def test_model_call_logs_cache_status(
    meterless: respx.MockRouter,
    make_client: Callable[..., MeterlessClient],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    body = completion()
    body["meterless"]["cache"] = {"status": "miss", "source": "winner"}
    meterless.post("/chat/completions").respond(200, json=body)
    await make_client().chat(MESSAGES)
    record = events(caplog, "model_call")[0]
    assert record.cache_status == "miss"
    assert record.from_cache is False


async def test_empty_reply_retry_is_logged_as_warning(
    meterless: respx.MockRouter,
    make_client: Callable[..., MeterlessClient],
    caplog: pytest.LogCaptureFixture,
) -> None:
    empty = completion()
    empty["choices"][0]["message"] = {"role": "assistant", "content": ""}
    empty["usage"]["completion_tokens"] = 0
    empty["meterless"]["cache"] = {"status": "exact"}
    meterless.post("/chat/completions").side_effect = [
        httpx.Response(200, json=empty),
        httpx.Response(200, json=completion()),
    ]
    await make_client().chat(MESSAGES)
    records = events(caplog, "model_empty_reply")
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    assert records[0].cache_status == "exact"
    assert records[0].requested_model == DEFAULT_MODEL
