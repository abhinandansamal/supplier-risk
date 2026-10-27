"""In-memory fake of the World Model client (record_event and timeline only)."""

from app.memory.world_model import (
    CaseEvent,
    RecordOutcome,
    StoredCaseEvent,
    WorldModelUnavailable,
)


class FakeBoardClient:
    """Case board fake; with down=True it behaves like an unreachable context service.

    Attributes:
        cases: Stored events per case id.
        timeline_reads: Number of timeline calls.
    """

    def __init__(self, *, down: bool = False) -> None:
        """Create the fake.

        Args:
            down: Answer memory_unavailable to appends and raise on timeline reads.
        """
        self.down = down
        self.cases: dict[str, list[StoredCaseEvent]] = {}
        self.timeline_reads = 0

    async def record_event(self, case_id: str, event: CaseEvent) -> RecordOutcome:
        """Store the event (idempotent by event id) or report memory_unavailable."""
        if self.down:
            return RecordOutcome(status="memory_unavailable")
        events = self.cases.setdefault(case_id, [])
        for stored in events:
            if stored.event_id == event.event_id:
                return RecordOutcome(status="duplicate", seq=stored.seq)
        stored = StoredCaseEvent(**event.model_dump(), seq=len(events) + 1)
        events.append(stored)
        return RecordOutcome(status="stored", seq=stored.seq)

    async def timeline(self, case_id: str) -> list[StoredCaseEvent]:
        """Return the case's events, oldest first."""
        self.timeline_reads += 1
        if self.down:
            raise WorldModelUnavailable("context service unavailable")
        return list(self.cases.get(case_id, []))
