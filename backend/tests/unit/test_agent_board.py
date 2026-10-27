"""Tests for the agent's case board: World Model writes, in-process copy, scoped reads."""

import pytest
from tests.fakes.board import FakeBoardClient

from app.agent.board import CaseBoard, scope_entity_view
from app.memory.world_model import (
    CaseEvent,
    EntityReference,
    EntityView,
    RecordOutcome,
    WorldModelRejected,
)


async def test_events_go_to_the_world_model_and_the_local_copy() -> None:
    client = FakeBoardClient()
    board = CaseBoard(client, "inv-1", suppliers={"S0003"})
    await board.add("evidence_found", "erp", "delivery_history returned ok.", ["ev-1"], "S0003")
    assert board.memory == "available"
    assert [e.kind for e in client.cases["inv-1"]] == ["evidence_found"]
    timeline = await board.timeline()
    assert [(e.kind, e.subjects.supplier) for e in timeline] == [("evidence_found", "S0003")]


async def test_board_keeps_working_in_process_when_memory_is_unavailable() -> None:
    client = FakeBoardClient(down=True)
    board = CaseBoard(client, "inv-1", suppliers={"S0003"})
    await board.add("plan_created", "planner", "Plan: delivery.")
    await board.add("gap_found", "evaluator", "Gap in weather.")
    assert board.memory == "unavailable"
    assert [e.kind for e in await board.timeline()] == ["plan_created", "gap_found"]
    # After the first failure the service is not asked again (each try costs its deadline).
    assert client.timeline_reads == 0


async def test_a_failing_timeline_read_falls_back_to_the_local_copy() -> None:
    client = FakeBoardClient()
    board = CaseBoard(client, "inv-1", suppliers={"S0003"})
    await board.add("plan_created", "planner", "Plan: delivery.")
    client.down = True
    assert [e.kind for e in await board.timeline()] == ["plan_created"]
    assert board.memory == "unavailable"


async def test_board_without_a_client_is_in_process_only() -> None:
    board = CaseBoard(None, "inv-1", suppliers=set())
    await board.add("input_refused", "guard", "Question refused: instruction_like.")
    assert board.memory == "unavailable"
    assert len(await board.timeline()) == 1


async def test_timeline_drops_events_about_suppliers_outside_the_scope() -> None:
    """SEC-084: a read only returns events about this investigation's suppliers."""
    client = FakeBoardClient()
    board = CaseBoard(client, "inv-1", suppliers={"S0003"})
    await board.add("evidence_found", "erp", "ok.", ["ev-1"], "S0003")
    other = CaseBoard(client, "inv-1", suppliers={"S0001"})
    await other.add("evidence_found", "erp", "planted.", ["ev-2"], "S0001")
    assert [e.evidence_ids for e in await board.timeline()] == [["ev-1"]]


async def test_invalid_events_are_bugs_and_raise() -> None:
    board = CaseBoard(FakeBoardClient(), "inv-1", suppliers=set())
    with pytest.raises(ValueError):
        await board.add("evidence_found", "erp", "")


async def test_rejected_conflicts_still_raise() -> None:
    class Conflicting(FakeBoardClient):
        async def record_event(self, case_id: str, event: CaseEvent) -> RecordOutcome:
            raise WorldModelRejected("conflict", 409, "event_conflict")

    board = CaseBoard(Conflicting(), "inv-1", suppliers=set())
    with pytest.raises(WorldModelRejected):
        await board.add("plan_created", "planner", "Plan.")


def test_scope_entity_view_keeps_only_visible_cases() -> None:
    """SEC-084 for the API (Feature 7b): an entity lists only the caller's investigations."""
    view = EntityView(
        id="supplier:erp:S0003",
        type="supplier",
        attrs={},
        created_at="2026-10-27T10:00:00.000Z",
        updated_at="2026-10-27T10:00:00.000Z",
        references=[
            EntityReference(case_id="inv-mine", event_id="e1", kind="evidence_found"),
            EntityReference(case_id="inv-other", event_id="e2", kind="evidence_found"),
        ],
        truncated=False,
    )
    scoped = scope_entity_view(view, {"inv-mine"})
    assert [r.case_id for r in scoped.references] == ["inv-mine"]
