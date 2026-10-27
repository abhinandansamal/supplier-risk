"""Live investigation: real Nightshift, Zetaris (warmed snapshot cache) and the context service.

"Why is Microchip (S0003) risky?" as risk_manager. Saves the result with latency per step and
in total to docs/test-runs/, before the asserts, so a failing run is kept for inspection.
At most INVESTIGATION_CALL_BUDGET (15) Nightshift calls.
"""

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.agent.graph import Investigator
from app.agent.models import InvestigationResult
from app.core.settings import Settings
from app.llm.client import MeterlessClient
from app.memory.world_model import WorldModelClient
from app.security.identity import UserContext
from app.tools.cache import SnapshotCache
from app.tools.views import ERP_VIEWS

pytestmark = [pytest.mark.integration, pytest.mark.slow_live]

ROOT = Path(__file__).resolve().parents[3]
QUESTION = "Why is Microchip (S0003) risky?"
OUT = ROOT / "docs" / "test-runs" / "2026-10-27-feat-agent-graph-live-S0003.json"


def _latency(result: InvestigationResult, warm_ms: int, total_ms: int) -> dict[str, Any]:
    """Return model latency per agent, wall time per step and the totals."""
    model_ms: dict[str, int] = {}
    for event in result.trace:
        if event.action == "model_call":
            model_ms[event.agent] = model_ms.get(event.agent, 0) + (event.latency_ms or 0)
    steps = []
    previous = result.trace[0].at if result.trace else None
    for event in result.trace:
        if event.action == "step_done" and previous is not None:
            wall = round((event.at - previous).total_seconds() * 1000)
            steps.append({"agent": event.agent, "step": event.step, "wall_ms": wall})
            previous = event.at
    return {
        "warm_cache_ms": warm_ms,
        "investigation_ms": result.duration_ms,
        "total_ms": total_ms,
        "model_ms_per_agent": model_ms,
        "model_ms_total": sum(model_ms.values()),
        "steps": steps,
    }


async def test_microchip_investigation_cites_the_delivery_critical_signal(
    live_settings: Settings,
) -> None:
    if live_settings.zetaris_key_risk is None or live_settings.context_service_token is None:
        pytest.skip("ZETARIS_KEY_RISK and CONTEXT_SERVICE_TOKEN must be set")
    cache = SnapshotCache(live_settings)
    llm = MeterlessClient(live_settings)
    board = WorldModelClient(live_settings)
    try:
        assert await board.health(), "context service is not answering /health"
        assert (await llm.status()).state == "online", "Nightshift is not online"
        started = time.monotonic()
        warm = await cache.warm_all()
        warm_ms = round((time.monotonic() - started) * 1000)
        failed = sorted(k for k, v in warm.items() if v != "ok")
        # SEC/Eurostat may fail upstream; that lowers coverage. ERP views must load.
        assert not [k for k in failed if k.split("/")[1] in ERP_VIEWS], failed
        investigator = Investigator(
            live_settings, llm=llm, cache=cache, board_client=board, trace_dir=ROOT / "runs"
        )
        user = UserContext(user_id="live-test", role="risk_manager")
        result = await investigator.run_investigation(user, QUESTION)
        total_ms = round((time.monotonic() - started) * 1000)
        timeline = await board.timeline(result.investigation_id)
    finally:
        await cache.aclose()
        await llm.aclose()
        await board.aclose()
    OUT.write_text(
        json.dumps(
            {
                "test": "test_microchip_investigation_cites_the_delivery_critical_signal",
                "run_at": datetime.now(UTC).isoformat(),
                "question": QUESTION,
                "role": "risk_manager",
                "models": live_settings.agent_step_models(),
                "latency": _latency(result, warm_ms, total_ms),
                "warm_failed": failed,
                "world_model_events": len(timeline),
                "result": result.model_dump(mode="json"),
            },
            indent=2,
        )
        + "\n"
    )
    assert result.status == "completed", (result.status, result.reason)
    assert result.model_calls <= 15
    score = result.score
    assert score is not None
    assert "delivery" in [s.component for s in score.critical_signals]
    delivery_ids = set(next(c for c in score.components if c.name == "delivery").evidence_ids)
    delivery_ids |= {e.evidence_id for e in result.evidence if e.tool == "delivery_history"}
    verified = [c for c in result.claims if c.verified]
    assert any(delivery_ids & set(c.evidence_ids) for c in verified)
    model_calls = [e for e in result.trace if e.action == "model_call"]
    assert model_calls and all(e.receipt_id for e in model_calls)
    assert result.memory == "available" and len(timeline) >= 5
