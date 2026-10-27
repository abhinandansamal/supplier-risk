"""Fake Nightshift, Zetaris and context service for load tests (one process, three ports).

Thousands of virtual users must never reach the real NVIDIA free tier (40 requests/minute),
the local Zetaris or the context service. This module serves all three on loopback with
configurable latency, error rate and HTTP 429 injection. The answers reuse the unit-test fakes:
the model replies come from backend/tests/fakes/llm.py (default_reply per agent, recognised by
the "# <agent> prompt vN" first line of the system prompt) and the Zetaris rows from
backend/tests/fakes/zetaris.py (seed-42 ERP, story facts), so the real agent graph runs end to
end and its verifier accepts the claims.

Run: uv run python loadtests/fake_upstreams.py (see loadtests/README.md for the settings).
Every fake has GET /_stats with request and injected-failure counts.
"""

import asyncio
import json
import os
import random
import re
import sys
import time
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend")]

from tests.fakes.llm import FakeCall, default_reply  # noqa: E402
from tests.fakes.zetaris import BUYER_KEY, BUYER_VIEWS, RISK_KEY, view_rows  # noqa: E402

AGENT_LINE = re.compile(r"^# ([a-z_]+) prompt v[0-9]+")
FROM_VIEW = re.compile(r"\bFROM\s+risk\.(v_[a-z_]+)\s*$", re.IGNORECASE)
# Bounds the memory of the fake case board across a long run.
MAX_CASES = 5000


def _env_float(name: str, default: float) -> float:
    """Return a float setting from the environment."""
    return float(os.environ.get(name, default))


class Faults:
    """Latency and failure injection of one fake, read from FAKE_<PREFIX>_* variables.

    Attributes:
        latency_ms: Mean added latency per request.
        jitter: Relative spread of the latency (0.5 = +-50 %).
        error_rate: Share of requests answered 503.
        rate_limit_rate: Share of requests answered 429 with Retry-After.
        retry_after: Seconds sent in Retry-After.
        stats: Request and injected-failure counts.
    """

    def __init__(self, prefix: str, latency_ms: float) -> None:
        """Read the settings of one fake.

        Args:
            prefix: LLM, ZETARIS or CONTEXT.
            latency_ms: Default mean latency.
        """
        self.latency_ms = _env_float(f"FAKE_{prefix}_LATENCY_MS", latency_ms)
        self.jitter = _env_float(f"FAKE_{prefix}_JITTER", 0.5)
        self.error_rate = _env_float(f"FAKE_{prefix}_ERROR_RATE", 0.0)
        self.rate_limit_rate = _env_float(f"FAKE_{prefix}_429_RATE", 0.0)
        self.retry_after = _env_float(f"FAKE_{prefix}_RETRY_AFTER", 2.0)
        self.stats: Counter[str] = Counter()

    async def apply(self, kind: str) -> Response | None:
        """Wait the injected latency; return an injected failure response or None."""
        self.stats[kind] += 1
        spread = 1 + self.jitter * (2 * random.random() - 1)  # noqa: S311 - not crypto
        await asyncio.sleep(max(0.0, self.latency_ms * spread) / 1000)
        roll = random.random()  # noqa: S311 - not crypto
        if roll < self.rate_limit_rate:
            self.stats["injected_429"] += 1
            headers = {"Retry-After": str(int(self.retry_after))}
            return JSONResponse({"error": "rate limited"}, status_code=429, headers=headers)
        if roll < self.rate_limit_rate + self.error_rate:
            self.stats["injected_503"] += 1
            return JSONResponse({"error": "unavailable"}, status_code=503)
        return None


def build_llm() -> FastAPI:
    """Return the fake OpenAI-compatible Nightshift runtime."""
    app = FastAPI()
    faults = Faults("LLM", 1500.0)
    counter = {"n": 0}

    @app.get("/v1/models")
    async def models() -> dict[str, Any]:
        """List the model ids the app pins."""
        names = ("MODEL", "DEEP_MODEL", "FAST_MODEL", "EMBED_MODEL")
        ids = [os.environ.get(f"METERLESS_{n}", f"fake/{n.lower()}") for n in names]
        return {"object": "list", "data": [{"id": i, "object": "model"} for i in ids]}

    @app.post("/v1/chat/completions")
    async def chat(request: Request) -> Response:
        """Answer like the agent's scripted default, as OpenAI tool calls or content."""
        failure = await faults.apply("chat")
        if failure is not None:
            return failure
        body = await request.json()
        messages = [m for m in body["messages"] if not str(m["content"]).startswith("request-id:")]
        match = AGENT_LINE.match(str(messages[0]["content"]).splitlines()[0])
        if match is None:
            return JSONResponse({"error": "unknown prompt"}, status_code=400)
        call = FakeCall(match.group(1), "default", messages, body.get("tools"))
        reply = default_reply(call)
        counter["n"] += 1
        message: dict[str, Any] = {"role": "assistant", "content": None}
        if isinstance(reply, list):
            message["tool_calls"] = [
                {
                    "id": f"call_{counter['n']}_{i}",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                }
                for i, (name, args) in enumerate(reply)
            ]
        else:
            message["content"] = reply
        return JSONResponse(
            {
                "id": f"chatcmpl-{counter['n']}",
                "object": "chat.completion",
                "model": body.get("model"),
                "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
                "meterless": {
                    "receipt_id": f"rcpt-load-{counter['n']:07d}",
                    "routed_model": body.get("model"),
                    "cache": {"status": "miss"},
                },
            }
        )

    @app.get("/_stats")
    async def stats() -> dict[str, int]:
        """Return the request counts."""
        return dict(faults.stats)

    return app


def build_zetaris() -> FastAPI:
    """Return the fake Zetaris SQL endpoint serving the nine risk views."""
    app = FastAPI()
    faults = Faults("ZETARIS", 150.0)
    rows = view_rows()

    @app.post("/api/v1.0/sql-editor/sqls/run")
    async def run(request: Request) -> Response:
        """Answer one fixed-view SELECT with the view's rows (string cells, no names)."""
        failure = await faults.apply("sql")
        if failure is not None:
            return failure
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        key = {BUYER_KEY: "buyer", RISK_KEY: "risk"}.get(token)
        if key is None:
            return Response(status_code=401)
        body = await request.json()
        match = FROM_VIEW.search(body["sql"])
        if match is None:
            return JSONResponse({"error": "unknown sql"}, status_code=400)
        view = match.group(1)
        # Security: mirrors the real row/column grants of the buyer key.
        if key == "buyer" and view not in BUYER_VIEWS:
            return JSONResponse({"error": "does not have privileges"}, status_code=403)
        return JSONResponse({"data": rows[view][: body["limit"]]})

    @app.get("/_stats")
    async def stats() -> dict[str, int]:
        """Return the request counts."""
        return dict(faults.stats)

    return app


def build_context() -> FastAPI:
    """Return the fake context service: /health and the case board append and timeline."""
    app = FastAPI()
    faults = Faults("CONTEXT", 20.0)
    cases: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Return ok."""
        return {"status": "ok"}

    @app.post("/cases/{case_id}/events")
    async def append(case_id: str, request: Request) -> Response:
        """Store an event idempotently by event id and return its seq."""
        failure = await faults.apply("append")
        if failure is not None:
            return failure
        stored, duplicate = _store_event(cases, case_id, await request.json())
        body = _appended(case_id, stored, duplicate=duplicate)
        return JSONResponse(body, status_code=200 if duplicate else 201)

    @app.get("/cases/{case_id}/timeline")
    async def timeline(case_id: str) -> Response:
        """Return the case's events, or the service's own case_not_found 404."""
        failure = await faults.apply("timeline")
        if failure is not None:
            return failure
        if case_id not in cases:
            return JSONResponse({"code": "case_not_found", "message": "no case"}, 404)
        return JSONResponse({"case_id": case_id, "events": cases[case_id]})

    @app.get("/_stats")
    async def stats() -> dict[str, int]:
        """Return the request counts."""
        return {**faults.stats, "cases": len(cases)}

    return app


def _store_event(
    cases: OrderedDict[str, list[dict[str, Any]]], case_id: str, event: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    """Store an event idempotently by event id; return it with seq and whether it existed."""
    events = cases.setdefault(case_id, [])
    cases.move_to_end(case_id)
    while len(cases) > MAX_CASES:
        cases.popitem(last=False)
    for stored in events:
        if stored["event_id"] == event["event_id"]:
            return stored, True
    stored = {**event, "seq": len(events) + 1}
    events.append(stored)
    return stored, False


def _appended(case_id: str, stored: dict[str, Any], *, duplicate: bool) -> dict[str, Any]:
    """Return the append answer the World Model client expects."""
    return {
        "case_id": case_id,
        "event_id": stored["event_id"],
        "seq": stored["seq"],
        "duplicate": duplicate,
    }


async def serve() -> None:
    """Serve the three fakes on loopback until interrupted."""
    ports = {
        "llm": int(os.environ.get("FAKE_LLM_PORT", "18088")),
        "zetaris": int(os.environ.get("FAKE_ZETARIS_PORT", "18888")),
        "context": int(os.environ.get("FAKE_CONTEXT_PORT", "17070")),
    }
    builders = {"llm": build_llm, "zetaris": build_zetaris, "context": build_context}
    servers = [
        uvicorn.Server(
            uvicorn.Config(
                builders[name](), host="127.0.0.1", port=port, log_level="warning", access_log=False
            )
        )
        for name, port in ports.items()
    ]
    started = time.monotonic()
    await asyncio.gather(*(s.serve() for s in servers))
    sys.stderr.write(f"fakes stopped after {time.monotonic() - started:.0f} s\n")


if __name__ == "__main__":
    asyncio.run(serve())
