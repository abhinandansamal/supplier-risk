"""Tests for the startup snapshot warm-up (non-blocking) and scripts/warm_cache.py."""

import asyncio
from collections.abc import Callable

import pytest
import respx
from fastapi.testclient import TestClient
from tests.fakes.zetaris import FakeZetaris

import app.main as main
from app.api.app import create_app
from app.core.logging import teardown_logging
from app.core.settings import Settings
from app.tools.cache import SnapshotCache
from scripts import warm_cache


class RecordingCache:
    """Stand-in for SnapshotCache that records the lifespan calls."""

    def __init__(self) -> None:
        """Start with nothing recorded."""
        self.started = 0
        self.closed = 0

    def start_warmup(self) -> "asyncio.Task[dict[str, str]]":
        """Record the call and return a finished-soon task."""
        self.started += 1

        async def nothing() -> dict[str, str]:
            return {}

        return asyncio.get_running_loop().create_task(nothing())

    async def aclose(self) -> None:
        """Record the close."""
        self.closed += 1


def test_app_starts_the_warm_up_on_startup_and_closes_on_shutdown(settings: Settings) -> None:
    cache = RecordingCache()
    app = create_app(settings, snapshot_cache=cache)  # type: ignore[arg-type]
    assert app.state.snapshot_cache is cache
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert cache.started == 1
    assert cache.closed == 1


def test_app_without_a_cache_does_not_warm_up(settings: Settings) -> None:
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
    assert app.state.snapshot_cache is None


def test_uvicorn_factory_wires_a_snapshot_cache(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    monkeypatch.setattr(main, "load_settings", lambda: settings)
    try:
        assert isinstance(main.create().state.snapshot_cache, SnapshotCache)
    finally:
        teardown_logging()


async def test_warm_cache_script_reports_every_view_and_exits_0(
    make_settings: Callable[..., Settings],
    router: respx.MockRouter,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert await warm_cache.main(make_settings()) == 0
    out = capsys.readouterr().out
    assert "ok risk/v_country_indicators" in out
    assert "ok buyer/v_deliveries" in out
    assert "zk-" not in out


async def test_warm_cache_script_exits_1_on_a_failed_view(
    make_settings: Callable[..., Settings],
    router: respx.MockRouter,
    fake: FakeZetaris,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake.fail_views.add("v_supplier_revenue")
    assert await warm_cache.main(make_settings(), sleep=_no_sleep) == 1
    assert "failed risk/v_supplier_revenue" in capsys.readouterr().out


async def test_warm_cache_script_skips_without_role_keys(
    make_settings: Callable[..., Settings], capsys: pytest.CaptureFixture[str]
) -> None:
    code = await warm_cache.main(make_settings(zetaris_key_buyer=None, zetaris_key_risk=None))
    assert code == 2
    assert "SKIP" in capsys.readouterr().out


async def _no_sleep(_: float) -> None:
    return None
