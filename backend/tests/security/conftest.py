"""Fixtures for security tests: fake environment only, never reads .env."""

from collections.abc import Callable
from pathlib import Path

import pytest
from tests.fakes.agent import investigate  # noqa: F401 - fixture for agent tests
from tests.fakes.tools import (  # noqa: F401 - fixtures for tool tests
    fake,
    make_ctx,
    router,
    snapshot_cache,
)

from app.core.settings import Settings

FAKE_ENV = {
    "ZETARIS_REST_URL": "http://localhost:8888/api/v1.0",
    "ZETARIS_ORG_ID": "org-test",
    "ZETARIS_KEY_RISK": "zk-secret-VALUE-1111",
    "ZETARIS_KEY_BUYER": "zk-buyer-VALUE-5555",
    "ZETARIS_ADMIN_API_KEY": "zk-admin-VALUE-6666",
    "ZETARIS_USER_AGENT": "supplier-risk test@example.com",
    "METERLESS_BASE_URL": "http://127.0.0.1:8088/v1",
    "METERLESS_RUNTIME_TOKEN": "mt-secret-VALUE-2222",
    "METERLESS_APP_ID": "supplier-risk",
    "METERLESS_MODEL": "m1",
    "METERLESS_DEEP_MODEL": "m2",
    "METERLESS_FAST_MODEL": "m3",
    "METERLESS_EMBED_MODEL": "m4",
    "CONTEXT_SERVICE_URL": "http://localhost:7070",
    "ERP_DATABASE_URL": "postgresql://erp:erp-pass-VALUE-3333@localhost:5433/erp",
    "APP_DATABASE_URL": "postgresql://app:app-pass-VALUE-4444@localhost:5434/app",
}


@pytest.fixture
def make_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Callable[..., Settings]:
    """Return a Settings factory built from a fake environment."""
    for name, value in FAKE_ENV.items():
        monkeypatch.setenv(name, value)

    def factory(**overrides: object) -> Settings:
        values: dict[str, object] = {"log_dir": tmp_path / "logs", "app_env": "dev"}
        values.update(overrides)
        return Settings(_env_file=None, **values)  # type: ignore[arg-type]

    return factory
