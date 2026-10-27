"""Clean model output: remove reasoning text and parse tool calls safely."""

import json
import re
from typing import Any

from app.llm.errors import LLMBadResponse, ToolCallParseError
from app.llm.models import ToolCall

# Greedy, so the match ends at the last closing tag; one pass, no nested quantifiers.
_UP_TO_THINK_END = re.compile(r".*</think>", re.IGNORECASE | re.DOTALL)
_THINK_START = re.compile(r"<think>", re.IGNORECASE)
# Security: bounds the work spent on untrusted model output before any tool sees it.
MAX_ARGUMENT_CHARS = 32_000
MAX_TOOL_CALLS = 16
MAX_TOOL_CALL_ID_CHARS = 128
# Tool names are model output; only plain identifiers can match our catalogue anyway.
TOOL_NAME_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,63}")


def strip_think(text: str | None) -> str:
    """Return the visible answer without the model's reasoning.

    Nemotron reasoning models put their reasoning before "</think>", sometimes without the
    opening "<think>". Everything up to and including the last closing tag (any case) is
    removed. An opening tag that is never closed (output cut off) drops everything from
    that tag on. Text without either tag is returned unchanged.

    Args:
        text: Raw message content, possibly None.

    Returns:
        The visible answer.
    """
    if not text:
        return ""
    closed = _UP_TO_THINK_END.match(text)
    if closed:
        return text[closed.end() :].strip()
    # Security: an unclosed block is pure reasoning; never show it as the answer.
    opened = _THINK_START.search(text)
    return text[: opened.start()].strip() if opened else text


def _reject_constant(name: str) -> Any:  # noqa: ANN401
    raise ValueError(f"non-standard JSON constant {name}")


def _parse_arguments(name: str, raw: object) -> dict[str, Any]:
    """Return tool-call arguments as a dict, or raise ToolCallParseError.

    Security: arguments are model output; they are only ever read with json.loads.
    """
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        raise ToolCallParseError(f"tool call {name!r}: arguments are not a JSON string")
    if not raw.strip():
        return {}
    if len(raw) > MAX_ARGUMENT_CHARS:
        raise ToolCallParseError(f"tool call {name!r}: arguments too long")
    try:
        value = json.loads(raw, parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        # `from None`: the JSON error would quote the arguments.
        raise ToolCallParseError(f"tool call {name!r}: arguments are not valid JSON") from None
    if not isinstance(value, dict):
        raise ToolCallParseError(f"tool call {name!r}: arguments are not a JSON object")
    return value


def _call_id(value: object, index: int) -> str:
    """Return a usable call id (generated when missing), or raise LLMBadResponse."""
    if value is None:
        return f"call_{index}"
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_TOOL_CALL_ID_CHARS
        or not value.isprintable()
    ):
        raise LLMBadResponse(f"tool call {index} has an unusable id")
    return value


def parse_tool_calls(raw: list[Any] | None) -> list[ToolCall]:
    """Parse the OpenAI-style tool_calls list of an assistant message.

    Args:
        raw: The message's tool_calls value (None when the model answered in text).

    Returns:
        Parsed tool calls in the order given.

    Raises:
        LLMBadResponse: If the list or a call is malformed, too long, has a name that is not
            a plain identifier, or repeats an id. Messages use the call index, not model text.
        ToolCallParseError: If a call's arguments are not a JSON object.
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise LLMBadResponse("tool_calls is not a list")
    if len(raw) > MAX_TOOL_CALLS:
        raise LLMBadResponse(f"more than {MAX_TOOL_CALLS} tool calls")
    calls: list[ToolCall] = []
    for index, item in enumerate(raw):
        function = item.get("function") if isinstance(item, dict) else None
        name = function.get("name") if isinstance(function, dict) else None
        if not isinstance(name, str) or not TOOL_NAME_PATTERN.fullmatch(name):
            raise LLMBadResponse(f"tool call {index} has no valid function name")
        call_id = _call_id(item.get("id"), index)
        if any(call.id == call_id for call in calls):
            raise LLMBadResponse(f"tool call {index} repeats an id")
        calls.append(
            ToolCall(
                id=call_id,
                name=name,
                arguments=_parse_arguments(name, function.get("arguments")),  # type: ignore[union-attr]
            )
        )
    return calls
