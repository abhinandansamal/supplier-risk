"""Tests for the Zetaris view snapshot cache: TTL, single flight, stale serving, roles, warm-up."""

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable, Iterator

import pytest
import respx
from tests.fakes.zetaris import BASE, RUN_PATH, FakeZetaris, view_rows

from app.core.settings import Settings
from app.tools import cache as cache_module
from app.tools.cache import SnapshotCache
from app.tools.errors import SourceUnavailable, ViewNotGranted
from app.tools.views import ERP_VIEWS, EXTERNAL_VIEWS, VIEW_SQL, VIEWS


async def _no_sleep(_: float) -> None:
    return None


class FakeClock:
    """Monotonic clock the test moves by hand."""

    def __init__(self) -> None:
        """Start at an arbitrary instant."""
        self.t = 1000.0

    def __call__(self) -> float:
        """Return the current fake time."""
        return self.t


@pytest.fixture
def fake() -> FakeZetaris:
    return FakeZetaris()


@pytest.fixture
def router(fake: FakeZetaris) -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        mock.post(RUN_PATH).mock(side_effect=fake)
        yield mock


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
async def make_cache(
    make_settings: Callable[..., Settings], clock: FakeClock, router: respx.MockRouter
) -> AsyncIterator[Callable[..., SnapshotCache]]:
    caches: list[SnapshotCache] = []

    def factory(**overrides: object) -> SnapshotCache:
        cache = SnapshotCache(make_settings(**overrides), clock=clock, sleep=_no_sleep)
        caches.append(cache)
        return cache

    yield factory
    for cache in caches:
        await cache.aclose()


async def _drain() -> None:
    """Let background refresh tasks run to completion."""
    for _ in range(20):
        await asyncio.sleep(0)


async def test_first_read_loads_the_view_and_later_reads_hit(
    make_cache: Callable[..., SnapshotCache], fake: FakeZetaris
) -> None:
    cache = make_cache()
    first = await cache.get("buyer", "v_deliveries")
    second = await cache.get("buyer", "v_deliveries")
    assert second is first
    assert fake.calls[("buyer", "v_deliveries")] == 1
    assert len(first.rows) == 1555
    assert first.rows[0]["supplier_id"] == "S0002"
    assert first.query_id
    assert first.snapshot_at.tzinfo is not None


async def test_sends_the_registered_constant_sql(
    make_cache: Callable[..., SnapshotCache], fake: FakeZetaris
) -> None:
    await make_cache().get("risk", "v_contracts_full")
    assert fake.sql_seen == [VIEW_SQL["v_contracts_full"]]


async def test_hundred_concurrent_misses_cause_one_upstream_call(
    make_cache: Callable[..., SnapshotCache], fake: FakeZetaris
) -> None:
    fake.delay = 0.05
    cache = make_cache()
    results = await asyncio.gather(*(cache.get("risk", "v_deliveries") for _ in range(100)))
    assert fake.calls[("risk", "v_deliveries")] == 1
    assert all(result is results[0] for result in results)


async def test_buyer_and_risk_snapshots_never_mix(
    make_cache: Callable[..., SnapshotCache], fake: FakeZetaris
) -> None:
    cache = make_cache()
    buyer = await cache.get("buyer", "v_deliveries")
    risk = await cache.get("risk", "v_deliveries")
    assert buyer is not risk
    assert (buyer.key, risk.key) == ("buyer", "risk")
    assert fake.calls[("buyer", "v_deliveries")] == 1
    assert fake.calls[("risk", "v_deliveries")] == 1


@pytest.mark.parametrize("view", ["v_contracts_full", "v_supplier_revenue", "v_open_orders_full"])
async def test_buyer_key_never_reads_a_risk_only_view(
    make_cache: Callable[..., SnapshotCache], fake: FakeZetaris, view: str
) -> None:
    with pytest.raises(ViewNotGranted):
        await make_cache().get("buyer", view)
    assert not fake.calls


async def test_unknown_view_is_refused(make_cache: Callable[..., SnapshotCache]) -> None:
    with pytest.raises(ViewNotGranted):
        await make_cache().get("risk", "erp_pg.contracts")


async def test_stale_snapshot_is_served_while_one_background_refresh_runs(
    make_cache: Callable[..., SnapshotCache], fake: FakeZetaris, clock: FakeClock
) -> None:
    cache = make_cache()
    old = await cache.get("risk", "v_deliveries")
    clock.t += 901
    fake.delay = 0.01
    served = await asyncio.gather(*(cache.get("risk", "v_deliveries") for _ in range(10)))
    assert all(snapshot is old for snapshot in served)
    await asyncio.sleep(0.05)
    assert fake.calls[("risk", "v_deliveries")] == 2
    fresh = await cache.get("risk", "v_deliveries")
    assert fresh is not old


async def test_external_views_keep_their_longer_ttl(
    make_cache: Callable[..., SnapshotCache], fake: FakeZetaris, clock: FakeClock
) -> None:
    cache = make_cache()
    await cache.get("risk", "v_supplier_revenue")
    await cache.get("risk", "v_deliveries")
    clock.t += 2 * 3600
    await cache.get("risk", "v_supplier_revenue")
    await cache.get("risk", "v_deliveries")
    await _drain()
    assert fake.calls[("risk", "v_supplier_revenue")] == 1
    assert fake.calls[("risk", "v_deliveries")] == 2


async def test_external_snapshot_is_served_up_to_a_day_old(
    make_cache: Callable[..., SnapshotCache], fake: FakeZetaris, clock: FakeClock
) -> None:
    cache = make_cache()
    old = await cache.get("risk", "v_country_indicators")
    fake.fail_views.add("v_country_indicators")
    clock.t += 23 * 3600
    assert await cache.get("risk", "v_country_indicators") is old
    clock.t += 2 * 3600
    await _drain()
    with pytest.raises(SourceUnavailable):
        await cache.get("risk", "v_country_indicators")


async def test_failed_refresh_keeps_serving_stale_and_logs_a_warning(
    make_cache: Callable[..., SnapshotCache],
    fake: FakeZetaris,
    clock: FakeClock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    cache = make_cache()
    old = await cache.get("risk", "v_deliveries")
    fake.fail_views.add("v_deliveries")
    clock.t += 1000
    with caplog.at_level(logging.WARNING):
        assert await cache.get("risk", "v_deliveries") is old
        await _drain()
        await asyncio.sleep(0.01)
    assert any(getattr(r, "event", "") == "snapshot_refresh_failed" for r in caplog.records)
    assert await cache.get("risk", "v_deliveries") is old


async def test_past_max_staleness_the_source_is_unavailable(
    make_cache: Callable[..., SnapshotCache],
    fake: FakeZetaris,
    clock: FakeClock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    cache = make_cache()
    await cache.get("risk", "v_deliveries")
    fake.fail_views.add("v_deliveries")
    clock.t += 3601
    with caplog.at_level(logging.WARNING), pytest.raises(SourceUnavailable) as info:
        await cache.get("risk", "v_deliveries")
    assert info.value.view == "v_deliveries"
    assert any(getattr(r, "event", "") == "source_unavailable" for r in caplog.records)


async def test_past_max_staleness_a_working_source_reloads_before_answering(
    make_cache: Callable[..., SnapshotCache], fake: FakeZetaris, clock: FakeClock
) -> None:
    cache = make_cache()
    old = await cache.get("risk", "v_deliveries")
    clock.t += 3601
    assert await cache.get("risk", "v_deliveries") is not old
    assert fake.calls[("risk", "v_deliveries")] == 2


async def test_a_snapshot_that_fills_the_row_limit_is_refused_as_truncated(
    make_cache: Callable[..., SnapshotCache], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cache_module, "MAX_SNAPSHOT_ROWS", 50)
    with pytest.raises(SourceUnavailable):
        await make_cache().get("risk", "v_deliveries")


async def test_rows_of_the_wrong_width_are_refused(
    make_cache: Callable[..., SnapshotCache], fake: FakeZetaris
) -> None:
    fake.overrides["v_deliveries"] = [["PO1", "S0001"]]
    with pytest.raises(SourceUnavailable):
        await make_cache().get("risk", "v_deliveries")


async def test_a_role_without_a_key_is_unavailable_not_admin(
    make_cache: Callable[..., SnapshotCache], fake: FakeZetaris
) -> None:
    cache = make_cache(zetaris_key_buyer=None)
    with pytest.raises(SourceUnavailable):
        await cache.get("buyer", "v_deliveries")
    assert not fake.calls


async def test_cache_hit_is_faster_than_50_ms(make_cache: Callable[..., SnapshotCache]) -> None:
    cache = make_cache()
    await cache.get("risk", "v_deliveries")
    started = time.perf_counter()
    for _ in range(100):
        await cache.get("risk", "v_deliveries")
    assert (time.perf_counter() - started) / 100 < 0.05


async def test_warm_all_loads_every_granted_view_erp_first_two_at_a_time(
    make_cache: Callable[..., SnapshotCache],
    fake: FakeZetaris,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake.delay = 0.01
    with caplog.at_level(logging.INFO):
        report = await make_cache().warm_all()
    expected = {(k, v) for v, spec in VIEWS.items() for k in spec.keys}
    assert set(fake.calls) == expected
    assert all(count == 1 for count in fake.calls.values())
    assert set(report.values()) == {"ok"}
    assert fake.max_in_flight == 2
    kinds = ["erp" if view in ERP_VIEWS else "external" for _, view in fake.order]
    assert kinds == sorted(kinds, key=lambda kind: kind != "erp")
    loads = [r for r in caplog.records if getattr(r, "event", "") == "snapshot_loaded"]
    assert len(loads) == len(expected)
    assert all(isinstance(getattr(r, "duration_ms", None), int) for r in loads)


async def test_warm_all_respects_the_concurrency_setting(
    make_cache: Callable[..., SnapshotCache], fake: FakeZetaris
) -> None:
    fake.delay = 0.01
    await make_cache(snapshot_max_concurrency=1).warm_all()
    assert fake.max_in_flight == 1


async def test_warm_all_skips_a_missing_key_and_survives_failures(
    make_cache: Callable[..., SnapshotCache], fake: FakeZetaris
) -> None:
    fake.fail_views.add("v_country_indicators")
    report = await make_cache(zetaris_key_buyer=None).warm_all()
    assert not any(key == "buyer" for key, _ in fake.calls)
    assert report["risk/v_country_indicators"] == "failed"
    assert report["buyer/v_deliveries"] == "skipped"
    assert report["risk/v_deliveries"] == "ok"


async def test_start_warmup_does_not_block(
    make_cache: Callable[..., SnapshotCache], fake: FakeZetaris
) -> None:
    fake.delay = 0.05
    cache = make_cache()
    task = cache.start_warmup()
    assert not task.done()
    await task
    assert set(task.result().values()) == {"ok"}


def test_view_groups_cover_every_view() -> None:
    assert set(ERP_VIEWS) | set(EXTERNAL_VIEWS) == set(VIEWS)
    assert set(EXTERNAL_VIEWS) == {"v_supplier_revenue", "v_country_indicators"}
    assert len(view_rows()) == len(VIEWS)


@pytest.mark.parametrize(
    ("view", "column", "bad"),
    [
        ("v_deliveries", 5, "27/10/2026"),
        ("v_deliveries", 7, "NaN"),
        ("v_deliveries", 1, "S3"),
        ("v_country_indicators", 3, ":"),
        ("v_country_indicators", 2, "2026-13"),
        ("v_part_sourcing", 6, "maybe"),
        ("v_supplier_revenue", 7, "inf"),
    ],
)
async def test_malformed_cells_drop_the_row_and_are_counted(
    make_cache: Callable[..., SnapshotCache],
    fake: FakeZetaris,
    caplog: pytest.LogCaptureFixture,
    view: str,
    column: int,
    bad: str,
) -> None:
    rows = [list(row) for row in view_rows()[view]]
    rows[0][column] = bad
    fake.overrides[view] = rows
    with caplog.at_level(logging.WARNING):
        snapshot = await make_cache().get("risk", view)
    assert len(snapshot.rows) == len(rows) - 1
    assert snapshot.dropped_rows == 1
    dropped = [r for r in caplog.records if getattr(r, "event", "") == "snapshot_rows_dropped"]
    assert dropped and dropped[0].dropped == 1
    assert bad not in dropped[0].getMessage()


async def test_null_cells_are_kept(
    make_cache: Callable[..., SnapshotCache], fake: FakeZetaris
) -> None:
    rows = [list(row) for row in view_rows()["v_supplier_revenue"]]
    rows[0][7] = None
    fake.overrides["v_supplier_revenue"] = rows
    snapshot = await make_cache().get("risk", "v_supplier_revenue")
    assert snapshot.dropped_rows == 0 and snapshot.rows[0]["value_usd"] is None


async def test_a_failed_load_is_retried_only_after_the_backoff(
    make_cache: Callable[..., SnapshotCache], fake: FakeZetaris, clock: FakeClock
) -> None:
    cache = make_cache()
    fake.fail_views.add("v_deliveries")
    with pytest.raises(SourceUnavailable):
        await cache.get("risk", "v_deliveries")
    fake.fail_views.clear()
    with pytest.raises(SourceUnavailable):
        await cache.get("risk", "v_deliveries")
    assert fake.calls[("risk", "v_deliveries")] == 2  # one load: first try + one retry
    clock.t += cache_module.FAILURE_BACKOFF_SECONDS + 1
    assert len((await cache.get("risk", "v_deliveries")).rows) == 1555


def test_typed_columns_belong_to_their_view() -> None:
    for spec in VIEWS.values():
        assert set(spec.kinds) <= set(spec.columns), spec.name
    assert "annual_value_eur" not in VIEWS["v_suppliers_buyer"].kinds
    assert VIEWS["v_suppliers_full"].kinds["annual_value_eur"] == "num"
