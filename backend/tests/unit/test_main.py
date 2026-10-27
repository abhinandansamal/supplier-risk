"""Tests for the uvicorn factory in app.main: settings, logging and critical startup errors."""

import logging
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi import FastAPI

import app.main as main
from app.core.logging import teardown_logging
from app.core.settings import Settings, SettingsError


@pytest.fixture
def app_settings(make_settings: Callable[..., Settings], tmp_path: Path) -> Settings:
    return make_settings(app_database_url=f"sqlite:///{tmp_path}/app.db")


def test_create_builds_app_and_configures_logging(
    monkeypatch: pytest.MonkeyPatch, app_settings: Settings
) -> None:
    monkeypatch.setattr(main, "load_settings", lambda: app_settings)
    try:
        application = main.create()
        assert isinstance(application, FastAPI)
        assert any(getattr(h, "_supplier_risk", False) for h in logging.getLogger().handlers)
    finally:
        teardown_logging()


def test_create_logs_critical_and_reraises_on_bad_settings(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def broken() -> Settings:
        raise SettingsError("Invalid or missing settings: ZETARIS_ORG_ID")

    monkeypatch.setattr(main, "load_settings", broken)
    with caplog.at_level(logging.CRITICAL), pytest.raises(SettingsError):
        main.create()
    critical = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert critical
    assert "ZETARIS_ORG_ID" in critical[0].getMessage()


@pytest.mark.parametrize(
    "override",
    [
        {"app_database_url": "postgresql://app:pw@localhost:5434/app"},
        {"jwt_secret": None},
    ],
)
def test_create_refuses_to_start_without_sqlite_or_jwt_secret(
    monkeypatch: pytest.MonkeyPatch,
    make_settings: Callable[..., Settings],
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    override: dict[str, object],
) -> None:
    settings = make_settings(**{"app_database_url": f"sqlite:///{tmp_path}/a.db", **override})
    monkeypatch.setattr(main, "load_settings", lambda: settings)
    try:
        with caplog.at_level(logging.CRITICAL), pytest.raises(SettingsError):
            main.create()
    finally:
        teardown_logging()
    assert [r for r in caplog.records if r.levelno == logging.CRITICAL]


@pytest.mark.parametrize("name", ["LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING"])
def test_create_refuses_to_start_with_langsmith_tracing_on(
    monkeypatch: pytest.MonkeyPatch, app_settings: Settings, name: str
) -> None:
    monkeypatch.setenv(name, "true")
    monkeypatch.setattr(main, "load_settings", lambda: app_settings)
    try:
        with pytest.raises(SettingsError, match=name):
            main.create()
    finally:
        teardown_logging()


def test_create_logs_that_tracing_is_off(
    monkeypatch: pytest.MonkeyPatch, app_settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    monkeypatch.setattr(main, "load_settings", lambda: app_settings)
    try:
        with caplog.at_level(logging.INFO):
            main.create()
    finally:
        teardown_logging()
    assert "langchain_tracing_off" in [getattr(r, "event", None) for r in caplog.records]
