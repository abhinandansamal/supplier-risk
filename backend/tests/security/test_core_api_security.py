"""Red-team tests for feat/core: open findings are xfail(strict), fixed ones run normally."""

import asyncio
import logging
import re
from collections.abc import Callable
from pathlib import Path

import pytest

from app.api.app import create_app
from app.api.middleware import _VALID_ID
from app.core.logging import RedactionFilter, Redactor
from app.core.settings import Settings

REPO = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize(
    "text,leak",
    [
        ("login failed password=hunter2", "hunter2"),
        ("GET http://x/v1?api_key=abc123def", "abc123def"),
        ("connect https://bob:s3cr3t@host/db", "s3cr3t"),
        ("Cookie: session=abc123", "abc123"),
        ("x-api-key: abc123", "abc123"),
        ("Authorization: Digest username=bob, response=deadbeef", "deadbeef"),
        ("Bearer abc*defXYZ", "defXYZ"),
    ],
)
def test_unlisted_secret_shapes_are_redacted(text: str, leak: str) -> None:
    assert leak not in Redactor(["unrelated-secret"]).redact_text(text)


@pytest.mark.xfail(strict=True, reason="SEC-012")
@pytest.mark.parametrize(
    "key", ["secret_key", "private_key", "access_key", "credentials", "passphrase", "pwd", "jwt"]
)
def test_sensitive_looking_extra_keys_are_masked(key: str) -> None:
    assert Redactor([]).redact_value("plain-value-xyz", key=key) == "[REDACTED]"


def test_decoded_url_password_is_redacted(make_settings: Callable[..., Settings]) -> None:
    settings: Settings = make_settings(
        erp_database_url="postgresql://erp:p%40ss%2Fw0rd@localhost/erp"
    )
    red = Redactor(settings.secret_values())
    assert "p@ss/w0rd" not in red.redact_text("driver error for password p@ss/w0rd")


@pytest.mark.xfail(strict=True, reason="SEC-014")
def test_unprintable_extra_does_not_crash_logging_call() -> None:
    class Bad:
        def __str__(self) -> str:
            raise RuntimeError("boom")

    rec = logging.LogRecord("a", 20, "p", 1, "m", (), None)
    rec.thing = Bad()  # type: ignore[attr-defined]
    RedactionFilter(Redactor([])).filter(rec)  # must not raise


@pytest.mark.xfail(strict=True, reason="SEC-015")
def test_request_id_with_trailing_newline_rejected() -> None:
    assert not _VALID_ID.match("abc\n")


@pytest.mark.xfail(strict=True, reason="SEC-016")
async def test_ready_is_not_an_unauthenticated_amplifier(
    make_settings: Callable[..., Settings],
) -> None:
    import httpx

    app = create_app(make_settings())
    calls = 0

    async def check() -> bool:
        nonlocal calls
        calls += 1
        return True

    app.state.readiness.register("zetaris", check)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        await asyncio.gather(*(client.get("/ready") for _ in range(50)))
    assert calls < 10  # results should be cached/coalesced for a few seconds


def test_env_example_defaults_to_prod() -> None:
    text = (REPO / ".env.example").read_text()
    assert re.search(r"^APP_ENV=prod\b", text, re.M)


@pytest.mark.parametrize(
    "text,leak",
    [
        ("Set-Cookie: sid=abc123; HttpOnly", "abc123"),
        ("Cookie: a=1; sid=abc123", "abc123"),
        ("GET /x?a=1&token=abc123&b=2", "abc123"),
        ("GET /x?access_token=abc123", "abc123"),
        ("GET /x?client_secret=abc123&x=1", "abc123"),
        ('{"password": "hunter2"}', "hunter2"),
        ("{'passwd': 'hunter2'}", "hunter2"),
        ("PASSWORD: hunter2", "hunter2"),
        ("postgresql://erp:p%40ss%2Fw0rd@localhost/erp", "p%40ss%2Fw0rd"),
        ("redis://:onlypass@host:6379/0", "onlypass"),
        ("https://user@host/path", "user@"),
    ],
)
def test_more_secret_shapes_are_redacted(text: str, leak: str) -> None:
    assert leak not in Redactor([]).redact_text(text)


def test_redaction_keeps_ordinary_text_readable() -> None:
    text = (
        "request done GET /health status 200 tokens=42 user_count=3 see https://example.com/a?b=1"
    )
    assert Redactor([]).redact_text(text) == text


def test_redaction_is_linear_on_adversarial_input() -> None:
    import time

    start = time.perf_counter()
    Redactor([]).redact_text("password=" * 50_000 + "a:" * 50_000 + "Cookie: " * 50_000)
    assert time.perf_counter() - start < 2


def test_encoded_and_decoded_forms_of_url_password_are_both_secret(
    make_settings: Callable[..., Settings],
) -> None:
    values = make_settings(
        app_database_url="postgresql://app:p%40ss%2Fw0rd@localhost/app"
    ).secret_values()
    assert "p%40ss%2Fw0rd" in values
    assert "p@ss/w0rd" in values
