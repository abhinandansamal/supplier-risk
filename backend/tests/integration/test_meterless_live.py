"""Live checks against the local Meterless Nightshift runtime (at most 5 requests, 3 to models).

Run with: uv run pytest -q -m integration backend/tests/integration/test_meterless_live.py
"""

import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
from pydantic import SecretStr

from app.core.settings import Settings
from app.llm.client import MeterlessClient
from app.llm.models import ChatResult
from app.llm.rate_limiter import AsyncRateLimiter

pytestmark = [
    pytest.mark.integration,
    # The properties are still written under xunit2; the receipt ids are what we want from the run.
    pytest.mark.filterwarnings("ignore:record_property is incompatible"),
]

RUN_SQL_TOOL = {
    "type": "function",
    "function": {
        "name": "run_sql",
        "description": "Run one of the fixed SQL templates with parameters.",
        "parameters": {
            "type": "object",
            "properties": {
                "template_id": {"type": "string"},
                "params": {"type": "object", "properties": {"supplier_id": {"type": "string"}}},
            },
            "required": ["template_id", "params"],
        },
    },
}
# At most one retry per request, so a failing live run does not spend the free-tier quota.
LIVE_MAX_ATTEMPTS = 2
Recorder = Callable[[str, object], None]


def _prompt(text: str) -> list[dict[str, Any]]:
    """Return a user message with a fresh uuid4, so Nightshift's exact cache never answers."""
    return [{"role": "user", "content": f"{text} (run {uuid.uuid4()})"}]


PLAIN_TEXT = "Reply with one short sentence: hello."


def _record(record_property: Recorder, result: ChatResult) -> None:
    """Store receipt ids in the JUnit report (ids only, never content)."""
    keys = ("requested_model", "routed_model", "provider", "receipt_id", "cache_status", "retries")
    for key in keys:
        record_property(key, getattr(result, key))


@pytest.fixture
async def client(live_settings: Settings) -> AsyncIterator[MeterlessClient]:
    # A fresh limiter per test: pytest-asyncio gives each test its own event loop.
    async with MeterlessClient(
        live_settings, limiter=AsyncRateLimiter(), max_attempts=LIVE_MAX_ATTEMPTS
    ) as meterless:
        yield meterless


async def test_wrong_token_is_unauthorized_not_offline(
    live_settings: Settings, record_property: Recorder
) -> None:
    wrong = live_settings.model_copy(
        update={"meterless_runtime_token": SecretStr("integration-wrong-token")}
    )
    async with MeterlessClient(
        wrong, limiter=AsyncRateLimiter(), max_attempts=LIVE_MAX_ATTEMPTS
    ) as meterless:
        state = (await meterless.status()).state
    record_property("state", state)
    assert state == "unauthorized"


async def test_status_is_online_with_our_four_models(
    client: MeterlessClient, live_settings: Settings, record_property: Recorder
) -> None:
    status = await client.status()
    record_property("state", status.state)
    record_property("model_count", len(status.model_ids))
    assert status.state == "online"
    for model in (
        live_settings.meterless_model,
        live_settings.meterless_deep_model,
        live_settings.meterless_fast_model,
        live_settings.meterless_embed_model,
    ):
        assert model in status.model_ids, model


async def test_plain_text_on_default_model_returns_string_and_receipt(
    client: MeterlessClient, live_settings: Settings, record_property: Recorder
) -> None:
    result = await client.chat(_prompt(PLAIN_TEXT))
    _record(record_property, result)
    assert isinstance(result.content, str)
    assert result.content
    assert result.receipt_id
    assert result.routed_model == live_settings.meterless_model


async def test_run_sql_tool_call_is_parsed(
    client: MeterlessClient, record_property: Recorder
) -> None:
    result = await client.chat(
        _prompt("Use run_sql with template_id supplier_financials for supplier S0001."),
        tools=[RUN_SQL_TOOL],
        tool_choice={"type": "function", "function": {"name": "run_sql"}},
    )
    _record(record_property, result)
    record_property("tool_calls", [call.name for call in result.tool_calls])
    assert result.tool_calls, "expected a tool call"
    call = result.tool_calls[0]
    assert call.name == "run_sql"
    assert isinstance(call.arguments, dict)
    assert call.arguments.get("template_id")


async def test_deep_model_content_has_no_think_text(
    client: MeterlessClient, record_property: Recorder
) -> None:
    result = await client.chat(_prompt(PLAIN_TEXT), role="deep")
    _record(record_property, result)
    record_property("fallback_used", result.fallback_used)
    assert result.content
    assert "</think>" not in result.content
    assert result.fallback_used is False
