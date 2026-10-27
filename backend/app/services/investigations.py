"""The investigation service: queue with one worker, per-user limits, access and erasure.

One asyncio worker in the one API process runs investigations one at a time (SEC-024: the
model rate limiter is per process and per event loop). New work beyond QUEUE_MAX_LENGTH, or
beyond INVESTIGATIONS_PER_USER_PER_HOUR for one user, gets 429.

Security (SEC-084, API side): a user reads only their own investigations (a director may read
all); anyone else gets 404, so ids are not confirmed. The API never reads the World Model:
the live timeline is the investigation's own trace. Evidence rows (untrusted ERP text such as
buyer notes) are removed before a result is stored or shown; the evidence panel needs only
tool, view, snapshot time, Zetaris query id and facts. Approving a proposal only records the
decision; no code path executes an action.
"""

import asyncio
import contextlib
import logging
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol

from app.agent.models import InvestigationResult
from app.agent.runtime import INVESTIGATION_ID_PATTERN, TraceListener, new_investigation_id
from app.core.errors import AppError
from app.core.settings import Settings
from app.security.identity import UserContext
from app.services.events import EventHub, Subscription
from app.store.db import AlreadyDecided, InvestigationRow, ProposalRow, Store

logger = logging.getLogger(__name__)

HOUR_SECONDS = 3600.0
MAX_STREAMS_PER_USER = 3
DECIDERS = frozenset({"buyer", "risk_manager"})
# Who may decide which impact (architecture 10.1, level 3).
BUYER_IMPACTS = frozenset({"low", "medium"})
FAILED_MESSAGE = "The investigation failed. It has been logged; please try again."
RATE_LIMIT_MESSAGE = (
    "You have started {limit} investigations in the last hour. "
    "Please wait a little before starting another one."
)
QUEUE_FULL_MESSAGE = "Many investigations are waiting right now. Please try again in a few minutes."
Decision = Literal["approved", "rejected"]


class InvestigatorPort(Protocol):
    """What the service needs from the agent (Investigator satisfies it)."""

    async def run_investigation(
        self,
        user: UserContext,
        question: str,
        *,
        investigation_id: str | None = None,
        on_event: TraceListener | None = None,
    ) -> InvestigationResult:
        """Run one investigation."""
        ...


def public_result(result: InvestigationResult) -> dict[str, Any]:
    """Return the result as stored and shown: no evidence rows, proposals kept separately."""
    payload = result.model_dump(mode="json", exclude={"proposals"})
    for record in payload["evidence"]:
        # Security: rows hold untrusted ERP text (buyer notes); the browser never gets them.
        record.pop("rows", None)
    return payload


def proposal_view(row: ProposalRow) -> dict[str, Any]:
    """Return a proposal with its decision state for the API."""
    return {
        "action_id": row.action_id,
        "investigation_id": row.investigation_id,
        "kind": row.kind,
        "supplier_id": row.supplier_id,
        "impact": row.impact,
        "status": row.status,
        "evidence_ids": row.payload.get("evidence_ids", []),
        "rationale": row.payload.get("rationale", ""),
        "decided_by": row.decided_by,
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
        "comment": row.comment,
    }


class InvestigationService:
    """Queue, worker, limits, access rules and erasure of investigations."""

    def __init__(
        self,
        settings: Settings,
        store: Store,
        investigator: InvestigatorPort | None,
        *,
        hub: EventHub | None = None,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Create the service (call start() inside the running event loop).

        Args:
            settings: Queue length, per-user limit, retention and trace directory.
            store: App database.
            investigator: Runs investigations; None means the API cannot start any (503).
            hub: Live events.
            clock: Monotonic clock for the per-user limit.
            now: Wall clock (UTC) for stored times.
        """
        self._settings = settings
        self.store = store
        self._investigator = investigator
        self.hub = hub or EventHub()
        self._clock = clock
        self._now = now
        self._pending: deque[str] = deque()
        self._running: str | None = None
        self._current: asyncio.Task[None] | None = None
        self._started: dict[str, deque[float]] = {}
        self._streams: dict[str, int] = {}
        self._wakeup: asyncio.Event | None = None
        self._worker: asyncio.Task[None] | None = None

    # --- lifecycle ----------------------------------------------------------------------------

    def start(self) -> None:
        """Purge old data, close interrupted work and start the worker."""
        cutoff = self._now() - timedelta(days=self._settings.retention_days)
        purged = self.store.purge_older_than(cutoff)
        for investigation_id in purged:
            self._remove_trace(investigation_id)
        interrupted = self.store.mark_interrupted()
        logger.info(
            "retention purge done",
            extra={
                "event": "retention_purged",
                "purged": len(purged),
                "interrupted": len(interrupted),
                "retention_days": self._settings.retention_days,
            },
        )
        self._wakeup = asyncio.Event()
        if self._investigator is not None:
            self._worker = asyncio.create_task(self._work(), name="investigation-worker")

    async def stop(self) -> None:
        """Stop the worker (a running investigation is cancelled and stays unfinished)."""
        if self._worker is not None:
            self._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker
            self._worker = None

    # --- queue --------------------------------------------------------------------------------

    def _check_hourly_limit(self, user: UserContext) -> deque[float]:
        started = self._started.setdefault(user.user_id, deque())
        cutoff = self._clock() - HOUR_SECONDS
        while started and started[0] <= cutoff:
            started.popleft()
        limit = self._settings.investigations_per_user_per_hour
        if len(started) >= limit:
            logger.warning(
                "per-user limit reached", extra={"event": "user_rate_limited", "role": user.role}
            )
            raise AppError(429, "rate_limited", RATE_LIMIT_MESSAGE.format(limit=limit))
        return started

    def submit(self, user: UserContext, question: str) -> tuple[str, int]:
        """Queue an investigation for the user.

        Returns:
            The new id and its queue position (1 = next).

        Raises:
            AppError: 503 without an investigator, 429 over the per-user limit or a full queue.
        """
        if self._investigator is None or self._wakeup is None:
            raise AppError(503, "unavailable", "Investigations are not available right now.")
        started = self._check_hourly_limit(user)
        if len(self._pending) >= self._settings.queue_max_length:
            logger.warning(
                "queue full", extra={"event": "queue_full", "queued": len(self._pending)}
            )
            raise AppError(429, "queue_full", QUEUE_FULL_MESSAGE)
        investigation_id = new_investigation_id()
        self.store.create_investigation(
            investigation_id, user.user_id, user.role, user.category, question, self._now()
        )
        # Counted when accepted, in memory: deleting investigations does not reset the limit.
        started.append(self._clock())
        self.hub.open(investigation_id)
        self._pending.append(investigation_id)
        self._wakeup.set()
        position = len(self._pending)
        status = {"status": "queued", "queue_position": position}
        self.hub.publish(investigation_id, "status", status)
        logger.info(
            "investigation queued",
            extra={
                "event": "investigation_queued",
                "investigation_id": investigation_id,
                "role": user.role,
                "queue_position": position,
            },
        )
        return investigation_id, position

    def queue_position(self, investigation_id: str) -> int | None:
        """Return 1.. while waiting, 0 while running, None otherwise."""
        if investigation_id == self._running:
            return 0
        try:
            return self._pending.index(investigation_id) + 1
        except ValueError:
            return None

    async def _work(self) -> None:
        assert self._wakeup is not None  # noqa: S101 - set by start()
        while True:
            await self._wakeup.wait()
            while self._pending:
                investigation_id = self._pending.popleft()
                self._running = investigation_id
                self._current = asyncio.create_task(self._run_one(investigation_id))
                try:
                    # wait() does not raise when the child is cancelled by a delete.
                    await asyncio.wait({self._current})
                except asyncio.CancelledError:
                    self._current.cancel()
                    raise
                finally:
                    self._running = None
                    self._current = None
                    self._publish_positions()
            self._wakeup.clear()

    def _publish_positions(self) -> None:
        for position, waiting in enumerate(self._pending, start=1):
            self.hub.publish(waiting, "status", {"status": "queued", "queue_position": position})

    async def _run_one(self, investigation_id: str) -> None:
        row = self.store.get_investigation(investigation_id)
        if row is None or row.status != "queued":
            return
        assert self._investigator is not None  # noqa: S101 - the worker runs only with one
        self.store.set_status(investigation_id, "running")
        self.hub.publish(investigation_id, "status", {"status": "running", "queue_position": 0})
        user = UserContext(user_id=row.user_id, role=row.role, category=row.category)  # type: ignore[arg-type]

        def on_event(event: Any) -> None:  # noqa: ANN401 - a TraceEvent
            self.hub.publish(investigation_id, "trace", event.model_dump(mode="json"))

        status = "failed"
        try:
            result = await self._investigator.run_investigation(
                user, row.question, investigation_id=investigation_id, on_event=on_event
            )
            saved = self.store.save_result(
                investigation_id,
                status=result.status,
                reason=result.reason,
                payload=public_result(result),
                proposals=[p.model_dump(mode="json") for p in result.proposals],
                model_calls=result.model_calls,
                at=self._now(),
            )
            if saved:
                status = result.status
            else:
                # Deleted while it ran: the trace file written meanwhile goes too.
                self._remove_trace(investigation_id)
                status = "deleted"
        except Exception:
            # Security (SEC-104): a crash or a store error still ends the investigation, so it
            # never stays "running" and the stream always gets "done".
            logger.exception(
                "investigation failed",
                extra={"event": "investigation_failed", "investigation_id": investigation_id},
            )
            self._mark_failed(investigation_id)
        finally:
            self.hub.close(investigation_id, status)

    def _mark_failed(self, investigation_id: str) -> None:
        """Store a failed result; if even that fails, set the status alone (best effort)."""
        try:
            self.store.save_result(
                investigation_id,
                status="failed",
                reason="internal_error",
                payload={},
                proposals=[],
                model_calls=0,
                at=self._now(),
            )
        except Exception:
            logger.exception(
                "failed result not stored",
                extra={"event": "store_write_failed", "investigation_id": investigation_id},
            )
            with contextlib.suppress(Exception):
                self.store.set_status(investigation_id, "failed", "internal_error")

    # --- reading ------------------------------------------------------------------------------

    def visible(self, user: UserContext, investigation_id: str) -> InvestigationRow | None:
        """Return the investigation if the user may read it (owner, or director)."""
        if not INVESTIGATION_ID_PATTERN.fullmatch(investigation_id):
            return None
        row = self.store.get_investigation(investigation_id)
        if row is None:
            return None
        if row.user_id == user.user_id or user.role == "director":
            return row
        return None

    def summary(self, row: InvestigationRow) -> dict[str, Any]:
        """Return the list view of an investigation (no result)."""
        return {
            "id": row.id,
            "question": row.question,
            "status": row.status,
            "reason": row.reason,
            "owner": row.user_id,
            "created_at": row.created_at.isoformat(),
            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
            "queue_position": self.queue_position(row.id),
        }

    def detail(self, row: InvestigationRow) -> dict[str, Any]:
        """Return the full view: summary, result, message and proposals with decisions."""
        result = self.store.get_result(row.id) or None
        message = FAILED_MESSAGE if row.status == "failed" else None
        if result is not None and result.get("message"):
            message = result["message"]
        proposals = self.store.list_proposals(investigation_id=row.id)
        return {
            **self.summary(row),
            "model_calls": row.model_calls,
            "message": message,
            "result": result,
            "proposals": [proposal_view(p) for p in proposals],
        }

    def list_for(self, user: UserContext) -> list[dict[str, Any]]:
        """Return the user's investigations (a director sees everyone's), newest first."""
        owner = None if user.role == "director" else user.user_id
        return [self.summary(r) for r in self.store.list_investigations(user_id=owner)]

    def subscribe(self, row: InvestigationRow) -> Subscription:
        """Return the live events, or a replay of the stored trace once finished."""
        live = self.hub.subscribe(row.id)
        if live is not None:
            return live
        result = self.store.get_result(row.id) or {}
        replay = [("trace", event) for event in result.get("trace", [])]
        return Subscription([*replay, ("done", {"status": row.status})])

    def unsubscribe(self, row: InvestigationRow, subscription: Subscription) -> None:
        """Stop feeding a live subscription."""
        self.hub.unsubscribe(row.id, subscription)

    def check_stream_limit(self, user: UserContext) -> None:
        """Refuse a new stream while the user has MAX_STREAMS_PER_USER open.

        Raises:
            AppError: 429 when the user already has that many open.
        """
        if self._streams.get(user.user_id, 0) >= MAX_STREAMS_PER_USER:
            raise AppError(429, "too_many_streams", "Too many open progress streams.")

    @contextlib.contextmanager
    def stream_slot(self, user: UserContext) -> Any:  # noqa: ANN401 - a context manager
        """Count one open stream of the user while the block runs."""
        self._streams[user.user_id] = self._streams.get(user.user_id, 0) + 1
        try:
            yield
        finally:
            self._streams[user.user_id] -= 1

    # --- erasure ------------------------------------------------------------------------------

    def _trace_path(self, investigation_id: str) -> Path:
        return self._settings.trace_dir / f"{investigation_id}.jsonl"

    def _remove_trace(self, investigation_id: str) -> None:
        if INVESTIGATION_ID_PATTERN.fullmatch(investigation_id):
            self._trace_path(investigation_id).unlink(missing_ok=True)

    def delete(self, user: UserContext, investigation_id: str) -> bool:
        """Erase the user's own investigation with its result, proposals, decisions and trace.

        Returns:
            True if it existed and belonged to the user.
        """
        row = self.visible(user, investigation_id)
        if row is None or row.user_id != user.user_id:
            return False
        with contextlib.suppress(ValueError):
            self._pending.remove(investigation_id)
        if self._running == investigation_id and self._current is not None:
            self._current.cancel()
        self.store.delete_investigation(investigation_id)
        self._remove_trace(investigation_id)
        self.hub.close(investigation_id, "deleted")
        self._publish_positions()
        logger.info(
            "investigation deleted",
            extra={"event": "investigation_deleted", "investigation_id": investigation_id},
        )
        return True

    # --- proposals ----------------------------------------------------------------------------

    def inbox(self, user: UserContext) -> list[dict[str, Any]]:
        """Return the pending proposals the user may decide.

        Raises:
            AppError: 403 for roles that never decide.
        """
        if user.role not in DECIDERS:
            raise AppError(403, "forbidden", "Only buyers and risk managers decide on proposals.")
        owner = user.user_id if user.role == "buyer" else None
        rows = self.store.list_proposals(owner_id=owner, status="pending")
        if user.role == "buyer":
            rows = [r for r in rows if r.impact in BUYER_IMPACTS]
        return [proposal_view(r) for r in rows]

    def decide(
        self, user: UserContext, action_id: str, decision: Decision, comment: str | None
    ) -> dict[str, Any]:
        """Record a human decision on a proposal. Nothing is executed.

        Raises:
            AppError: 403 for a role or impact the user may not decide, 404 for a proposal the
                user cannot see, 409 when it was already decided.
        """
        if user.role not in DECIDERS:
            raise AppError(403, "forbidden", "Only buyers and risk managers decide on proposals.")
        proposal = self.store.get_proposal(action_id)
        if proposal is None or (user.role == "buyer" and proposal.owner_id != user.user_id):
            raise AppError(404, "not_found", "Proposal not found.")
        if user.role == "buyer" and proposal.impact not in BUYER_IMPACTS:
            raise AppError(403, "forbidden", "A high-impact proposal needs a risk manager.")
        try:
            decided = self.store.decide(
                action_id, decision, user.user_id, user.role, comment, self._now()
            )
        except AlreadyDecided:
            raise AppError(409, "already_decided", "This proposal was already decided.") from None
        except KeyError:
            raise AppError(404, "not_found", "Proposal not found.") from None
        logger.info(
            "proposal decided",
            extra={
                "event": "proposal_decided",
                "investigation_id": decided.investigation_id,
                "action_id": action_id,
                "decision": decision,
                "impact": decided.impact,
                "role": user.role,
            },
        )
        return proposal_view(decided)
