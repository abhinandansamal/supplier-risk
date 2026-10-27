"""Tests for app.zetaris.client: role keys, request shape, retries, response parsing, logging."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable, Iterator

import httpx
import pytest
import respx

from app.core.settings import Settings
from app.zetaris.client import (
    MAX_QUERY_ROWS,
    MAX_QUERY_TIMEOUT_SECONDS,
    QUERY_TIMEOUT_SECONDS,
    ZetarisClient,
    role_api_key,
)
from app.zetaris.errors import (
    ZetarisBadResponse,
    ZetarisConfigError,
    ZetarisRequestError,
    ZetarisUnauthorized,
    ZetarisUnavailable,
)

BASE = "http://localhost:8888/api/v1.0"
RUN_PATH = "/sql-editor/sqls/run"
SQL = "SELECT supplier_id, name FROM risk.v_suppliers_full LIMIT 5"


class Sleeps:
    """Record backoff delays instead of sleeping."""

    def __init__(self) -> None:
        """Start with no recorded delays."""
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        """Record the delay and return at once."""
        self.delays.append(seconds)


@pytest.fixture
def zetaris() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        yield router


@pytest.fixture
def sleeps() -> Sleeps:
    return Sleeps()


@pytest.fixture
async def make_zclient(
    settings: Settings, sleeps: Sleeps
) -> AsyncIterator[Callable[..., ZetarisClient]]:
    clients: list[ZetarisClient] = []

    def factory(role: str = "risk_manager", **overrides: object) -> ZetarisClient:
        client_settings = overrides.pop("settings", settings)
        client = ZetarisClient(client_settings, role, sleep=sleeps, **overrides)  # type: ignore[arg-type]
        clients.append(client)
        return client

    yield factory
    for client in clients:
        await client.aclose()


def _ok(rows: list[list[object]]) -> httpx.Response:
    return httpx.Response(200, json={"data": rows})


# --- role keys -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        ("buyer", "zk-buyer-VALUE-5555"),
        ("risk_manager", "zk-secret-VALUE-1111"),
        ("director", "zk-secret-VALUE-1111"),
    ],
)
def test_role_api_key_picks_the_key_of_the_role(
    settings: Settings, role: str, expected: str
) -> None:
    assert role_api_key(settings, role).get_secret_value() == expected  # type: ignore[arg-type]


def test_auditor_has_no_zetaris_access(settings: Settings) -> None:
    with pytest.raises(ZetarisConfigError, match="no Zetaris access"):
        role_api_key(settings, "auditor")  # type: ignore[arg-type]


def test_missing_role_key_names_the_variable(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings(zetaris_key_buyer=None)
    with pytest.raises(ZetarisConfigError, match="ZETARIS_KEY_BUYER"):
        role_api_key(settings, "buyer")


# --- request shape ---------------------------------------------------------------------------


async def test_sends_the_documented_request(
    zetaris: respx.MockRouter, make_zclient: Callable[..., ZetarisClient]
) -> None:
    route = zetaris.post(RUN_PATH).mock(return_value=_ok([["S0001", "TI"]]))
    await make_zclient("buyer").query(SQL, limit=5)
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer zk-buyer-VALUE-5555"
    assert request.headers["X-Org-ID"] == "org-test"
    assert len(request.headers["X-Request-ID"]) == 36
    body = json.loads(request.content)
    assert body["sql"] == SQL
    assert body["source"] == "SqlEditor"
    assert body["limit"] == 5
    assert len(body["queryId"]) == 36


async def test_every_call_gets_a_new_request_and_query_id(
    zetaris: respx.MockRouter, make_zclient: Callable[..., ZetarisClient]
) -> None:
    route = zetaris.post(RUN_PATH).mock(return_value=_ok([]))
    client = make_zclient()
    first = await client.query(SQL)
    second = await client.query(SQL)
    ids = [c.request.headers["X-Request-ID"] for c in route.calls]
    assert ids[0] != ids[1]
    assert first.query_id != second.query_id


@pytest.mark.parametrize("limit", [0, -1, MAX_QUERY_ROWS + 1])
async def test_rejects_limits_out_of_range(
    make_zclient: Callable[..., ZetarisClient], limit: int
) -> None:
    with pytest.raises(ValueError, match="limit"):
        await make_zclient().query(SQL, limit=limit)


# --- response parsing ------------------------------------------------------------------------


async def test_returns_rows_as_strings_or_none(
    zetaris: respx.MockRouter, make_zclient: Callable[..., ZetarisClient]
) -> None:
    zetaris.post(RUN_PATH).mock(return_value=_ok([["S0001", 12, None, 1.5, True]]))
    result = await make_zclient().query(SQL)
    assert result.rows == [["S0001", "12", None, "1.5", "true"]]
    assert result.row_count == 1
    assert result.duration_ms >= 0


async def test_cuts_rows_to_the_limit(
    zetaris: respx.MockRouter, make_zclient: Callable[..., ZetarisClient]
) -> None:
    zetaris.post(RUN_PATH).mock(return_value=_ok([[str(i)] for i in range(10)]))
    result = await make_zclient().query(SQL, limit=3)
    assert result.rows == [["0"], ["1"], ["2"]]


async def test_empty_data_list_means_no_rows(
    zetaris: respx.MockRouter, make_zclient: Callable[..., ZetarisClient]
) -> None:
    zetaris.post(RUN_PATH).mock(return_value=httpx.Response(200, json={"data": []}))
    result = await make_zclient().query(SQL)
    assert result.rows == []


async def test_null_data_is_a_bad_response_not_zero_rows(
    zetaris: respx.MockRouter, make_zclient: Callable[..., ZetarisClient]
) -> None:
    zetaris.post(RUN_PATH).mock(return_value=httpx.Response(200, json={"data": None}))
    with pytest.raises(ZetarisBadResponse):
        await make_zclient().query(SQL)


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"[1, 2]",
        b'{"data": "rows"}',
        b'{"data": [["ok"], "not a row"]}',
        b'{"data": [[{"nested": 1}]]}',
    ],
)
async def test_unexpected_body_is_a_bad_response(
    zetaris: respx.MockRouter, make_zclient: Callable[..., ZetarisClient], body: bytes
) -> None:
    zetaris.post(RUN_PATH).mock(return_value=httpx.Response(200, content=body))
    with pytest.raises(ZetarisBadResponse):
        await make_zclient().query(SQL)


# --- errors and retries ----------------------------------------------------------------------


@pytest.mark.parametrize("status", [401, 403])
async def test_unauthorized_is_not_retried(
    zetaris: respx.MockRouter, make_zclient: Callable[..., ZetarisClient], status: int
) -> None:
    route = zetaris.post(RUN_PATH).mock(return_value=httpx.Response(status))
    with pytest.raises(ZetarisUnauthorized) as info:
        await make_zclient().query(SQL)
    assert info.value.status_code == status
    assert route.call_count == 1


@pytest.mark.parametrize("status", [400, 404, 500])
async def test_refused_query_is_a_request_error(
    zetaris: respx.MockRouter, make_zclient: Callable[..., ZetarisClient], status: int
) -> None:
    route = zetaris.post(RUN_PATH).mock(return_value=httpx.Response(status))
    with pytest.raises(ZetarisRequestError) as info:
        await make_zclient().query(SQL)
    assert info.value.status_code == status
    assert route.call_count == 1


@pytest.mark.parametrize("status", [429, 502, 503, 504])
async def test_retries_once_then_succeeds(
    zetaris: respx.MockRouter,
    make_zclient: Callable[..., ZetarisClient],
    sleeps: Sleeps,
    status: int,
) -> None:
    route = zetaris.post(RUN_PATH).mock(side_effect=[httpx.Response(status), _ok([["S0001"]])])
    result = await make_zclient().query(SQL)
    assert result.rows == [["S0001"]]
    assert route.call_count == 2
    assert len(sleeps.delays) == 1


async def test_gives_up_after_one_retry(
    zetaris: respx.MockRouter, make_zclient: Callable[..., ZetarisClient]
) -> None:
    route = zetaris.post(RUN_PATH).mock(return_value=httpx.Response(503))
    with pytest.raises(ZetarisUnavailable) as info:
        await make_zclient().query(SQL)
    assert info.value.status_code == 503
    assert route.call_count == 2


async def test_timeout_is_retried_then_unavailable(
    zetaris: respx.MockRouter, make_zclient: Callable[..., ZetarisClient]
) -> None:
    route = zetaris.post(RUN_PATH).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(ZetarisUnavailable):
        await make_zclient().query(SQL)
    assert route.call_count == 2


def _slow_transport(seconds: float) -> httpx.MockTransport:
    async def handler(_: httpx.Request) -> httpx.Response:
        await asyncio.sleep(seconds)
        return _ok([["1"]])

    return httpx.MockTransport(handler)


async def test_default_timeout_is_the_app_limit(
    make_zclient: Callable[..., ZetarisClient],
) -> None:
    assert QUERY_TIMEOUT_SECONDS == 20.0
    client = make_zclient(transport=_slow_transport(0.0))
    assert (await client.query(SQL)).rows == [["1"]]


async def test_query_timeout_cuts_a_slow_query(
    make_zclient: Callable[..., ZetarisClient],
) -> None:
    client = make_zclient(transport=_slow_transport(0.5), query_timeout=0.05)
    with pytest.raises(ZetarisUnavailable):
        await client.query(SQL)


async def test_longer_query_timeout_waits_for_a_slow_query(
    make_zclient: Callable[..., ZetarisClient],
) -> None:
    """Live REST views (Eurostat) take minutes until Feature 5 caches them."""
    client = make_zclient(transport=_slow_transport(0.1), query_timeout=2.0)
    assert (await client.query(SQL)).rows == [["1"]]


@pytest.mark.parametrize("seconds", [0.0, -1.0, MAX_QUERY_TIMEOUT_SECONDS + 1])
def test_rejects_query_timeouts_out_of_range(settings: Settings, seconds: float) -> None:
    with pytest.raises(ValueError, match="query_timeout"):
        ZetarisClient(settings, "risk_manager", query_timeout=seconds)


async def test_retry_after_is_honoured_but_capped(
    zetaris: respx.MockRouter, make_zclient: Callable[..., ZetarisClient], sleeps: Sleeps
) -> None:
    zetaris.post(RUN_PATH).mock(
        side_effect=[httpx.Response(429, headers={"Retry-After": "3600"}), _ok([])]
    )
    await make_zclient().query(SQL)
    assert sleeps.delays == [10.0]


# --- logging ---------------------------------------------------------------------------------


async def test_logs_query_done_without_sql_or_rows(
    zetaris: respx.MockRouter,
    make_zclient: Callable[..., ZetarisClient],
    caplog: pytest.LogCaptureFixture,
) -> None:
    zetaris.post(RUN_PATH).mock(return_value=_ok([["S0001", "Secret Row Value"]]))
    with caplog.at_level(logging.INFO, logger="app.zetaris.client"):
        result = await make_zclient().query(SQL)
    records = [r for r in caplog.records if getattr(r, "event", None) == "zetaris_query_done"]
    assert len(records) == 1
    record = records[0]
    assert record.levelno == logging.INFO
    assert record.rows == 1  # type: ignore[attr-defined]
    assert record.query_id == result.query_id  # type: ignore[attr-defined]
    assert record.role == "risk_manager"  # type: ignore[attr-defined]
    text = caplog.text + json.dumps([vars(r) for r in records], default=str)
    assert "Secret Row Value" not in text
    assert "v_suppliers_full" not in text


async def test_logs_retry_as_warning(
    zetaris: respx.MockRouter,
    make_zclient: Callable[..., ZetarisClient],
    caplog: pytest.LogCaptureFixture,
) -> None:
    zetaris.post(RUN_PATH).mock(side_effect=[httpx.Response(502), _ok([])])
    with caplog.at_level(logging.INFO, logger="app.zetaris.client"):
        await make_zclient().query(SQL)
    retries = [r for r in caplog.records if getattr(r, "event", None) == "zetaris_retry"]
    assert len(retries) == 1
    assert retries[0].levelno == logging.WARNING
    assert retries[0].status_code == 502  # type: ignore[attr-defined]
