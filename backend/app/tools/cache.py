"""In-memory snapshot cache: one Zetaris query per (role key, view) loads the whole view.

Why snapshots: Zetaris queries take 13 s (ERP over JDBC) to minutes (live SEC/Eurostat REST),
too slow for a request (docs/devlog.md), and tools must never put input into SQL (SEC-045).
So each view is loaded whole with its fixed SQL and tools filter in Python.

Per entry:
- younger than the TTL: served;
- older, but within the max staleness: served, and one background refresh starts;
- older than that, or missing: callers wait for one shared load (single flight);
- a failed load is remembered for FAILURE_BACKOFF_SECONDS: callers get SourceUnavailable at
  once instead of starting another slow load each (SEC-061); a stale entry stays served.

Cells are checked when a snapshot loads (dates, months, finite numbers, booleans, supplier
ids; NULL is allowed): a row with a malformed cell is dropped and counted, so tools never
parse bad text from an external source (SEC-063, SEC-064).

ERP views use TOOL_CACHE_TTL_SECONDS / TOOL_CACHE_MAX_STALE_SECONDS; the SEC and Eurostat
views use the TOOL_CACHE_EXTERNAL_* settings. At most SNAPSHOT_MAX_CONCURRENCY loads run at
once, and SEC/Eurostat loads (minutes each) use at most all but one of those slots, so an ERP
load never queues behind them (SEC-073).

Security: entries are keyed by the role key, so a buyer snapshot never serves a risk manager
request or the reverse, and a key only reads views it is granted.
"""

import asyncio
import logging
import math
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime

import httpx

from app.core.settings import Settings
from app.tools.errors import SourceUnavailable, ViewNotGranted
from app.tools.views import (
    ERP_VIEWS,
    EXTERNAL_VIEWS,
    KEY_CLIENT_ROLE,
    VIEWS,
    KeyName,
    Kind,
    ViewSpec,
)
from app.zetaris.client import MAX_QUERY_ROWS, ROLE_KEY_SETTINGS, QueryResult, ZetarisClient
from app.zetaris.errors import ZetarisBadResponse, ZetarisError

logger = logging.getLogger(__name__)

# A snapshot that fills the row limit may be cut short, so it is refused, never cached.
MAX_SNAPSHOT_ROWS = MAX_QUERY_ROWS
KEYS: tuple[KeyName, ...] = ("buyer", "risk")
FAILURE_BACKOFF_SECONDS = 60.0
_SUPPLIER_ID = re.compile(r"S[0-9]{4}")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}([ T].*)?")
_MONTH = re.compile(r"[0-9]{4}-(0[1-9]|1[0-2])")
_INT = re.compile(r"-?[0-9]{1,12}(\.0+)?")
_BOOLS = frozenset({"true", "false", "t", "f", "1", "0"})

CacheKey = tuple[KeyName, str]
Row = dict[str, str | None]


@dataclass(frozen=True)
class Snapshot:
    """All rows of one view as read with one role key.

    Attributes:
        view: View name in container risk.
        key: Role key the view was read with.
        rows: Rows as {column: text or None}, in Zetaris order.
        query_id: Zetaris queryId of the load (goes into evidence).
        snapshot_at: When the load finished (UTC); evidence shows it, so staleness is visible.
        loaded_monotonic: Monotonic time of the load, for TTL checks.
        dropped_rows: Rows dropped at load because a cell was malformed.
    """

    view: str
    key: KeyName
    rows: tuple[Row, ...]
    query_id: str
    snapshot_at: datetime
    loaded_monotonic: float
    dropped_rows: int = 0


def valid_cell(kind: Kind, text: str | None) -> bool:
    """Return True if a cell is NULL or well formed for its kind (supplier ids are required)."""
    if text is None:
        return kind != "supplier_id"
    if kind == "supplier_id":
        return _SUPPLIER_ID.fullmatch(text) is not None
    if kind == "month":
        return _MONTH.fullmatch(text) is not None
    if kind == "int":
        return _INT.fullmatch(text) is not None
    if kind == "bool":
        return text.strip().lower() in _BOOLS
    if kind == "date":
        if not _DATE.fullmatch(text):
            return False
        try:
            date.fromisoformat(text[:10])
        except ValueError:
            return False
        return True
    try:
        return math.isfinite(float(text))
    except ValueError:
        return False


class SnapshotCache:
    """Role-keyed, single-flight cache of whole-view snapshots (in memory only)."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Create an empty cache.

        Args:
            settings: TTLs, staleness limits, concurrency, Zetaris URL and role keys.
            transport: Optional httpx transport for the Zetaris clients (tests, load tests).
            clock: Monotonic clock in seconds; injectable for tests.
            sleep: Async sleep the Zetaris clients use before a retry; injectable for tests.
            now: Wall clock (UTC) for snapshot_at, which also dates "overdue"; for tests.
        """
        self._settings = settings
        self._transport = transport
        self._clock = clock
        self._sleep = sleep
        self._now = now
        self._entries: dict[CacheKey, Snapshot] = {}
        self._inflight: dict[CacheKey, asyncio.Task[Snapshot]] = {}
        self._clients: dict[KeyName, ZetarisClient] = {}
        self._limit = asyncio.Semaphore(settings.snapshot_max_concurrency)
        self._external_limit = asyncio.Semaphore(max(1, settings.snapshot_max_concurrency - 1))
        self._failed_until: dict[CacheKey, float] = {}
        self._warmup: asyncio.Task[dict[str, str]] | None = None
        self._closed = False

    async def get(self, key: KeyName, view: str) -> Snapshot:
        """Return a servable snapshot of a view for a role key.

        Args:
            key: Role key name (buyer or risk), derived from the session role.
            view: View name from the registry.

        Returns:
            The snapshot (possibly stale, within the view's max staleness).

        Raises:
            ViewNotGranted: If the view is unknown or the key has no grant on it.
            SourceUnavailable: If no snapshot within the max staleness can be served.
        """
        spec = VIEWS.get(view)
        if spec is None or key not in spec.keys:
            raise ViewNotGranted("view is not granted to this role key")
        if self._closed:
            raise SourceUnavailable(view)
        ttl, max_stale = self._limits(spec)
        cache_key = (key, view)
        entry = self._entries.get(cache_key)
        if entry is not None:
            age = self._clock() - entry.loaded_monotonic
            if age <= ttl:
                return entry
            if age <= max_stale:
                self._refresh_in_background(cache_key)
                logger.info(
                    "stale snapshot served",
                    extra={
                        "event": "snapshot_stale_served",
                        "key": key,
                        "view": view,
                        "age_s": round(age),
                    },
                )
                return entry
        if self._in_backoff(cache_key):
            raise SourceUnavailable(view)
        try:
            # shield: a cancelled caller must not cancel the load other callers share.
            return await asyncio.shield(self._load_once(cache_key))
        except ZetarisError:
            logger.exception(
                "snapshot source unavailable",
                extra={"event": "source_unavailable", "key": key, "view": view},
            )
            raise SourceUnavailable(view) from None

    async def warm_all(self) -> dict[str, str]:
        """Load every granted view for both role keys: ERP views first, then SEC/Eurostat.

        Returns:
            Status per "key/view": ok, failed, or skipped (role key not configured).
        """
        started = time.monotonic()
        report: dict[str, str] = {}
        for group in (ERP_VIEWS, EXTERNAL_VIEWS):
            jobs: list[tuple[str, KeyName, str]] = []
            for view in group:
                for key in KEYS:
                    if key not in VIEWS[view].keys:
                        continue
                    label = f"{key}/{view}"
                    if self._has_key(key):
                        jobs.append((label, key, view))
                    else:
                        report[label] = "skipped"
            results = await asyncio.gather(*(self._warm_one(key, view) for _, key, view in jobs))
            report.update(
                {label: status for (label, _, _), status in zip(jobs, results, strict=True)}
            )
        statuses = list(report.values())
        logger.info(
            "snapshot warm-up done",
            extra={
                "event": "snapshot_warmup_done",
                "ok": statuses.count("ok"),
                "failed": statuses.count("failed"),
                "skipped": statuses.count("skipped"),
                "duration_ms": round((time.monotonic() - started) * 1000),
            },
        )
        return report

    def start_warmup(self) -> "asyncio.Task[dict[str, str]]":
        """Start warm_all in the background and return its task (non-blocking)."""
        if self._warmup is None or self._warmup.done():
            self._warmup = asyncio.create_task(self.warm_all())
        return self._warmup

    async def aclose(self) -> None:
        """Cancel background work and close the Zetaris clients; later reads are refused."""
        self._closed = True
        tasks = [*self._inflight.values(), *([self._warmup] if self._warmup else [])]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for client in self._clients.values():
            await client.aclose()
        self._clients.clear()

    def _limits(self, spec: ViewSpec) -> tuple[int, int]:
        """Return (ttl, max staleness) in seconds for a view."""
        s = self._settings
        if spec.source == "erp":
            return s.tool_cache_ttl_seconds, s.tool_cache_max_stale_seconds
        return s.tool_cache_external_ttl_seconds, s.tool_cache_external_max_stale_seconds

    def _in_backoff(self, cache_key: CacheKey) -> bool:
        """Return True while a recent failed load of this key is remembered."""
        return self._clock() < self._failed_until.get(cache_key, -math.inf)

    def _has_key(self, key: KeyName) -> bool:
        """Return True when the role key is configured."""
        field = ROLE_KEY_SETTINGS[KEY_CLIENT_ROLE[key]]
        return getattr(self._settings, field) is not None

    async def _warm_one(self, key: KeyName, view: str) -> str:
        """Load one view for warm-up; failures are logged by get() and reported."""
        try:
            await self.get(key, view)
        except SourceUnavailable:
            return "failed"
        return "ok"

    def _load_once(self, cache_key: CacheKey) -> "asyncio.Task[Snapshot]":
        """Return the running load for a key, or start one (single flight)."""
        task = self._inflight.get(cache_key)
        if task is None:
            task = asyncio.create_task(self._load(*cache_key))
            self._inflight[cache_key] = task
            task.add_done_callback(lambda done: self._load_finished(cache_key, done))
        return task

    def _load_finished(self, cache_key: CacheKey, task: "asyncio.Task[Snapshot]") -> None:
        """Forget the running load; remember a failure for FAILURE_BACKOFF_SECONDS."""
        self._inflight.pop(cache_key, None)
        if not task.cancelled() and task.exception() is not None:
            self._failed_until[cache_key] = self._clock() + FAILURE_BACKOFF_SECONDS
        else:
            self._failed_until.pop(cache_key, None)

    def _refresh_in_background(self, cache_key: CacheKey) -> None:
        """Start (or join) a refresh whose failure is logged, never raised."""
        if cache_key in self._inflight or self._in_backoff(cache_key):
            return
        self._load_once(cache_key).add_done_callback(self._log_refresh_failure)

    @staticmethod
    def _log_refresh_failure(task: "asyncio.Task[Snapshot]") -> None:
        """Log a failed background refresh (the stale snapshot stays served)."""
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.warning(
                "snapshot refresh failed; serving the stale snapshot",
                extra={"event": "snapshot_refresh_failed", "error_type": type(error).__name__},
            )

    def _client(self, key: KeyName) -> ZetarisClient:
        """Return the Zetaris client of a role key (created on first use)."""
        client = self._clients.get(key)
        if client is None:
            client = ZetarisClient(
                self._settings,
                KEY_CLIENT_ROLE[key],
                transport=self._transport,
                sleep=self._sleep,
                query_timeout=self._settings.zetaris_snapshot_timeout_seconds,
            )
            self._clients[key] = client
        return client

    async def _load(self, key: KeyName, view: str) -> Snapshot:
        """Read one whole view (under the concurrency limit) and store the snapshot."""
        spec = VIEWS[view]
        if spec.source == "erp":
            async with self._limit:
                result, duration_ms = await self._query(key, spec)
        else:
            async with self._external_limit, self._limit:
                result, duration_ms = await self._query(key, spec)
        if result.row_count >= MAX_SNAPSHOT_ROWS:
            raise ZetarisBadResponse("snapshot may be truncated at the row limit", 200)
        width = len(spec.columns)
        if any(len(row) != width for row in result.rows):
            raise ZetarisBadResponse("snapshot row width does not match the view", 200)
        rows = [dict(zip(spec.columns, row, strict=True)) for row in result.rows]
        kept = tuple(
            row for row in rows if all(valid_cell(k, row[c]) for c, k in spec.kinds.items())
        )
        dropped = len(rows) - len(kept)
        if dropped:
            logger.warning(
                "malformed snapshot rows dropped",
                extra={
                    "event": "snapshot_rows_dropped",
                    "key": key,
                    "view": view,
                    "dropped": dropped,
                },
            )
        snapshot = Snapshot(
            view=view,
            key=key,
            rows=kept,
            query_id=result.query_id,
            snapshot_at=self._now(),
            loaded_monotonic=self._clock(),
            dropped_rows=dropped,
        )
        self._entries[(key, view)] = snapshot
        logger.info(
            "snapshot loaded",
            extra={
                "event": "snapshot_loaded",
                "key": key,
                "view": view,
                "rows": len(snapshot.rows),
                "query_id": result.query_id,
                "duration_ms": duration_ms,
            },
        )
        return snapshot

    async def _query(self, key: KeyName, spec: ViewSpec) -> tuple[QueryResult, int]:
        """Run the view's fixed SQL; return the result and its duration in ms."""
        started = time.monotonic()
        result = await self._client(key).query(spec.sql, limit=MAX_SNAPSHOT_ROWS)
        return result, round((time.monotonic() - started) * 1000)
