"""Tests for computed facts and row digests on evidence records (SEC-066)."""

from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from app.evidence.models import MAX_FACTS, EvidenceRecord, rows_digest
from app.tools.catalogue import ToolContext, run_tool
from app.tools.context import record_computed
from app.tools.scoring import compute_score

Ctx = Callable[..., ToolContext]
PRICE_FACTS = {"total_value_eur", "annual_value_eur"}


def _record(**overrides: object) -> EvidenceRecord:
    values: dict[str, object] = {
        "tool": "t",
        "source": "erp",
        "view": None,
        "sql_template_id": None,
        "role": "risk_manager",
        "params": {},
        "rows": [],
        "row_count": 0,
        "zetaris_query_id": None,
        "snapshot_at": None,
        "retrieved_at": datetime.now(UTC),
    }
    values.update(overrides)
    return EvidenceRecord.model_validate(values)


def test_rows_digest_is_stable_and_order_sensitive() -> None:
    rows = [{"a": "1", "b": None}, {"a": "2", "b": "x"}]
    assert rows_digest(rows) == rows_digest([{"b": None, "a": "1"}, {"a": "2", "b": "x"}])
    assert rows_digest(rows) != rows_digest(rows[::-1])
    assert len(rows_digest(rows)) == 64


@pytest.mark.parametrize(
    "facts",
    [
        {"nested": {"a": 1}},
        {"Bad Key": 1},
        {f"k{i}": i for i in range(MAX_FACTS + 1)},
        {"rate": float("nan")},
    ],
)
def test_facts_are_flat_bounded_and_finite(facts: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        _record(facts=facts)


async def test_delivery_history_records_its_aggregates(make_ctx: Ctx) -> None:
    ctx = make_ctx("risk_manager")
    result = await run_tool(ctx, "delivery_history", {"supplier_id": "S0003", "months": 6})
    facts = ctx.evidence.get(result.evidence_ids[0]).facts
    assert facts["window_months"] == 6
    assert facts["deliveries"] == result.data["deliveries"]
    month = result.data["months"][-1]
    key = month["month"].replace("-", "_")
    assert facts[f"deliveries_{key}"] == month["deliveries"]
    assert facts[f"on_time_rate_{key}"] == month["on_time_rate"]


async def test_open_orders_record_counts_and_value_for_risk_roles_only(make_ctx: Ctx) -> None:
    risk = make_ctx("risk_manager")
    result = await run_tool(risk, "open_orders_at_risk", {"supplier_id": "S0003"})
    facts = risk.evidence.get(result.evidence_ids[0]).facts
    for name in ("open_orders", "overdue", "due_within_30_days", "on_single_source_parts"):
        assert facts[name] == result.data[name]
    assert facts["total_value_eur"] == result.data["total_value_eur"]
    buyer = make_ctx("buyer", "electronics")
    result = await run_tool(buyer, "open_orders_at_risk", {"supplier_id": "S0003"})
    for evidence_id in result.evidence_ids:
        assert not PRICE_FACTS & set(buyer.evidence.get(evidence_id).facts)


async def test_financial_trend_records_the_change_and_each_year(make_ctx: Ctx) -> None:
    ctx = make_ctx("risk_manager")
    result = await run_tool(ctx, "financial_trend", {"supplier_id": "S0003"})
    facts = ctx.evidence.get(result.evidence_ids[0]).facts
    assert facts["yoy_change_pct"] == result.data["yoy_change_pct"]
    year = result.data["years"][-1]
    assert facts[f"revenue_usd_fy{year['fiscal_year']}"] == year["value_usd"]


async def test_country_and_single_source_tools_record_their_numbers(make_ctx: Ctx) -> None:
    ctx = make_ctx("risk_manager")
    country = await run_tool(
        ctx, "country_indicators", {"country_iso": "PL", "indicator": "hicp_annual_rate"}
    )
    facts = ctx.evidence.get(country.evidence_ids[0]).facts
    assert facts["change_6m_pp"] == country.data["change_6m_pp"]
    assert facts["latest_value"] == country.data["series"][-1]["value"]
    parts = await run_tool(ctx, "single_source_parts", {"supplier_id": "S0002"})
    assert ctx.evidence.get(parts.evidence_ids[0]).facts["count"] == parts.data["count"]


async def test_score_evidence_holds_every_number_of_the_component_reasons(make_ctx: Ctx) -> None:
    """The writer fallback quotes these reasons, so the verifier must find their numbers."""
    ctx = make_ctx("risk_manager")
    score = await compute_score(ctx, "S0003")
    delivery = next(c for c in score.components if c.name == "delivery")
    facts = ctx.evidence.get(delivery.evidence_ids[0]).facts
    assert facts["window_months"] == 6
    assert {"on_time_rate_last_6m", "deliveries_last_6m", "overdue_last_6m"} <= set(facts)
    financial = next(c for c in score.components if c.name == "financial")
    assert "yoy_change_pct" in ctx.evidence.get(financial.evidence_ids[0]).facts


async def test_record_computed_stores_facts_without_a_view(make_ctx: Ctx) -> None:
    ctx = make_ctx("risk_manager")
    evidence_id = record_computed(
        ctx, "risk_score", {"supplier_id": "S0003"}, {"total": 35.9}, note="computed"
    )
    record = ctx.evidence.get(evidence_id)
    assert record.source == "computed"
    assert record.view is None and record.facts == {"total": 35.9}
