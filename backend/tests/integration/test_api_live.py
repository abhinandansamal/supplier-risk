"""Live end-to-end run through the API: login, queue, SSE trace and result for S0003.

Real Nightshift (at most INVESTIGATION_CALL_BUDGET calls), Zetaris (the app warms the cache in
the background; the test waits until /ready says it is warm) and the context service. The app
database is a temporary SQLite file and the demo password is set by the test, so .env needs
no API settings. Saves what it saw to docs/test-runs/ before the asserts.
"""

import json
import secrets
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

import app.main as main
from app.core.settings import Settings

pytestmark = [pytest.mark.integration, pytest.mark.slow_live]

ROOT = Path(__file__).resolve().parents[3]
QUESTION = "Why is Microchip (S0003) risky?"
OUT = ROOT / "docs" / "test-runs" / "2026-10-27-feat-api-ui-live-S0003.json"
WARM_TIMEOUT_SECONDS = 900
RUN_TIMEOUT_SECONDS = 1200


def _sse(client: TestClient, investigation_id: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    kind = "message"
    url = f"/api/v1/investigations/{investigation_id}/events"
    with client.stream("GET", url, timeout=RUN_TIMEOUT_SECONDS) as stream:
        for line in stream.iter_lines():
            if line.startswith("event: "):
                kind = line.removeprefix("event: ")
            elif line.startswith("data: "):
                events.append((kind, json.loads(line.removeprefix("data: "))))
                if kind == "done":
                    break
    return events


def test_s0003_through_the_api_streams_the_trace_and_cites_delivery(
    live_settings: Settings, tmp_path: Path
) -> None:
    if live_settings.zetaris_key_risk is None or live_settings.context_service_token is None:
        pytest.skip("ZETARIS_KEY_RISK and CONTEXT_SERVICE_TOKEN must be set")
    password = secrets.token_urlsafe(16)
    settings = live_settings.model_copy(
        update={
            "app_database_url": SecretStr(f"sqlite:///{tmp_path}/app.db"),
            "jwt_secret": SecretStr(secrets.token_hex(32)),
            "demo_risk_password": SecretStr(password),
            "trace_dir": ROOT / "runs",
        }
    )
    started = time.monotonic()
    with TestClient(main._build(settings)) as client:
        deadline = time.monotonic() + WARM_TIMEOUT_SECONDS
        while client.get("/ready").json().get("cache") == "warming":
            assert time.monotonic() < deadline, "cache warm-up did not finish"
            time.sleep(5)
        warm_ms = round((time.monotonic() - started) * 1000)
        login = client.post(
            "/api/v1/auth/login", json={"username": "risk_manager", "password": password}
        )
        assert login.status_code == 200
        accepted = client.post("/api/v1/investigations", json={"question": QUESTION})
        assert accepted.status_code == 202
        investigation_id = accepted.json()["id"]
        events = _sse(client, investigation_id)
        body = client.get(f"/api/v1/investigations/{investigation_id}").json()
    total_ms = round((time.monotonic() - started) * 1000)
    trace = [data for kind, data in events if kind == "trace"]
    OUT.write_text(
        json.dumps(
            {
                "test": "test_s0003_through_the_api_streams_the_trace_and_cites_delivery",
                "run_at": datetime.now(UTC).isoformat(),
                "question": QUESTION,
                "role": "risk_manager",
                "warm_cache_ms": warm_ms,
                "total_ms": total_ms,
                "sse_events": len(events),
                "sse_trace_events": len(trace),
                "investigation": body,
            },
            indent=2,
        )
        + "\n"
    )
    assert events[-1] == ("done", {"status": "completed"}), events[-1]
    assert body["status"] == "completed" and body["model_calls"] <= 15
    result = body["result"]
    model_calls = [t for t in trace if t["action"] == "model_call"]
    assert model_calls and all(t["receipt_id"] for t in model_calls)
    assert len(trace) == len(result["trace"])
    assert all("rows" not in e for e in result["evidence"])
    score = result["scores"][0]
    assert "delivery" in [s["component"] for s in score["critical_signals"]]
    delivery_ids = {e["evidence_id"] for e in result["evidence"] if e["tool"] == "delivery_history"}
    delivery = next(c for c in score["components"] if c["name"] == "delivery")
    delivery_ids |= set(delivery["evidence_ids"])
    claims = [c for c in result["report"]["claims"] if c["verified"]]
    assert any(delivery_ids & set(c["evidence_ids"]) for c in claims)
