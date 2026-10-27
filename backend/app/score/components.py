"""Sub-score functions (0-100, higher is riskier), one per component of weights v1.

Each function takes already-filtered numbers plus the evidence ids they came from and
returns a ComponentScore with a one-line reason. Scales are fixed constants (architecture
6.5); unknown inputs make a component "not assessed", never 0.
"""

from decimal import ROUND_HALF_UP, Decimal

from app.score.model import ComponentScore
from app.score.weights import WEIGHTS, ComponentName

# Delivery: on-time target 95 %; 50 % or worse gives the full level part (80 points);
# a drop of 20 points against the previous 6 months gives the full trend part (20 points).
ON_TIME_TARGET = 0.95
ON_TIME_FLOOR = 0.50
DELIVERY_LEVEL_POINTS = 80.0
DELIVERY_TREND_FULL_DROP = 0.20
DELIVERY_TREND_POINTS = 20.0
MIN_DELIVERIES = 10
# Concentration: points per single-source part by criticality (capped), plus up to 30 points
# for the share of open orders that sit on single-source parts.
SINGLE_SOURCE_POINTS = {"A": 50.0, "B": 25.0, "C": 10.0}
SINGLE_SOURCE_CAP = 70.0
OPEN_ORDER_SHARE_POINTS = 30.0
# Financial: a 20 % fall in yearly revenue gives 100.
REVENUE_FULL_DECLINE = 0.20
# Country: +0.5 pp unemployment or +2.0 pp HICP (latest 6 vs previous 6 months) gives 100.
UNEMPLOYMENT_FULL_RISE_PP = 0.5
HICP_FULL_RISE_PP = 2.0


def _clamp(x: float) -> float:
    """Return x limited to 0..1."""
    return min(max(x, 0.0), 1.0)


def round_half_up(x: float) -> float:
    """Round half up to one decimal (Python's round() is banker's rounding)."""
    return float(Decimal(str(x)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def _pct(x: float) -> str:
    """Return a rate 0..1 as a whole percentage, e.g. "37 %"."""
    return f"{round_half_up(x * 100):.0f} %"


def _assessed(
    name: ComponentName, value: float, evidence_ids: list[str], reason: str
) -> ComponentScore:
    """Return an assessed component with the value rounded to one decimal."""
    return ComponentScore(
        name=name,
        weight=WEIGHTS[name],
        assessed=True,
        value=round_half_up(min(max(value, 0.0), 100.0)),
        evidence_ids=list(evidence_ids),
        reason=reason,
    )


def not_assessed(name: ComponentName, reason: str, evidence_ids: list[str]) -> ComponentScore:
    """Return a component that could not be assessed (excluded from total and coverage).

    Args:
        name: Component name.
        reason: Why it could not be assessed (shown to the user).
        evidence_ids: Evidence that shows the gap, if any.

    Returns:
        The component with assessed=False and no value.
    """
    return ComponentScore(
        name=name,
        weight=WEIGHTS[name],
        assessed=False,
        value=None,
        evidence_ids=list(evidence_ids),
        reason=reason,
    )


def delivery(
    on_time_last: float,
    count_last: int,
    on_time_prev: float | None,
    evidence_ids: list[str],
    overdue_last: int = 0,
) -> ComponentScore:
    """Score the on-time rate of the last 6 months and its trend.

    Score rules v2 (decision 0005): an open order past its due date without a delivery counts
    as a late delivery, so a supplier that stopped delivering is assessed, never hidden.

    Args:
        on_time_last: On-time share of deliveries promised in the last 6 months (0..1),
            overdue undelivered orders counted as late.
        count_last: Number of those deliveries, overdue undelivered orders included.
        on_time_prev: On-time share of the 6 months before, None if too few deliveries.
        evidence_ids: Delivery evidence.
        overdue_last: Overdue undelivered orders among count_last.

    Returns:
        The delivery component; not assessed below MIN_DELIVERIES deliveries unless an order
        is overdue.
    """
    if count_last < MIN_DELIVERIES and not overdue_last:
        return not_assessed(
            "delivery", f"only {count_last} deliveries in the last 6 months", evidence_ids
        )
    level = _clamp((ON_TIME_TARGET - on_time_last) / (ON_TIME_TARGET - ON_TIME_FLOOR))
    value = level * DELIVERY_LEVEL_POINTS
    overdue = f", {overdue_last} overdue undelivered" if overdue_last else ""
    reason = (
        f"On-time rate {_pct(on_time_last)} over the last 6 months "
        f"({count_last} deliveries{overdue})"
    )
    if on_time_prev is not None:
        value += _clamp((on_time_prev - on_time_last) / DELIVERY_TREND_FULL_DROP) * (
            DELIVERY_TREND_POINTS
        )
        reason += f", {_pct(on_time_prev)} in the 6 months before"
    return _assessed("delivery", value, evidence_ids, reason + ".")


def concentration(
    single_source: dict[str, int],
    open_orders: int,
    open_orders_single_source: int,
    evidence_ids: list[str],
) -> ComponentScore:
    """Score single-sourced parts with this supplier and open orders on them.

    Args:
        single_source: Count of parts single-sourced with the supplier, by criticality A/B/C.
        open_orders: The supplier's open orders.
        open_orders_single_source: Those of them for single-source parts.
        evidence_ids: Sourcing and open-order evidence.

    Returns:
        The concentration component.
    """
    parts = sum(SINGLE_SOURCE_POINTS[k] * single_source.get(k, 0) for k in SINGLE_SOURCE_POINTS)
    share = open_orders_single_source / open_orders if open_orders else 0.0
    value = min(parts, SINGLE_SOURCE_CAP) + share * OPEN_ORDER_SHARE_POINTS
    counts = ", ".join(f"{single_source.get(k, 0)} {k}" for k in SINGLE_SOURCE_POINTS)
    reason = (
        f"Single-source parts with this supplier: {counts}; "
        f"{open_orders_single_source} of {open_orders} open orders on single-source parts."
    )
    return _assessed("concentration", value, evidence_ids, reason)


def financial(
    latest: float, previous: float, latest_period: str, evidence_ids: list[str]
) -> ComponentScore:
    """Score the year-on-year change of revenue (latest filing for each period).

    Args:
        latest: Revenue of the latest full fiscal year (USD).
        previous: Revenue of the fiscal year before (USD).
        latest_period: Period of the latest year, "start..end".
        evidence_ids: Revenue evidence.

    Returns:
        The financial component (growth scores 0).
    """
    if previous <= 0:
        return not_assessed("financial", "previous year revenue is not positive", evidence_ids)
    change = (latest - previous) / previous
    value = _clamp(-change / REVENUE_FULL_DECLINE) * 100
    reason = (
        f"Revenue {latest / 1e6:,.1f} M USD ({latest_period}) vs {previous / 1e6:,.1f} M "
        f"the year before ({round_half_up(change * 100):+.1f} %)."
    )
    return _assessed("financial", value, evidence_ids, reason)


def country(
    country_iso: str,
    unemployment_change: float | None,
    hicp_change: float | None,
    evidence_ids: list[str],
) -> ComponentScore:
    """Score the change of Eurostat indicators in the site country.

    Args:
        country_iso: Site country.
        unemployment_change: Latest 6 minus previous 6 months average, percentage points.
        hicp_change: The same for the HICP annual rate, percentage points.
        evidence_ids: Country indicator evidence.

    Returns:
        The country component (not assessed without any indicator).
    """
    parts: list[float] = []
    notes: list[str] = []
    if unemployment_change is not None:
        parts.append(_clamp(unemployment_change / UNEMPLOYMENT_FULL_RISE_PP) * 100)
        notes.append(f"unemployment {unemployment_change:+.2f} pp")
    if hicp_change is not None:
        parts.append(_clamp(hicp_change / HICP_FULL_RISE_PP) * 100)
        notes.append(f"HICP {hicp_change:+.2f} pp")
    if not parts:
        return not_assessed("country", f"no Eurostat indicators for {country_iso}", evidence_ids)
    reason = f"{country_iso}, latest 6 vs previous 6 months: " + ", ".join(notes) + "."
    return _assessed("country", sum(parts) / len(parts), evidence_ids, reason)
