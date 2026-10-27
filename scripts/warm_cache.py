"""Warm the Zetaris snapshot cache once and report each view (setup and demo check).

Usage: uv run python scripts/warm_cache.py

Loads every view granted to the buyer and risk role keys, ERP views first, at most
SNAPSHOT_MAX_CONCURRENCY at a time, with ZETARIS_SNAPSHOT_TIMEOUT_SECONDS per load. Prints one
line per view: ok, failed or skipped. Never prints keys or Zetaris response bodies. The cache
lives in memory, so this checks that the app's own startup warm-up can succeed; it does not
fill the running app's cache.

Exit codes: 0 all loaded, 1 a view failed, 2 no role key is configured.
"""

import argparse
import asyncio
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx

# The backend is not an installed package; make it importable when run as a script.
_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT / "backend"), str(_ROOT)]

from app.core.logging import setup_logging  # noqa: E402
from app.core.settings import Settings, SettingsError, load_settings  # noqa: E402
from app.tools.cache import SnapshotCache  # noqa: E402


async def main(
    settings: Settings | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> int:
    """Warm all views and print the result.

    Args:
        settings: Settings; loaded from .env when omitted.
        transport: Optional httpx transport (tests).
        sleep: Async sleep before a Zetaris retry (tests).

    Returns:
        The exit code.
    """
    if settings is None:
        try:
            settings = load_settings()
        except SettingsError as exc:
            print(f"FAIL settings: {exc}")
            return 1
        setup_logging(settings)
    if settings.zetaris_key_buyer is None and settings.zetaris_key_risk is None:
        print("SKIP no Zetaris role key is set (see docs/zetaris-setup.md)")
        return 2
    cache = SnapshotCache(settings, transport=transport, sleep=sleep)
    try:
        report = await cache.warm_all()
    finally:
        await cache.aclose()
    for label, status in report.items():
        print(f"{status} {label}")
    return 1 if "failed" in report.values() else 0


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
