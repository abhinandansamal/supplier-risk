"""Collect the signals of one supplier from snapshots and compute its deterministic risk score.

The arithmetic lives in app/score (pure); this module only filters snapshot rows, records
evidence and decides when a component cannot be assessed (weather not onboarded, private
supplier, financials hidden from the buyer role, an external source unavailable).
"""

from collections.abc import Callable, Sequence
from datetime import date, timedelta
from statistics import fmean

from app.score import components
from app.score.model import ComponentScore, RiskScore
from app.score.scorer import combine
from app.tools.cache import Row
from app.tools.context import ToolContext, read, record, record_gap, scoped_rows, supplier
from app.tools.errors import SourceUnavailable
from app.tools.rows import full_years, latest_filing_facts, month_start, to_bool, to_date, to_float

TOOL = "compute_risk_score"
SCORE_MONTHS = 6
WEATHER_GAP = "weather not assessed: NOAA data is not onboarded"


def is_late(row: Row, as_of: date | None) -> bool | None:
    """Return whether a delivery row counts as late, or None if it is not counted yet.

    Score rules v2 (decision 0005, SEC-067): a delivered row is late when days_late > 0; an
    open order whose promised date is before `as_of` (the snapshot date) is late too; an open
    order not yet due is not counted.

    Args:
        row: A v_deliveries row.
        as_of: Snapshot date; None counts delivered rows only.

    Returns:
        True (late), False (on time), or None (not counted).
    """
    if row["delivered_date"]:
        return (to_float(row["days_late"]) or 0.0) > 0
    promised = to_date(row["promised_date"])
    if as_of is not None and promised is not None and promised < as_of:
        return True
    return None


def counted_between(rows: list[Row], start: date, end: date, as_of: date) -> list[Row]:
    """Return rows promised in [start, end] that count (delivered, or overdue as of as_of)."""
    found = []
    for row in rows:
        promised = to_date(row["promised_date"])
        if promised and start <= promised <= end and is_late(row, as_of) is not None:
            found.append(row)
    return found


def on_time_rate(rows: list[Row], as_of: date | None = None) -> float | None:
    """Return the on-time share of the counted rows, None if none counts."""
    verdicts = [v for v in (is_late(r, as_of) for r in rows) if v is not None]
    if not verdicts:
        return None
    return verdicts.count(False) / len(verdicts)


def overdue_count(rows: list[Row]) -> int:
    """Return how many counted rows are overdue orders without a delivery."""
    return sum(1 for r in rows if not r["delivered_date"])


def six_month_change(values: list[float]) -> float | None:
    """Return the mean of the last 6 values minus the mean of the 6 before, None if < 12."""
    if len(values) < 12:
        return None
    return fmean(values[-6:]) - fmean(values[-12:-6])


def indicator_series(rows: Sequence[Row], country_iso: str, indicator: str) -> list[Row]:
    """Return one country's indicator rows sorted by period, NULL values dropped."""
    found = [
        r
        for r in rows
        if r["country_iso"] == country_iso and r["indicator"] == indicator and r["value"]
    ]
    return sorted(found, key=lambda r: r["period"] or "")


async def _delivery(ctx: ToolContext, supplier_id: str) -> ComponentScore:
    """Score on-time delivery: last 6 calendar months vs the 6 before."""
    snapshot = await read(ctx, "v_deliveries")
    rows = scoped_rows(ctx, snapshot, supplier_id)
    last_start = month_start(ctx.today, SCORE_MONTHS - 1)
    prev_start = month_start(ctx.today, 2 * SCORE_MONTHS - 1)
    as_of = snapshot.snapshot_at.date()
    last = counted_between(rows, last_start, ctx.today, as_of)
    prev = counted_between(rows, prev_start, last_start - timedelta(days=1), as_of)
    params = {
        "supplier_id": supplier_id,
        "from": prev_start.isoformat(),
        "to": ctx.today.isoformat(),
        "overdue_as_of": as_of.isoformat(),
    }
    evidence = [record(ctx, TOOL, snapshot, params, prev + last)]
    prev_rate = on_time_rate(prev, as_of) if len(prev) >= components.MIN_DELIVERIES else None
    return components.delivery(
        on_time_rate(last, as_of) or 0.0, len(last), prev_rate, evidence, overdue_count(last)
    )


async def _concentration(ctx: ToolContext, supplier_id: str) -> ComponentScore:
    """Score single-source parts with this supplier and its open orders on them."""
    sourcing = await read(ctx, "v_part_sourcing")
    single = [r for r in scoped_rows(ctx, sourcing, supplier_id) if to_bool(r["is_single_source"])]
    orders_view = "v_open_orders_full" if ctx.key == "risk" else "v_open_orders_buyer"
    orders_snapshot = await read(ctx, orders_view)
    orders = scoped_rows(ctx, orders_snapshot, supplier_id)
    single_parts = {r["part_id"] for r in single}
    counts = {k: sum(1 for r in single if r["criticality"] == k) for k in ("A", "B", "C")}
    params = {"supplier_id": supplier_id}
    evidence = [
        record(ctx, TOOL, sourcing, params, single),
        record(ctx, TOOL, orders_snapshot, params, orders),
    ]
    on_single = sum(1 for r in orders if r["part_id"] in single_parts)
    return components.concentration(counts, len(orders), on_single, evidence)


async def _financial(
    ctx: ToolContext, supplier_row: Row, supplier_evidence: Callable[[], str]
) -> ComponentScore:
    """Score the latest year-on-year revenue change, or say why it is not assessed."""
    supplier_id = supplier_row["supplier_id"] or ""
    params = {"supplier_id": supplier_id}
    if ctx.key != "risk":
        reason = "financials not assessed: hidden from the buyer role"
        return components.not_assessed(
            "financial", reason, [record_gap(ctx, TOOL, "edgar", params, reason)]
        )
    if not supplier_row["cik"]:
        reason = "financials unknown: private supplier, no SEC filings"
        return components.not_assessed("financial", reason, [supplier_evidence()])
    try:
        snapshot = await read(ctx, "v_supplier_revenue")
    except SourceUnavailable:
        reason = "financials not assessed: SEC source unavailable"
        return components.not_assessed(
            "financial", reason, [record_gap(ctx, TOOL, "edgar", params, reason)]
        )
    years = full_years(latest_filing_facts(scoped_rows(ctx, snapshot, supplier_id)))
    if len(years) < 2:
        reason = "financials not assessed: fewer than two full fiscal years filed"
        return components.not_assessed(
            "financial", reason, [record(ctx, TOOL, snapshot, params, years, note=reason)]
        )
    prev, last = years[-2], years[-1]
    evidence = [record(ctx, TOOL, snapshot, params, [prev, last])]
    latest, previous = to_float(last["value_usd"]), to_float(prev["value_usd"])
    if latest is None or previous is None:
        # Unknown is never 0 (SEC-062): a NULL revenue must not read as a collapse.
        reason = "financials not assessed: revenue value missing in the latest filing"
        return components.not_assessed("financial", reason, evidence)
    period = f"{last['period_start']}..{last['period_end']}"
    return components.financial(latest, previous, period, evidence)


async def _country(ctx: ToolContext, country_iso: str) -> ComponentScore:
    """Score the 6-month change of unemployment and HICP in the site country."""
    params = {"country_iso": country_iso}
    try:
        snapshot = await read(ctx, "v_country_indicators")
    except SourceUnavailable:
        reason = "country not assessed: Eurostat source unavailable"
        return components.not_assessed(
            "country", reason, [record_gap(ctx, TOOL, "eurostat", params, reason)]
        )
    unemployment = indicator_series(snapshot.rows, country_iso, "unemployment_rate")[-12:]
    hicp = indicator_series(snapshot.rows, country_iso, "hicp_annual_rate")[-12:]
    evidence = [record(ctx, TOOL, snapshot, params, unemployment + hicp)]
    return components.country(
        country_iso,
        six_month_change([to_float(r["value"]) or 0.0 for r in unemployment]),
        six_month_change([to_float(r["value"]) or 0.0 for r in hicp]),
        evidence,
    )


async def compute_score(ctx: ToolContext, supplier_id: str) -> RiskScore:
    """Return the deterministic risk score of one supplier the session may see.

    Args:
        ctx: Tool context (session scope, cache, evidence log).
        supplier_id: Validated supplier id.

    Returns:
        The score; every component cites evidence ids from ctx.evidence.

    Raises:
        SupplierNotFound: If the supplier is unknown or outside the session's scope.
        SourceUnavailable: If an ERP view cannot be served (SEC/Eurostat only lower coverage).
    """
    row, suppliers = await supplier(ctx, supplier_id)

    def supplier_evidence() -> str:
        # Recorded only when cited, so every score evidence record is cited (SEC-065).
        return record(ctx, TOOL, suppliers, {"supplier_id": supplier_id}, [row])

    weather_gap = record_gap(ctx, TOOL, "noaa", {"supplier_id": supplier_id}, WEATHER_GAP)
    return combine(
        supplier_id,
        [
            await _delivery(ctx, supplier_id),
            await _concentration(ctx, supplier_id),
            await _financial(ctx, row, supplier_evidence),
            await _country(ctx, row["country_iso"] or ""),
            components.not_assessed("weather", WEATHER_GAP, [weather_gap]),
        ],
    )
