"""Shared fixtures: a complete, fake environment and a Settings factory (never reads .env)."""

from collections.abc import Callable
from pathlib import Path

import pytest

from app.core.settings import Settings

FAKE_ENV: dict[str, str] = {
    "ZETARIS_REST_URL": "http://localhost:8888/api/v1.0",
    "ZETARIS_ORG_ID": "org-test",
    "ZETARIS_APP_API_KEY": "zk-secret-VALUE-1111",
    "ZETARIS_USER_AGENT": "supplier-risk test@example.com",
    "METERLESS_BASE_URL": "http://127.0.0.1:8088/v1",
    "METERLESS_RUNTIME_TOKEN": "mt-secret-VALUE-2222",
    "METERLESS_APP_ID": "supplier-risk",
    "METERLESS_MODEL": "nvidia/nemotron-3.5-lightning-30b-a3b",
    "METERLESS_DEEP_MODEL": "nvidia/nemotron-3.5-ultra",
    "METERLESS_FAST_MODEL": "nvidia/nemotron-3.5-bolt-e4b",
    "METERLESS_EMBED_MODEL": "nvidia/nemotron-3.5-embed-1b",
    "CONTEXT_SERVICE_URL": "http://localhost:7070",
    "ERP_DATABASE_URL": "postgresql://erp:erp-pass-VALUE-3333@localhost:5433/erp",
    "APP_DATABASE_URL": "postgresql://app:app-pass-VALUE-4444@localhost:5434/app",
}

OPTIONAL_ENV = ("TOOL_CACHE_TTL_SECONDS", "LOG_LEVEL", "LOG_DIR", "APP_ENV")


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Set a complete fake environment and clear the optional variables."""
    for name in OPTIONAL_ENV:
        monkeypatch.delenv(name, raising=False)
    for name, value in FAKE_ENV.items():
        monkeypatch.setenv(name, value)
    return dict(FAKE_ENV)


@pytest.fixture
def make_settings(env: dict[str, str], tmp_path: Path) -> Callable[..., Settings]:
    """Return a factory building Settings from the fake env, with log_dir in tmp_path."""

    def factory(**overrides: object) -> Settings:
        values: dict[str, object] = {"log_dir": tmp_path / "logs", "app_env": "dev"}
        values.update(overrides)
        return Settings(_env_file=None, **values)  # type: ignore[arg-type]

    return factory


@pytest.fixture
def settings(make_settings: Callable[..., Settings]) -> Settings:
    """Return default fake settings (dev environment)."""
    return make_settings()
