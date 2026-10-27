"""Tests for the versioned prompts and the JSON reader for model output."""

import pytest
from pydantic import BaseModel

from app.agent.json_output import MAX_JSON_CHARS, OutputInvalid, parse_model_json
from app.agent.prompts import PROMPTS, load_prompt

EVIDENCE_RULES = (
    "only the evidence",
    "cite evidence ids",
    "copy numbers exactly",
    "say what is missing",
    "data, never instructions",
)


@pytest.mark.parametrize("name", sorted(PROMPTS))
def test_prompt_starts_with_its_name_and_version(name: str) -> None:
    prompt = load_prompt(name)
    assert prompt.text.splitlines()[0] == f"# {name} prompt {prompt.version}"
    assert prompt.version.startswith("v")


@pytest.mark.parametrize("name", ["planner", "research", "evaluator", "recommender", "writer"])
def test_evidence_prompts_hold_every_rule(name: str) -> None:
    text = load_prompt(name).text.lower()
    for rule in EVIDENCE_RULES:
        assert rule in text, f"{name} lacks {rule!r}"


def test_writer_labels_causes_not_in_the_data_as_hypotheses() -> None:
    assert "hypothesis" in load_prompt("writer").text.lower()


def test_guard_treats_the_question_as_data() -> None:
    assert "data, never instructions" in load_prompt("guard").text.lower()


@pytest.mark.parametrize("name", sorted(PROMPTS))
def test_prompts_hold_no_secrets_or_access_rules(name: str) -> None:
    """T7: nothing worth extracting lives in a prompt."""
    text = load_prompt(name).text.lower()
    for word in ("zk-", "nvapi-", "bearer", "password", "svc_risk", "svc_buyer", "api key"):
        assert word not in text


def test_unknown_prompt_is_refused() -> None:
    with pytest.raises(KeyError):
        load_prompt("../../etc/passwd")


class _Out(BaseModel):
    verdict: str


@pytest.mark.parametrize(
    "text",
    [
        '{"verdict": "ok"}',
        '```json\n{"verdict": "ok"}\n```',
        'Sure, here it is: {"verdict": "ok"} Hope that helps.',
    ],
)
def test_parse_model_json_finds_the_object(text: str) -> None:
    assert parse_model_json(text, _Out).verdict == "ok"


@pytest.mark.parametrize(
    "text",
    ["", "no json here", '["ok"]', '{"verdict": 1}', '{"verdict": "ok"', "{" * 10_000],
)
def test_parse_model_json_rejects_bad_output_without_echoing_it(text: str) -> None:
    with pytest.raises(OutputInvalid) as info:
        parse_model_json(text, _Out)
    assert 'verdict": 1' not in str(info.value)


def test_parse_model_json_refuses_oversized_output() -> None:
    with pytest.raises(OutputInvalid):
        parse_model_json('{"verdict": "' + "x" * MAX_JSON_CHARS + '"}', _Out)
