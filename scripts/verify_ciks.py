"""Verify the SEC CIKs and revenue tags of the public suppliers in data_pipeline/suppliers.yaml.

Usage (the declared User-Agent comes from the environment, never from a file):
    ZETARIS_USER_AGENT="<name> <email>" uv run python scripts/verify_ciks.py lookup TXN ADI
    ZETARIS_USER_AGENT="<name> <email>" uv run python scripts/verify_ciks.py verify

`lookup` maps tickers to CIKs through the SEC ticker file. `verify` checks every supplier with a
CIK: the submissions endpoint must answer, and a us-gaap revenue tag must exist on the
companyconcept endpoint. It prints Markdown rows for docs/data-contract.md. At most 2 requests/s.
"""

import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import yaml

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
CONCEPT_URL = "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/us-gaap/{tag}.json"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
REVENUE_TAGS = ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax")
MIN_INTERVAL_SECONDS = 0.5  # 2 requests per second
SUPPLIERS_YAML = Path(__file__).resolve().parents[1] / "data_pipeline" / "suppliers.yaml"
USER_AGENT_ENV = "ZETARIS_USER_AGENT"
TIMEOUT_SECONDS = 20


@dataclass(frozen=True)
class CikCheck:
    """Outcome of verifying one CIK."""

    cik: str
    entity_name: str | None
    revenue_tag: str | None
    ok: bool


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


def verify_cik(
    client: httpx.Client, cik: int | str, min_interval: float = MIN_INTERVAL_SECONDS
) -> CikCheck:
    """Check that the CIK exists and has a us-gaap revenue tag with data."""
    padded = pad_cik(cik)
    throttle = _Throttle(min_interval)
    throttle.wait()
    response = client.get(SUBMISSIONS_URL.format(cik=padded))
    if response.status_code != httpx.codes.OK:
        return CikCheck(padded, None, None, ok=False)
    name = str(response.json().get("name", ""))
    for tag in REVENUE_TAGS:
        throttle.wait()
        concept = client.get(CONCEPT_URL.format(cik=padded, tag=tag))
        if concept.status_code == httpx.codes.OK:
            return CikCheck(padded, name, tag, ok=True)
    return CikCheck(padded, name, None, ok=False)


def markdown_row(supplier_id: str, name: str, check: CikCheck) -> str:
    """Format one check as a Markdown table row (no request headers are ever included)."""
    status = "PASS" if check.ok else "FAIL"
    return (
        f"| {supplier_id} | {name} | {check.cik} | {check.entity_name or '-'} "
        f"| {check.revenue_tag or '-'} | {status} |"
    )


def _client(user_agent: str) -> httpx.Client:
    return httpx.Client(headers={"User-Agent": user_agent}, timeout=TIMEOUT_SECONDS)


def _load_suppliers() -> list[dict[str, Any]]:
    data = yaml.safe_load(SUPPLIERS_YAML.read_text(encoding="utf-8"))
    return list(data["suppliers"])


def main(argv: list[str]) -> int:
    """Run `lookup <tickers...>` or `verify`; return the process exit code."""
    user_agent = os.environ.get(USER_AGENT_ENV, "").strip()
    if not user_agent:
        print(f"Set {USER_AGENT_ENV} in the environment (name and email).", file=sys.stderr)
        return 2
    if not argv or argv[0] not in {"lookup", "verify"}:
        print("usage: verify_ciks.py lookup <ticker>... | verify", file=sys.stderr)
        return 2
    with _client(user_agent) as client:
        if argv[0] == "lookup":
            throttle = _Throttle(MIN_INTERVAL_SECONDS)
            for ticker in argv[1:]:
                throttle.wait()
                found = lookup_ticker(client, ticker)
                print(f"{ticker}: {found if found else 'NOT FOUND'}")
            return 0
        failures = 0
        for supplier in _load_suppliers():
            if supplier["cik"] is None:
                continue
            check = verify_cik(client, supplier["cik"])
            failures += 0 if check.ok else 1
            print(markdown_row(supplier["supplier_id"], supplier["name"], check))
        return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
