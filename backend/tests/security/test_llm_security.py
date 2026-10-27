"""Security tests for the Meterless client: no token or upstream body leaks, no redirects."""

from collections.abc import AsyncIterator, Callable, Iterator

import httpx
import pytest
import respx

from app.core.settings import Settings
from app.llm.client import MAX_RECEIPT_FIELD_CHARS, MeterlessClient
from app.llm.errors import LLMError, LLMRequestError, LLMUnauthorized, LLMUnavailable
from app.llm.rate_limiter import AsyncRateLimiter

TOKEN = "mt-secret-VALUE-2222"
BASE = "http://127.0.0.1:8088/v1"
MESSAGES = [{"role": "user", "content": "hi"}]


async def _no_sleep(_: float) -> None:
    return None


@pytest.fixture
def router() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        yield mock


@pytest.fixture
async def client(make_settings: Callable[..., Settings]) -> AsyncIterator[MeterlessClient]:
    meterless = MeterlessClient(
        make_settings(), limiter=AsyncRateLimiter(max_calls=1000), sleep=_no_sleep
    )
    yield meterless
    await meterless.aclose()


def _assert_clean(exc: BaseException, *secrets: str) -> None:
    text = f"{exc!s} {exc!r} {exc.args!r}"
    for secret in secrets:
        assert secret not in text


def test_token_is_not_in_client_repr(client: MeterlessClient) -> None:
    assert TOKEN not in repr(client)
    assert TOKEN not in str(vars(client).get("_settings", ""))


@pytest.mark.parametrize(
    ("code", "error"), [(400, LLMRequestError), (401, LLMUnauthorized), (503, LLMUnavailable)]
)
async def test_errors_do_not_echo_upstream_body_or_token(
    router: respx.MockRouter, client: MeterlessClient, code: int, error: type[LLMError]
) -> None:
    router.post("/chat/completions").respond(code, text=f"UPSTREAM-BODY {TOKEN}")
    with pytest.raises(error) as info:
        await client.chat(MESSAGES)
    _assert_clean(info.value, "UPSTREAM-BODY", TOKEN)
    assert info.value.__cause__ is None or TOKEN not in str(info.value.__cause__)


async def test_does_not_follow_redirects_with_the_token(
    router: respx.MockRouter, client: MeterlessClient
) -> None:
    router.post("/chat/completions").respond(307, headers={"Location": "http://evil.example/x"})
    evil = router.post("http://evil.example/x").respond(200, json={})
    with pytest.raises(LLMRequestError):
        await client.chat(MESSAGES)
    assert evil.call_count == 0


async def test_receipt_fields_from_upstream_are_bounded(
    router: respx.MockRouter, client: MeterlessClient
) -> None:
    long = "r" * 10_000
    router.post("/chat/completions").respond(
        200,
        json={
            "model": long,
            "choices": [{"message": {"content": "ok"}}],
            "meterless": {
                "receipt_id": long,
                "routed_model": long,
                "winning_route": {"provider": long},
            },
        },
    )
    result = await client.chat(MESSAGES)
    for value in (result.receipt_id, result.routed_model, result.provider):
        assert value is not None
        assert len(value) <= MAX_RECEIPT_FIELD_CHARS


async def test_tool_arguments_are_never_evaluated(
    router: respx.MockRouter, client: MeterlessClient, tmp_path: object
) -> None:
    payload = "__import__('pathlib').Path('/tmp/pwned-by-model').touch()"
    router.post("/chat/completions").respond(
        200,
        json={
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {"id": "c", "function": {"name": "run_sql", "arguments": payload}}
                        ],
                    }
                }
            ]
        },
    )
    with pytest.raises(LLMError) as info:
        await client.chat(MESSAGES)
    _assert_clean(info.value, payload)


def test_settings_reject_credentials_in_the_base_url(
    make_settings: Callable[..., Settings],
) -> None:
    with pytest.raises(ValueError):
        make_settings(meterless_base_url="http://user:pass@127.0.0.1:8088/v1")


async def test_wrong_token_is_unauthorized_not_offline(
    router: respx.MockRouter, make_settings: Callable[..., Settings]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization") == f"Bearer {TOKEN}":
            return httpx.Response(200, json={"data": [{"id": "m1"}]})
        return httpx.Response(401)

    router.get("/models").mock(side_effect=handler)
    wrong = MeterlessClient(make_settings(meterless_runtime_token="wrong-token"))
    right = MeterlessClient(make_settings())
    try:
        assert (await wrong.status()).state == "unauthorized"
        assert (await right.status()).state == "online"
    finally:
        await wrong.aclose()
        await right.aclose()
