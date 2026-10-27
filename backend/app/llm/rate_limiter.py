"""Shared async rate limiter for model calls.

The NVIDIA free tier allows 40 requests per minute per account, so the whole process stays
at 30 per sliding minute with at most 2 calls in flight.
"""

import asyncio
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from functools import cache

DEFAULT_MAX_CALLS = 30
DEFAULT_PERIOD_SECONDS = 60.0
DEFAULT_MAX_CONCURRENCY = 2


class AsyncRateLimiter:
    """Sliding-window rate limit plus a concurrency cap.

    Attributes:
        max_calls: Calls allowed per window.
        period_seconds: Window length.
        max_concurrency: Calls allowed in flight at once.
    """

    def __init__(
        self,
        max_calls: int = DEFAULT_MAX_CALLS,
        period_seconds: float = DEFAULT_PERIOD_SECONDS,
        max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """Create a limiter.

        Args:
            max_calls: Calls allowed per window (at least 1).
            period_seconds: Window length in seconds (above 0).
            max_concurrency: Calls allowed in flight at once (at least 1).
            clock: Monotonic clock; injectable for tests.
            sleep: Async sleep; injectable for tests.

        Raises:
            ValueError: If a limit is not positive.
        """
        if max_calls < 1 or period_seconds <= 0 or max_concurrency < 1:
            raise ValueError("rate limits must be positive")
        self.max_calls = max_calls
        self.period_seconds = period_seconds
        self.max_concurrency = max_concurrency
        self._clock = clock
        self._sleep = sleep
        self._starts: deque[float] = deque()
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._window_lock = asyncio.Lock()

    async def _enter_window(self) -> None:
        """Wait until the window has room, then record this call's start."""
        async with self._window_lock:
            while True:
                now = self._clock()
                while self._starts and self._starts[0] <= now - self.period_seconds:
                    self._starts.popleft()
                if len(self._starts) < self.max_calls:
                    self._starts.append(now)
                    return
                await self._sleep(self._starts[0] + self.period_seconds - now)

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        """Hold one call slot for the duration of the block.

        Yields:
            Nothing; the block runs once a slot is free.
        """
        async with self._semaphore:
            await self._enter_window()
            yield


@cache
def get_rate_limiter() -> AsyncRateLimiter:
    """Return the process-wide limiter shared by every model client."""
    return AsyncRateLimiter()
