"""Typed readers for snapshot cells (Zetaris returns every cell as text) and row helpers."""

from datetime import date

from app.tools.cache import Row

# Fiscal period rank: a later filing of the same fiscal year is FY > Q3 > Q2 > Q1.
FISCAL_PERIOD_RANK = {"FY": 4, "Q3": 3, "Q2": 2, "Q1": 1}
FULL_YEAR_DAYS = (350, 380)


def to_date(text: str | None) -> date | None:
    """Return an ISO date (YYYY-MM-DD, extra time part ignored), or None."""
    return date.fromisoformat(text[:10]) if text else None


def to_float(text: str | None) -> float | None:
    """Return a number, or None for NULL."""
    return float(text) if text not in (None, "") else None


def to_int(text: str | None) -> int | None:
    """Return an integer (Zetaris may send "3" or "3.0"), or None for NULL."""
    value = to_float(text)
    return int(value) if value is not None else None


def to_bool(text: str | None) -> bool:
    """Return True for "true", "t" or "1" (any case)."""
    return (text or "").strip().lower() in {"true", "t", "1"}


def month_start(day: date, back: int = 0) -> date:
    """Return the first day of the month `back` calendar months before `day`'s month."""
    index = day.year * 12 + day.month - 1 - back
    return date(index // 12, index % 12 + 1, 1)


def latest_filing_facts(rows: list[Row]) -> list[Row]:
    """Return one revenue fact per period, taken from the latest filing (data contract).

    For each (period_start, period_end), the fact with the highest fiscal_year wins, then
    FY > Q3 > Q2 > Q1. A later filing that restates a period therefore replaces the original.

    Args:
        rows: v_supplier_revenue rows of one supplier.

    Returns:
        One row per period, sorted by period_end.
    """
    best: dict[tuple[str | None, str | None], Row] = {}
    for row in rows:
        period = (row["period_start"], row["period_end"])
        rank = (
            to_int(row["fiscal_year"]) or 0,
            FISCAL_PERIOD_RANK.get(row["fiscal_period"] or "", 0),
        )
        current = best.get(period)
        if current is None or rank > (
            to_int(current["fiscal_year"]) or 0,
            FISCAL_PERIOD_RANK.get(current["fiscal_period"] or "", 0),
        ):
            best[period] = row
    return sorted(best.values(), key=lambda row: row["period_end"] or "")


def full_years(facts: list[Row]) -> list[Row]:
    """Return consecutive full fiscal years (about 365 days each), oldest first.

    Args:
        facts: Latest-filing facts sorted by period_end.

    Returns:
        The longest run of full years ending with the latest one, each ending about one
        year after the one before.
    """
    years = []
    for row in facts:
        start, end = to_date(row["period_start"]), to_date(row["period_end"])
        if start and end and FULL_YEAR_DAYS[0] <= (end - start).days <= FULL_YEAR_DAYS[1]:
            years.append(row)
    run: list[Row] = []
    for row in reversed(years):
        if run:
            gap = (to_date(run[0]["period_end"]) - to_date(row["period_end"])).days  # type: ignore[operator]
            if not FULL_YEAR_DAYS[0] <= gap <= FULL_YEAR_DAYS[1]:
                break
        run.insert(0, row)
    return run
