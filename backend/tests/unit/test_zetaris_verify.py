"""Tests for scripts/zetaris_verify.py: fixed read-only SQL, a Markdown table, clear skips."""

import json
from collections.abc import Callable

import httpx
import pytest
import respx

from app.core.settings import Settings
from scripts import zetaris_verify
from scripts.zetaris_verify import (
    EXIT_FAILED,
    EXIT_OK,
    EXIT_SKIPPED,
    VERIFY_TIMEOUT_SECONDS,
    VIEW_CHECKS,
    ViewStats,
    build_sql,
    format_table,
    main,
)

BASE = "http://localhost:8888/api/v1.0"
RUN_PATH = "/sql-editor/sqls/run"
KEYS = ("zk-secret-VALUE-1111", "zk-buyer-VALUE-5555", "zk-admin-VALUE-6666")


def _answer(request: httpx.Request) -> httpx.Response:
    sql = json.loads(request.content)["sql"]
    if "MIN(" in sql:
        return httpx.Response(200, json={"data": [["42", "2025-01-01", "2026-10-27"]]})
    return httpx.Response(200, json={"data": [["12"]]})


def test_checks_every_risk_view_once() -> None:
    views = [check.name for check in VIEW_CHECKS]
    assert len(views) == len(set(views)) == 9
    assert all(view.startswith("v_") for view in views)


def test_sql_counts_rows_and_reads_the_date_range() -> None:
    assert build_sql("v_deliveries", "promised_date") == (
        "SELECT COUNT(1) AS row_count, MIN(promised_date) AS min_date, "
        "MAX(promised_date) AS max_date FROM risk.v_deliveries"
    )


def test_sql_without_a_date_column_only_counts() -> None:
    assert build_sql("v_part_sourcing", None) == (
        "SELECT COUNT(1) AS row_count FROM risk.v_part_sourcing"
    )


@pytest.mark.parametrize(("view", "column"), [("v_x; DROP", None), ("v_x", "a b")])
def test_sql_refuses_names_that_are_not_identifiers(view: str, column: str | None) -> None:
    with pytest.raises(ValueError, match="identifier"):
        build_sql(view, column)


def test_format_table_is_markdown() -> None:
    table = format_table(
        [ViewStats("v_deliveries", 10, "2025-01-01", "2026-10-27"), ViewStats("v_x", 3, None, None)]
    )
    lines = table.splitlines()
    assert lines[0] == "| View | Rows | From | To |"
    assert "| risk.v_deliveries | 10 | 2025-01-01 | 2026-10-27 |" in lines
    assert "| risk.v_x | 3 | - | - |" in lines


async def test_main_prints_the_table_and_exits_zero(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    with respx.mock(base_url=BASE) as router:
        route = router.post(RUN_PATH).mock(side_effect=_answer)
        code = await main(settings)
    assert code == EXIT_OK
    assert route.call_count == len(VIEW_CHECKS)
    assert all(
        c.request.headers["Authorization"] == "Bearer zk-secret-VALUE-1111" for c in route.calls
    )
    out = capsys.readouterr().out
    assert "| risk.v_country_indicators |" in out
    for key in KEYS:
        assert key not in out


async def test_main_waits_long_enough_for_live_rest_views(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """v_country_indicators took 3.5 minutes as svc_risk on 2026-10-27 (live Eurostat)."""
    seen: list[float] = []
    real = zetaris_verify.ZetarisClient

    def spy(*args: object, **kwargs: float) -> object:
        seen.append(kwargs["query_timeout"])
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(zetaris_verify, "ZetarisClient", spy)
    with respx.mock(base_url=BASE) as router:
        router.post(RUN_PATH).mock(side_effect=_answer)
        assert await main(settings) == EXIT_OK
    assert seen == [VERIFY_TIMEOUT_SECONDS]
    assert VERIFY_TIMEOUT_SECONDS >= 240


async def test_main_skips_when_the_risk_key_is_missing(
    make_settings: Callable[..., Settings], capsys: pytest.CaptureFixture[str]
) -> None:
    code = await main(make_settings(zetaris_key_risk=None))
    assert code == EXIT_SKIPPED
    out = capsys.readouterr().out
    assert "SKIP" in out
    assert "ZETARIS_KEY_RISK" in out
    assert "docs/zetaris-setup.md" in out


async def test_main_skips_when_the_key_has_no_access(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    with respx.mock(base_url=BASE) as router:
        router.post(RUN_PATH).mock(return_value=httpx.Response(403))
        code = await main(settings)
    assert code == EXIT_SKIPPED
    out = capsys.readouterr().out
    assert "SKIP" in out and "no access" in out
    for key in KEYS:
        assert key not in out


async def test_main_fails_on_a_broken_view(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    with respx.mock(base_url=BASE) as router:
        router.post(RUN_PATH).mock(return_value=httpx.Response(500, text="boom zk-secret"))
        code = await main(settings)
    assert code == EXIT_FAILED
    out = capsys.readouterr().out
    assert "FAIL" in out
    assert "boom" not in out


async def test_main_names_the_view_that_failed_and_keeps_the_others(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        if "v_supplier_revenue" in json.loads(request.content)["sql"]:
            return httpx.Response(400, text="boom")
        return _answer(request)

    with respx.mock(base_url=BASE) as router:
        route = router.post(RUN_PATH).mock(side_effect=answer)
        code = await main(settings)
    assert code == EXIT_FAILED
    assert route.call_count == len(VIEW_CHECKS)
    out = capsys.readouterr().out
    assert "FAIL risk.v_supplier_revenue: ZetarisRequestError" in out
    assert "| risk.v_country_indicators | 42 |" in out
    assert "boom" not in out
