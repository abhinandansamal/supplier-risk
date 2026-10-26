"""Tests for the uvicorn factory in app.main: settings, logging and critical startup errors."""

import logging

import pytest
from fastapi import FastAPI

import app.main as main
from app.core.logging import teardown_logging
from app.core.settings import Settings, SettingsError


def test_create_builds_app_and_configures_logging(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    monkeypatch.setattr(main, "load_settings", lambda: settings)
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
