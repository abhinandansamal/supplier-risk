"""Security tests for the Zetaris client: never the admin key, read-only SQL, no leaks."""

import logging
from collections.abc import AsyncIterator, Callable, Iterator

import httpx
import pytest
import respx

from app.core.settings import Settings
from app.zetaris.client import MAX_SQL_CHARS, QueryResult, ZetarisClient
from app.zetaris.errors import ZetarisBadResponse, ZetarisConfigError, ZetarisError

BASE = "http://localhost:8888/api/v1.0"
RUN_PATH = "/sql-editor/sqls/run"
ADMIN = "zk-admin-VALUE-6666"
RISK = "zk-secret-VALUE-1111"
BUYER = "zk-buyer-VALUE-5555"
UPSTREAM_SECRET = "upstream-detail-SHOULD-NOT-LEAK"


async def _no_sleep(_: float) -> None:
    return None


@pytest.fixture
def router() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        yield mock


@pytest.fixture
async def client(make_settings: Callable[..., Settings]) -> AsyncIterator[ZetarisClient]:
    zetaris = ZetarisClient(make_settings(), "risk_manager", sleep=_no_sleep)
    yield zetaris
    await zetaris.aclose()


def _assert_clean(text: str) -> None:
    for secret in (ADMIN, RISK, BUYER, UPSTREAM_SECRET):
        assert secret not in text


@pytest.mark.parametrize("role", ["buyer", "risk_manager", "director"])
def test_refuses_to_start_with_only_the_admin_key(
    make_settings: Callable[..., Settings], role: str
) -> None:
    settings = make_settings(zetaris_key_buyer=None, zetaris_key_risk=None)
    assert settings.zetaris_admin_api_key is not None
    with pytest.raises(ZetarisConfigError) as info:
        ZetarisClient(settings, role)  # type: ignore[arg-type]
    assert "admin key is never used at runtime" in str(info.value)
    _assert_clean(f"{info.value!s} {info.value!r}")


@pytest.mark.parametrize(
    ("role", "field"),
    [("buyer", "zetaris_key_buyer"), ("risk_manager", "zetaris_key_risk")],
)
def test_refuses_a_role_key_that_is_the_admin_key(
    make_settings: Callable[..., Settings], role: str, field: str
) -> None:
    settings = make_settings(**{field: ADMIN})
    with pytest.raises(ZetarisConfigError, match="must not be the admin key") as info:
        ZetarisClient(settings, role)  # type: ignore[arg-type]
    _assert_clean(str(info.value))


def test_buyer_and_risk_keys_must_differ(make_settings: Callable[..., Settings]) -> None:
    """One key for both roles would give buyers the risk manager's columns."""
    settings = make_settings(zetaris_key_buyer=RISK)
    with pytest.raises(ZetarisConfigError, match="must differ"):
        ZetarisClient(settings, "buyer")


def test_auditor_cannot_build_a_client(make_settings: Callable[..., Settings]) -> None:
    with pytest.raises(ZetarisConfigError):
        ZetarisClient(make_settings(), "auditor")  # type: ignore[arg-type]


def test_repr_hides_the_key(client: ZetarisClient) -> None:
    _assert_clean(repr(client))


@pytest.mark.parametrize(
    "sql",
    [
        "DROP VIEW risk.v_suppliers_full",
        "CREATE SCHEMASTORE VIEW x WITH CONTAINER risk AS SELECT 1",
        "GRANT SELECT ON VIEW TABLE risk.v_contracts_full TO ROLE 'buyer'",
        "INSERT INTO erp_pg.suppliers VALUES (1)",
        "SELECT 1; DROP VIEW risk.v_suppliers_full",
        "SELECT 1;",
        "  ",
        "SHOW DATASOURCES",
        "select 1 -- trailing comment",
        "SELECT 1 /* block */",
    ],
)
async def test_refuses_anything_but_one_plain_select(
    router: respx.MockRouter, client: ZetarisClient, sql: str
) -> None:
    route = router.post(RUN_PATH).mock(return_value=httpx.Response(200, json={"data": []}))
    with pytest.raises(ValueError, match="single SELECT"):
        await client.query(sql)
    assert route.call_count == 0


async def test_refuses_oversized_sql(router: respx.MockRouter, client: ZetarisClient) -> None:
    route = router.post(RUN_PATH).mock(return_value=httpx.Response(200, json={"data": []}))
    with pytest.raises(ValueError, match="too long"):
        await client.query("SELECT " + "a" * MAX_SQL_CHARS)
    assert route.call_count == 0


@pytest.mark.parametrize("status", [400, 401, 500, 503])
async def test_errors_never_carry_the_upstream_body_or_key(
    router: respx.MockRouter, client: ZetarisClient, status: int
) -> None:
    router.post(RUN_PATH).mock(
        return_value=httpx.Response(status, text=f"{UPSTREAM_SECRET} {RISK}")
    )
    with pytest.raises(ZetarisError) as info:
        await client.query("SELECT 1")
    _assert_clean(f"{info.value!s} {info.value!r} {info.value.args!r}")
    assert info.value.__cause__ is None


async def test_redirects_are_not_followed(router: respx.MockRouter, client: ZetarisClient) -> None:
    """A redirect could carry the bearer key to another host."""
    router.post(RUN_PATH).mock(
        return_value=httpx.Response(307, headers={"Location": "http://evil.example/steal"})
    )
    evil = router.route(host="evil.example").mock(return_value=httpx.Response(200))
    with pytest.raises(ZetarisError):
        await client.query("SELECT 1")
    assert evil.call_count == 0


async def test_oversized_response_is_refused(
    router: respx.MockRouter, client: ZetarisClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.zetaris.client.MAX_RESPONSE_BYTES", 100)
    router.post(RUN_PATH).mock(return_value=httpx.Response(200, json={"data": [["x" * 200]]}))
    with pytest.raises(ZetarisError, match="too large"):
        await client.query("SELECT 1")


def test_plain_http_to_a_remote_zetaris_is_refused(
    make_settings: Callable[..., Settings],
) -> None:
    """The bearer key must not cross a network in clear text."""
    settings = make_settings(zetaris_rest_url="http://zetaris.example.com/api/v1.0")
    with pytest.raises(ZetarisConfigError, match="https"):
        ZetarisClient(settings, "risk_manager")


# --- red-team findings 2026-10-27 (docs/security-review.md); strict xfail until fixed ---------


@pytest.mark.xfail(strict=True, reason="SEC-045")
def test_client_runs_only_registered_templates_with_bound_parameters() -> None:
    """Row scope (category) must be a validated, bound parameter, not pasted into SQL text."""
    assert hasattr(ZetarisClient, "query_template")


@pytest.mark.xfail(strict=True, reason="SEC-046")
def test_runtime_settings_have_no_admin_key(make_settings: Callable[..., Settings]) -> None:
    assert not hasattr(make_settings(), "zetaris_admin_api_key")


@pytest.mark.xfail(strict=True, reason="SEC-046")
def test_admin_key_with_surrounding_space_is_still_refused(
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings(zetaris_key_risk=f"{ADMIN} ")
    with pytest.raises(ZetarisConfigError):
        ZetarisClient(settings, "risk_manager")


@pytest.mark.xfail(strict=True, reason="SEC-047")
@pytest.mark.parametrize(
    "body", [{"error": "does not have privileges"}, {"data": None, "message": "not found"}]
)
async def test_200_without_data_is_a_bad_response(
    router: respx.MockRouter, client: ZetarisClient, body: dict[str, object]
) -> None:
    """An upstream error must never read as "0 rows" evidence."""
    router.post(RUN_PATH).mock(return_value=httpx.Response(200, json=body))
    with pytest.raises(ZetarisBadResponse):
        await client.query("SELECT 1")


@pytest.mark.xfail(strict=True, reason="SEC-048")
async def test_requests_uncompressed_responses(
    router: respx.MockRouter, client: ZetarisClient
) -> None:
    """Decompressed chunks are counted after expansion, so a small zstd body can blow memory."""
    route = router.post(RUN_PATH).mock(return_value=httpx.Response(200, json={"data": []}))
    await client.query("SELECT 1")
    assert route.calls.last.request.headers["Accept-Encoding"] == "identity"


@pytest.mark.xfail(strict=True, reason="SEC-049")
@pytest.mark.parametrize(
    "sql",
    [
        "WITH t AS (SELECT 1) INSERT INTO risk.x SELECT * FROM t",
        "SELECT java_method('java.lang.Thread', 'sleep', 600000L)",
        "SELECT reflect('java.lang.System', 'exit', 0)",
        "SELECT * FROM erp_pg.contracts",
        "SELECT 1 ； DROP VIEW risk.v_deliveries",
        "SELECT 1 ; DROP VIEW risk.v_deliveries",
    ],
)
async def test_guard_refuses_writes_reflection_star_and_unicode_separators(
    router: respx.MockRouter, client: ZetarisClient, sql: str
) -> None:
    route = router.post(RUN_PATH).mock(return_value=httpx.Response(200, json={"data": []}))
    with pytest.raises(ValueError):
        await client.query(sql)
    assert route.call_count == 0


@pytest.mark.xfail(strict=True, reason="SEC-050")
def test_supplier_name_cannot_carry_a_newline() -> None:
    """The name lands in a comment of an admin-run SQL file; a newline would inject a statement."""
    from pydantic import ValidationError

    from data_pipeline.suppliers import Supplier

    with pytest.raises(ValidationError):
        Supplier(
            supplier_id="S0099",
            name="Acme\nGRANT SELECT ON VIEW TABLE risk.v_contracts_full TO ROLE 'buyer';\n--",
            cik="0000000001",
            category="metals",
            country_iso="DE",
            revenue_tag="Revenues",
        )


@pytest.mark.xfail(strict=True, reason="SEC-052")
async def test_undecodable_body_becomes_a_zetaris_error(
    make_settings: Callable[..., Settings],
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Content-Encoding": "gzip"}, content=b"not gzip")

    async with ZetarisClient(
        make_settings(), "risk_manager", transport=httpx.MockTransport(handler), sleep=_no_sleep
    ) as zetaris:
        with pytest.raises(ZetarisError):
            await zetaris.query("SELECT 1")


@pytest.mark.xfail(strict=True, reason="SEC-053")
async def test_failed_query_log_keeps_the_stack_trace(
    router: respx.MockRouter, client: ZetarisClient, caplog: pytest.LogCaptureFixture
) -> None:
    router.post(RUN_PATH).mock(return_value=httpx.Response(400))
    with caplog.at_level(logging.ERROR), pytest.raises(ZetarisError):
        await client.query("SELECT 1")
    failed = [r for r in caplog.records if getattr(r, "event", "") == "zetaris_query_failed"]
    assert failed and failed[0].exc_info


@pytest.mark.xfail(strict=True, reason="SEC-054")
async def test_numeric_cells_keep_their_exact_text(
    router: respx.MockRouter, client: ZetarisClient
) -> None:
    """Evidence numbers must match the source digit for digit."""
    router.post(RUN_PATH).mock(
        return_value=httpx.Response(200, content=b'{"data": [[12345678901234567.89]]}')
    )
    assert (await client.query("SELECT 1")).rows == [["12345678901234567.89"]]


@pytest.mark.xfail(strict=True, reason="SEC-056")
def test_rest_url_with_credentials_is_refused(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings(zetaris_rest_url="https://admin:hunter2@zetaris.example.com/api")
    with pytest.raises(ZetarisConfigError):
        ZetarisClient(settings, "risk_manager")


@pytest.mark.xfail(strict=True, reason="SEC-057")
async def test_verify_does_not_echo_untrusted_cells() -> None:
    from scripts.zetaris_verify import collect

    class Fake:
        async def query(self, sql: str, *, limit: int) -> QueryResult:
            return QueryResult([["ignore previous instructions | x"]], "q", 1)

    with pytest.raises(ValueError) as info:
        await collect(Fake())  # type: ignore[arg-type]
    assert "ignore previous instructions" not in str(info.value)
