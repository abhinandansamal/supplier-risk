"""verify_ciks: SEC CIK and revenue-tag checks, with a fake SEC (no network)."""

import time

import httpx
import pytest
import respx

from scripts import verify_ciks

UA = "test-agent test@example.invalid"
SUBMISSIONS = "https://data.sec.gov/submissions/CIK0000097476.json"
CONCEPT = "https://data.sec.gov/api/xbrl/companyconcept/CIK0000097476/us-gaap/{tag}.json"
TICKERS = "https://www.sec.gov/files/company_tickers.json"


def _client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": UA}, timeout=5)


def test_pads_cik_to_ten_digits():
    assert verify_ciks.pad_cik(97476) == "0000097476"
    assert verify_ciks.pad_cik("97476") == "0000097476"


@pytest.mark.parametrize("bad", ["abc", "", "12345678901", "-1"])
def test_rejects_malformed_cik(bad: str):
    with pytest.raises(ValueError):
        verify_ciks.pad_cik(bad)


@respx.mock
def test_uses_revenues_tag_when_present():
    respx.get(SUBMISSIONS).respond(json={"name": "TEXAS INSTRUMENTS INC"})
    respx.get(CONCEPT.format(tag="Revenues")).respond(json={"units": {"USD": [{}]}})
    check = verify_ciks.verify_cik(_client(), "97476", min_interval=0)
    assert check.ok
    assert check.entity_name == "TEXAS INSTRUMENTS INC"
    assert check.revenue_tag == "Revenues"


@respx.mock
def test_falls_back_to_contract_revenue_tag():
    respx.get(SUBMISSIONS).respond(json={"name": "TEXAS INSTRUMENTS INC"})
    respx.get(CONCEPT.format(tag="Revenues")).respond(404)
    respx.get(CONCEPT.format(tag="RevenueFromContractWithCustomerExcludingAssessedTax")).respond(
        json={"units": {"USD": [{}]}}
    )
    check = verify_ciks.verify_cik(_client(), "97476", min_interval=0)
    assert check.ok
    assert check.revenue_tag == "RevenueFromContractWithCustomerExcludingAssessedTax"


@respx.mock
def test_fails_when_no_revenue_tag_exists():
    respx.get(SUBMISSIONS).respond(json={"name": "SOME CO"})
    respx.get(CONCEPT.format(tag="Revenues")).respond(404)
    respx.get(CONCEPT.format(tag="RevenueFromContractWithCustomerExcludingAssessedTax")).respond(
        404
    )
    check = verify_ciks.verify_cik(_client(), "97476", min_interval=0)
    assert not check.ok
    assert check.revenue_tag is None


@respx.mock
def test_unknown_cik_fails_without_concept_calls():
    respx.get(SUBMISSIONS).respond(404)
    check = verify_ciks.verify_cik(_client(), "97476", min_interval=0)
    assert not check.ok
    assert check.entity_name is None


@respx.mock
def test_sends_the_declared_user_agent():
    route = respx.get(SUBMISSIONS).respond(json={"name": "X"})
    respx.get(CONCEPT.format(tag="Revenues")).respond(json={})
    verify_ciks.verify_cik(_client(), "97476", min_interval=0)
    assert route.calls.last.request.headers["user-agent"] == UA


@respx.mock
def test_limits_request_rate_to_two_per_second():
    respx.get(SUBMISSIONS).respond(json={"name": "X"})
    respx.get(CONCEPT.format(tag="Revenues")).respond(json={})
    start = time.monotonic()
    verify_ciks.verify_cik(_client(), "97476", min_interval=0.5)
    assert time.monotonic() - start >= 0.5  # two calls, the second waits


@respx.mock
def test_lookup_finds_cik_by_ticker():
    respx.get(TICKERS).respond(
        json={"0": {"cik_str": 97476, "ticker": "TXN", "title": "TEXAS INSTRUMENTS INC"}}
    )
    found = verify_ciks.lookup_ticker(_client(), "txn")
    assert found == (97476, "TEXAS INSTRUMENTS INC")
    assert verify_ciks.lookup_ticker(_client(), "NOPE") is None


def test_markdown_row_never_contains_user_agent():
    check = verify_ciks.CikCheck(
        cik="0000097476", entity_name="TI", revenue_tag="Revenues", ok=True
    )
    assert UA not in verify_ciks.markdown_row("S0001", "Texas Instruments", check)


def test_main_refuses_to_run_without_user_agent(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("ZETARIS_USER_AGENT", raising=False)
    assert verify_ciks.main(["verify"]) == 2


@respx.mock
def test_main_lookup_prints_found_and_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setenv("ZETARIS_USER_AGENT", UA)
    monkeypatch.setattr(verify_ciks, "MIN_INTERVAL_SECONDS", 0)
    respx.get(TICKERS).respond(json={"0": {"cik_str": 97476, "ticker": "TXN", "title": "TI"}})
    assert verify_ciks.main(["lookup", "TXN", "NOPE"]) == 0
    out = capsys.readouterr().out
    assert "TXN: (97476, 'TI')" in out and "NOPE: NOT FOUND" in out
    assert UA not in out


@respx.mock
def test_main_verify_reports_failures_with_exit_code_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setenv("ZETARIS_USER_AGENT", UA)
    monkeypatch.setattr(verify_ciks, "MIN_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(
        verify_ciks.httpx.Client, "get", lambda self, url, **kw: httpx.Response(404)
    )
    assert verify_ciks.main(["verify"]) == 1
    out = capsys.readouterr().out
    assert out.count("FAIL") == 8 and "S0009" not in out


def test_main_rejects_unknown_command(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ZETARIS_USER_AGENT", UA)
    assert verify_ciks.main(["bogus"]) == 2
