"""Print row counts and date ranges of every risk view in Zetaris (read-only).

Usage:
    uv run python scripts/zetaris_verify.py

Uses ZETARIS_KEY_RISK (the svc_risk user, docs/zetaris-setup.md), never the admin key. Sends
one fixed COUNT/MIN/MAX query per view in container risk and prints a Markdown table for
docs/data-contract.md. Exit codes: 0 ok, 1 a view failed, 2 skipped (key missing or no
access yet). Never prints the key or Zetaris response bodies.
"""

import argparse
import asyncio
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# The backend and data_pipeline are not installed packages; make them importable as a script.
_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT / "backend"), str(_ROOT)]

from app.core.settings import Settings, SettingsError, load_settings  # noqa: E402
from app.zetaris.client import ZetarisClient  # noqa: E402
from app.zetaris.errors import ZetarisError, ZetarisUnauthorized  # noqa: E402
from data_pipeline.zetaris_sql.generate import RISK_CONTAINER, RISK_VIEWS, RiskView  # noqa: E402

EXIT_OK, EXIT_FAILED, EXIT_SKIPPED = 0, 1, 2
VIEW_CHECKS: tuple[RiskView, ...] = RISK_VIEWS
SETUP_HINT = "see docs/zetaris-setup.md"
# risk.v_country_indicators took 3.5 minutes on 2026-10-27 (live Eurostat, not cached yet).
VERIFY_TIMEOUT_SECONDS = 300.0
_IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]{0,62}")


@dataclass(frozen=True)
class ViewStats:
    """Row count and date range of one risk view."""

    view: str
    rows: int
    min_date: str | None
    max_date: str | None


def build_sql(view: str, date_column: str | None) -> str:
    """Return the fixed verification query for one view.

    Args:
        view: View name in container risk.
        date_column: Column whose range is reported, or None.

    Returns:
        One SELECT with a count (and MIN/MAX of the date column).

    Raises:
        ValueError: If a name is not a plain lowercase identifier.
    """
    for name in (view, date_column):
        if name is not None and not _IDENTIFIER.fullmatch(name):
            raise ValueError("view and column must be plain identifiers")
    columns = "COUNT(1) AS row_count"
    if date_column:
        columns += f", MIN({date_column}) AS min_date, MAX({date_column}) AS max_date"
    return f"SELECT {columns} FROM {RISK_CONTAINER}.{view}"  # noqa: S608 - checked identifiers


async def collect(client: ZetarisClient) -> tuple[list[ViewStats], list[str]]:
    """Return the stats of every risk view (VIEW_CHECKS order) and one line per failed view.

    A failing view does not stop the others, so one slow live source does not hide the rest.

    Raises:
        ZetarisUnauthorized: If the key has no access (the setup is not done yet).
    """
    stats: list[ViewStats] = []
    failures: list[str] = []
    for check in VIEW_CHECKS:
        try:
            result = await client.query(build_sql(check.name, check.date_column), limit=1)
        except ZetarisUnauthorized:
            raise
        except ZetarisError as exc:
            # Our errors carry no response body or key, so the message is safe to print.
            failures.append(f"FAIL {RISK_CONTAINER}.{check.name}: {type(exc).__name__}: {exc}")
            continue
        row = result.rows[0] if result.rows else ["0"]
        min_date = row[1] if len(row) > 1 else None
        max_date = row[2] if len(row) > 2 else None
        stats.append(ViewStats(check.name, int(row[0] or 0), min_date, max_date))
    return stats, failures


def format_table(stats: list[ViewStats]) -> str:
    """Return the stats as a Markdown table."""
    lines = ["| View | Rows | From | To |", "| --- | --- | --- | --- |"]
    lines.extend(
        f"| {RISK_CONTAINER}.{s.view} | {s.rows} | {s.min_date or '-'} | {s.max_date or '-'} |"
        for s in stats
    )
    return "\n".join(lines)


async def main(settings: Settings | None = None) -> int:
    """Run the verification and print the table (or a SKIP/FAIL line).

    Args:
        settings: Settings to use; loaded from the environment and .env when None.

    Returns:
        EXIT_OK, EXIT_FAILED or EXIT_SKIPPED.
    """
    if settings is None:
        try:
            settings = load_settings()
        except SettingsError as exc:
            print(f"FAIL settings: {exc}")
            return EXIT_FAILED
    if settings.zetaris_key_risk is None:
        print(f"SKIP ZETARIS_KEY_RISK is not set ({SETUP_HINT})")
        return EXIT_SKIPPED
    try:
        async with ZetarisClient(
            settings, "risk_manager", query_timeout=VERIFY_TIMEOUT_SECONDS
        ) as client:
            stats, failures = await collect(client)
    except ZetarisUnauthorized:
        print(f"SKIP ZETARIS_KEY_RISK has no access to the risk views yet ({SETUP_HINT})")
        return EXIT_SKIPPED
    except (ZetarisError, ValueError) as exc:
        print(f"FAIL {type(exc).__name__}: {exc}")
        return EXIT_FAILED
    print(format_table(stats))
    for line in failures:
        print(line)
    return EXIT_FAILED if failures else EXIT_OK


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the command line before any work, so --help only prints usage.

    Args:
        argv: Command line arguments, or None to use sys.argv.

    Returns:
        The parsed arguments (this script takes none).
    """
    parser = argparse.ArgumentParser(
        description=(__doc__ or "").strip(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    parse_args()
    sys.exit(asyncio.run(main()))
