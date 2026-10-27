"""Tests for scripts/investigate.py: arguments, the printed report and trace, the JSON file."""

import json
from pathlib import Path

import pytest
from tests.fakes.agent import Investigate

from scripts import investigate as script


def test_parse_args_reads_the_session_and_question() -> None:
    args = script.parse_args(
        ["--role", "buyer", "--category", "electronics", "Why is S0003 risky?"]
    )
    assert (args.role, args.category, args.question) == (
        "buyer",
        "electronics",
        "Why is S0003 risky?",
    )
    assert script.user_from_args(args).category == "electronics"


def test_a_buyer_without_a_category_is_refused() -> None:
    with pytest.raises(SystemExit):
        script.user_from_args(script.parse_args(["--role", "buyer", "Why?"]))


async def test_render_prints_report_claims_and_trace(investigate: Investigate) -> None:
    result = (await investigate()).result
    text = script.render(result)
    assert text.startswith(f"Investigation {result.investigation_id}: completed")
    assert "S0003: Amber" in text
    assert "[1] " in text and result.claims[0].evidence_ids[0] in text
    assert "Proposals (pending approval)" in text
    receipts = [e.receipt_id for e in result.trace if e.receipt_id]
    assert receipts and all(r in text for r in receipts)


async def test_write_json_saves_the_whole_result(investigate: Investigate, tmp_path: Path) -> None:
    result = (await investigate()).result
    path = tmp_path / "out" / "result.json"
    script.write_json(result, path)
    saved = json.loads(path.read_text())
    assert saved["investigation_id"] == result.investigation_id
    assert saved["claims"] and saved["trace"]
