"""Fixtures for tool tests: fake Zetaris + snapshot cache + a session-bound ToolContext."""

from collections.abc import AsyncIterator, Callable, Iterator

import pytest
import respx
from tests.fakes.zetaris import BASE, RUN_PATH, SNAPSHOT_AT, TODAY, FakeZetaris, no_sleep

from app.core.settings import Settings
from app.evidence.models import EvidenceLog
from app.security.identity import UserContext
from app.tools.cache import SnapshotCache
from app.tools.catalogue import ToolContext


@pytest.fixture
def fake() -> FakeZetaris:
    """Return the fake Zetaris endpoint."""
    return FakeZetaris()


@pytest.fixture
def router(fake: FakeZetaris) -> Iterator[respx.MockRouter]:
    """Route the Zetaris URL to the fake."""
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        mock.post(RUN_PATH).mock(side_effect=fake)
        yield mock


@pytest.fixture
async def snapshot_cache(
    make_settings: Callable[..., Settings], router: respx.MockRouter
) -> AsyncIterator[SnapshotCache]:
    """Return a snapshot cache over the fake Zetaris."""
    cache = SnapshotCache(make_settings(), sleep=no_sleep, now=lambda: SNAPSHOT_AT)
    yield cache
    await cache.aclose()


@pytest.fixture
def make_ctx(snapshot_cache: SnapshotCache) -> Callable[..., ToolContext]:
    """Return a factory for a ToolContext with a fresh evidence log."""

    def factory(role: str = "risk_manager", category: str | None = None) -> ToolContext:
        user = UserContext(user_id="u-test", role=role, category=category)  # type: ignore[arg-type]
        return ToolContext(user=user, cache=snapshot_cache, evidence=EvidenceLog(), today=TODAY)

    return factory
