"""The agents' case board: World Model events with an in-process copy (architecture 7, 7.3).

Every event goes to the World Model through the existing client and into a local list. When
the context service is unavailable the investigation goes on with the local copy, and after
the first failure the service is not asked again for this investigation (each try would cost
its deadline). Summaries are fixed Python templates; model output never becomes a summary.

SEC-084: the service does not scope reads. The agent reads only its own case and drops events
about suppliers outside the investigation's resolved scope; scope_entity_view() does the same
for entity views, for the API (Feature 7b).
"""

from collections.abc import Collection, Mapping, Sequence
from typing import Literal, Protocol

from app.memory.world_model import (
    AgentName,
    CaseEvent,
    DataValue,
    EntityView,
    EventKind,
    RecordOutcome,
    StoredCaseEvent,
    Subjects,
    WorldModelError,
)

MAX_EVENT_EVIDENCE_IDS = 50


class BoardClient(Protocol):
    """What the board needs from the World Model client (WorldModelClient satisfies it)."""

    async def record_event(self, case_id: str, event: CaseEvent) -> RecordOutcome:
        """Append an event or report memory_unavailable."""
        ...

    async def timeline(self, case_id: str) -> list[StoredCaseEvent]:
        """Return the case's events in order."""
        ...


class CaseBoard:
    """Case board of one investigation.

    Attributes:
        case_id: The investigation id.
        suppliers: Suppliers this investigation may read events about (set after the guard).
    """

    def __init__(
        self, client: BoardClient | None, case_id: str, *, suppliers: Collection[str]
    ) -> None:
        """Create the board.

        Args:
            client: World Model client, or None for an in-process board only.
            case_id: The investigation id.
            suppliers: Suppliers in scope.
        """
        self.case_id = case_id
        self.suppliers: set[str] = set(suppliers)
        self._client = client
        self._available = client is not None
        self._local: list[CaseEvent] = []

    @property
    def memory(self) -> Literal["available", "unavailable"]:
        """Return whether the World Model is in use for this investigation."""
        return "available" if self._available else "unavailable"

    async def add(
        self,
        kind: EventKind,
        agent: AgentName,
        summary: str,
        evidence_ids: Sequence[str] = (),
        supplier: str | None = None,
        data: Mapping[str, DataValue] | None = None,
    ) -> CaseEvent:
        """Record an event on the World Model (if available) and in the local copy.

        Args:
            kind: Event kind.
            agent: Writing agent.
            summary: Fixed-template summary (never model output or data text).
            evidence_ids: Evidence backing the event (at most 50 are kept).
            supplier: Supplier the event is about.
            data: Small flat details.

        Returns:
            The event.

        Raises:
            ValueError: If the event is invalid (a bug in the caller).
            WorldModelRejected: If the service reports an event id conflict (a bug).
        """
        event = CaseEvent(
            kind=kind,
            agent=agent,
            summary=summary,
            evidence_ids=list(evidence_ids)[:MAX_EVENT_EVIDENCE_IDS],
            subjects=Subjects(supplier=supplier),
            data=dict(data or {}),
        )
        self._local.append(event)
        if self._client is not None and self._available:
            outcome = await self._client.record_event(self.case_id, event)
            if outcome.status == "memory_unavailable":
                self._available = False
        return event

    async def timeline(self) -> list[CaseEvent]:
        """Return this case's events in order, limited to the suppliers in scope.

        Returns:
            Events from the World Model, or the local copy when it is unavailable.
        """
        events: Sequence[CaseEvent] = self._local
        if self._client is not None and self._available:
            try:
                events = await self._client.timeline(self.case_id)
            except WorldModelError:
                self._available = False
        return [e for e in events if e.subjects.supplier in (None, *self.suppliers)]


def scope_entity_view(view: EntityView, visible_case_ids: Collection[str]) -> EntityView:
    """Return an entity view that lists only the investigations the caller may see (SEC-084).

    Args:
        view: Entity view from the World Model (references across every case).
        visible_case_ids: Investigations the caller owns or may audit.

    Returns:
        A copy with the other investigations' references removed.
    """
    visible = set(visible_case_ids)
    return view.model_copy(
        update={"references": [r for r in view.references if r.case_id in visible]}
    )
