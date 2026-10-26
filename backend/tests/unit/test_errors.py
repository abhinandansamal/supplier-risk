"""Tests for the one error model and the exception handlers."""

import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response
from pydantic import BaseModel, ConfigDict

from app.api.app import create_app
from app.core.errors import AppError, ErrorResponse
from app.core.settings import Settings


class Item(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    application = create_app(settings)

    @application.get("/boom")
    async def boom() -> None:
        raise RuntimeError("upstream body: Bearer leaktoken123 /Users/me/secret.py")

    @application.get("/app-error")
    async def app_error() -> None:
        raise AppError(status_code=409, code="conflict", message="Already approved.")

    @application.post("/items")
    async def items(item: Item) -> dict[str, str]:
        return {"question": item.question}

    return application


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def assert_error_model(response: Response, code: str, status: int) -> dict[str, str]:
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"code", "message", "request_id"}
    assert body["code"] == code
    assert body["request_id"] == response.headers["X-Request-ID"]
    ErrorResponse.model_validate(body)
    return dict(body)


def test_unexpected_error_returns_generic_500(client: TestClient) -> None:
    response = client.get("/boom")
    body = assert_error_model(response, "internal_error", 500)
    text = response.text
    assert "leaktoken123" not in text
    assert "Traceback" not in text
    assert "secret.py" not in text
    assert "RuntimeError" not in text
    assert body["message"]


def test_unexpected_error_is_logged_with_exception(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR):
        client.get("/boom", headers={"X-Request-ID": "req-boom-1"})
    records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert records
    assert records[0].exc_info is not None
    assert getattr(records[0], "event", None) == "unhandled_exception"
    assert getattr(records[0], "request_id", None) in (None, "req-boom-1")


def test_app_error_uses_its_status_code_and_message(client: TestClient) -> None:
    body = assert_error_model(client.get("/app-error"), "conflict", 409)
    assert body["message"] == "Already approved."


def test_unknown_route_uses_error_model(client: TestClient) -> None:
    assert_error_model(client.get("/nope"), "not_found", 404)


def test_wrong_method_uses_error_model(client: TestClient) -> None:
    assert_error_model(client.post("/health"), "method_not_allowed", 405)


def test_validation_error_uses_model_without_echoing_input(client: TestClient) -> None:
    response = client.post("/items", json={"question": "hi", "role": "director-SECRETVALUE"})
    body = assert_error_model(response, "validation_error", 422)
    assert "SECRETVALUE" not in response.text
    assert "role" in body["message"]


def test_http_exception_detail_is_not_echoed(settings: Settings) -> None:
    from fastapi import HTTPException

    application = create_app(settings)

    @application.get("/teapot")
    async def teapot() -> None:
        raise HTTPException(status_code=403, detail="db password is hunter2")

    response = TestClient(application).get("/teapot")
    assert_error_model(response, "forbidden", 403)
    assert "hunter2" not in response.text
