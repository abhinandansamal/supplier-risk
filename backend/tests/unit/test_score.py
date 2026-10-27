"""Tests for the deterministic risk score: components, weights v1, coverage, rating, critical."""

import pytest

from app.score import components as c
from app.score.model import ComponentScore, RiskScore
from app.score.scorer import combine
from app.score.weights import (
    AMBER_FROM,
    CRITICAL_FROM,
    MIN_COVERAGE_PCT,
    RED_FROM,
    SCORE_RULES_VERSION,
    WEIGHTS,
    WEIGHTS_VERSION,
)


def test_weights_and_thresholds_v1_are_pinned() -> None:
    """Changing these is a documented design decision (architecture 6.5), never demo tuning."""
    assert WEIGHTS_VERSION == "v1"
    assert SCORE_RULES_VERSION == "v2"
    assert WEIGHTS == {
        "delivery": 25,
        "concentration": 25,
        "financial": 20,
        "country": 15,
        "weather": 15,
    }
    assert (AMBER_FROM, RED_FROM, MIN_COVERAGE_PCT, CRITICAL_FROM) == (35, 60, 50, 80)


def test_delivery_collapse_scores_the_maximum() -> None:
    score = c.delivery(on_time_last=0.37, count_last=54, on_time_prev=0.74, evidence_ids=["e1"])
    assert score.assessed and score.value == 100.0
    assert score.evidence_ids == ["e1"]
    assert "37 %" in score.reason and "74 %" in score.reason


def test_delivery_at_target_and_improving_scores_zero() -> None:
    score = c.delivery(on_time_last=0.97, count_last=38, on_time_prev=0.89, evidence_ids=["e"])
    assert score.value == 0.0


def test_delivery_level_and_trend_add_up() -> None:
    score = c.delivery(on_time_last=0.80, count_last=44, on_time_prev=0.86, evidence_ids=["e"])
    # level (0.95 - 0.80) / 0.45 * 80 = 26.67; trend (0.86 - 0.80) / 0.20 * 20 = 6.0
    assert score.value == pytest.approx(32.7, abs=0.05)


def test_delivery_without_previous_window_uses_level_only() -> None:
    score = c.delivery(on_time_last=0.50, count_last=12, on_time_prev=None, evidence_ids=["e"])
    assert score.value == 80.0


def test_delivery_with_too_few_deliveries_is_not_assessed() -> None:
    score = c.delivery(on_time_last=0.0, count_last=9, on_time_prev=None, evidence_ids=["e"])
    assert not score.assessed and score.value is None
    assert "9 deliveries" in score.reason


def test_concentration_weights_single_source_parts_by_criticality() -> None:
    score = c.concentration(
        single_source={"A": 1, "B": 0, "C": 0},
        open_orders=10,
        open_orders_single_source=5,
        evidence_ids=["s", "o"],
    )
    assert score.value == 65.0


def test_concentration_part_points_are_capped_at_70() -> None:
    score = c.concentration(
        single_source={"A": 2, "B": 1, "C": 3},
        open_orders=0,
        open_orders_single_source=0,
        evidence_ids=["s"],
    )
    assert score.value == 70.0


def test_no_single_source_parts_is_zero_concentration_not_unknown() -> None:
    score = c.concentration(
        single_source={"A": 0, "B": 0, "C": 0},
        open_orders=8,
        open_orders_single_source=0,
        evidence_ids=["s", "o"],
    )
    assert score.assessed and score.value == 0.0


def test_financial_decline_is_scaled_to_minus_20_percent() -> None:
    score = c.financial(
        latest=9_544.0e6,
        previous=10_268.0e6,
        latest_period="2025-01-01..2025-12-31",
        evidence_ids=["f"],
    )
    assert score.value == pytest.approx(35.3, abs=0.05)
    assert "-7.1 %" in score.reason


def test_financial_growth_scores_zero() -> None:
    score = c.financial(
        latest=4_713.1e6,
        previous=4_401.6e6,
        latest_period="2025-04-01..2026-03-31",
        evidence_ids=["f"],
    )
    assert score.value == 0.0
    assert "+7.1 %" in score.reason


def test_not_assessed_component_has_no_value_and_says_why() -> None:
    score = c.not_assessed("financial", "private supplier: no SEC filings", ["e"])
    assert not score.assessed and score.value is None
    assert score.reason == "private supplier: no SEC filings"


def test_country_averages_both_indicators() -> None:
    score = c.country(
        country_iso="FR", unemployment_change=0.25, hicp_change=1.65, evidence_ids=["k"]
    )
    # unemployment 0.25 / 0.5 = 50; HICP 1.65 / 2.0 = 82.5
    assert score.value == pytest.approx(66.3, abs=0.05)
    assert (
        score.reason == "FR, latest 6 vs previous 6 months: unemployment +0.25 pp, HICP +1.65 pp."
    )


def test_country_with_one_indicator_uses_it_alone() -> None:
    score = c.country(country_iso="ES", unemployment_change=None, hicp_change=1.0, evidence_ids=[])
    assert score.value == 50.0


def test_country_without_indicators_is_not_assessed() -> None:
    score = c.country(country_iso="ES", unemployment_change=None, hicp_change=None, evidence_ids=[])
    assert not score.assessed


def _comp(name: str, value: float | None) -> ComponentScore:
    if value is None:
        return c.not_assessed(name, "not assessed", [])  # type: ignore[arg-type]
    return ComponentScore(
        name=name,
        weight=WEIGHTS[name],
        assessed=True,
        value=value,
        evidence_ids=[f"e-{name}"],
        reason=f"{name} reason",
    )


def _score(**values: float | None) -> RiskScore:
    names = ("delivery", "concentration", "financial", "country", "weather")
    return combine("S0001", [_comp(n, values.get(n)) for n in names])


def test_total_is_the_weighted_average_of_assessed_components() -> None:
    score = _score(delivery=100, concentration=0, financial=0, country=40)
    assert score.total == pytest.approx((2500 + 600) / 85, abs=0.05)
    assert score.coverage_pct == 85
    assert score.weights_version == "v1"


@pytest.mark.parametrize(
    ("value", "rating"), [(34.9, "green"), (35.0, "amber"), (59.9, "amber"), (60.0, "red")]
)
def test_rating_thresholds(value: float, rating: str) -> None:
    assert _score(delivery=value, concentration=value).rating == rating


def test_low_coverage_is_insufficient_not_green() -> None:
    score = _score(delivery=0, country=0)
    assert score.coverage_pct == 40
    assert score.rating == "insufficient"


def test_nothing_assessed_has_no_total() -> None:
    score = _score()
    assert score.total is None and score.coverage_pct == 0 and score.rating == "insufficient"


def test_critical_signals_list_components_at_or_above_80_without_changing_the_rating() -> None:
    score = _score(delivery=100, concentration=0, financial=0, country=0)
    assert score.rating == "green"
    assert [s.component for s in score.critical_signals] == ["delivery"]
    assert score.critical_signals[0].reason == "delivery reason"
    assert score.label == "Green · coverage 85 % · critical: delivery"


def test_component_at_79_is_not_critical() -> None:
    assert _score(delivery=79.9, concentration=0).critical_signals == []


def test_unassessed_components_are_never_critical() -> None:
    assert _score(delivery=10).critical_signals == []


def test_combine_needs_each_component_once() -> None:
    with pytest.raises(ValueError, match="each component"):
        combine("S0001", [_comp("delivery", 1.0)])
