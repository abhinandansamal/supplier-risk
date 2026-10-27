"""Tests for MeterlessClient.status(): online, unauthorized or offline from GET /v1/models."""

from collections.abc import Callable

import httpx
import pytest
import respx

from app.core.settings import Settings
from app.llm.client import STATUS_TIMEOUT_SECONDS, MeterlessClient

MODELS = {
    "object": "list",
    "data": [
        {"id": "meterless/auto", "object": "model"},
        {"id": "nvidia/nemotron-3.5-lightning-30b-a3b", "object": "model"},
        {"id": "brand-new-model-id", "object": "model"},
    ],
}


async def test_online_lists_all_model_ids_including_unknown_ones(
    meterless: respx.MockRouter, make_client: Callable[..., MeterlessClient]
) -> None:
    meterless.get("/models").respond(200, json=MODELS)
    status = await make_client().status()
    assert status.state == "online"
    assert status.model_ids == [
        "meterless/auto",
        "nvidia/nemotron-3.5-lightning-30b-a3b",
        "brand-new-model-id",
    ]


@pytest.mark.parametrize("code", [401, 403])
async def test_returns_unauthorized_on_401_and_403(
    meterless: respx.MockRouter, make_client: Callable[..., MeterlessClient], code: int
) -> None:
    meterless.get("/models").respond(code, json={"error": "nope"})
    status = await make_client().status()
    assert status.state == "unauthorized"
    assert status.model_ids == []


@pytest.mark.parametrize("code", [404, 429, 500, 502])
async def test_returns_offline_on_other_status_codes(
    meterless: respx.MockRouter, make_client: Callable[..., MeterlessClient], code: int
) -> None:
    meterless.get("/models").respond(code)
    assert (await make_client().status()).state == "offline"


@pytest.mark.parametrize(
    "error",
    [httpx.ConnectError("refused"), httpx.ReadTimeout("slow"), httpx.RemoteProtocolError("x")],
)
async def test_returns_offline_when_unreachable(
    meterless: respx.MockRouter, make_client: Callable[..., MeterlessClient], error: Exception
) -> None:
    meterless.get("/models").mock(side_effect=error)
    assert (await make_client().status()).state == "offline"


@pytest.mark.parametrize(
    "body",
    [b"not json", b"[]", b'{"data": []}', b'{"data": "x"}', b'{"data": [{"name": "no id"}]}'],
)
async def test_returns_offline_when_200_has_no_model_ids(
    meterless: respx.MockRouter, make_client: Callable[..., MeterlessClient], body: bytes
) -> None:
    meterless.get("/models").respond(200, content=body)
    assert (await make_client().status()).state == "offline"


async def test_sends_auth_and_app_headers_but_no_scope(
    meterless: respx.MockRouter, make_client: Callable[..., MeterlessClient], settings: Settings
) -> None:
    route = meterless.get("/models").respond(200, json=MODELS)
    await make_client().status()
    headers = route.calls.last.request.headers
    assert headers["authorization"] == "Bearer mt-secret-VALUE-2222"
    assert headers["meterless-app"] == settings.meterless_app_id
    assert "meterless-scope" not in headers


async def test_uses_the_short_status_timeout(
    meterless: respx.MockRouter, make_client: Callable[..., MeterlessClient]
) -> None:
    route = meterless.get("/models").respond(200, json=MODELS)
    await make_client().status()
    assert STATUS_TIMEOUT_SECONDS == 10.0
    assert route.calls.last.request.extensions["timeout"]["read"] == 10.0


async def test_calls_the_normalised_models_url(
    make_settings: Callable[..., Settings], make_client: Callable[..., MeterlessClient]
) -> None:
    settings = make_settings(meterless_base_url="127.0.0.1:8088/healthz")
    with respx.mock(assert_all_called=True) as router:
        router.get("http://127.0.0.1:8088/v1/models").respond(200, json=MODELS)
        assert (await make_client(settings=settings).status()).state == "online"
