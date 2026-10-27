"""Tests for compute_risk_score on the planted stories (seed-42 ERP, real SEC/Eurostat facts)."""

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

import pytest
import respx
from tests.fakes.zetaris import TODAY, FakeZetaris, no_sleep, view_rows

from app.core.settings import Settings
from app.evidence.models import EvidenceLog
from app.score.model import RiskScore
from app.security.identity import UserContext
from app.tools.cache import SnapshotCache
from app.tools.catalogue import ToolContext, run_tool

Ctx = Callable[..., ToolContext]


async def _score(ctx: ToolContext, supplier_id: str) -> RiskScore:
    result = await run_tool(ctx, "compute_risk_score", {"supplier_id": supplier_id})
    return RiskScore.model_validate(result.data["score"])


def _component(score: RiskScore, name: str):  # noqa: ANN202 - test helper
    return next(c for c in score.components if c.name == name)


async def test_story_1a_s0003_delivery_high_financial_low(make_ctx: Ctx) -> None:
    score = await _score(make_ctx(), "S0003")
    delivery, financial = _component(score, "delivery"), _component(score, "financial")
    assert delivery.assessed and delivery.value is not None and delivery.value >= 80
    assert financial.assessed and financial.value == 0.0
    assert "delivery" in [s.component for s in score.critical_signals]
    assert "financial" not in [s.component for s in score.critical_signals]
    assert score.coverage_pct == 85
    assert score.weights_version == "v1"


async def test_story_1b_s0005_financial_and_country(make_ctx: Ctx) -> None:
    score = await _score(make_ctx(), "S0005")
    financial, country = _component(score, "financial"), _component(score, "country")
    assert financial.value == pytest.approx(35.3, abs=0.05)
    assert "-7.1 %" in financial.reason
    assert country.value == pytest.approx(66.3, abs=0.05)
    assert country.reason.startswith("FR")


async def test_private_supplier_has_unknown_financials_and_lower_coverage(make_ctx: Ctx) -> None:
    score = await _score(make_ctx(), "S0009")
    financial = _component(score, "financial")
    assert not financial.assessed and financial.value is None
    assert "private" in financial.reason
    assert score.coverage_pct == 65
    assert "coverage 65 %" in score.label


async def test_buyer_score_hides_financials_and_never_reads_revenue(
    make_ctx: Ctx, fake: FakeZetaris
) -> None:
    score = await _score(make_ctx("buyer", "electronics"), "S0003")
    financial = _component(score, "financial")
    assert not financial.assessed and "buyer" in financial.reason
    assert score.coverage_pct == 65
    assert "delivery" in [s.component for s in score.critical_signals]
    assert not any(view == "v_supplier_revenue" for _, view in fake.calls)


async def test_weather_is_never_assessed(make_ctx: Ctx) -> None:
    weather = _component(await _score(make_ctx(), "S0001"), "weather")
    assert not weather.assessed and "not assessed" in weather.reason


async def test_every_component_cites_evidence_in_the_log(make_ctx: Ctx) -> None:
    ctx = make_ctx()
    score = await _score(ctx, "S0003")
    for component in score.components:
        assert component.evidence_ids, component.name
        for evidence_id in component.evidence_ids:
            assert ctx.evidence.get(evidence_id).evidence_id == evidence_id


async def test_unavailable_external_source_lowers_coverage_instead_of_failing(
    make_ctx: Ctx, fake: FakeZetaris
) -> None:
    fake.fail_views.add("v_country_indicators")
    score = await _score(make_ctx(), "S0005")
    country = _component(score, "country")
    assert not country.assessed and "unavailable" in country.reason
    assert score.coverage_pct == 70


async def test_score_is_deterministic(make_ctx: Ctx) -> None:
    first = await _score(make_ctx(), "S0005")
    second = await _score(make_ctx(), "S0005")
    assert first.total == second.total
    assert first.components[0].value == second.components[0].value


# --- SEC-067 / score rules v2: overdue undelivered orders count as late deliveries ----------


def _deliveries_without(supplier_id: str) -> list[list[str | None]]:
    return [list(r) for r in view_rows()["v_deliveries"] if r[1] != supplier_id]


def _open(po: str, supplier_id: str, promised: str) -> list[str | None]:
    return [po, supplier_id, "P0099", "electronics", "open", promised, None, None]


def _delivered(po: str, supplier_id: str, promised: str, days_late: int) -> list[str | None]:
    delivered = (date.fromisoformat(promised) + timedelta(days=days_late)).isoformat()
    return [
        po,
        supplier_id,
        "P0099",
        "electronics",
        "delivered",
        promised,
        delivered,
        str(days_late),
    ]


async def test_scores_carry_weights_v1_and_score_rules_v2(make_ctx: Ctx) -> None:
    score = await _score(make_ctx(), "S0001")
    assert (score.weights_version, score.score_rules_version) == ("v1", "v2")


async def test_overdue_orders_without_any_completed_delivery_are_assessed(
    make_ctx: Ctx, fake: FakeZetaris
) -> None:
    rows = _deliveries_without("S0009")
    rows += [_open(f"PX{i}", "S0009", f"2026-10-0{i}") for i in (1, 2, 3)]
    fake.overrides["v_deliveries"] = rows
    delivery = _component(await _score(make_ctx(), "S0009"), "delivery")
    assert delivery.assessed
    assert delivery.value == 80.0  # on-time 0 %, no previous window
    assert "3 overdue" in delivery.reason


async def test_overdue_orders_count_as_late_in_rate_and_trend(
    make_ctx: Ctx, fake: FakeZetaris
) -> None:
    rows = _deliveries_without("S0009")
    # previous window (2025-11..2026-04): 10 on time; last (2026-05..10): 8 on time, 2 overdue
    rows += [_delivered(f"PA{i}", "S0009", "2026-01-15", 0) for i in range(10)]
    rows += [_delivered(f"PB{i}", "S0009", "2026-06-15", 0) for i in range(8)]
    rows += [_open(f"PC{i}", "S0009", "2026-10-20") for i in range(2)]
    fake.overrides["v_deliveries"] = rows
    delivery = _component(await _score(make_ctx(), "S0009"), "delivery")
    # level (0.95 - 0.80) / 0.45 * 80 = 26.67; trend (1.00 - 0.80) / 0.20 * 20 = 20
    assert delivery.value == pytest.approx(46.7, abs=0.05)
    assert "10 deliveries, 2 overdue" in delivery.reason


async def test_open_orders_not_yet_due_are_not_counted(make_ctx: Ctx, fake: FakeZetaris) -> None:
    rows = _deliveries_without("S0009")
    rows += [_delivered(f"PB{i}", "S0009", "2026-06-15", 0) for i in range(10)]
    rows += [_open("PD1", "S0009", "2026-10-27"), _open("PD2", "S0009", "2026-11-20")]
    fake.overrides["v_deliveries"] = rows
    delivery = _component(await _score(make_ctx(), "S0009"), "delivery")
    assert delivery.value == 0.0
    assert "overdue" not in delivery.reason


async def test_overdue_is_judged_as_of_the_snapshot_date(
    make_settings: Callable[..., Settings], router: respx.MockRouter, fake: FakeZetaris
) -> None:
    rows = _deliveries_without("S0009")
    rows += [_delivered(f"PB{i}", "S0009", "2026-06-15", 0) for i in range(10)]
    rows += [_open("PE1", "S0009", "2026-10-20")]
    fake.overrides["v_deliveries"] = rows
    early = SnapshotCache(
        make_settings(), sleep=no_sleep, now=lambda: datetime(2026, 10, 19, tzinfo=UTC)
    )
    try:
        user = UserContext(user_id="u", role="risk_manager")
        ctx = ToolContext(user=user, cache=early, evidence=EvidenceLog(), today=TODAY)
        delivery = _component(await _score(ctx, "S0009"), "delivery")
    finally:
        await early.aclose()
    assert delivery.value == 0.0  # due 2026-10-20 was not yet overdue on 2026-10-19


async def test_delivery_history_applies_the_same_rule(make_ctx: Ctx, fake: FakeZetaris) -> None:
    rows = _deliveries_without("S0009")
    rows += [_delivered(f"PB{i}", "S0009", "2026-10-05", 0) for i in range(3)]
    rows += [_open("PF1", "S0009", "2026-10-20"), _open("PF2", "S0009", "2026-11-20")]
    fake.overrides["v_deliveries"] = rows
    result = await run_tool(make_ctx(), "delivery_history", {"supplier_id": "S0009", "months": 1})
    october = result.data["months"][-1]
    assert (october["deliveries"], october["late"], october["overdue_undelivered"]) == (4, 1, 1)
    assert october["on_time_rate"] == 0.75
    assert result.data["overdue_undelivered"] == 1
