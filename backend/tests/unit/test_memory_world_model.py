"""Tests for app.memory.world_model: the case board client (respx, no network)."""

import json
import logging
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta, timezone

import httpx
import pytest
import respx
from pydantic import ValidationError

from app.core.logging import request_id_var
from app.core.settings import Settings
from app.memory.world_model import (
    CaseEvent,
    WorldModelBadResponse,
    WorldModelClient,
    WorldModelRejected,
    WorldModelUnavailable,
)

BASE = "http://localhost:7070"
TOKEN = "cs-secret-VALUE-7777-0123456789abcdef"


def _event(**overrides: object) -> CaseEvent:
    values: dict[str, object] = {
        "event_id": "e-0001",
        "kind": "evidence_found",
        "agent": "financial",
        "at": datetime(2026, 10, 27, 10, 0, tzinfo=UTC),
        "summary": "Revenue fell 12 % year on year.",
        "evidence_ids": ["ev-fin-1"],
        "subjects": {"supplier": "S0001"},
        "data": {"revenue_change_pct": -12},
    }
    values.update(overrides)
    return CaseEvent.model_validate(values)


def _stored(seq: int, **overrides: object) -> dict[str, object]:
    return {**_event(**overrides).to_wire(), "seq": seq}


@pytest.fixture
def board() -> respx.MockRouter:
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        yield router


@pytest.fixture
async def make_client(
    make_settings: Callable[..., Settings],
) -> AsyncIterator[Callable[..., WorldModelClient]]:
    clients: list[WorldModelClient] = []

    def factory(**overrides: object) -> WorldModelClient:
        client = WorldModelClient(make_settings(**overrides))
        clients.append(client)
        return client

    yield factory
    for client in clients:
        await client.aclose()


# --- CaseEvent ---------------------------------------------------------------------------------


def test_event_defaults_to_a_uuid_id_and_now_utc() -> None:
    event = CaseEvent(kind="gap_found", agent="evaluator", summary="No delivery data.")
    assert len(event.event_id) == 36
    assert event.at.tzinfo is not None
    assert datetime.now(UTC) - event.at < timedelta(seconds=5)


def test_event_serialises_time_as_utc_z() -> None:
    at = datetime(2026, 10, 27, 12, 0, tzinfo=timezone(timedelta(hours=2)))
    assert _event(at=at).to_wire()["at"] == "2026-10-27T10:00:00.000Z"


@pytest.mark.parametrize(
    "overrides",
    [
        {"kind": "drop_table"},
        {"agent": "attacker"},
        {"event_id": "../x"},
        {"summary": "x" * 501},
        {"summary": ""},
        {"subjects": {"supplier": "S1"}},
        {"subjects": {"person": "x"}},
        {"evidence_ids": [f"ev-{i}" for i in range(51)]},
        {"data": {"nested": {"a": 1}}},
        {"data": {f"k{i}": i for i in range(21)}},
        {"at": datetime(2026, 10, 27, 10, 0)},  # naive time
        {"role": "director"},
    ],
)
def test_event_rejects_invalid_input(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        _event(**overrides)


# --- append_event ------------------------------------------------------------------------------


async def test_append_posts_the_event_with_token_and_request_id(
    board: respx.MockRouter, make_client: Callable[..., WorldModelClient]
) -> None:
    route = board.post("/cases/inv-1/events").respond(
        201, json={"case_id": "inv-1", "event_id": "e-0001", "seq": 1, "duplicate": False}
    )
    token = request_id_var.set("req-77")
    try:
        result = await make_client().append_event("inv-1", _event())
    finally:
        request_id_var.reset(token)
    assert result.seq == 1
    assert result.duplicate is False
    request = route.calls.last.request
    assert request.headers["x-internal-token"] == TOKEN
    assert request.headers["x-request-id"] == "req-77"
    assert json.loads(request.content) == _event().to_wire()


async def test_append_reports_a_duplicate(
    board: respx.MockRouter, make_client: Callable[..., WorldModelClient]
) -> None:
    board.post("/cases/inv-1/events").respond(
        200, json={"case_id": "inv-1", "event_id": "e-0001", "seq": 1, "duplicate": True}
    )
    assert (await make_client().append_event("inv-1", _event())).duplicate is True


@pytest.mark.parametrize("case_id", ["../admin", "inv 1", "inv:1", "x" * 65, ""])
async def test_append_refuses_a_bad_case_id_without_a_request(
    board: respx.MockRouter, make_client: Callable[..., WorldModelClient], case_id: str
) -> None:
    route = board.route()
    with pytest.raises(ValueError):
        await make_client().append_event(case_id, _event())
    assert not route.called


@pytest.mark.parametrize(
    ("status", "code"),
    [(400, "invalid_request"), (401, "unauthorized"), (409, "event_conflict"), (409, "case_full")],
)
async def test_append_raises_rejected_on_4xx_with_the_service_code(
    board: respx.MockRouter, make_client: Callable[..., WorldModelClient], status: int, code: str
) -> None:
    board.post("/cases/inv-1/events").respond(
        status, json={"code": code, "message": "upstream detail", "request_id": "r"}
    )
    with pytest.raises(WorldModelRejected) as info:
        await make_client().append_event("inv-1", _event())
    assert info.value.status_code == status
    assert info.value.code == code
    assert "upstream detail" not in str(info.value)


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ConnectError("refused"),
        httpx.ReadTimeout("slow"),
        httpx.Response(503, json={"code": "x", "message": "m", "request_id": "r"}),
    ],
)
async def test_append_raises_unavailable_when_the_service_is_down(
    board: respx.MockRouter,
    make_client: Callable[..., WorldModelClient],
    failure: Exception | httpx.Response,
) -> None:
    route = board.post("/cases/inv-1/events")
    if isinstance(failure, httpx.Response):
        route.mock(return_value=failure)
    else:
        route.mock(side_effect=failure)
    with pytest.raises(WorldModelUnavailable):
        await make_client().append_event("inv-1", _event())


async def test_unavailable_without_a_token_and_no_request(
    board: respx.MockRouter, make_client: Callable[..., WorldModelClient]
) -> None:
    route = board.route()
    with pytest.raises(WorldModelUnavailable, match="token"):
        await make_client(context_service_token=None).append_event("inv-1", _event())
    assert not route.called


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(201, content=b"not json"),
        httpx.Response(201, json={"seq": "one"}),
        httpx.Response(201, content=b"x" * 3_000_000),
        httpx.Response(302, headers={"location": "http://evil.example/"}),
    ],
)
async def test_append_raises_bad_response_on_unusable_answers(
    board: respx.MockRouter, make_client: Callable[..., WorldModelClient], response: httpx.Response
) -> None:
    board.post("/cases/inv-1/events").mock(return_value=response)
    with pytest.raises(WorldModelBadResponse):
        await make_client().append_event("inv-1", _event())


# --- record_event (the path the agent uses) ----------------------------------------------------


async def test_record_event_returns_stored(
    board: respx.MockRouter, make_client: Callable[..., WorldModelClient]
) -> None:
    board.post("/cases/inv-1/events").respond(
        201, json={"case_id": "inv-1", "event_id": "e-0001", "seq": 3, "duplicate": False}
    )
    outcome = await make_client().record_event("inv-1", _event())
    assert (outcome.status, outcome.seq) == ("stored", 3)


async def test_record_event_continues_with_memory_unavailable_when_down(
    board: respx.MockRouter,
    make_client: Callable[..., WorldModelClient],
    caplog: pytest.LogCaptureFixture,
) -> None:
    board.post("/cases/inv-1/events").mock(side_effect=httpx.ConnectError("refused"))
    with caplog.at_level(logging.WARNING, logger="app.memory.world_model"):
        outcome = await make_client().record_event("inv-1", _event())
    assert outcome.status == "memory_unavailable"
    assert outcome.seq is None
    records = [r for r in caplog.records if getattr(r, "event", None) == "memory_unavailable"]
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    assert getattr(records[0], "error_type", None) == "ConnectError"
    assert TOKEN not in caplog.text


async def test_record_event_still_raises_on_rejection(
    board: respx.MockRouter, make_client: Callable[..., WorldModelClient]
) -> None:
    """A rejected event is a bug in our code, not an outage, so it must not be hidden."""
    board.post("/cases/inv-1/events").respond(
        400, json={"code": "invalid_request", "message": "m", "request_id": "r"}
    )
    with pytest.raises(WorldModelRejected):
        await make_client().record_event("inv-1", _event())


# --- reads -------------------------------------------------------------------------------------


async def test_timeline_returns_events_in_order(
    board: respx.MockRouter, make_client: Callable[..., WorldModelClient]
) -> None:
    board.get("/cases/inv-1/timeline").respond(
        200,
        json={
            "case_id": "inv-1",
            "count": 2,
            "events": [_stored(1), _stored(2, event_id="e-0002", agent="country")],
        },
    )
    events = await make_client().timeline("inv-1")
    assert [(e.seq, e.event_id, e.agent) for e in events] == [
        (1, "e-0001", "financial"),
        (2, "e-0002", "country"),
    ]
    assert events[0].at == datetime(2026, 10, 27, 10, 0, tzinfo=UTC)


async def test_timeline_of_an_unknown_case_is_empty(
    board: respx.MockRouter, make_client: Callable[..., WorldModelClient]
) -> None:
    board.get("/cases/nope/timeline").respond(
        404, json={"code": "case_not_found", "message": "m", "request_id": "r"}
    )
    assert await make_client().timeline("nope") == []


async def test_entity_returns_references_and_none_when_unknown(
    board: respx.MockRouter, make_client: Callable[..., WorldModelClient]
) -> None:
    board.get("/entities/supplier:erp:S0001").respond(
        200,
        json={
            "id": "supplier:erp:S0001",
            "type": "supplier",
            "attrs": {},
            "created_at": "2026-10-27T10:00:00.000Z",
            "updated_at": "2026-10-27T10:00:00.000Z",
            "references": [{"case_id": "inv-1", "event_id": "e-0001", "kind": "evidence_found"}],
            "truncated": False,
        },
    )
    board.get("/entities/supplier:erp:S9999").respond(
        404, json={"code": "entity_not_found", "message": "m", "request_id": "r"}
    )
    client = make_client()
    view = await client.entity("supplier:erp:S0001")
    assert view is not None
    assert view.references[0].case_id == "inv-1"
    assert await client.entity("supplier:erp:S9999") is None


@pytest.mark.parametrize("entity_id", ["case_event:inv-1:e1", "supplier:erp:../x", "S0001"])
async def test_entity_refuses_ids_outside_the_read_api(
    board: respx.MockRouter, make_client: Callable[..., WorldModelClient], entity_id: str
) -> None:
    with pytest.raises(ValueError):
        await make_client().entity(entity_id)


async def test_health(
    board: respx.MockRouter, make_client: Callable[..., WorldModelClient]
) -> None:
    board.get("/health").respond(200, json={"status": "ok"})
    assert await make_client().health() is True
    board.get("/health").mock(side_effect=httpx.ConnectError("refused"))
    assert await make_client().health() is False


def test_repr_has_no_token(make_settings: Callable[..., Settings]) -> None:
    assert TOKEN not in repr(WorldModelClient(make_settings()))


# --- security review fixes -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"summary": "bad \ud800 text"},
        {"summary": "invoice \u202efdp.exe"},
        {"data": {"note": "a\u0007b"}},
        {"data": {"open_po_value": 12345678901234567890}},
        {"data": {"big": -(2**53)}},
    ],
)
def test_event_rejects_unsafe_text_and_numbers(overrides: dict[str, object]) -> None:
    """Security: SEC-079, SEC-080. Same rules as the service, so the agent fails early."""
    with pytest.raises(ValidationError):
        _event(**overrides)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(409, json={"code": "store_full", "message": "m", "request_id": "r"}),
        httpx.Response(409, json={"code": "case_full", "message": "m", "request_id": "r"}),
        httpx.Response(401, json={"code": "unauthorized", "message": "m", "request_id": "r"}),
        httpx.Response(403, content=b""),
        httpx.Response(503, json={"code": "store_unavailable", "message": "m", "request_id": "r"}),
        httpx.Response(302, headers={"location": "http://elsewhere/"}),
        httpx.Response(201, content=b"<html>proxy</html>"),
    ],
)
async def test_record_event_degrades_on_conditions_that_are_not_our_bug(
    board: respx.MockRouter,
    make_client: Callable[..., WorldModelClient],
    caplog: pytest.LogCaptureFixture,
    response: httpx.Response,
) -> None:
    """Security: SEC-077. A full or misconfigured board must not stop an investigation."""
    board.post("/cases/inv-1/events").mock(return_value=response)
    with caplog.at_level(logging.WARNING, logger="app.memory.world_model"):
        outcome = await make_client().record_event("inv-1", _event())
    assert outcome.status == "memory_unavailable"
    assert any(getattr(r, "event", None) == "memory_unavailable" for r in caplog.records)


async def test_record_event_raises_on_event_conflict(
    board: respx.MockRouter, make_client: Callable[..., WorldModelClient]
) -> None:
    board.post("/cases/inv-1/events").respond(
        409, json={"code": "event_conflict", "message": "m", "request_id": "r"}
    )
    with pytest.raises(WorldModelRejected):
        await make_client().record_event("inv-1", _event())


async def test_full_case_timeline_is_readable(
    board: respx.MockRouter, make_client: Callable[..., WorldModelClient]
) -> None:
    """Security: SEC-078. 500 large events (about 6 MB) still fit the response cap."""
    big = {f"k{i}": "x" * 200 for i in range(20)}
    events = [_stored(i + 1, event_id=f"e-{i}", summary="s" * 500, data=big) for i in range(500)]
    board.get("/cases/inv-1/timeline").respond(
        200, json={"case_id": "inv-1", "count": 500, "events": events}
    )
    assert len(await make_client().timeline("inv-1")) == 500


async def test_one_unreadable_event_does_not_hide_the_timeline(
    board: respx.MockRouter,
    make_client: Callable[..., WorldModelClient],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Security: SEC-078."""
    bad = {**_stored(2, event_id="e-2"), "summary": "bad \ud800"}
    payload = {
        "case_id": "inv-1",
        "count": 3,
        "events": [_stored(1), bad, _stored(3, event_id="e-3")],
    }
    # json.dumps escapes the lone surrogate as \ud800, as a JSON service would send it.
    board.get("/cases/inv-1/timeline").respond(200, content=json.dumps(payload).encode())
    with caplog.at_level(logging.WARNING, logger="app.memory.world_model"):
        events = await make_client().timeline("inv-1")
    assert [e.seq for e in events] == [1, 3]
    skipped = [r for r in caplog.records if getattr(r, "event", None) == "timeline_events_skipped"]
    assert len(skipped) == 1
    assert getattr(skipped[0], "count", None) == 1


async def test_generic_404_is_not_an_empty_timeline(
    board: respx.MockRouter, make_client: Callable[..., WorldModelClient]
) -> None:
    """Security: SEC-082. A wrong base path must not look like an unknown case."""
    board.get("/cases/inv-1/timeline").respond(
        404, json={"code": "not_found", "message": "m", "request_id": "r"}
    )
    with pytest.raises(WorldModelRejected):
        await make_client().timeline("inv-1")


async def test_append_answer_for_another_event_is_a_bad_response(
    board: respx.MockRouter, make_client: Callable[..., WorldModelClient]
) -> None:
    """Security: SEC-082."""
    board.post("/cases/inv-1/events").respond(
        201, json={"case_id": "OTHER", "event_id": "e-0001", "seq": 99, "duplicate": False}
    )
    with pytest.raises(WorldModelBadResponse):
        await make_client().append_event("inv-1", _event())
