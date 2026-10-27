"""Integration: the case board client against the running context service.

Needs: docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d context-service, and
CONTEXT_SERVICE_TOKEN set in .env (the same value the container uses).
"""

import asyncio
import uuid

import pytest

from app.core.settings import Settings
from app.memory.world_model import CaseEvent, WorldModelClient, WorldModelRejected

pytestmark = pytest.mark.integration


@pytest.fixture
async def board(live_settings: Settings) -> WorldModelClient:
    if live_settings.context_service_token is None:
        pytest.skip("CONTEXT_SERVICE_TOKEN is not set")
    client = WorldModelClient(live_settings)
    if not await client.health():
        await client.aclose()
        pytest.skip("context service is not running")
    return client


async def test_two_research_agents_write_one_case_and_the_evaluator_reads_both(
    board: WorldModelClient,
) -> None:
    """Architecture §7.4 (World Model): concurrent writers, no lost events, idempotent replays."""
    case_id = f"it-{uuid.uuid4().hex[:12]}"
    financial = [
        CaseEvent(
            kind="evidence_found",
            agent="financial",
            summary=f"Financial finding {i}",
            evidence_ids=[f"ev-fin-{i}"],
            subjects={"supplier": "S0001"},
        )
        for i in range(10)
    ]
    country = [
        CaseEvent(
            kind="evidence_found",
            agent="country",
            summary=f"Country finding {i}",
            evidence_ids=[f"ev-cty-{i}"],
            subjects={"country": "DE"},
        )
        for i in range(10)
    ]
    try:
        outcomes = await asyncio.gather(
            *(board.record_event(case_id, e) for e in [*financial, *country])
        )
        assert all(o.status == "stored" for o in outcomes)
        # A retried write (same event id) is answered as a duplicate and writes nothing.
        replay = await board.record_event(case_id, financial[0])
        assert replay.status == "duplicate"

        timeline = await board.timeline(case_id)
        assert [e.seq for e in timeline] == list(range(1, 21))
        assert {e.event_id for e in timeline} == {e.event_id for e in [*financial, *country]}
        assert {e.agent for e in timeline} == {"financial", "country"}

        supplier = await board.entity("case:board:" + case_id)
        assert supplier is not None
        assert len(supplier.references) == 20 or supplier.truncated

        with pytest.raises(WorldModelRejected) as info:
            await board.append_event(case_id, financial[0].model_copy(update={"summary": "edited"}))
        assert info.value.code == "event_conflict"
    finally:
        await board.aclose()
