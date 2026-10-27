"""Zetaris (local): every risk view answers through the svc_risk key; buyers cannot see prices.

Needs ZETARIS_KEY_RISK (and ZETARIS_KEY_BUYER for the buyer checks), created as described in
docs/zetaris-setup.md. Skips with the reason when a key is missing or has no access yet.

Tests on views over live SEC/Eurostat REST tables are marked slow_live (minutes each until
Feature 5 caches them): run the rest with -m "integration and not slow_live".
"""

from collections.abc import AsyncIterator

import pytest

from app.core.settings import Settings
from app.zetaris.client import ZetarisClient
from app.zetaris.errors import ZetarisError, ZetarisUnauthorized
from scripts.zetaris_verify import VERIFY_TIMEOUT_SECONDS, VIEW_CHECKS

pytestmark = pytest.mark.integration

SETUP = "see docs/zetaris-setup.md"
# Views over live REST tables are queried at the source on every call (not cached yet).
LIVE_VIEWS = {"v_supplier_revenue", "v_country_indicators"}
slow_live = pytest.mark.slow_live


async def _client(settings: Settings, role: str, variable: str) -> ZetarisClient:
    if getattr(settings, variable.lower()) is None:
        pytest.skip(f"{variable} is not set ({SETUP})")
    # Even ERP views took 13-20 s on the local Zetaris, so the app default (20 s) is too tight.
    return ZetarisClient(settings, role, query_timeout=VERIFY_TIMEOUT_SECONDS)  # type: ignore[arg-type]


@pytest.fixture
async def risk(live_settings: Settings) -> AsyncIterator[ZetarisClient]:
    client = await _client(live_settings, "risk_manager", "ZETARIS_KEY_RISK")
    try:
        await client.query("SELECT COUNT(1) FROM risk.v_suppliers_full")
    except ZetarisUnauthorized:
        await client.aclose()
        pytest.skip(f"ZETARIS_KEY_RISK has no access to risk views yet ({SETUP})")
    yield client
    await client.aclose()


@pytest.fixture
async def buyer(live_settings: Settings) -> AsyncIterator[ZetarisClient]:
    """Yield the buyer client only once it can read a buyer view.

    Without this probe, a key with no access at all would make the "cannot see" tests pass.
    """
    client = await _client(live_settings, "buyer", "ZETARIS_KEY_BUYER")
    try:
        await client.query("SELECT COUNT(1) FROM risk.v_suppliers_buyer")
    except ZetarisUnauthorized:
        await client.aclose()
        pytest.skip(f"ZETARIS_KEY_BUYER has no access to buyer views yet ({SETUP})")
    yield client
    await client.aclose()


async def _scalar(client: ZetarisClient, sql: str) -> int:
    result = await client.query(sql, limit=1)
    return int(str(result.rows[0][0]))


@pytest.mark.parametrize(
    "view",
    [
        pytest.param(check.name, marks=slow_live) if check.name in LIVE_VIEWS else check.name
        for check in VIEW_CHECKS
    ],
)
async def test_every_risk_view_has_rows(risk: ZetarisClient, view: str) -> None:
    assert await _scalar(risk, f"SELECT COUNT(1) FROM risk.{view}") > 0  # noqa: S608 - fixed names


async def test_all_twelve_suppliers_are_visible(risk: ZetarisClient) -> None:
    assert await _scalar(risk, "SELECT COUNT(1) FROM risk.v_suppliers_full") == 12
    assert await _scalar(risk, "SELECT COUNT(1) FROM risk.v_contracts_full") == 12


@slow_live
async def test_revenue_covers_the_eight_public_suppliers(risk: ZetarisClient) -> None:
    sql = "SELECT COUNT(DISTINCT supplier_id) FROM risk.v_supplier_revenue"
    assert await _scalar(risk, sql) == 8


@slow_live
async def test_country_indicators_cover_six_countries_and_two_indicators(
    risk: ZetarisClient,
) -> None:
    sql = "SELECT COUNT(DISTINCT CONCAT(country_iso, indicator)) FROM risk.v_country_indicators"
    assert await _scalar(risk, sql) == 12


async def test_buyer_reads_the_buyer_views(buyer: ZetarisClient) -> None:
    assert await _scalar(buyer, "SELECT COUNT(1) FROM risk.v_open_orders_buyer") > 0


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT unit_price_eur FROM risk.v_open_orders_full",
        "SELECT annual_value_eur FROM risk.v_suppliers_full",
        "SELECT buyer_notes FROM risk.v_contracts_full",
        "SELECT value_usd FROM risk.v_supplier_revenue",
    ],
)
async def test_buyer_cannot_see_prices(buyer: ZetarisClient, sql: str) -> None:
    """T1: the buyer key has no grant on the full views, whatever the agent is told."""
    with pytest.raises(ZetarisError):
        await buyer.query(sql, limit=1)


# --- story 1a / 1b: the real SEC numbers the demo states (docs/data-card.md) -----------------

STORY_FACTS = [
    # (supplier, period_start, period_end, value_usd from the latest filing)
    ("S0003", "2025-04-01", "2026-03-31", 4_713_100_000),  # Microchip FY2026
    ("S0003", "2024-04-01", "2025-03-31", 4_401_600_000),  # Microchip FY2025
    ("S0003", "2026-04-01", "2026-06-30", 1_484_700_000),  # Microchip latest quarter
    ("S0003", "2025-04-01", "2025-06-30", 1_075_500_000),  # same quarter a year earlier
    ("S0005", "2025-01-01", "2025-12-31", 9_544_000_000),  # Celanese FY2025
    ("S0005", "2024-01-01", "2024-12-31", 10_268_000_000),  # Celanese FY2024 (restated)
]
# Latest filing first: highest fiscal_year, then FY > Q3 > Q2 > Q1 (docs/data-contract.md).
_PERIOD_ORDER = {"FY": 4, "Q3": 3, "Q2": 2, "Q1": 1}


def latest_filing_value(rows: list[list[str | None]]) -> int:
    """Return the value of the fact from the latest filing; all its facts must agree."""
    keyed = [(int(str(y)), _PERIOD_ORDER.get(str(p), 0), int(float(str(v)))) for y, p, v in rows]
    newest = max((year, period) for year, period, _ in keyed)
    values = {value for year, period, value in keyed if (year, period) == newest}
    assert len(values) == 1, f"latest filing {newest} has several values: {values}"
    return values.pop()


def test_latest_filing_wins_over_the_original_report() -> None:
    """Pure check of the rule; no Zetaris needed."""
    rows: list[list[str | None]] = [
        ["2024", "FY", "10280000000"],
        ["2025", "FY", "10268000000"],
        ["2025", "Q1", "1"],
    ]
    assert latest_filing_value(rows) == 10_268_000_000


@slow_live
@pytest.mark.parametrize(("supplier", "start", "end", "expected"), STORY_FACTS)
async def test_story_1_revenue_facts_match_sec(
    risk: ZetarisClient, supplier: str, start: str, end: str, expected: int
) -> None:
    """If SEC restates a figure again, this fails and the story numbers must be updated."""
    sql = (
        "SELECT fiscal_year, fiscal_period, value_usd FROM risk.v_supplier_revenue "  # noqa: S608
        f"WHERE supplier_id = '{supplier}' AND period_start = '{start}' AND period_end = '{end}'"
    )
    result = await risk.query(sql, limit=50)
    assert result.rows, f"no fact for {supplier} {start}..{end}"
    assert latest_filing_value(result.rows) == expected
