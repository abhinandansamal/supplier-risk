"""Verify the SEC CIKs and revenue tags of the public suppliers in data_pipeline/suppliers.yaml.

Usage (the declared User-Agent is ZETARIS_USER_AGENT from the settings, i.e. .env):
    uv run python scripts/verify_ciks.py lookup TXN ADI
    uv run python scripts/verify_ciks.py verify

`lookup` maps tickers to CIKs through the SEC ticker file. `verify` checks every supplier with a
CIK: the submissions endpoint must answer, and of the candidate us-gaap revenue tags the one
with the latest period end must be the tag in suppliers.yaml and must be fresh (filers move
revenue to another tag over time, and the old tag silently stops; SEC-036). It prints Markdown
rows for docs/data-contract.md. At most 2 requests/s.
"""

import argparse
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx
import yaml

# The backend is not an installed package; make `app` importable when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.settings import SettingsError, load_settings  # noqa: E402

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
CONCEPT_URL = "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/us-gaap/{tag}.json"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
REVENUE_TAGS = (
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
)
# Quarterly filers report within about 45 days of a quarter end; 9 months also covers a
# late annual report. Older data means the filer moved to another tag (or stopped filing).
MAX_AGE_DAYS = 275
MIN_INTERVAL_SECONDS = 0.5  # 2 requests per second
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
SUPPLIERS_YAML = Path(__file__).resolve().parents[1] / "data_pipeline" / "suppliers.yaml"
TIMEOUT_SECONDS = 20


@dataclass(frozen=True)
class CikCheck:
    """Outcome of verifying one CIK.

    Attributes:
        cik: 10-digit CIK.
        entity_name: Name from the submissions endpoint, None if the CIK is unknown.
        revenue_tag: Candidate tag with the latest period end, None if no tag has data.
        ok: True if that tag is fresh and (when given) equals the expected tag.
        latest_end: Latest period end (YYYY-MM-DD) of revenue_tag.
        tag_ends: Latest period end per candidate tag (None = missing or no USD facts).
        reason: Why the check failed, empty when ok.
    """

    cik: str
    entity_name: str | None
    revenue_tag: str | None
    ok: bool
    latest_end: str | None = None
    tag_ends: dict[str, str | None] = field(default_factory=dict)
    reason: str = ""


class _Throttle:
    """Wait so that consecutive calls are at least `interval` seconds apart."""

    def __init__(self, interval: float) -> None:
        self._interval = interval
        self._last = 0.0

    def wait(self) -> None:
        delay = self._last + self._interval - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self._last = time.monotonic()


def pad_cik(cik: int | str) -> str:
    """Return the CIK as a 10-digit, zero-padded string; raise ValueError if malformed."""
    text = str(cik)
    if not re.fullmatch(r"[0-9]{1,10}", text):
        raise ValueError("CIK must be 1 to 10 digits")
    return text.zfill(10)


def lookup_ticker(client: httpx.Client, ticker: str) -> tuple[int, str] | None:
    """Return (cik, company title) for a ticker from the SEC ticker file, or None."""
    response = client.get(TICKERS_URL)
    response.raise_for_status()
    wanted = ticker.upper()
    for entry in response.json().values():
        if entry.get("ticker", "").upper() == wanted:
            return int(entry["cik_str"]), str(entry["title"])
    return None


def _latest_end(response: httpx.Response) -> str | None:
    """Return the latest USD fact end date (YYYY-MM-DD) of a concept response, or None."""
    if response.status_code != httpx.codes.OK:
        return None
    try:
        facts = response.json().get("units", {}).get("USD", [])
    except (ValueError, AttributeError):
        return None
    ends = [
        f["end"]
        for f in facts
        if isinstance(f, dict) and isinstance(f.get("end"), str) and _DATE.fullmatch(f["end"])
    ]
    return max(ends, default=None)


def _verdict(tag: str | None, latest: str | None, expected_tag: str | None, today: date) -> str:
    """Return why the check fails, or an empty string when it passes."""
    if tag is None or latest is None:
        return "no revenue tag with USD facts"
    if expected_tag is not None and tag != expected_tag:
        return f"suppliers.yaml has {expected_tag}"
    if date.fromisoformat(latest) < today - timedelta(days=MAX_AGE_DAYS):
        return f"stale: latest period ends {latest}"
    return ""


def verify_cik(
    client: httpx.Client,
    cik: int | str,
    min_interval: float = MIN_INTERVAL_SECONDS,
    *,
    expected_tag: str | None = None,
    today: date | None = None,
) -> CikCheck:
    """Check that the CIK exists and find its freshest revenue tag.

    Args:
        client: HTTP client that sends the declared User-Agent.
        cik: CIK in any padding.
        min_interval: Minimum seconds between requests.
        expected_tag: Tag from suppliers.yaml; the check fails if another tag is fresher.
        today: Reference date for freshness (default: today).

    Returns:
        The check, with the latest period end of every candidate tag.
    """
    padded = pad_cik(cik)
    throttle = _Throttle(min_interval)
    throttle.wait()
    response = client.get(SUBMISSIONS_URL.format(cik=padded))
    if response.status_code != httpx.codes.OK:
        return CikCheck(padded, None, None, ok=False, reason="unknown CIK")
    name = str(response.json().get("name", ""))
    tag_ends: dict[str, str | None] = {}
    for candidate in REVENUE_TAGS:
        throttle.wait()
        url = CONCEPT_URL.format(cik=padded, tag=candidate)
        tag_ends[candidate] = _latest_end(client.get(url))
    dated = [(end, t) for t, end in tag_ends.items() if end is not None]
    # Latest end wins; on a tie the earlier candidate (REVENUE_TAGS order) wins.
    best = max(dated, key=lambda d: (d[0], -REVENUE_TAGS.index(d[1])), default=None)
    latest: str | None = best[0] if best else None
    tag: str | None = best[1] if best else None
    reason = _verdict(tag, latest, expected_tag, today or date.today())
    return CikCheck(padded, name, tag, not reason, latest, tag_ends, reason)


def markdown_row(supplier_id: str, name: str, check: CikCheck) -> str:
    """Format one check as a Markdown table row (no request headers are ever included)."""
    status = "PASS" if check.ok else f"FAIL: {check.reason}"
    tag = f"{check.revenue_tag} (to {check.latest_end})" if check.latest_end else "-"
    if check.revenue_tag and not check.latest_end:
        tag = check.revenue_tag
    return (
        f"| {supplier_id} | {name} | {check.cik} | {check.entity_name or '-'} | {tag} | {status} |"
    )


def _client(user_agent: str) -> httpx.Client:
    return httpx.Client(headers={"User-Agent": user_agent}, timeout=TIMEOUT_SECONDS)


def _load_suppliers() -> list[dict[str, Any]]:
    data = yaml.safe_load(SUPPLIERS_YAML.read_text(encoding="utf-8"))
    return list(data["suppliers"])


def user_agent(env_file: str | None = ".env") -> str | None:
    """Return ZETARIS_USER_AGENT from the settings, or None (with a message) if invalid.

    Args:
        env_file: Dotenv file to read, or None to read only the environment (tests).
    """
    try:
        return load_settings(env_file=env_file).zetaris_user_agent
    except SettingsError as exc:
        # The message names the variables only, never their values.
        print(f"{exc} (ZETARIS_USER_AGENT must hold a name and an email).", file=sys.stderr)
        return None


def _parser() -> argparse.ArgumentParser:
    """Return the command line parser (lookup <tickers...> | verify)."""
    parser = argparse.ArgumentParser(
        description=(__doc__ or "").strip(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    commands = parser.add_subparsers(dest="command", required=True)
    lookup = commands.add_parser("lookup", help="look up CIKs for tickers")
    lookup.add_argument("tickers", nargs="+", help="ticker symbols, e.g. TXN")
    commands.add_parser("verify", help="verify the CIKs and revenue tags in suppliers.yaml")
    return parser


def main(argv: list[str]) -> int:
    """Run `lookup <tickers...>` or `verify`; return the process exit code.

    The command line is parsed before any work, so --help and bad commands do nothing.
    """
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2
    agent = user_agent()
    if not agent:
        return 2
    with _client(agent) as client:
        if args.command == "lookup":
            throttle = _Throttle(MIN_INTERVAL_SECONDS)
            for ticker in args.tickers:
                throttle.wait()
                found = lookup_ticker(client, ticker)
                print(f"{ticker}: {found if found else 'NOT FOUND'}")
            return 0
        failures = 0
        for supplier in _load_suppliers():
            if supplier["cik"] is None:
                continue
            check = verify_cik(client, supplier["cik"], expected_tag=supplier["revenue_tag"])
            failures += 0 if check.ok else 1
            print(markdown_row(supplier["supplier_id"], supplier["name"], check))
        return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
