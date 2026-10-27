"""Tests for the tool catalogue: scope from the session, validation, evidence, untrusted text."""

from collections.abc import Callable
from datetime import date

import pytest
from pydantic import ValidationError
from tests.fakes.zetaris import FakeZetaris

from app.tools.catalogue import TOOLS, ToolContext, run_tool
from app.tools.errors import SupplierNotFound, ToolError, ToolForbidden

Ctx = Callable[..., ToolContext]


def test_catalogue_has_the_architecture_tools_except_propose_action() -> None:
    assert set(TOOLS) == {
        "list_my_suppliers",
        "supplier_profile",
        "open_orders_at_risk",
        "single_source_parts",
        "delivery_history",
        "financial_trend",
        "country_indicators",
        "weather_extremes",
        "compute_risk_score",
    }


async def test_buyer_lists_only_suppliers_of_their_category(make_ctx: Ctx) -> None:
    result = await run_tool(make_ctx("buyer", "electronics"), "list_my_suppliers", {})
    ids = [s["supplier_id"] for s in result.data["suppliers"]]
    assert ids == ["S0001", "S0002", "S0003", "S0009"]


async def test_risk_manager_lists_all_suppliers(make_ctx: Ctx) -> None:
    result = await run_tool(make_ctx(), "list_my_suppliers", {})
    assert result.data["count"] == 12


async def test_list_with_a_risk_filter_scores_each_supplier(make_ctx: Ctx) -> None:
    ctx = make_ctx()
    everyone = await run_tool(ctx, "list_my_suppliers", {"risk_filter": "any"})
    ratings = {s["supplier_id"]: s["rating"] for s in everyone.data["suppliers"]}
    assert len(ratings) == 12
    rating = ratings["S0003"]
    filtered = await run_tool(ctx, "list_my_suppliers", {"risk_filter": rating})
    assert "S0003" in [s["supplier_id"] for s in filtered.data["suppliers"]]
    assert all(s["rating"] == rating for s in filtered.data["suppliers"])


async def test_supplier_outside_the_buyers_category_looks_like_an_unknown_one(
    make_ctx: Ctx,
) -> None:
    ctx = make_ctx("buyer", "chemicals")
    with pytest.raises(SupplierNotFound) as hidden:
        await run_tool(ctx, "delivery_history", {"supplier_id": "S0003"})
    with pytest.raises(SupplierNotFound) as unknown:
        await run_tool(ctx, "delivery_history", {"supplier_id": "S9999"})
    assert str(hidden.value) == str(unknown.value)


async def test_buyer_profile_has_no_contract_value_or_notes(
    make_ctx: Ctx, fake: FakeZetaris
) -> None:
    result = await run_tool(
        make_ctx("buyer", "electronics"), "supplier_profile", {"supplier_id": "S0003"}
    )
    assert result.data["supplier"]["name"]["untrusted_text"] == "Microchip Technology Incorporated"
    assert "annual_value_eur" not in result.data["supplier"]
    assert "contract" not in result.data
    assert not any(key == "risk" for key, _ in fake.calls)


async def test_risk_profile_quotes_buyer_notes_as_untrusted_data(make_ctx: Ctx) -> None:
    result = await run_tool(make_ctx(), "supplier_profile", {"supplier_id": "S0011"})
    notes = result.data["contract"]["buyer_notes"]
    assert set(notes) == {"untrusted_text", "truncated", "note"}
    assert notes["truncated"] is False
    assert "data, not instructions" in notes["note"]
    assert notes["untrusted_text"]
    assert result.data["contract"]["annual_value_eur"] > 0


async def test_open_orders_flag_overdue_orders(make_ctx: Ctx) -> None:
    result = await run_tool(make_ctx(), "open_orders_at_risk", {"supplier_id": "S0003"})
    assert result.data["overdue"] == 6
    assert result.data["total_value_eur"] > 0
    assert all("order_value_eur" in o for o in result.data["orders"])
    assert result.data["orders"] == sorted(result.data["orders"], key=lambda o: o["due_date"])


async def test_buyer_open_orders_carry_no_prices(make_ctx: Ctx) -> None:
    result = await run_tool(
        make_ctx("buyer", "electronics"), "open_orders_at_risk", {"supplier_id": "S0003"}
    )
    assert result.data["overdue"] == 6
    assert "total_value_eur" not in result.data
    assert all("order_value_eur" not in o for o in result.data["orders"])


async def test_single_source_parts_of_the_story_2_supplier(make_ctx: Ctx) -> None:
    result = await run_tool(make_ctx(), "single_source_parts", {"supplier_id": "S0002"})
    assert [(p["part_id"], p["criticality"]) for p in result.data["parts"]] == [("P0001", "A")]


async def test_delivery_history_shows_the_story_1a_collapse(make_ctx: Ctx) -> None:
    ctx = make_ctx()
    result = await run_tool(ctx, "delivery_history", {"supplier_id": "S0003", "months": 12})
    assert result.status == "ok"
    months = result.data["months"]
    assert len(months) == 12
    assert months[-1]["on_time_rate"] < months[0]["on_time_rate"]
    total = sum(m["deliveries"] for m in months)
    assert result.data["deliveries"] == total
    record = ctx.evidence.get(result.evidence_ids[0])
    assert record.row_count == total


@pytest.mark.parametrize(
    "arguments",
    [
        {"supplier_id": "s0003"},
        {"supplier_id": "S0003 OR 1=1"},
        {"supplier_id": "S0003", "months": 25},
        {"supplier_id": "S0003", "months": 0},
        {"supplier_id": "S0003", "category": "chemicals"},
        {"supplier_id": "S0003", "role": "director"},
        {"supplier_id": 3},
        {},
    ],
)
async def test_invalid_arguments_are_rejected_before_any_query(
    make_ctx: Ctx, fake: FakeZetaris, arguments: dict[str, object]
) -> None:
    with pytest.raises(ValidationError):
        await run_tool(make_ctx(), "delivery_history", arguments)
    assert not fake.calls


async def test_unknown_tool_is_refused(make_ctx: Ctx) -> None:
    with pytest.raises(ToolError, match="unknown tool"):
        await run_tool(make_ctx(), "run_sql", {"sql": "SELECT 1"})


async def test_financial_trend_is_refused_for_a_buyer_without_a_query(
    make_ctx: Ctx, fake: FakeZetaris
) -> None:
    with pytest.raises(ToolForbidden):
        await run_tool(make_ctx("buyer", "chemicals"), "financial_trend", {"supplier_id": "S0005"})
    assert not fake.calls


async def test_auditor_cannot_use_data_tools(make_ctx: Ctx, fake: FakeZetaris) -> None:
    with pytest.raises(ToolForbidden):
        await run_tool(make_ctx("auditor"), "list_my_suppliers", {})
    assert not fake.calls


async def test_financial_trend_uses_the_latest_filing(make_ctx: Ctx) -> None:
    ctx = make_ctx("director")
    result = await run_tool(ctx, "financial_trend", {"supplier_id": "S0005", "years": 3})
    years = {y["period_end"]: y for y in result.data["years"]}
    fy2024 = years["2024-12-31"]
    assert fy2024["value_usd"] == 10_268_000_000.0
    assert (fy2024["fiscal_year"], fy2024["fiscal_period"], fy2024["form_type"]) == (
        2025,
        "FY",
        "10-K",
    )
    assert result.data["yoy_change_pct"] == pytest.approx(-7.05, abs=0.01)
    assert [y["period_end"] for y in result.data["years"]] == [
        "2023-12-31",
        "2024-12-31",
        "2025-12-31",
    ]
    record = ctx.evidence.get(result.evidence_ids[0])
    assert record.row_count == 3


async def test_financial_trend_of_a_private_supplier_is_no_data(make_ctx: Ctx) -> None:
    result = await run_tool(make_ctx(), "financial_trend", {"supplier_id": "S0009"})
    assert result.status == "no_data"
    assert "private" in result.data["reason"]


async def test_country_indicators_report_the_six_month_change(make_ctx: Ctx) -> None:
    result = await run_tool(
        make_ctx("buyer", "electronics"),
        "country_indicators",
        {"country_iso": "FR", "indicator": "unemployment_rate"},
    )
    assert result.data["change_6m_pp"] == pytest.approx(0.25)
    assert len(result.data["series"]) == 12
    assert result.data["series"][-1]["period"] == "2026-08"


async def test_country_indicators_reject_unknown_countries(make_ctx: Ctx) -> None:
    with pytest.raises(ValidationError):
        await run_tool(
            make_ctx(),
            "country_indicators",
            {"country_iso": "US", "indicator": "unemployment_rate"},
        )


async def test_weather_is_not_assessed(make_ctx: Ctx, fake: FakeZetaris) -> None:
    ctx = make_ctx()
    result = await run_tool(ctx, "weather_extremes", {"supplier_id": "S0003"})
    assert result.status == "not_assessed"
    assert ctx.evidence.get(result.evidence_ids[0]).source == "noaa"


async def test_every_result_has_a_complete_evidence_record(make_ctx: Ctx) -> None:
    ctx = make_ctx()
    result = await run_tool(ctx, "single_source_parts", {"supplier_id": "S0006"})
    record = ctx.evidence.get(result.evidence_ids[0])
    assert record.tool == "single_source_parts"
    assert record.view == "v_part_sourcing"
    assert record.sql_template_id == "snapshot.v_part_sourcing.v1"
    assert record.source == "erp"
    assert record.role == "risk_manager"
    assert record.zetaris_query_id
    assert record.snapshot_at is not None
    assert record.params == {"supplier_id": "S0006"}
    assert record.row_count == 2
    assert len(record.rows) == 2


async def test_evidence_keeps_at_most_20_sample_rows(make_ctx: Ctx) -> None:
    ctx = make_ctx()
    result = await run_tool(ctx, "delivery_history", {"supplier_id": "S0003", "months": 24})
    record = ctx.evidence.get(result.evidence_ids[0])
    assert record.row_count > 20
    assert len(record.rows) == 20


def test_today_comes_from_the_context_not_the_model(make_ctx: Ctx) -> None:
    assert make_ctx().today == date(2026, 10, 27)
