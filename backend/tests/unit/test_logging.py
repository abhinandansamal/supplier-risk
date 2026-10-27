"""Tests for app.core.logging: JSON fields, handlers, levels and context ids."""

import json
import logging
import logging.handlers
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.core.logging import (
    JsonFormatter,
    investigation_id_var,
    request_id_var,
    setup_logging,
    teardown_logging,
)
from app.core.settings import Settings

logger = logging.getLogger("app.test_logging")


@pytest.fixture
def configured(settings: Settings) -> Iterator[Settings]:
    setup_logging(settings)
    yield settings
    teardown_logging()


def read_lines(settings: Settings, name: str) -> list[dict[str, object]]:
    for handler in logging.getLogger().handlers:
        handler.flush()
    path = Path(settings.log_dir) / name
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def test_creates_log_dir_and_both_files(configured: Settings) -> None:
    logger.info("hello", extra={"event": "greeting"})
    logger.error("boom", extra={"event": "failure"})
    assert read_lines(configured, "app.jsonl")
    assert read_lines(configured, "errors.jsonl")


def test_record_has_required_fields(configured: Settings) -> None:
    logger.info("hello world", extra={"event": "greeting"})
    record = read_lines(configured, "app.jsonl")[-1]
    for key in ("timestamp", "level", "logger", "message", "event", "request_id"):
        assert key in record
    assert record["level"] == "INFO"
    assert record["logger"] == "app.test_logging"
    assert record["message"] == "hello world"
    assert record["event"] == "greeting"
    stamp = datetime.fromisoformat(str(record["timestamp"]))
    assert stamp.utcoffset() == UTC.utcoffset(None)


def test_event_defaults_when_not_given(configured: Settings) -> None:
    logger.info("plain")
    assert read_lines(configured, "app.jsonl")[-1]["event"] == "log"


def test_request_and_investigation_ids_come_from_contextvars(configured: Settings) -> None:
    rid_token = request_id_var.set("req-123")
    inv_token = investigation_id_var.set("inv-456")
    try:
        logger.info("inside", extra={"event": "inside"})
    finally:
        request_id_var.reset(rid_token)
        investigation_id_var.reset(inv_token)
    record = read_lines(configured, "app.jsonl")[-1]
    assert record["request_id"] == "req-123"
    assert record["investigation_id"] == "inv-456"


def test_ids_are_null_outside_a_request(configured: Settings) -> None:
    logger.info("outside", extra={"event": "outside"})
    record = read_lines(configured, "app.jsonl")[-1]
    assert record["request_id"] is None
    assert record["investigation_id"] is None


def test_extra_fields_are_included(configured: Settings) -> None:
    logger.info("tool", extra={"event": "tool_call_done", "tool": "get_orders", "duration_ms": 12})
    record = read_lines(configured, "app.jsonl")[-1]
    assert record["tool"] == "get_orders"
    assert record["duration_ms"] == 12


def test_errors_file_only_has_warning_and_above(configured: Settings) -> None:
    logger.info("fine", extra={"event": "fine"})
    logger.warning("careful", extra={"event": "careful"})
    levels = [r["level"] for r in read_lines(configured, "errors.jsonl")]
    assert levels == ["WARNING"]


def test_log_level_setting_filters_app_file(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings(log_level="WARNING")
    setup_logging(settings)
    try:
        logger.info("dropped")
        logger.warning("kept")
        messages = [r["message"] for r in read_lines(settings, "app.jsonl")]
    finally:
        teardown_logging()
    assert messages == ["kept"]


def test_exception_is_logged_with_stack_trace(configured: Settings) -> None:
    try:
        raise ValueError("bad thing")
    except ValueError:
        logger.exception("failed", extra={"event": "op_failed"})
    record = read_lines(configured, "errors.jsonl")[-1]
    assert record["level"] == "ERROR"
    assert "Traceback" in str(record["exception"])
    assert "ValueError" in str(record["exception"])


def test_rotation_is_10mb_times_5(configured: Settings) -> None:
    rotating = [
        h
        for h in logging.getLogger().handlers
        if isinstance(h, logging.handlers.RotatingFileHandler)
    ]
    assert len(rotating) == 2
    for handler in rotating:
        assert handler.maxBytes == 10 * 1024 * 1024
        assert handler.backupCount == 5


def test_console_handler_writes_json(configured: Settings) -> None:
    console = [h for h in logging.getLogger().handlers if type(h) is logging.StreamHandler]
    assert len(console) == 1
    assert isinstance(console[0].formatter, JsonFormatter)


def test_setup_is_idempotent(settings: Settings) -> None:
    setup_logging(settings)
    first = len(logging.getLogger().handlers)
    setup_logging(settings)
    try:
        assert len(logging.getLogger().handlers) == first
    finally:
        teardown_logging()


def test_teardown_removes_only_our_handlers(settings: Settings) -> None:
    before = list(logging.getLogger().handlers)
    setup_logging(settings)
    teardown_logging()
    assert logging.getLogger().handlers == before
