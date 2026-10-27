"""Tests for the claim verifier: cited ids exist, numbers come from facts, ratings match."""

from datetime import UTC, datetime
from typing import Any

import pytest

from app.agent.models import Claim, Report
from app.agent.verifier import extract_numbers, mark_unverified, verify_report
from app.evidence.models import EvidenceRecord


def _evidence(evidence_id: str, facts: dict[str, Any], **params: Any) -> EvidenceRecord:  # noqa: ANN401
    return EvidenceRecord(
        evidence_id=evidence_id,
        tool="t",
        source="erp",
        view=None,
        sql_template_id=None,
        role="risk_manager",
        params=params,
        rows=[],
        row_count=0,
        zetaris_query_id=None,
        snapshot_at=None,
        retrieved_at=datetime.now(UTC),
        facts=facts,
    )


EVIDENCE = {
    "ev-delivery00001": _evidence(
        "ev-delivery00001",
        {"on_time_rate_last_6m": 0.3158, "deliveries_last_6m": 57, "overdue_last_6m": 6},
        supplier_id="S0003",
        window_months=6,
    ),
    "ev-revenue000001": _evidence(
        "ev-revenue000001",
        {"revenue_latest_usd": 4_713_100_000.0, "yoy_change_pct": 7.1},
        supplier_id="S0003",
    ),
    "ev-score00000001": _evidence(
        "ev-score00000001", {"score_total": 35.9, "coverage_pct": 85, "delivery_score": 100.0}
    ),
}
RATINGS = {"S0003": "amber"}


def _report(*texts: str, ids: tuple[str, ...] = ("ev-delivery00001",), **kw: Any) -> Report:  # noqa: ANN401
    claims = [Claim(text=t, evidence_ids=list(ids)) for t in texts]
    return Report(headline="S0003: Amber", claims=claims, missing=kw.get("missing", []))


def _codes(report: Report) -> list[str]:
    return [p.code for p in verify_report(report, EVIDENCE, RATINGS)]


def test_score_reason_with_rates_counts_and_window_passes() -> None:
    text = (
        "On-time rate 32 % over the last 6 months (57 deliveries, 6 overdue undelivered) "
        "for S0003 (PO000123, ev-delivery00001), as of 2026-10-27."
    )
    assert _codes(_report(text)) == []


def test_rates_may_be_written_with_the_precision_of_the_writer() -> None:
    assert _codes(_report("On-time rate 31.6 %.", "On-time rate 31.58 %.")) == []
    assert _codes(_report("On-time rate 0.32.")) == []


def test_an_invented_number_is_caught_and_named() -> None:
    problems = verify_report(_report("On-time rate 41 % (57 deliveries)."), EVIDENCE, RATINGS)
    assert [(p.code, p.detail, p.index) for p in problems] == [("unsupported_number", "41", 0)]


def test_a_number_from_an_evidence_record_that_is_not_cited_is_caught() -> None:
    """Numbers must come from the claim's own evidence, not from anywhere in the case."""
    assert _codes(_report("Risk score 35.9.")) == ["unsupported_number"]
    assert _codes(_report("Risk score 35.9.", ids=("ev-score00000001",))) == []


def test_money_in_millions_and_signed_changes_are_checked() -> None:
    ok = _report("Revenue 4,713.1 M USD (+7.1 %).", ids=("ev-revenue000001",))
    assert _codes(ok) == []
    assert _codes(_report("Revenue 4.7 billion USD.", ids=("ev-revenue000001",))) == []
    wrong_sign = _report("Revenue changed -7.1 %.", ids=("ev-revenue000001",))
    assert _codes(wrong_sign) == ["unsupported_number"]


def test_unknown_and_missing_evidence_ids_are_caught() -> None:
    assert _codes(_report("Late deliveries.", ids=("ev-000000000000",))) == ["unknown_evidence"]
    report = Report(headline="h", claims=[Claim.model_construct(text="x", evidence_ids=[])])
    assert _codes(report) == ["no_evidence"]


@pytest.mark.parametrize(
    "text",
    ["S0003 is low risk.", "S0003 should be rated green.", "This is a high-risk supplier."],
)
def test_a_rating_that_differs_from_the_score_is_caught(text: str) -> None:
    assert _codes(_report(text)) == ["rating_mismatch"]


def test_the_computed_rating_may_be_stated() -> None:
    assert _codes(_report("S0003 is rated amber (medium risk).")) == []


@pytest.mark.parametrize(
    "text", ["See https://evil.example.com/x", "Delivery <img src=x onerror=alert(1)>"]
)
def test_links_and_markup_are_caught(text: str) -> None:
    assert _codes(_report(text)) == ["link_or_markup"]


def test_missing_items_are_not_model_text_and_not_checked_here() -> None:
    """SEC-093: "missing" is built in Python, so the verifier only checks claims."""
    report = _report("Late deliveries.", missing=["Financials not assessed: two years"])
    assert _codes(report) == []


def test_extract_numbers_skips_ids_dates_and_periods() -> None:
    text = "S0003 PO000004 ev-1a2b3c4d5e6f FY2025 Q1 2026-10-27 2026-10 and 1,234.5 M and -3"
    assert [(n.sign + n.text, n.scale) for n in extract_numbers(text)] == [
        ("1,234.5", 1e6),
        ("-3", 1.0),
    ]


def test_mark_unverified_flags_only_failing_claims() -> None:
    report = _report("On-time rate 32 %.", "On-time rate 41 %.")
    marked = mark_unverified(report, verify_report(report, EVIDENCE, RATINGS))
    assert [c.verified for c in marked.claims] == [True, False]
    assert marked.claims[1].problems == ["unsupported_number: 41"]


def test_percentage_points_are_a_percent_unit() -> None:
    record = _evidence("ev-country000001", {"hicp_change_pp": 0.68})
    report = Report(
        headline="h",
        claims=[Claim(text="HICP rose 0.68 percentage points.", evidence_ids=["ev-country000001"])],
    )
    assert verify_report(report, {"ev-country000001": record}, RATINGS) == []
    (token,) = extract_numbers("HICP rose 0.68 percentage points.")
    assert token.unit == "percent"
