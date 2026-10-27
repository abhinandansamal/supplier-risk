"""Tests for app.llm.rate_limiter: sliding-window rate and concurrency limits."""

import asyncio

import pytest

from app.llm.rate_limiter import AsyncRateLimiter, get_rate_limiter


class FakeClock:
    """Monotonic clock that only moves when the limiter sleeps or the test says so."""

    def __init__(self) -> None:
        """Start at time zero with no sleeps."""
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        """Return the current fake time."""
        return self.now

    async def sleep(self, seconds: float) -> None:
        """Record the sleep and advance the clock by it."""
        self.sleeps.append(seconds)
        self.now += seconds


def _limiter(clock: FakeClock, max_calls: int = 3) -> AsyncRateLimiter:
    return AsyncRateLimiter(
        max_calls=max_calls, period_seconds=60.0, clock=clock, sleep=clock.sleep
    )


async def _use(limiter: AsyncRateLimiter, times: int = 1) -> None:
    for _ in range(times):
        async with limiter.slot():
            pass


def test_defaults_match_the_free_tier_headroom() -> None:
    limiter = AsyncRateLimiter()
    assert limiter.max_calls == 30
    assert limiter.period_seconds == 60.0
    assert limiter.max_concurrency == 2


async def test_allows_max_calls_without_waiting() -> None:
    clock = FakeClock()
    await _use(_limiter(clock), times=3)
    assert clock.sleeps == []


async def test_waits_until_the_oldest_call_leaves_the_window() -> None:
    clock = FakeClock()
    limiter = _limiter(clock)
    await _use(limiter, times=3)
    clock.now = 10.0
    await _use(limiter)
    assert clock.sleeps == [pytest.approx(50.0)]


async def test_window_frees_after_the_period() -> None:
    clock = FakeClock()
    limiter = _limiter(clock)
    await _use(limiter, times=3)
    clock.now = 60.5
    await _use(limiter, times=3)
    assert clock.sleeps == []


async def test_limits_concurrent_calls_to_two() -> None:
    limiter = AsyncRateLimiter(max_calls=100)
    active = 0
    peak = 0

    async def worker() -> None:
        nonlocal active, peak
        async with limiter.slot():
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1

    await asyncio.gather(*(worker() for _ in range(5)))
    assert peak == 2


async def test_releases_slot_when_the_call_fails() -> None:
    limiter = AsyncRateLimiter(max_calls=100, max_concurrency=1)
    with pytest.raises(RuntimeError):
        async with limiter.slot():
            raise RuntimeError("boom")
    await asyncio.wait_for(_use(limiter), timeout=1.0)


def test_shared_instance_is_reused() -> None:
    assert get_rate_limiter() is get_rate_limiter()


@pytest.mark.parametrize(
    "kwargs",
    [{"max_calls": 0}, {"period_seconds": 0.0}, {"max_concurrency": 0}],
)
def test_rejects_invalid_limits(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        AsyncRateLimiter(**kwargs)  # type: ignore[arg-type]
