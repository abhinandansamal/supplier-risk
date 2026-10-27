"""Tests for row helpers (latest filing, fiscal years), score edge cases and the evidence log."""

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from tests.fakes.zetaris import FakeZetaris

from app.evidence import models as evidence_models
from app.evidence.models import EvidenceLog, EvidenceLogFull, EvidenceRecord
from app.score import components
from app.tools.cache import Row
from app.tools.catalogue import ToolContext, run_tool
from app.tools.errors import ToolForbidden
from app.tools.rows import full_years, latest_filing_facts, to_bool, to_int
from app.tools.scoring import on_time_rate, six_month_change


def _fact(start: str, end: str, fy: int, fp: str, value: str) -> Row:
    return {
        "supplier_id": "S0001",
        "cik": "1",
        "fiscal_year": str(fy),
        "fiscal_period": fp,
        "period_start": start,
        "period_end": end,
        "form_type": "10-K",
        "value_usd": value,
    }


def test_latest_filing_wins_and_fy_beats_quarters_of_the_same_year() -> None:
    facts = latest_filing_facts(
        [
            _fact("2024-01-01", "2024-12-31", 2024, "FY", "100"),
            _fact("2024-01-01", "2024-12-31", 2025, "Q1", "101"),
            _fact("2024-01-01", "2024-12-31", 2025, "FY", "102"),
        ]
    )
    assert [f["value_usd"] for f in facts] == ["102"]


def test_full_years_stop_at_a_gap() -> None:
    facts = [
        _fact("2020-01-01", "2020-12-31", 2020, "FY", "1"),
        _fact("2023-01-01", "2023-12-31", 2023, "FY", "2"),
        _fact("2024-01-01", "2024-12-31", 2024, "FY", "3"),
        _fact("2025-04-01", "2025-06-30", 2025, "Q1", "4"),
    ]
    assert [f["value_usd"] for f in full_years(facts)] == ["2", "3"]


def test_cell_readers() -> None:
    assert to_int("3.0") == 3
    assert to_int(None) is None
    assert to_bool("TRUE") and to_bool("1") and not to_bool("false") and not to_bool(None)


def test_on_time_rate_of_nothing_is_unknown() -> None:
    assert on_time_rate([]) is None


def test_financial_with_non_positive_previous_year_is_not_assessed() -> None:
    score = components.financial(latest=1.0, previous=0.0, latest_period="x", evidence_ids=["e"])
    assert not score.assessed


async def test_supplier_with_one_full_year_has_financials_not_assessed(
    make_ctx: Callable[..., ToolContext], fake: FakeZetaris
) -> None:
    fake.overrides["v_supplier_revenue"] = [
        ["S0003", "0000827054", "2026", "FY", "2025-04-01", "2026-03-31", "10-K", "4713100000.0"]
    ]
    result = await run_tool(make_ctx(), "compute_risk_score", {"supplier_id": "S0003"})
    financial = next(c for c in result.data["score"]["components"] if c["name"] == "financial")
    assert not financial["assessed"]
    assert "fewer than two full fiscal years" in financial["reason"]


def test_evidence_log_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(evidence_models, "MAX_RECORDS", 1)
    log = EvidenceLog()

    def make() -> EvidenceRecord:
        return EvidenceRecord(
            tool="t",
            source="erp",
            view=None,
            sql_template_id=None,
            role="buyer",
            params={},
            rows=[],
            row_count=0,
            zetaris_query_id=None,
            snapshot_at=None,
            retrieved_at=datetime.now(UTC),
        )

    log.add(make())
    with pytest.raises(EvidenceLogFull):
        log.add(make())


def test_six_month_change_needs_twelve_values() -> None:
    assert six_month_change([1.0] * 11) is None
    assert six_month_change([1.0] * 6 + [2.0] * 6) == 1.0


def test_auditor_context_has_no_zetaris_key(make_ctx: Callable[..., ToolContext]) -> None:
    with pytest.raises(ToolForbidden):
        _ = make_ctx("auditor").key
