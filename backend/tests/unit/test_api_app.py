"""Tests for the FastAPI app factory: request id, access log, health, ready, docs, body limit."""

import asyncio
import logging
import re
from collections.abc import Callable, Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.app import create_app
from app.api.health import register_readiness_check
from app.core.settings import Settings

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def build(settings: Settings, **kwargs: float) -> FastAPI:
    application = create_app(settings, **kwargs)

    @application.post("/echo")
    async def echo(payload: dict[str, str]) -> dict[str, int]:
        return {"keys": len(payload)}

    return application


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return build(settings)


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


# --- request id -------------------------------------------------------------------------------


def test_accepts_and_returns_a_valid_request_id(client: TestClient) -> None:
    response = client.get("/health", headers={"X-Request-ID": "req-abc_123.4"})
    assert response.headers["X-Request-ID"] == "req-abc_123.4"


def test_creates_a_request_id_when_missing(client: TestClient) -> None:
    assert UUID_RE.match(client.get("/health").headers["X-Request-ID"])


@pytest.mark.parametrize("bad", ["x" * 65, "has space", "line\\nbreak", "semi;colon", "<script>"])
def test_replaces_invalid_request_ids(client: TestClient, bad: str) -> None:
    response = client.get("/health", headers={"X-Request-ID": bad})
    assert UUID_RE.match(response.headers["X-Request-ID"])


def test_request_ids_differ_between_requests(client: TestClient) -> None:
    first = client.get("/health").headers["X-Request-ID"]
    assert first != client.get("/health").headers["X-Request-ID"]


# --- access log -------------------------------------------------------------------------------


def test_logs_one_info_line_per_request(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        client.get("/health?token=querysecret", headers={"X-Request-ID": "req-log-1"})
    lines = [r for r in caplog.records if getattr(r, "event", "") == "http_request"]
    assert len(lines) == 1
    record = lines[0]
    assert record.levelno == logging.INFO
    assert record.method == "GET"  # type: ignore[attr-defined]
    assert record.path == "/health"  # type: ignore[attr-defined]
    assert record.status_code == 200  # type: ignore[attr-defined]
    assert record.duration_ms >= 0  # type: ignore[attr-defined]
    assert "querysecret" not in str(record.__dict__)


def test_access_log_has_request_id_in_json(settings: Settings) -> None:
    import json

    from app.core.logging import setup_logging, teardown_logging

    setup_logging(settings)
    try:
        TestClient(build(settings)).get("/health", headers={"X-Request-ID": "req-json-9"})
        for handler in logging.getLogger().handlers:
            handler.flush()
        lines = (settings.log_dir / "app.jsonl").read_text().splitlines()
    finally:
        teardown_logging()
    records = [json.loads(line) for line in lines]
    access = [r for r in records if r["event"] == "http_request"]
    assert access[-1]["request_id"] == "req-json-9"


def test_logs_status_500_for_unhandled_errors(
    app: FastAPI, caplog: pytest.LogCaptureFixture
) -> None:
    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("x")

    with caplog.at_level(logging.INFO):
        TestClient(app).get("/boom")
    access = [r for r in caplog.records if getattr(r, "event", "") == "http_request"]
    assert access[-1].status_code == 500  # type: ignore[attr-defined]


# --- health and ready -------------------------------------------------------------------------


def test_health_is_always_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_stays_ok_when_a_readiness_check_fails(app: FastAPI) -> None:
    async def failing() -> bool:
        return False

    register_readiness_check(app, "zetaris", failing)
    assert TestClient(app).get("/health").status_code == 200


def test_ready_is_ok_with_no_checks(client: TestClient) -> None:
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_ready_runs_registered_checks(app: FastAPI) -> None:
    calls: list[str] = []

    async def ok() -> bool:
        calls.append("db")
        return True

    register_readiness_check(app, "db", ok)
    response = TestClient(app).get("/ready")
    assert response.status_code == 200
    assert calls == ["db"]
    # app_db is registered by create_app itself.
    assert response.json()["checked"] == ["app_db", "db"]


def test_ready_returns_503_naming_only_failed_checks(app: FastAPI) -> None:
    async def ok() -> bool:
        return True

    async def down() -> bool:
        return False

    register_readiness_check(app, "db", ok)
    register_readiness_check(app, "zetaris", down)
    response = TestClient(app).get("/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "failed": ["zetaris"], "cache": "off"}


def test_ready_treats_exception_as_failed_without_leaking_it(
    app: FastAPI, caplog: pytest.LogCaptureFixture
) -> None:
    async def explode() -> bool:
        raise ConnectionError("postgresql://app:pw@host/db refused")

    register_readiness_check(app, "db", explode)
    with caplog.at_level(logging.WARNING):
        response = TestClient(app).get("/ready")
    assert response.status_code == 503
    assert "refused" not in response.text
    assert any(getattr(r, "event", "") == "readiness_check_failed" for r in caplog.records)


def test_ready_treats_a_slow_check_as_failed(settings: Settings) -> None:
    application = build(settings, readiness_timeout=0.05)

    async def slow() -> bool:
        await asyncio.sleep(1)
        return True

    register_readiness_check(application, "context_service", slow)
    response = TestClient(application).get("/ready")
    assert response.status_code == 503
    assert response.json()["failed"] == ["context_service"]


# --- docs -------------------------------------------------------------------------------------


def test_docs_available_in_dev(make_settings: Callable[..., Settings]) -> None:
    client = TestClient(create_app(make_settings(app_env="dev")))
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200


def test_docs_hidden_in_prod(make_settings: Callable[..., Settings]) -> None:
    client = TestClient(create_app(make_settings(app_env="prod")))
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


# --- body limit -------------------------------------------------------------------------------


def test_small_body_is_accepted(client: TestClient) -> None:
    assert client.post("/echo", json={"a": "b"}).status_code == 200


def test_body_over_16kb_is_rejected_by_content_length(client: TestClient) -> None:
    response = client.post("/echo", json={"a": "x" * (16 * 1024 + 1)})
    assert response.status_code == 413
    body = response.json()
    assert set(body) == {"code", "message", "request_id"}
    assert body["code"] == "payload_too_large"
    assert "X-Request-ID" in response.headers


def test_body_exactly_16kb_is_accepted(client: TestClient) -> None:
    raw = b'{"a":"' + b"x" * (16 * 1024 - 8) + b'"}'
    assert len(raw) == 16 * 1024
    response = client.post("/echo", content=raw, headers={"Content-Type": "application/json"})
    assert response.status_code == 200


def test_chunked_body_over_limit_is_rejected(client: TestClient) -> None:
    def chunks() -> Iterator[bytes]:
        yield b'{"a":"'
        for _ in range(20):
            yield b"x" * 1024
        yield b'"}'

    response = client.post("/echo", content=chunks(), headers={"Content-Type": "application/json"})
    assert response.status_code == 413
    assert response.json()["code"] == "payload_too_large"


def test_invalid_content_length_is_a_bad_request(client: TestClient) -> None:
    response = client.post(
        "/echo",
        content=b"{}",
        headers={"Content-Length": "abc", "Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert response.json()["code"] == "bad_request"
