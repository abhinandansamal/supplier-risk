"""verify_ciks: SEC CIK and revenue-tag checks, with a fake SEC (no network)."""

import time
from datetime import date

import httpx
import pytest
import respx

from scripts import verify_ciks

UA = "test-agent test@example.invalid"
SUBMISSIONS = "https://data.sec.gov/submissions/CIK0000097476.json"
CONCEPT = "https://data.sec.gov/api/xbrl/companyconcept/CIK0000097476/us-gaap/{tag}.json"
TICKERS = "https://www.sec.gov/files/company_tickers.json"
REV, RFC_EX, RFC_IN = verify_ciks.REVENUE_TAGS
TODAY = date(2026, 10, 27)


def _client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": UA}, timeout=5)


def _facts(*ends: str) -> dict[str, object]:
    return {"units": {"USD": [{"end": end, "val": 1} for end in ends]}}


def _concepts(fresh: dict[str, list[str]]) -> None:
    """Mock every candidate tag: listed tags answer with facts ending on the given dates."""
    for tag in verify_ciks.REVENUE_TAGS:
        if tag in fresh:
            respx.get(CONCEPT.format(tag=tag)).respond(json=_facts(*fresh[tag]))
        else:
            respx.get(CONCEPT.format(tag=tag)).respond(404)


def _verify(expected: str | None = None) -> verify_ciks.CikCheck:
    return verify_ciks.verify_cik(
        _client(), "97476", min_interval=0, expected_tag=expected, today=TODAY
    )


def test_pads_cik_to_ten_digits():
    assert verify_ciks.pad_cik(97476) == "0000097476"
    assert verify_ciks.pad_cik("97476") == "0000097476"


@pytest.mark.parametrize("bad", ["abc", "", "12345678901", "-1"])
def test_rejects_malformed_cik(bad: str):
    with pytest.raises(ValueError):
        verify_ciks.pad_cik(bad)


def test_candidate_tags_match_the_supplier_model():
    from data_pipeline.suppliers import REVENUE_TAGS

    assert verify_ciks.REVENUE_TAGS == REVENUE_TAGS


# --- freshest tag (SEC-036) ------------------------------------------------------------------


@respx.mock
def test_picks_the_tag_with_the_latest_period_end():
    respx.get(SUBMISSIONS).respond(json={"name": "TEXAS INSTRUMENTS INC"})
    _concepts({REV: ["2018-11-03", "2017-10-28"], RFC_EX: ["2026-08-01", "2025-08-02"]})
    check = _verify()
    assert check.ok
    assert check.entity_name == "TEXAS INSTRUMENTS INC"
    assert check.revenue_tag == RFC_EX
    assert check.latest_end == "2026-08-01"
    assert check.tag_ends == {REV: "2018-11-03", RFC_EX: "2026-08-01", RFC_IN: None}


@respx.mock
def test_passes_when_the_yaml_tag_is_the_freshest():
    respx.get(SUBMISSIONS).respond(json={"name": "X"})
    _concepts({REV: ["2026-09-30"], RFC_EX: ["2019-06-30"]})
    assert _verify(expected=REV).ok


@respx.mock
def test_fails_when_the_yaml_tag_is_not_the_freshest():
    respx.get(SUBMISSIONS).respond(json={"name": "X"})
    _concepts({REV: ["2019-06-30"], RFC_EX: ["2026-06-30"]})
    check = _verify(expected=REV)
    assert not check.ok
    assert check.revenue_tag == RFC_EX
    assert "suppliers.yaml has Revenues" in check.reason


@respx.mock
def test_fails_when_even_the_freshest_tag_is_stale():
    respx.get(SUBMISSIONS).respond(json={"name": "X"})
    _concepts({REV: ["2024-09-28"]})
    check = _verify(expected=REV)
    assert not check.ok
    assert check.latest_end == "2024-09-28"
    assert "stale" in check.reason


@respx.mock
def test_fresh_means_within_nine_months():
    respx.get(SUBMISSIONS).respond(json={"name": "X"})
    _concepts({REV: ["2026-01-31"]})  # 269 days before TODAY
    assert _verify().ok


@respx.mock
def test_facts_without_a_usable_end_date_are_ignored():
    respx.get(SUBMISSIONS).respond(json={"name": "X"})
    respx.get(CONCEPT.format(tag=REV)).respond(
        json={"units": {"USD": [{}, {"end": 7}, {"end": "not-a-date"}, {"end": "2026-06-30"}]}}
    )
    respx.get(CONCEPT.format(tag=RFC_EX)).respond(json={"units": {"EUR": [{"end": "2026-09-30"}]}})
    respx.get(CONCEPT.format(tag=RFC_IN)).respond(json={"units": {"USD": []}})
    check = _verify()
    assert check.revenue_tag == REV
    assert check.latest_end == "2026-06-30"


@respx.mock
def test_fails_when_no_revenue_tag_exists():
    respx.get(SUBMISSIONS).respond(json={"name": "SOME CO"})
    _concepts({})
    check = _verify()
    assert not check.ok
    assert check.revenue_tag is None
    assert "no revenue tag" in check.reason


@respx.mock
def test_unknown_cik_fails_without_concept_calls():
    respx.get(SUBMISSIONS).respond(404)
    check = _verify()
    assert not check.ok
    assert check.entity_name is None


@respx.mock
def test_sends_the_declared_user_agent():
    route = respx.get(SUBMISSIONS).respond(json={"name": "X"})
    _concepts({REV: ["2026-09-30"]})
    _verify()
    assert route.calls.last.request.headers["user-agent"] == UA


@respx.mock
def test_limits_request_rate_to_two_per_second():
    respx.get(SUBMISSIONS).respond(json={"name": "X"})
    _concepts({REV: ["2026-09-30"]})
    start = time.monotonic()
    verify_ciks.verify_cik(_client(), "97476", min_interval=0.5, today=TODAY)
    # four calls (submissions + three tags): at least three waits of 0.5 s
    assert time.monotonic() - start >= 1.5


@respx.mock
def test_lookup_finds_cik_by_ticker():
    respx.get(TICKERS).respond(
        json={"0": {"cik_str": 97476, "ticker": "TXN", "title": "TEXAS INSTRUMENTS INC"}}
    )
    found = verify_ciks.lookup_ticker(_client(), "txn")
    assert found == (97476, "TEXAS INSTRUMENTS INC")
    assert verify_ciks.lookup_ticker(_client(), "NOPE") is None


# --- output ----------------------------------------------------------------------------------


def test_markdown_row_never_contains_user_agent():
    check = verify_ciks.CikCheck(
        cik="0000097476", entity_name="TI", revenue_tag="Revenues", ok=True
    )
    assert UA not in verify_ciks.markdown_row("S0001", "Texas Instruments", check)


def test_markdown_row_shows_the_latest_period_and_the_reason():
    check = verify_ciks.CikCheck(
        cik="0000097476",
        entity_name="TI",
        revenue_tag=RFC_EX,
        ok=False,
        latest_end="2026-06-30",
        reason="suppliers.yaml has Revenues",
    )
    row = verify_ciks.markdown_row("S0001", "Texas Instruments", check)
    assert f"| {RFC_EX} (to 2026-06-30) |" in row
    assert row.endswith("| FAIL: suppliers.yaml has Revenues |")
    assert row.count("|") == 7


# --- user agent from settings ----------------------------------------------------------------


def test_user_agent_comes_from_settings(env: dict[str, str]):
    assert verify_ciks.user_agent(env_file=None) == env["ZETARIS_USER_AGENT"]


def test_user_agent_is_none_when_settings_are_invalid(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.delenv("ZETARIS_USER_AGENT")
    assert verify_ciks.user_agent(env_file=None) is None
    assert "ZETARIS_USER_AGENT" in capsys.readouterr().err


def test_main_refuses_to_run_without_user_agent(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(verify_ciks, "user_agent", lambda env_file=".env": None)
    assert verify_ciks.main(["verify"]) == 2


@respx.mock
def test_main_lookup_prints_found_and_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setattr(verify_ciks, "user_agent", lambda env_file=".env": UA)
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
    monkeypatch.setattr(verify_ciks, "user_agent", lambda env_file=".env": UA)
    monkeypatch.setattr(verify_ciks, "MIN_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(
        verify_ciks.httpx.Client, "get", lambda self, url, **kw: httpx.Response(404)
    )
    assert verify_ciks.main(["verify"]) == 1
    out = capsys.readouterr().out
    assert out.count("FAIL") == 8 and "S0009" not in out


def test_main_rejects_unknown_command(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(verify_ciks, "user_agent", lambda env_file=".env": UA)
    assert verify_ciks.main(["bogus"]) == 2
