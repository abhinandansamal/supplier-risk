"""Shared fixtures: a fake environment, a Settings factory and a mocked Meterless runtime."""

from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path

import pytest
import respx
from tests.fakes.agent import investigate  # noqa: F401 - fixture for agent tests
from tests.fakes.tools import (  # noqa: F401 - fixtures for tool tests
    fake,
    make_ctx,
    router,
    snapshot_cache,
)

from app.core.settings import Settings
from app.llm.client import MeterlessClient
from app.llm.rate_limiter import AsyncRateLimiter

FAKE_ENV: dict[str, str] = {
    "ZETARIS_REST_URL": "http://localhost:8888/api/v1.0",
    "ZETARIS_ORG_ID": "org-test",
    "ZETARIS_KEY_RISK": "zk-secret-VALUE-1111",
    "ZETARIS_KEY_BUYER": "zk-buyer-VALUE-5555",
    "ZETARIS_ADMIN_API_KEY": "zk-admin-VALUE-6666",
    "ZETARIS_USER_AGENT": "supplier-risk test@example.com",
    "METERLESS_BASE_URL": "http://127.0.0.1:8088/v1",
    "METERLESS_RUNTIME_TOKEN": "mt-secret-VALUE-2222",
    "METERLESS_APP_ID": "supplier-risk",
    "METERLESS_MODEL": "nvidia/nemotron-3.5-lightning-30b-a3b",
    "METERLESS_DEEP_MODEL": "nvidia/nemotron-3.5-ultra",
    "METERLESS_FAST_MODEL": "nvidia/nemotron-3.5-bolt-e4b",
    "METERLESS_EMBED_MODEL": "nvidia/nemotron-3.5-embed-1b",
    "CONTEXT_SERVICE_URL": "http://localhost:7070",
    "CONTEXT_SERVICE_TOKEN": "cs-secret-VALUE-7777-0123456789abcdef",
    "ERP_DATABASE_URL": "postgresql://erp:erp-pass-VALUE-3333@localhost:5433/erp",
    "APP_DATABASE_URL": "postgresql://app:app-pass-VALUE-4444@localhost:5434/app",
}

OPTIONAL_ENV = (
    "TOOL_CACHE_TTL_SECONDS",
    "TOOL_CACHE_MAX_STALE_SECONDS",
    "TOOL_CACHE_EXTERNAL_TTL_SECONDS",
    "TOOL_CACHE_EXTERNAL_MAX_STALE_SECONDS",
    "ZETARIS_SNAPSHOT_TIMEOUT_SECONDS",
    "SNAPSHOT_MAX_CONCURRENCY",
    "CONTEXT_SERVICE_TIMEOUT_SECONDS",
    "LOG_LEVEL",
    "LOG_DIR",
    "APP_ENV",
    "INVESTIGATION_CALL_BUDGET",
    "INVESTIGATION_DEADLINE_SECONDS",
    "MODEL_CALL_TIMEOUT_SECONDS",
    "TOOL_CALL_TIMEOUT_SECONDS",
    "AGENT_MODEL_GUARD",
    "AGENT_MODEL_PLANNER",
    "AGENT_MODEL_RESEARCH",
    "AGENT_MODEL_EVALUATOR",
    "AGENT_MODEL_RECOMMENDER",
    "AGENT_MODEL_WRITER",
)


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


class RecordingSleep:
    """Async sleep replacement that records the requested delays instead of waiting."""

    def __init__(self) -> None:
        """Start with no recorded delays."""
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        """Record the delay and return at once."""
        self.delays.append(seconds)


@pytest.fixture
def meterless() -> Iterator[respx.MockRouter]:
    """Mock the Meterless runtime at the fake base URL; no network."""
    with respx.mock(base_url=FAKE_ENV["METERLESS_BASE_URL"], assert_all_called=False) as mock:
        yield mock


@pytest.fixture
def sleeper() -> RecordingSleep:
    """Return the sleep recorder that client fixtures use for backoff."""
    return RecordingSleep()


@pytest.fixture
async def make_client(
    settings: Settings, sleeper: RecordingSleep
) -> AsyncIterator[Callable[..., MeterlessClient]]:
    """Return a factory for clients with no real sleeping, zero jitter and a roomy rate limit."""
    clients: list[MeterlessClient] = []

    def factory(**overrides: object) -> MeterlessClient:
        options: dict[str, object] = {
            "limiter": AsyncRateLimiter(max_calls=1000),
            "sleep": sleeper,
            "rand": lambda: 0.0,
        }
        options.update(overrides)
        client_settings = options.pop("settings", settings)
        client = MeterlessClient(client_settings, **options)  # type: ignore[arg-type]
        clients.append(client)
        return client

    yield factory
    for client in clients:
        await client.aclose()
