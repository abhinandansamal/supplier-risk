"""API v1 tests with a fake investigator: login, roles, isolation, queue limits, SSE, proposals."""

import base64
import json
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from tests.fakes.api import INJECTION_NOTE, FakeInvestigator

from app.api.app import create_app
from app.core.settings import Settings
from app.store.db import Store

API = "/api/v1"
FINISHED = {"completed", "incomplete", "escalated", "rejected", "failed"}
QUESTION = "Why is Microchip Technology Incorporated (S0003) risky?"


class Api:
    """A test client plus the fakes behind it."""

    def __init__(
        self, client: TestClient, settings: Settings, store: Store, investigator: FakeInvestigator
    ) -> None:
        """Bundle the parts."""
        self.client = client
        self.settings = settings
        self.store = store
        self.investigator = investigator

    def login(self, username: str) -> Response:
        """Log in as a demo user (the cookie replaces the previous session)."""
        field = {"buyer": "buyer", "risk_manager": "risk", "director": "director"}[username]
        secret = getattr(self.settings, f"demo_{field}_password")
        return self.client.post(
            f"{API}/auth/login",
            json={"username": username, "password": secret.get_secret_value()},
        )

    def start(self, question: str = QUESTION) -> Response:
        """Start an investigation as the current user."""
        return self.client.post(f"{API}/investigations", json={"question": question})

    def wait(self, investigation_id: str, timeout: float = 5.0) -> dict[str, Any]:
        """Poll until the investigation is finished and return it."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            body = self.client.get(f"{API}/investigations/{investigation_id}").json()
            if body.get("status") in FINISHED:
                return dict(body)
            time.sleep(0.02)
        raise AssertionError("investigation did not finish")


@pytest.fixture
def make_api(
    make_settings: Callable[..., Settings], tmp_path: Path
) -> Iterator[Callable[..., Api]]:
    clients: list[TestClient] = []

    def factory(investigator: FakeInvestigator | None = None, **overrides: Any) -> Api:  # noqa: ANN401
        settings = make_settings(trace_dir=tmp_path / "runs", **overrides)
        store = Store("sqlite://")
        fake = investigator or FakeInvestigator()
        client = TestClient(create_app(settings, store=store, investigator=fake))
        client.__enter__()
        clients.append(client)
        return Api(client, settings, store, fake)

    yield factory
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def api(make_api: Callable[..., Api]) -> Api:
    return make_api()


def _finished_as(api: Api, username: str) -> dict[str, Any]:
    api.login(username)
    return api.wait(api.start().json()["id"])


# --- login -----------------------------------------------------------------------------------


def test_login_sets_an_http_only_strict_cookie_and_no_token_in_the_body(api: Api) -> None:
    response = api.login("buyer")
    assert response.status_code == 200
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "max-age=1800" in cookie
    assert response.json() == {
        "username": "buyer",
        "role": "buyer",
        "category": "electronics",
        "expires_at": response.json()["expires_at"],
    }
    token = cookie.split("=", 1)[1].split(";")[0]
    assert token not in response.text.lower()


@pytest.mark.parametrize("username", ["buyer", "nobody"])
def test_bad_credentials_get_one_generic_401(api: Api, username: str) -> None:
    response = api.client.post(
        f"{API}/auth/login", json={"username": username, "password": "wrong-password"}
    )
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"
    assert "set-cookie" not in response.headers


def test_repeated_failures_lock_the_login(api: Api) -> None:
    for _ in range(5):
        api.client.post(f"{API}/auth/login", json={"username": "buyer", "password": "nope-nope"})
    assert api.login("buyer").status_code == 429


def test_login_body_is_strict(api: Api) -> None:
    response = api.client.post(
        f"{API}/auth/login", json={"username": "buyer", "password": "x", "role": "director"}
    )
    assert response.status_code == 422


def test_me_and_logout(api: Api) -> None:
    api.login("risk_manager")
    assert api.client.get(f"{API}/auth/me").json()["role"] == "risk_manager"
    stolen = api.client.cookies.get("sr_session")
    assert api.client.post(f"{API}/auth/logout").status_code == 204
    assert api.client.get(f"{API}/auth/me").status_code == 401
    # Security: the old token is revoked, not only deleted from the browser.
    api.client.cookies.set("sr_session", stolen or "")
    assert api.client.get(f"{API}/auth/me").status_code == 401


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/investigations"),
        ("POST", "/investigations"),
        ("GET", "/investigations/inv-0123456789abcdef"),
        ("GET", "/investigations/inv-0123456789abcdef/events"),
        ("DELETE", "/investigations/inv-0123456789abcdef"),
        ("GET", "/proposals"),
        ("POST", "/proposals/act-1/approve"),
    ],
)
def test_every_api_route_needs_a_session(api: Api, method: str, path: str) -> None:
    response = api.client.request(method, API + path, json={"question": "x"})
    assert response.status_code == 401
    assert set(response.json()) == {"code", "message", "request_id"}


def _unsigned_token(claims: dict[str, Any]) -> str:
    """Return an alg=none JWT built at runtime (no token literal in the repo)."""
    parts = [{"alg": "none"}, claims]
    encoded = [base64.urlsafe_b64encode(json.dumps(p).encode()).rstrip(b"=") for p in parts]
    return b".".join(encoded).decode() + "."


def test_a_forged_cookie_is_refused(api: Api) -> None:
    api.client.cookies.set("sr_session", _unsigned_token({"sub": "director"}))
    assert api.client.get(f"{API}/investigations").status_code == 401


def test_cross_site_writes_are_refused(api: Api) -> None:
    api.login("buyer")
    response = api.client.post(
        f"{API}/investigations",
        json={"question": QUESTION},
        headers={"Origin": "http://evil.example"},
    )
    assert response.status_code == 403


# --- investigations --------------------------------------------------------------------------


def test_start_returns_202_with_id_and_queue_position(api: Api) -> None:
    api.login("buyer")
    response = api.start()
    assert response.status_code == 202
    body = response.json()
    assert body["id"].startswith("inv-") and body["queue_position"] >= 0
    finished = api.wait(body["id"])
    assert finished["status"] == "completed"
    user, question, investigation_id = api.investigator.calls[0]
    assert (user.user_id, user.role, user.category) == ("buyer", "buyer", "electronics")
    assert question == QUESTION and investigation_id == body["id"]


def test_the_role_comes_from_the_session_never_from_the_body(api: Api) -> None:
    api.login("buyer")
    response = api.client.post(
        f"{API}/investigations", json={"question": QUESTION, "role": "director"}
    )
    assert response.status_code == 422


@pytest.mark.parametrize("question", ["", "x" * 1001])
def test_question_length_is_limited(api: Api, question: str) -> None:
    api.login("buyer")
    assert api.start(question).status_code == 422


def test_result_has_report_score_evidence_and_trace_but_no_raw_rows(api: Api) -> None:
    body = _finished_as(api, "buyer")
    result = body["result"]
    assert result["report"]["claims"][0]["evidence_ids"] == ["ev-000000000001"]
    (evidence,) = result["evidence"]
    assert evidence["zetaris_query_id"] == "q-123" and evidence["facts"]["deliveries"] == 57
    assert "rows" not in evidence
    # Security: ERP free text (the planted note) never reaches the browser.
    assert INJECTION_NOTE not in json.dumps(body)
    assert [p["status"] for p in body["proposals"]] == ["pending", "pending"]


def test_users_see_only_their_own_investigations(api: Api) -> None:
    mine = _finished_as(api, "buyer")
    api.login("risk_manager")
    assert api.client.get(f"{API}/investigations/{mine['id']}").status_code == 404
    assert api.client.get(f"{API}/investigations").json() == {"investigations": []}
    assert api.client.get(f"{API}/investigations/{mine['id']}/events").status_code == 404


def test_director_may_see_all_investigations(api: Api) -> None:
    mine = _finished_as(api, "buyer")
    api.login("director")
    listed = api.client.get(f"{API}/investigations").json()["investigations"]
    assert [i["id"] for i in listed] == [mine["id"]]
    assert api.client.get(f"{API}/investigations/{mine['id']}").status_code == 200


def test_list_shows_only_own_summaries(api: Api) -> None:
    first = _finished_as(api, "buyer")
    (item,) = api.client.get(f"{API}/investigations").json()["investigations"]
    assert item["id"] == first["id"] and item["question"] == QUESTION
    assert "result" not in item


@pytest.mark.parametrize("bad_id", ["inv-../../etc", "x" * 200, "inv-XYZ", "%2e%2e"])
def test_malformed_ids_are_not_found(api: Api, bad_id: str) -> None:
    api.login("buyer")
    assert api.client.get(f"{API}/investigations/{bad_id}").status_code in (404, 422)


def test_a_crashing_investigation_fails_without_leaking_details(
    make_api: Callable[..., Api],
) -> None:
    api = make_api(FakeInvestigator(fail=True))
    body = _finished_as(api, "buyer")
    assert body["status"] == "failed"
    assert "secret internal detail" not in json.dumps(body)


# --- limits ----------------------------------------------------------------------------------


def test_per_user_hourly_limit_gives_a_friendly_429(make_api: Callable[..., Api]) -> None:
    api = make_api(investigations_per_user_per_hour=2)
    api.login("buyer")
    assert [api.start().status_code for _ in range(2)] == [202, 202]
    response = api.start()
    assert response.status_code == 429
    assert response.json()["code"] == "rate_limited"
    assert "hour" in response.json()["message"]
    api.login("risk_manager")
    assert api.start().status_code == 202


def test_deleting_does_not_reset_the_hourly_limit(make_api: Callable[..., Api]) -> None:
    api = make_api(investigations_per_user_per_hour=1)
    api.login("buyer")
    first = api.start().json()["id"]
    api.wait(first)
    api.client.delete(f"{API}/investigations/{first}")
    assert api.start().status_code == 429


def test_a_full_queue_gives_429(make_api: Callable[..., Api]) -> None:
    gate = threading.Event()
    api = make_api(FakeInvestigator(gate=gate), queue_max_length=1)
    try:
        api.login("risk_manager")
        codes = [api.start() for _ in range(4)]
        refused = [r for r in codes if r.status_code == 429]
        assert refused and refused[0].json()["code"] == "queue_full"
        assert all(r.status_code in (202, 429) for r in codes)
    finally:
        gate.set()


def test_queue_position_counts_waiting_investigations(make_api: Callable[..., Api]) -> None:
    gate = threading.Event()
    api = make_api(FakeInvestigator(gate=gate))
    try:
        api.login("risk_manager")
        first = api.start().json()
        deadline = time.monotonic() + 5
        while api.client.get(f"{API}/investigations/{first['id']}").json()["status"] != "running":
            assert time.monotonic() < deadline
            time.sleep(0.02)
        second, third = api.start().json(), api.start().json()
        assert (second["queue_position"], third["queue_position"]) == (1, 2)
        assert api.client.get(f"{API}/investigations/{third['id']}").json()["queue_position"] == 2
    finally:
        gate.set()


# --- live trace (SSE) ------------------------------------------------------------------------


def _sse(api: Api, investigation_id: str) -> list[tuple[str, Any]]:
    events: list[tuple[str, Any]] = []
    kind = "message"
    with api.client.stream("GET", f"{API}/investigations/{investigation_id}/events") as stream:
        assert stream.headers["content-type"].startswith("text/event-stream")
        for line in stream.iter_lines():
            if line.startswith("event: "):
                kind = line.removeprefix("event: ")
            elif line.startswith("data: "):
                events.append((kind, json.loads(line.removeprefix("data: "))))
                if kind == "done":
                    break
    return events


def test_events_stream_trace_events_live_and_end_with_done(make_api: Callable[..., Api]) -> None:
    gate = threading.Event()
    api = make_api(FakeInvestigator(gate=gate))
    api.login("buyer")
    investigation_id = api.start().json()["id"]
    threading.Timer(0.3, gate.set).start()
    events = _sse(api, investigation_id)
    kinds = [k for k, _ in events]
    assert kinds[-1] == "done" and events[-1][1]["status"] == "completed"
    trace = [d for k, d in events if k == "trace"]
    assert [t["agent"] for t in trace] == ["guard", "planner", "research"]
    assert trace[1]["receipt_id"] == "rcpt-1" and trace[2]["evidence_ids"] == ["ev-000000000001"]


def test_events_of_a_finished_investigation_replay_the_stored_trace(api: Api) -> None:
    body = _finished_as(api, "buyer")
    events = _sse(api, body["id"])
    assert [k for k, _ in events].count("trace") == 3
    assert events[-1] == ("done", {"status": "completed"})


# --- proposals -------------------------------------------------------------------------------


def _proposal(body: dict[str, Any], impact: str) -> str:
    return str(next(p["action_id"] for p in body["proposals"] if p["impact"] == impact))


def test_buyer_approves_a_low_impact_proposal_once(api: Api) -> None:
    body = _finished_as(api, "buyer")
    action = _proposal(body, "low")
    response = api.client.post(f"{API}/proposals/{action}/approve", json={"comment": "ok"})
    assert response.status_code == 200
    assert response.json()["status"] == "approved" and response.json()["decided_by"] == "buyer"
    again = api.client.post(f"{API}/proposals/{action}/reject", json={})
    assert again.status_code == 409


def test_buyer_cannot_decide_a_high_impact_proposal(api: Api) -> None:
    body = _finished_as(api, "buyer")
    response = api.client.post(f"{API}/proposals/{_proposal(body, 'high')}/approve", json={})
    assert response.status_code == 403


def test_risk_manager_decides_any_impact_and_sees_the_inbox(api: Api) -> None:
    body = _finished_as(api, "buyer")
    api.login("risk_manager")
    inbox = api.client.get(f"{API}/proposals").json()["proposals"]
    assert {p["impact"] for p in inbox} == {"low", "high"}
    action = _proposal(body, "high")
    response = api.client.post(f"{API}/proposals/{action}/reject", json={"comment": "no"})
    assert response.status_code == 200 and response.json()["status"] == "rejected"


def test_director_cannot_decide(api: Api) -> None:
    body = _finished_as(api, "buyer")
    api.login("director")
    response = api.client.post(f"{API}/proposals/{_proposal(body, 'low')}/approve", json={})
    assert response.status_code == 403


def test_buyer_cannot_decide_another_users_proposal(api: Api) -> None:
    body = _finished_as(api, "risk_manager")
    api.login("buyer")
    response = api.client.post(f"{API}/proposals/{_proposal(body, 'low')}/approve", json={})
    assert response.status_code == 404
    assert api.client.get(f"{API}/proposals").json() == {"proposals": []}


def test_approval_only_records_the_decision_and_never_executes(api: Api) -> None:
    body = _finished_as(api, "buyer")
    before = api.store.counts()
    calls = len(api.investigator.calls)
    api.client.post(f"{API}/proposals/{_proposal(body, 'low')}/approve", json={})
    assert api.store.counts() == {**before, "decisions": before["decisions"] + 1}
    assert len(api.investigator.calls) == calls
    shown = api.client.get(f"{API}/investigations/{body['id']}").json()
    statuses = {p["impact"]: p["status"] for p in shown["proposals"]}
    assert statuses == {"low": "approved", "high": "pending"}
    assert shown["status"] == "completed"


def test_decision_comment_is_bounded(api: Api) -> None:
    body = _finished_as(api, "buyer")
    response = api.client.post(
        f"{API}/proposals/{_proposal(body, 'low')}/approve", json={"comment": "x" * 501}
    )
    assert response.status_code == 422


# --- delete (GDPR erasure) -------------------------------------------------------------------


def test_delete_removes_the_investigation_results_proposals_and_trace(api: Api) -> None:
    body = _finished_as(api, "buyer")
    api.client.post(f"{API}/proposals/{_proposal(body, 'low')}/approve", json={})
    trace_file = api.settings.trace_dir / f"{body['id']}.jsonl"
    trace_file.parent.mkdir(parents=True, exist_ok=True)
    trace_file.write_text("{}\n")
    assert api.client.delete(f"{API}/investigations/{body['id']}").status_code == 204
    assert api.client.get(f"{API}/investigations/{body['id']}").status_code == 404
    assert api.store.counts() == {"investigations": 0, "results": 0, "proposals": 0, "decisions": 0}
    assert not trace_file.exists()


def test_only_the_owner_may_delete(api: Api) -> None:
    body = _finished_as(api, "buyer")
    for other in ("risk_manager", "director"):
        api.login(other)
        assert api.client.delete(f"{API}/investigations/{body['id']}").status_code == 404
    assert api.store.counts()["investigations"] == 1


def test_deleting_a_queued_investigation_means_it_never_runs(make_api: Callable[..., Api]) -> None:
    gate = threading.Event()
    api = make_api(FakeInvestigator(gate=gate))
    try:
        api.login("buyer")
        api.start()
        queued = api.start().json()["id"]
        assert api.client.delete(f"{API}/investigations/{queued}").status_code == 204
    finally:
        gate.set()
    time.sleep(0.3)
    assert queued not in [c[2] for c in api.investigator.calls]
    assert api.store.get_investigation(queued) is None


# --- surface, headers, readiness -------------------------------------------------------------


def test_only_the_expected_api_routes_exist(api: Api) -> None:
    """SEC-084: no route reads the World Model (case boards, entities) directly."""
    schema = api.client.app.openapi()  # type: ignore[attr-defined]
    paths = {
        (method.upper(), path)
        for path, operations in schema["paths"].items()
        if path.startswith(API)
        for method in operations
    }
    assert paths == {
        ("POST", f"{API}/auth/login"),
        ("POST", f"{API}/auth/logout"),
        ("GET", f"{API}/auth/me"),
        ("POST", f"{API}/investigations"),
        ("GET", f"{API}/investigations"),
        ("GET", f"{API}/investigations/{{investigation_id}}"),
        ("DELETE", f"{API}/investigations/{{investigation_id}}"),
        ("GET", f"{API}/investigations/{{investigation_id}}/events"),
        ("GET", f"{API}/proposals"),
        ("POST", f"{API}/proposals/{{action_id}}/approve"),
        ("POST", f"{API}/proposals/{{action_id}}/reject"),
    }


@pytest.mark.parametrize("path", ["/", "/health", f"{API}/auth/me"])
def test_security_headers_on_every_response(api: Api, path: str) -> None:
    headers = api.client.get(path).headers
    csp = headers["content-security-policy"]
    assert "default-src 'none'" in csp and "script-src 'self'" in csp
    assert "unsafe-inline" not in csp and "frame-ancestors 'none'" in csp
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "no-referrer"


def test_api_responses_are_not_cached(api: Api) -> None:
    api.login("buyer")
    assert api.client.get(f"{API}/investigations").headers["cache-control"] == "no-store"


def test_ui_page_and_assets_are_served(api: Api) -> None:
    page = api.client.get("/")
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    assert api.client.get("/static/app.js").status_code == 200
    assert api.client.get("/static/app.css").status_code == 200


def test_ready_reports_the_cache_state(api: Api) -> None:
    body = api.client.get("/ready").json()
    assert body["cache"] == "off"
    assert "app_db" in body["checked"]
