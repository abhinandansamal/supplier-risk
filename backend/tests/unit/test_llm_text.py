"""Tests for app.llm.text: reasoning removal and tool-call parsing."""

import json

import pytest

from app.llm.errors import LLMBadResponse, ToolCallParseError
from app.llm.text import MAX_ARGUMENT_CHARS, MAX_TOOL_CALLS, parse_tool_calls, strip_think


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("<think>plan the answer</think>Answer", "Answer"),
        ("reasoning without an opening tag</think>\n\nAnswer", "Answer"),
        ("<think>a</think>b</think>  final", "final"),
        ("Plain answer", "Plain answer"),
        ("  keeps spacing  ", "  keeps spacing  "),
        ("", ""),
        (None, ""),
    ],
)
def test_strip_think(raw: str | None, expected: str) -> None:
    assert strip_think(raw) == expected


def _call(
    arguments: object, name: object = "run_sql", call_id: str = "call_1"
) -> dict[str, object]:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


def test_parses_json_arguments() -> None:
    args = {"template_id": "supplier_financials", "params": {"supplier_id": "S0001"}}
    calls = parse_tool_calls([_call(json.dumps(args))])
    assert len(calls) == 1
    assert calls[0].id == "call_1"
    assert calls[0].name == "run_sql"
    assert calls[0].arguments == args


def test_accepts_arguments_already_decoded_as_object() -> None:
    assert parse_tool_calls([_call({"a": 1})])[0].arguments == {"a": 1}


def test_empty_arguments_mean_no_arguments() -> None:
    assert parse_tool_calls([_call("")])[0].arguments == {}


@pytest.mark.parametrize("raw", [None, []])
def test_no_tool_calls(raw: list[object] | None) -> None:
    assert parse_tool_calls(raw) == []


@pytest.mark.parametrize(
    "bad",
    [
        "{not json",
        "[1, 2]",
        '"text"',
        "__import__('os').system('id')",
        "NaN",
        '{"a": NaN}',
        '{"a": Infinity}',
        42,
    ],
)
def test_invalid_arguments_raise_typed_error(bad: object) -> None:
    with pytest.raises(ToolCallParseError):
        parse_tool_calls([_call(bad)])


def test_error_names_the_tool_but_not_the_arguments() -> None:
    with pytest.raises(ToolCallParseError) as info:
        parse_tool_calls([_call('{"x": "SECRET-ARG", ')])
    assert "run_sql" in str(info.value)
    assert "SECRET-ARG" not in str(info.value)


def test_oversized_arguments_raise_typed_error() -> None:
    big = json.dumps({"x": "a" * MAX_ARGUMENT_CHARS})
    with pytest.raises(ToolCallParseError):
        parse_tool_calls([_call(big)])


def test_tool_call_parse_error_is_a_bad_response() -> None:
    assert issubclass(ToolCallParseError, LLMBadResponse)


@pytest.mark.parametrize(
    "raw",
    [
        "not a list",
        [{"id": "c1", "type": "function"}],
        [_call("{}", name="")],
        [_call("{}", name=7)],
        ["not a dict"],
    ],
)
def test_malformed_tool_calls_raise_bad_response(raw: object) -> None:
    with pytest.raises(LLMBadResponse):
        parse_tool_calls(raw)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("<think>secret plan, cut off by max_tokens", ""),
        ("Intro <THINK>unfinished", "Intro"),
        ("plan</Think>Answer", "Answer"),
    ],
)
def test_strip_think_drops_unclosed_and_differently_cased_blocks(raw: str, expected: str) -> None:
    assert strip_think(raw) == expected


def test_too_many_tool_calls_raise_bad_response() -> None:
    calls = [_call("{}", call_id=f"c{i}") for i in range(MAX_TOOL_CALLS + 1)]
    with pytest.raises(LLMBadResponse):
        parse_tool_calls(calls)


def test_duplicate_tool_call_ids_raise_bad_response() -> None:
    with pytest.raises(LLMBadResponse):
        parse_tool_calls([_call("{}"), _call("{}")])


@pytest.mark.parametrize("call_id", ["c" * 129, "id\nwith newline", 7])
def test_unusable_tool_call_ids_raise_bad_response(call_id: object) -> None:
    call = {"id": call_id, "type": "function", "function": {"name": "run_sql", "arguments": "{}"}}
    with pytest.raises(LLMBadResponse):
        parse_tool_calls([call])
