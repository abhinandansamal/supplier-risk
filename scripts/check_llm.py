"""Check the Meterless Nightshift runtime against our provider contract.

Usage:
    uv run python scripts/check_llm.py

Runs the same checks as backend/tests/integration/test_meterless_live.py and prints one
PASS/FAIL line per check. It makes at most 3 model calls (plus 2 status requests to the local
runtime), never prints the token or model output, and exits 1 if any check fails.
"""

import asyncio
import sys
import uuid
from collections.abc import Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import SecretStr

# The backend is not an installed package; make `app` importable when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.settings import Settings, SettingsError, load_settings  # noqa: E402
from app.llm.client import MAX_ATTEMPTS, MeterlessClient  # noqa: E402
from app.llm.errors import LLMError  # noqa: E402
from app.llm.models import RuntimeStatus  # noqa: E402

CHECK_NAMES = ("wrong_token", "status", "plain_text", "tool_call", "deep_no_think")
# Not a secret: a token that must be rejected, to prove 401 shows as "unauthorized".
WRONG_TOKEN = "check-llm-deliberately-wrong-token"  # noqa: S105
RUN_SQL_TOOL = {
    "type": "function",
    "function": {
        "name": "run_sql",
        "description": "Run one of the fixed SQL templates with parameters.",
        "parameters": {
            "type": "object",
            "properties": {
                "template_id": {"type": "string"},
                "params": {
                    "type": "object",
                    "properties": {"supplier_id": {"type": "string"}},
                },
            },
            "required": ["template_id", "params"],
        },
    },
}
PLAIN_TEXT = "Reply with one short sentence: hello."
TOOL_TEXT = "Use run_sql with template_id supplier_financials for supplier S0001."
TOOL_CHOICE = {"type": "function", "function": {"name": "run_sql"}}


def _prompt(text: str) -> list[dict[str, Any]]:
    """Return a user message with a fresh uuid4, so Nightshift's exact cache never answers."""
    return [{"role": "user", "content": f"{text} (run {uuid.uuid4()})"}]


@dataclass(frozen=True)
class CheckResult:
    """Outcome of one check.

    Attributes:
        name: Check name from CHECK_NAMES.
        passed: True if the check passed.
        detail: Short reason; ids and counts only, no secrets or model output.
    """

    name: str
    passed: bool
    detail: str


def format_result(result: CheckResult) -> str:
    """Return the PASS/FAIL line for one result."""
    return f"{'PASS' if result.passed else 'FAIL'} {result.name}: {result.detail}"


async def _check_wrong_token(settings: Settings, max_attempts: int) -> CheckResult:
    wrong = settings.model_copy(update={"meterless_runtime_token": SecretStr(WRONG_TOKEN)})
    async with MeterlessClient(wrong, max_attempts=max_attempts) as client:
        state = (await client.status()).state
    return CheckResult("wrong_token", state == "unauthorized", f"wrong token -> {state}")


def _status_result(status: RuntimeStatus, settings: Settings) -> CheckResult:
    wanted = [
        settings.meterless_model,
        settings.meterless_deep_model,
        settings.meterless_fast_model,
        settings.meterless_embed_model,
    ]
    missing = [model for model in wanted if model not in status.model_ids]
    if status.state != "online":
        return CheckResult("status", False, status.state)
    if missing:
        return CheckResult("status", False, "online, missing " + ", ".join(missing))
    return CheckResult("status", True, f"online, {len(status.model_ids)} models incl. all 4")


async def _check_plain_text(client: MeterlessClient, settings: Settings) -> CheckResult:
    result = await client.chat(_prompt(PLAIN_TEXT))
    detail = (
        f"{len(result.content)} chars, routed {result.routed_model} via {result.provider}, "
        f"receipt {result.receipt_id}, cache {result.cache_status}"
    )
    passed = (
        isinstance(result.content, str)
        and bool(result.content)
        and result.receipt_id is not None
        and result.routed_model == settings.meterless_model
    )
    return CheckResult("plain_text", passed, detail)


async def _check_tool_call(client: MeterlessClient) -> CheckResult:
    result = await client.chat(_prompt(TOOL_TEXT), tools=[RUN_SQL_TOOL], tool_choice=TOOL_CHOICE)
    names = [call.name for call in result.tool_calls]
    return CheckResult(
        "tool_call",
        "run_sql" in names,
        f"tool calls {names}, receipt {result.receipt_id}, cache {result.cache_status}",
    )


async def _check_deep(client: MeterlessClient) -> CheckResult:
    result = await client.chat(_prompt(PLAIN_TEXT), role="deep")
    passed = bool(result.content) and "</think>" not in result.content and not result.fallback_used
    detail = (
        f"{len(result.content)} chars, routed {result.routed_model}, "
        f"fallback {result.fallback_used}, receipt {result.receipt_id}, "
        f"cache {result.cache_status}"
    )
    return CheckResult("deep_no_think", passed, detail)


async def _guarded(name: str, check: Coroutine[Any, Any, CheckResult]) -> CheckResult:
    """Await a check and turn a client error into a FAIL with the error type only."""
    try:
        return await check
    except LLMError as exc:
        return CheckResult(name, False, f"{type(exc).__name__} (status {exc.status_code})")


async def run_checks(settings: Settings, *, max_attempts: int = MAX_ATTEMPTS) -> list[CheckResult]:
    """Run every check in CHECK_NAMES order.

    Chat checks run only when the runtime is online, so an offline runtime costs no calls.

    Args:
        settings: Application settings (base URL, token, app id, model ids).
        max_attempts: Attempts per model request.

    Returns:
        One result per check.
    """
    results = [await _check_wrong_token(settings, max_attempts)]
    async with MeterlessClient(settings, max_attempts=max_attempts) as client:
        status = await client.status()
        results.append(_status_result(status, settings))
        if status.state != "online":
            skipped = "skipped: runtime not online"
            return results + [CheckResult(n, False, skipped) for n in CHECK_NAMES[2:]]
        results.append(await _guarded("plain_text", _check_plain_text(client, settings)))
        results.append(await _guarded("tool_call", _check_tool_call(client)))
        results.append(await _guarded("deep_no_think", _check_deep(client)))
    return results


async def main(settings: Settings | None = None, *, max_attempts: int = MAX_ATTEMPTS) -> int:
    """Run the checks and print PASS/FAIL lines.

    Args:
        settings: Settings to use; loaded from the environment and .env when None.
        max_attempts: Attempts per model request.

    Returns:
        0 if every check passed, else 1.
    """
    if settings is None:
        try:
            settings = load_settings()
        except SettingsError as exc:
            print(f"FAIL settings: {exc}")
            return 1
    results = await run_checks(settings, max_attempts=max_attempts)
    for result in results:
        print(format_result(result))
    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
