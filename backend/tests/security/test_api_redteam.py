"""Red-team tests for API v1: login, sessions, queue, SSE, proposals, erasure, UI (SEC-100+)."""

import asyncio
import contextlib
import json
import logging
import re
import time
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from tests.fakes.agent import Investigate
from tests.fakes.api import FakeInvestigator
from tests.fakes.board import FakeBoardClient
from tests.fakes.llm import FakeLLM
from tests.fakes.zetaris import TODAY, FakeZetaris, view_rows

from app.agent.graph import Investigator
from app.api.app import create_app
from app.api.investigations import _sse
from app.api.web import STATIC_DIR
from app.core.settings import Settings
from app.security import auth as auth_module
from app.security.auth import AUDIENCE, ISSUER, InvalidSession, SessionTokens
from app.services.investigations import InvestigationService, InvestigatorPort
from app.store.db import Store
from app.tools.cache import SnapshotCache

API = "/api/v1"
FINISHED = {"completed", "incomplete", "escalated", "rejected", "failed"}
QUESTION = "Why is Microchip Technology Incorporated (S0003) risky?"
JWT_SECRET = "jwt-secret-VALUE-8888-0123456789abcdef"
PASSWORDS = {
    "buyer": "buyer-pass-VALUE-9991",
    "risk_manager": "risk-pass-VALUE-9992",
    "director": "director-pass-VALUE-9993",
}
SECRETS: dict[str, Any] = {
    "jwt_secret": JWT_SECRET,
    "demo_buyer_password": PASSWORDS["buyer"],
    "demo_risk_password": PASSWORDS["risk_manager"],
    "demo_director_password": PASSWORDS["director"],
}


# --- fixtures --------------------------------------------------------------------------------


class Api:
    """A synchronous test client plus the store behind it."""

    def __init__(self, client: TestClient, store: Store) -> None:
        """Bundle the parts."""
        self.client = client
        self.store = store

    def login(self, username: str, password: str | None = None) -> httpx.Response:
        """Log in (the cookie replaces the previous session)."""
        return self.client.post(
            f"{API}/auth/login",
            json={"username": username, "password": password or PASSWORDS[username]},
        )

    def start(self, question: str = QUESTION) -> str:
        """Start an investigation and return its id."""
        response = self.client.post(f"{API}/investigations", json={"question": question})
        assert response.status_code == 202, response.text
        return str(response.json()["id"])

    def wait(self, investigation_id: str, timeout: float = 5.0) -> dict[str, Any]:
        """Poll until finished; return the last body either way."""
        deadline = time.monotonic() + timeout
        body: dict[str, Any] = {}
        while time.monotonic() < deadline:
            body = self.client.get(f"{API}/investigations/{investigation_id}").json()
            if body.get("status") in FINISHED:
                break
            time.sleep(0.02)
        return body


@pytest.fixture
def make_api(
    make_settings: Callable[..., Settings], tmp_path: Path
) -> Iterator[Callable[..., Api]]:
    clients: list[TestClient] = []

    def factory(investigator: InvestigatorPort | None = None, **overrides: Any) -> Api:  # noqa: ANN401
        settings = make_settings(trace_dir=tmp_path / "runs", **{**SECRETS, **overrides})
        store = Store("sqlite://")
        client = TestClient(
            create_app(settings, store=store, investigator=investigator or FakeInvestigator())
        )
        client.__enter__()
        clients.append(client)
        return Api(client, store)

    yield factory
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def api(make_api: Callable[..., Api]) -> Api:
    return make_api()


class AsyncApi:
    """The app driven in the test's own event loop (worker started by hand)."""

    def __init__(self, app: FastAPI, store: Store, service: InvestigationService) -> None:
        """Bundle the parts."""
        self.app = app
        self.store = store
        self.service = service

    def client(self, ip: str = "127.0.0.1") -> httpx.AsyncClient:
        """Return a client that looks like it connects from the given address."""
        transport = httpx.ASGITransport(app=self.app, client=(ip, 50000))
        return httpx.AsyncClient(transport=transport, base_url="http://testserver")

    @staticmethod
    async def login(client: httpx.AsyncClient, username: str) -> httpx.Response:
        """Log in with the right password."""
        return await client.post(
            f"{API}/auth/login", json={"username": username, "password": PASSWORDS[username]}
        )

    @staticmethod
    async def start(client: httpx.AsyncClient, question: str = QUESTION) -> str:
        """Start an investigation and return its id."""
        response = await client.post(f"{API}/investigations", json={"question": question})
        assert response.status_code == 202, response.text
        return str(response.json()["id"])

    @staticmethod
    async def wait(
        client: httpx.AsyncClient, investigation_id: str, timeout: float = 3.0
    ) -> dict[str, Any]:
        """Poll until finished; return the last body either way."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        body: dict[str, Any] = {}
        while loop.time() < deadline:
            body = (await client.get(f"{API}/investigations/{investigation_id}")).json()
            if body.get("status") in FINISHED:
                break
            await asyncio.sleep(0.02)
        return body


@pytest.fixture
async def make_async_api(
    make_settings: Callable[..., Settings], tmp_path: Path
) -> AsyncIterator[Callable[..., AsyncApi]]:
    services: list[InvestigationService] = []

    def factory(
        investigator: InvestigatorPort | None = None,
        store: Store | None = None,
        **overrides: Any,  # noqa: ANN401
    ) -> AsyncApi:
        settings = make_settings(trace_dir=tmp_path / "runs", **{**SECRETS, **overrides})
        store = store or Store("sqlite://")
        app = create_app(settings, store=store, investigator=investigator or FakeInvestigator())
        service: InvestigationService = app.state.investigations
        service.start()
        services.append(service)
        return AsyncApi(app, store, service)

    yield factory
    for service in services:
        await service.stop()


def _wrong_login(api: Api, username: str) -> int:
    response = api.client.post(
        f"{API}/auth/login", json={"username": username, "password": "wrong-password-1"}
    )
    return response.status_code


# --- SEC-100: concurrent wrong logins are not throttled ---------------------------------------


async def test_concurrent_wrong_logins_cannot_exceed_the_failure_limit(
    make_async_api: Callable[..., AsyncApi],
) -> None:
    api = make_async_api()
    async with api.client() as client:
        responses = await asyncio.gather(
            *(
                client.post(
                    f"{API}/auth/login",
                    json={"username": "risk_manager", "password": f"guess-{i:04d}"},
                )
                for i in range(30)
            )
        )
    checked = sum(r.status_code == 401 for r in responses)
    # Every 401 is a password that was really checked; the throttle allows 5 per window.
    assert checked <= 5, f"{checked} passwords checked in one burst"


# --- SEC-111: concurrent correct logins of one account lock it out (found by the load test) --


@pytest.mark.xfail(strict=True, reason="SEC-111")
async def test_concurrent_correct_logins_of_one_account_all_succeed(
    make_async_api: Callable[..., AsyncApi],
) -> None:
    # SEC-100 counts an attempt before the password check, so 6+ logins in flight at once
    # lock the account although every password is right.
    api = make_async_api()
    async with api.client() as client:
        responses = await asyncio.gather(*(api.login(client, "risk_manager") for _ in range(10)))
    assert [r.status_code for r in responses] == [200] * 10


# --- SEC-101: failures for other usernames evict a locked user --------------------------------


def test_failures_for_other_usernames_cannot_unlock_a_locked_user(
    make_api: Callable[..., Api], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The real bound is 10 000; 3 keeps the test fast. Same code path.
    monkeypatch.setattr(auth_module, "_MAX_TRACKED", 3)
    api = make_api()
    for _ in range(5):
        _wrong_login(api, "buyer")
    assert api.login("buyer").status_code == 429
    for junk in ("aaaa", "bbbb", "cccc"):
        _wrong_login(api, junk)
    # The buyer's failures were evicted, so the lock is gone and guessing can go on.
    assert api.login("buyer").status_code == 429


# --- SEC-102: anyone can lock out a known demo user -------------------------------------------


@pytest.mark.xfail(strict=True, reason="SEC-102")
async def test_failed_logins_from_one_client_do_not_lock_out_the_real_user(
    make_async_api: Callable[..., AsyncApi],
) -> None:
    api = make_async_api()
    async with api.client("203.0.113.66") as attacker:
        for i in range(5):
            await attacker.post(
                f"{API}/auth/login", json={"username": "risk_manager", "password": f"x-{i:06d}"}
            )
    async with api.client("10.0.0.7") as victim:
        assert (await api.login(victim, "risk_manager")).status_code == 200


# --- SEC-103: an SSE stream dropped before it starts leaks its slot ---------------------------


async def _drop_before_start(app: FastAPI, path: str, cookie: str) -> None:
    """Call the events route like a client that disconnects as the response starts."""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"testserver"), (b"cookie", f"sr_session={cookie}".encode())],
        "client": ("127.0.0.1", 50001),
        "server": ("testserver", 80),
    }
    messages = iter([{"type": "http.request", "body": b"", "more_body": False}])

    async def receive() -> dict[str, Any]:
        return next(messages, {"type": "http.disconnect"})

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            raise OSError("client went away")

    with contextlib.suppress(Exception):
        await app(scope, receive, send)


class KeepRecords(logging.Handler):
    """Hold log records (with exc_info) like a batching log exporter does until it flushes."""

    def __init__(self) -> None:
        """Start empty."""
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        """Keep the record."""
        self.records.append(record)


@pytest.fixture
def kept_records() -> Iterator[KeepRecords]:
    handler = KeepRecords()
    middleware_logger = logging.getLogger("app.api.middleware")
    middleware_logger.addHandler(handler)
    yield handler
    middleware_logger.removeHandler(handler)


async def test_a_stream_dropped_before_it_starts_frees_its_slot(
    make_async_api: Callable[..., AsyncApi], kept_records: KeepRecords
) -> None:
    # The slot is released only when its entered @contextmanager generator is garbage
    # collected; a kept traceback (exporter, caplog, Sentry, a reference cycle) keeps it.
    api = make_async_api()
    async with api.client() as client:
        await api.login(client, "buyer")
        investigation_id = await api.start(client)
        assert (await api.wait(client, investigation_id))["status"] == "completed"
        cookie = client.cookies.get("sr_session")
        assert cookie
        path = f"{API}/investigations/{investigation_id}/events"
        for _ in range(3):
            await _drop_before_start(api.app, path, cookie)
        # Three dead connections hold all three slots of the user until a restart.
        response = await client.get(path)
    assert response.status_code == 200, response.text


# --- SEC-104: a store error after the run leaves the investigation running forever ------------


class SaveFailsStore(Store):
    """A store whose result write fails (for example "database is locked")."""

    def save_result(self, investigation_id: str, **kwargs: Any) -> bool:  # noqa: ANN401
        """Fail like SQLite under write contention."""
        raise OperationalError("UPDATE investigations", {}, Exception("database is locked"))


async def test_a_store_error_after_the_run_still_finishes_the_investigation(
    make_async_api: Callable[..., AsyncApi],
) -> None:
    api = make_async_api(store=SaveFailsStore("sqlite://"))
    async with api.client() as client:
        await api.login(client, "buyer")
        investigation_id = await api.start(client)
        body = await api.wait(client, investigation_id, timeout=1.5)
    # Today: status stays "running", the SSE channel never sends "done".
    assert body["status"] == "failed"
    assert not api.service.hub.is_open(investigation_id)


# --- SEC-105: the owner's delete erases another user's approval -------------------------------


@pytest.mark.xfail(strict=True, reason="SEC-105")
def test_deleting_an_investigation_keeps_the_record_of_another_users_approval(api: Api) -> None:
    api.login("buyer")
    investigation_id = api.start()
    body = api.wait(investigation_id)
    high = next(p for p in body["proposals"] if p["impact"] == "high")
    api.login("risk_manager")
    approved = api.client.post(f"{API}/proposals/{high['action_id']}/approve", json={})
    assert approved.status_code == 200
    api.login("buyer")
    assert api.client.delete(f"{API}/investigations/{investigation_id}").status_code == 204
    # Who approved what must survive (append-only audit log); today nothing is left.
    assert api.store.counts()["decisions"] == 1


# --- SEC-106: GDPR delete leaves the World Model case board ----------------------------------


@pytest.mark.xfail(strict=True, reason="SEC-106")
async def test_gdpr_delete_also_erases_the_case_board(
    make_async_api: Callable[..., AsyncApi],
    make_settings: Callable[..., Settings],
    snapshot_cache: SnapshotCache,
    tmp_path: Path,
) -> None:
    board = FakeBoardClient()
    investigator = Investigator(
        make_settings(),
        llm=FakeLLM(),
        cache=snapshot_cache,
        board_client=board,
        today=TODAY,
        trace_dir=tmp_path / "runs",
    )
    api = make_async_api(investigator)
    async with api.client() as client:
        await api.login(client, "risk_manager")
        investigation_id = await api.start(client, "Why is Microchip (S0003) risky?")
        body = await api.wait(client, investigation_id, timeout=15)
        assert body["status"] in FINISHED
        assert board.cases.get(investigation_id), "precondition: the run wrote to the board"
        deleted = await client.delete(f"{API}/investigations/{investigation_id}")
        assert deleted.status_code == 204
    # Claims, evidence summaries and decisions of the erased case are still on the board.
    assert investigation_id not in board.cases


# --- SEC-107: a risk manager decides proposals of investigations they cannot open -------------


@pytest.mark.xfail(strict=True, reason="SEC-107")
def test_a_risk_manager_can_open_the_investigation_behind_an_inbox_proposal(api: Api) -> None:
    api.login("buyer")
    api.wait(api.start())
    api.login("risk_manager")
    inbox = api.client.get(f"{API}/proposals").json()["proposals"]
    assert inbox, "precondition: the buyer's proposals are in the risk manager's inbox"
    behind = api.client.get(f"{API}/investigations/{inbox[0]['investigation_id']}")
    # Today 404: the approver cannot see the claims and evidence the proposal rests on.
    assert behind.status_code == 200


# --- SEC-108: SEC-098 bypass, a name that is part of another supplier's name ------------------


def _rename(fake: FakeZetaris, supplier_id: str, name: str) -> None:
    for view in ("v_suppliers_full", "v_suppliers_buyer"):
        rows = [list(r) for r in view_rows()[view]]
        for row in rows:
            if row[0] == supplier_id:
                row[1] = name
        fake.overrides[view] = rows


@pytest.mark.parametrize("planted", ["Microchip", "Ｍｉｃｒｏｃｈｉｐ"])  # ASCII and full-width
async def test_a_short_planted_name_does_not_capture_a_question_about_another_supplier(
    investigate: Investigate, fake: FakeZetaris, planted: str
) -> None:
    # ERP text is untrusted: S0012 is renamed to the short form of S0003's name.
    _rename(fake, "S0012", planted)
    case = await investigate(question="Why is Microchip risky?")
    # Today the guard silently investigates S0012; it should ask for the supplier id.
    assert "S0012" not in case.result.suppliers
    assert case.result.reason == "ambiguous_supplier"


# --- SEC-109: the session cookie is not Secure outside dev ------------------------------------


@pytest.mark.xfail(strict=True, reason="SEC-109")
def test_the_session_cookie_is_secure_outside_dev(make_api: Callable[..., Api]) -> None:
    api = make_api(app_env="prod")
    cookie = api.login("buyer").headers["set-cookie"].lower()
    assert "; secure" in cookie


# --- SEC-110: logout revocation is lost or skipped --------------------------------------------


@pytest.mark.xfail(strict=True, reason="SEC-110")
def test_a_logged_out_token_stays_revoked_after_a_restart() -> None:
    before = SessionTokens(JWT_SECRET)
    token, _ = before.issue("buyer")
    before.revoke(before.verify(token))
    after_restart = SessionTokens(JWT_SECRET)
    with pytest.raises(InvalidSession):
        after_restart.verify(token)


@pytest.mark.xfail(strict=True, reason="SEC-110")
def test_logout_revokes_even_when_the_revocation_list_is_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(auth_module, "_MAX_TRACKED", 1)
    tokens = SessionTokens(JWT_SECRET)
    first, _ = tokens.issue("buyer")
    tokens.revoke(tokens.verify(first))
    second, _ = tokens.issue("buyer")
    tokens.revoke(tokens.verify(second))  # silently not recorded
    with pytest.raises(InvalidSession):
        tokens.verify(second)


# --- checked and safe ------------------------------------------------------------------------


def _token(claims: dict[str, Any], key: str = JWT_SECRET, algorithm: str = "HS256") -> str:
    base = {
        "sub": "director",
        "iat": int(time.time()),
        "exp": int(time.time()) + 600,
        "jti": "a" * 32,
        "iss": ISSUER,
        "aud": AUDIENCE,
    }
    return jwt.encode({**base, **claims}, key, algorithm=algorithm)


@pytest.mark.parametrize(
    "token",
    [
        _token({}, key="another-secret-of-at-least-32-characters!"),
        _token({"sub": "auditor"}),
        _token({"sub": "admin"}),
        _token({"aud": "someone-else"}),
        _token({"iss": "evil"}),
        _token({"exp": int(time.time()) - 1}),
        _token({"sub": None}),
        jwt.encode({"sub": "director"}, None, algorithm="none"),
        "x" * 5000,
    ],
)
def test_forged_expired_or_unknown_tokens_are_refused(api: Api, token: str) -> None:
    api.client.cookies.set("sr_session", token)
    assert api.client.get(f"{API}/auth/me").status_code == 401
    assert api.client.get(f"{API}/investigations").status_code == 401


def test_a_token_cannot_carry_its_own_role(api: Api) -> None:
    api.client.cookies.set("sr_session", _token({"sub": "buyer", "role": "director"}))
    assert api.client.get(f"{API}/auth/me").json()["role"] == "buyer"


@pytest.mark.parametrize(
    "origin", ["null", "http://evil.example", "http://testserver.evil.example"]
)
def test_cross_site_writes_are_refused_for_any_foreign_origin(api: Api, origin: str) -> None:
    response = api.client.post(
        f"{API}/auth/login",
        json={"username": "buyer", "password": PASSWORDS["buyer"]},
        headers={"Origin": origin},
    )
    assert response.status_code == 403


def test_a_text_plain_form_post_cannot_log_in(api: Api) -> None:
    body = json.dumps({"username": "buyer", "password": PASSWORDS["buyer"]})
    response = api.client.post(
        f"{API}/auth/login", content=body, headers={"Content-Type": "text/plain"}
    )
    assert response.status_code != 200
    assert "set-cookie" not in response.headers


def test_login_logs_hold_no_password_or_token(api: Api, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    _wrong_login(api, "buyer")
    token_cookie = api.login("buyer").cookies.get("sr_session") or ""
    assert token_cookie
    text = caplog.text + " ".join(json.dumps(r.__dict__, default=str) for r in caplog.records)
    assert "wrong-password-1" not in text
    assert PASSWORDS["buyer"] not in text
    assert token_cookie not in text


def test_other_users_cannot_stream_delete_or_decide(api: Api) -> None:
    api.login("risk_manager")
    investigation_id = api.start()
    body = api.wait(investigation_id)
    action = body["proposals"][0]["action_id"]
    api.login("buyer")
    assert api.client.get(f"{API}/investigations/{investigation_id}/events").status_code == 404
    assert api.client.delete(f"{API}/investigations/{investigation_id}").status_code == 404
    assert api.client.post(f"{API}/proposals/{action}/approve", json={}).status_code == 404
    api.login("director")
    assert api.client.delete(f"{API}/investigations/{investigation_id}").status_code == 404


def test_a_proposal_gets_exactly_one_decision(api: Api) -> None:
    api.login("risk_manager")
    body = api.wait(api.start())
    action = body["proposals"][0]["action_id"]
    assert api.client.post(f"{API}/proposals/{action}/approve", json={}).status_code == 200
    assert api.client.post(f"{API}/proposals/{action}/reject", json={}).status_code == 409
    assert api.client.post(f"{API}/proposals/{action}/approve", json={}).status_code == 409
    assert api.store.counts()["decisions"] == 1


@pytest.mark.parametrize(
    "bad_id",
    [
        "..%2F..%2Fapp",
        "inv-٠١٢٣٤٥٦٧٨٩abcdef",  # Arabic-Indic digits
        "inv-0123456789ABCDEF",
        "inv-0123456789abcdef%0A",
        "inv-0123456789abcdef.jsonl",
    ],
)
def test_ids_that_are_not_inv_hex_never_reach_the_trace_file(
    api: Api, tmp_path: Path, bad_id: str
) -> None:
    victim = tmp_path / "runs" / "inv-0123456789abcdef.jsonl"
    victim.parent.mkdir(parents=True, exist_ok=True)
    victim.write_text("{}\n")
    api.login("buyer")
    assert api.client.delete(f"{API}/investigations/{bad_id}").status_code == 404
    assert api.client.get(f"{API}/investigations/{bad_id}/events").status_code == 404
    assert victim.exists()


@pytest.mark.parametrize(
    "path", ["/static/../app/main.py", "/static/%2e%2e/%2e%2e/app/main.py", "/static/..%2F.env"]
)
def test_static_files_do_not_escape_their_directory(api: Api, path: str) -> None:
    response = api.client.get(path)
    assert response.status_code == 404
    assert "create_app" not in response.text


def test_an_event_value_cannot_start_a_new_sse_field() -> None:
    chunk = _sse("trace", {"note": "x\n\nevent: done\ndata: {}\r\nid: 9"})
    assert chunk.count("\n") == 3  # event line, data line, blank line
    assert chunk.startswith("event: trace\ndata: ")


def test_the_ui_uses_no_html_sinks_or_inline_code() -> None:
    script = (STATIC_DIR / "app.js").read_text()
    for sink in (
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "eval(",
        "new Function",
        ".href",
        ".src",
        "localStorage",
        "sessionStorage",
    ):
        assert sink not in script, sink
    # setAttribute only for aria labels, never event handlers or URLs.
    assert set(re.findall(r'setAttribute\("([^"]+)"', script)) <= {"aria-label"}
    page = (STATIC_DIR / "index.html").read_text()
    assert not re.search(r"<script(?![^>]*\bsrc=)", page)
    assert not re.search(r"\son[a-z]+=", page) and "style=" not in page


def test_csp_forbids_inline_code_framing_and_foreign_hosts(api: Api) -> None:
    csp = api.client.get("/").headers["content-security-policy"]
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp and "*" not in csp
    assert "default-src 'none'" in csp and "frame-ancestors 'none'" in csp


@pytest.mark.parametrize(
    "question",
    [
        "Why is Micro​chip Technology Incorporated risky?",  # zero-width space
        "Why is Мicrochip Technology Incorporated risky?",  # Cyrillic M
        "Why is Microchip Technology Incorporatedly risky?",  # glued word
    ],
)
async def test_unicode_tricks_in_a_name_ask_for_the_id_instead_of_guessing(
    investigate: Investigate, question: str
) -> None:
    case = await investigate(question=question)
    assert case.result.suppliers == []
    # Hidden characters are already refused by the detector; the others ask for the id.
    assert case.result.reason in {"no_supplier", "instruction_like"}
