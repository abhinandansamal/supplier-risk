"""Tests that secrets never reach any log handler or output file."""

import json
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.core.logging import (
    REDACTED,
    RedactionFilter,
    Redactor,
    setup_logging,
    teardown_logging,
)
from app.core.settings import Settings

logger = logging.getLogger("app.test_redaction")

SETTING_SECRETS = [
    "zk-secret-VALUE-1111",
    "mt-secret-VALUE-2222",
    "erp-pass-VALUE-3333",
    "app-pass-VALUE-4444",
    "postgresql://erp:erp-pass-VALUE-3333@localhost:5433/erp",
]


@pytest.fixture
def configured(settings: Settings) -> Iterator[Settings]:
    setup_logging(settings)
    yield settings
    teardown_logging()


def all_output(settings: Settings) -> str:
    for handler in logging.getLogger().handlers:
        handler.flush()
    return "".join(p.read_text() for p in Path(settings.log_dir).glob("*.jsonl"))


@pytest.fixture
def redactor(settings: Settings) -> Redactor:
    return Redactor(settings.secret_values())


def test_redacts_bearer_token(redactor: Redactor) -> None:
    assert "abc.DEF-123" not in redactor.redact_text("sent Bearer abc.DEF-123 upstream")


def test_redacts_authorization_header_forms(redactor: Redactor) -> None:
    for text in (
        "Authorization: Bearer abc123",
        "authorization=Basic dXNlcjpwYXNz",
        "{'Authorization': 'Bearer abc123'}",
        '{"authorization": "abc123"}',
    ):
        redacted = redactor.redact_text(text)
        assert "abc123" not in redacted
        assert "dXNlcjpwYXNz" not in redacted
        assert REDACTED in redacted


def test_redacts_nvapi_keys(redactor: Redactor) -> None:
    assert "nvapi-AbC_123-xyz" not in redactor.redact_text("key nvapi-AbC_123-xyz used")


@pytest.mark.parametrize("secret", SETTING_SECRETS)
def test_redacts_each_setting_secret(redactor: Redactor, secret: str) -> None:
    assert secret not in redactor.redact_text(f"value={secret}!")


def test_redacts_sensitive_dict_keys_but_not_token_counts(redactor: Redactor) -> None:
    result = redactor.redact_value({"token": "plainsecret", "tokens": 42, "api_key": "k"})
    assert result == {"token": REDACTED, "tokens": 42, "api_key": REDACTED}


def test_redacts_nested_structures_and_objects(redactor: Redactor) -> None:
    class Holder:
        def __str__(self) -> str:
            return "holds zk-secret-VALUE-1111"

    result = redactor.redact_value({"a": [("x", Holder())], "b": {"c": "nvapi-secret1"}})
    assert "zk-secret-VALUE-1111" not in json.dumps(result)
    assert "nvapi-secret1" not in json.dumps(result)


def test_every_handler_has_the_redaction_filter(configured: Settings) -> None:
    # pytest adds its own capture handlers to the root logger; only ours are marked.
    handlers = [h for h in logging.getLogger().handlers if getattr(h, "_supplier_risk", False)]
    assert len(handlers) == 3
    for handler in handlers:
        assert any(isinstance(f, RedactionFilter) for f in handler.filters)


@pytest.mark.parametrize("secret", SETTING_SECRETS)
def test_secret_in_message_args_and_extra_never_reaches_files(
    configured: Settings, secret: str
) -> None:
    logger.info(f"in message {secret}")
    logger.info("in args %s", secret)
    logger.warning("in extra", extra={"event": "x", "detail": secret})
    logger.warning("nested", extra={"event": "x", "detail": {"k": [secret]}})
    assert secret not in all_output(configured)


def test_secret_in_exception_text_and_traceback_is_redacted(configured: Settings) -> None:
    try:
        raise RuntimeError("upstream said Bearer topsecret999 and nvapi-leak777")
    except RuntimeError:
        logger.exception("failed", extra={"event": "op_failed"})
    output = all_output(configured)
    assert "topsecret999" not in output
    assert "nvapi-leak777" not in output
    assert "Traceback" in output


def test_secret_in_stack_info_is_redacted(configured: Settings) -> None:
    secret = "mt-secret-VALUE-2222"
    secret_local = secret  # noqa: F841 - present in frame, must not leak via message
    logger.warning("with stack %s", secret, stack_info=True, extra={"event": "x"})
    assert secret not in all_output(configured)
