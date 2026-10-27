"""Fake Zetaris SQL endpoint serving the nine risk views from the seed-42 ERP and story facts.

ERP views are built from data_pipeline's generator (seed 42, today 2026-10-27), so the
numbers match what the real views return. SEC revenue holds the story 1a/1b facts from
docs/devlog.md (including the Celanese restatement); Eurostat series are built so the
latest-6 minus previous-6 month change equals the 2026-10-27 values in docs/data-contract.md.
"""

import asyncio
import json
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from functools import cache
from typing import Any

import httpx

from data_pipeline.synthetic_erp.generate import generate

BASE = "http://localhost:8888/api/v1.0"
RUN_PATH = "/sql-editor/sqls/run"
TODAY = date(2026, 10, 27)
# Snapshots in tool tests are taken at this instant, so "overdue" does not drift with the CI date.
SNAPSHOT_AT = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
BUYER_KEY = "zk-buyer-VALUE-5555"
RISK_KEY = "zk-secret-VALUE-1111"
BUYER_VIEWS = frozenset(
    {
        "v_suppliers_buyer",
        "v_open_orders_buyer",
        "v_part_sourcing",
        "v_deliveries",
        "v_country_indicators",
    }
)
_FROM = re.compile(r"\bFROM\s+risk\.(v_[a-z_]+)\s*$", re.IGNORECASE)

# (supplier_id, cik, fiscal_year, fiscal_period, period_start, period_end, form, value_usd)
REVENUE: list[tuple[str, str, int, str, str, str, str, float]] = [
    # Story 1a, S0003 Microchip (fiscal year ends 31 March): revenue recovers.
    ("S0003", "0000827054", 2024, "FY", "2023-04-01", "2024-03-31", "10-K", 7_634_400_000.0),
    ("S0003", "0000827054", 2025, "FY", "2024-04-01", "2025-03-31", "10-K", 4_401_600_000.0),
    ("S0003", "0000827054", 2026, "FY", "2025-04-01", "2026-03-31", "10-K", 4_713_100_000.0),
    ("S0003", "0000827054", 2026, "FY", "2024-04-01", "2025-03-31", "10-K", 4_401_600_000.0),
    ("S0003", "0000827054", 2027, "Q1", "2026-04-01", "2026-06-30", "10-Q", 1_484_700_000.0),
    ("S0003", "0000827054", 2026, "Q1", "2025-04-01", "2025-06-30", "10-Q", 1_075_500_000.0),
    # Story 1b, S0005 Celanese: FY2024 restated in the FY2025 10-K (10,268.0 M wins).
    ("S0005", "0001306830", 2023, "FY", "2023-01-01", "2023-12-31", "10-K", 10_940_000_000.0),
    ("S0005", "0001306830", 2024, "FY", "2024-01-01", "2024-12-31", "10-K", 10_280_000_000.0),
    ("S0005", "0001306830", 2025, "FY", "2024-01-01", "2024-12-31", "10-K", 10_268_000_000.0),
    ("S0005", "0001306830", 2025, "FY", "2025-01-01", "2025-12-31", "10-K", 9_544_000_000.0),
    ("S0005", "0001306830", 2026, "Q2", "2026-04-01", "2026-06-30", "10-Q", 2_400_000_000.0),
    # S0001: flat revenue (control).
    ("S0001", "0000097476", 2024, "FY", "2024-01-01", "2024-12-31", "10-K", 15_641_000_000.0),
    ("S0001", "0000097476", 2025, "FY", "2025-01-01", "2025-12-31", "10-K", 15_700_000_000.0),
]

# Latest-6 minus previous-6 month change (pp), docs/data-contract.md "Worsening country".
UNEMPLOYMENT_CHANGE = {"FR": 0.25, "PL": 0.20, "IT": 0.12, "DE": 0.08, "CZ": 0.02, "ES": -0.12}
HICP_CHANGE = {"IT": 1.93, "FR": 1.65, "ES": 1.08, "PL": 0.68, "DE": 0.55, "CZ": 0.08}


def _months(last: str, count: int) -> list[str]:
    """Return `count` YYYY-MM periods ending at `last`, oldest first."""
    year, month = (int(part) for part in last.split("-"))
    periods = []
    for _ in range(count):
        periods.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return periods[::-1]


def _series(country: str, indicator: str, last: str, base: float, change: float) -> list[list[Any]]:
    """Return 18 months: 6 older, 6 at `base`, then 6 at `base + change`."""
    periods = _months(last, 18)
    values = [base] * 12 + [round(base + change, 2)] * 6
    return [
        [country, indicator, period, f"{value:.2f}"]
        for period, value in zip(periods, values, strict=True)
    ]


def _fmt(value: Any) -> str | None:  # noqa: ANN401 - generator cells are str/int/float/bool/date
    """Format one generator cell the way Zetaris returns it (strings; NULL as None)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


@cache
def view_rows() -> dict[str, list[list[str | None]]]:
    """Return the rows of every risk view, in the registry's column order."""
    data = generate(today=TODAY)
    t = data.tables
    parts = {p["part_id"]: p for p in t["parts"]}
    contracts = {c["supplier_id"]: c for c in t["contracts"]}
    orders = {o["po_id"]: o for o in t["purchase_orders"]}
    full, buyer = [], []
    for s in t["suppliers"]:
        c = contracts.get(s["supplier_id"], {})
        head = [s["supplier_id"], s["name"], s["cik"], s["country_iso"], s["category"]]
        site = [s["site_lat"], s["site_lon"]]
        full.append(head + site + [c.get("annual_value_eur"), c.get("end_date")])
        buyer.append(head + site + [c.get("end_date")])
    open_full, open_buyer = [], []
    for o in t["purchase_orders"]:
        if o["status"] != "open":
            continue
        p = parts[o["part_id"]]
        head = [o["po_id"], o["supplier_id"], o["part_id"], p["description"], p["criticality"]]
        value = round(o["qty"] * o["unit_price_eur"], 2)
        open_full.append(
            head + [o["category"], o["qty"], o["unit_price_eur"], value, o["due_date"]]
        )
        open_buyer.append(head + [o["category"], o["qty"], o["due_date"]])
    sourcing = [
        [
            s["part_id"],
            s["supplier_id"],
            parts[s["part_id"]]["description"],
            parts[s["part_id"]]["category"],
            parts[s["part_id"]]["criticality"],
            s["share_pct"],
            s["is_single_source"],
        ]
        for s in t["part_sourcing"]
    ]
    deliveries = []
    for d in t["deliveries"]:
        o = orders[d["po_id"]]
        late = (d["delivered_date"] - d["promised_date"]).days if d["delivered_date"] else None
        deliveries.append(
            [d["po_id"], o["supplier_id"], o["part_id"], o["category"], o["status"]]
            + [d["promised_date"], d["delivered_date"], late]
        )
    contract_rows = [
        [c["supplier_id"], c["annual_value_eur"], c["end_date"], c["buyer_notes"]]
        for c in t["contracts"]
    ]
    countries = []
    for country, change in UNEMPLOYMENT_CHANGE.items():
        countries += _series(country, "unemployment_rate", "2026-08", 7.0, change)
    for country, change in HICP_CHANGE.items():
        countries += _series(country, "hicp_annual_rate", "2026-09", 2.0, change)
    raw = {
        "v_suppliers_full": full,
        "v_suppliers_buyer": buyer,
        "v_open_orders_full": open_full,
        "v_open_orders_buyer": open_buyer,
        "v_part_sourcing": sourcing,
        "v_deliveries": deliveries,
        "v_contracts_full": contract_rows,
        "v_supplier_revenue": [list(row) for row in REVENUE],
        "v_country_indicators": countries,
    }
    return {view: [[_fmt(cell) for cell in row] for row in rows] for view, rows in raw.items()}


@dataclass
class FakeZetaris:
    """Callable respx side effect that answers fixed-view SQL like Zetaris.

    Attributes:
        calls: Number of requests per (key name, view).
        sql_seen: Every SQL text received, in order.
        delay: Seconds each request takes (asyncio.sleep).
        fail_views: Views that answer HTTP 503.
        in_flight: Requests running right now; max_in_flight is the peak.
        overrides: Replacement rows per view.
    """

    calls: Counter[tuple[str, str]] = field(default_factory=Counter)
    sql_seen: list[str] = field(default_factory=list)
    order: list[tuple[str, str]] = field(default_factory=list)
    delay: float = 0.0
    fail_views: set[str] = field(default_factory=set)
    in_flight: int = 0
    max_in_flight: int = 0
    overrides: dict[str, list[list[str | None]]] = field(default_factory=dict)
    on_request: Callable[[str, str], None] | None = None

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        """Answer one /sql-editor/sqls/run request."""
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        key = {BUYER_KEY: "buyer", RISK_KEY: "risk"}.get(token)
        if key is None:
            return httpx.Response(401)
        body = json.loads(request.content)
        sql = body["sql"]
        self.sql_seen.append(sql)
        match = _FROM.search(sql)
        if match is None:
            return httpx.Response(400, json={"error": "unknown sql"})
        view = match.group(1)
        self.calls[(key, view)] += 1
        self.order.append((key, view))
        if self.on_request:
            self.on_request(key, view)
        if key == "buyer" and view not in BUYER_VIEWS:
            return httpx.Response(403, json={"error": "does not have privileges"})
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
        finally:
            self.in_flight -= 1
        if view in self.fail_views:
            return httpx.Response(503)
        rows = self.overrides.get(view, view_rows()[view])
        return httpx.Response(200, json={"data": rows[: body["limit"]]})


async def no_sleep(_: float) -> None:
    """Replace the Zetaris client's retry sleep in tests."""
    return None
