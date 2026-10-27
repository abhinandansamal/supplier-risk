"""Investigation and proposal routes (architecture 8.3), all behind the session cookie.

Other users' investigations are answered with 404, never 403, so ids are not confirmed.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.agent.guard import MAX_QUESTION_CHARS
from app.api.session import check_origin, current_user
from app.core.errors import AppError
from app.security.identity import UserContext
from app.services.events import Subscription
from app.services.investigations import InvestigationService
from app.store.db import InvestigationRow

router = APIRouter(dependencies=[Depends(check_origin)])

HEARTBEAT_SECONDS = 15.0
# A stream ends after this; the browser's EventSource reconnects and gets the history again.
MAX_STREAM_SECONDS = 3600.0
MAX_COMMENT_CHARS = 500

User = Annotated[UserContext, Depends(current_user)]
InvestigationId = Annotated[str, Path(max_length=64)]
ActionId = Annotated[str, Path(max_length=128)]


class StartBody(BaseModel):
    """A new investigation; the role and scope come from the session only."""

    model_config = ConfigDict(extra="forbid")

    question: Annotated[str, Field(min_length=1, max_length=MAX_QUESTION_CHARS)]


class DecisionBody(BaseModel):
    """A human decision on a proposal."""

    model_config = ConfigDict(extra="forbid")

    comment: Annotated[str | None, Field(max_length=MAX_COMMENT_CHARS)] = None


def _service(request: Request) -> InvestigationService:
    service: InvestigationService = request.app.state.investigations
    return service


def _visible(request: Request, user: UserContext, investigation_id: str) -> InvestigationRow:
    row = _service(request).visible(user, investigation_id)
    if row is None:
        raise AppError(404, "not_found", "Investigation not found.")
    return row


@router.post("/investigations", status_code=202)
async def start(body: StartBody, request: Request, user: User) -> dict[str, Any]:
    """Queue an investigation; it runs in the background."""
    investigation_id, position = _service(request).submit(user, body.question)
    return {"id": investigation_id, "queue_position": position}


@router.get("/investigations")
async def list_investigations(request: Request, user: User) -> dict[str, Any]:
    """List the user's investigations (a director sees everyone's)."""
    return {"investigations": _service(request).list_for(user)}


@router.get("/investigations/{investigation_id}")
async def get_investigation(
    investigation_id: InvestigationId, request: Request, user: User
) -> dict[str, Any]:
    """Return status, result (report, claims, evidence, score, trace) and proposals."""
    return _service(request).detail(_visible(request, user, investigation_id))


@router.delete("/investigations/{investigation_id}", status_code=204)
async def delete_investigation(
    investigation_id: InvestigationId, request: Request, user: User
) -> Response:
    """Erase the user's own investigation and everything stored with it (GDPR)."""
    if not _service(request).delete(user, investigation_id):
        raise AppError(404, "not_found", "Investigation not found.")
    return Response(status_code=204)


def _sse(kind: str, data: dict[str, Any]) -> str:
    # json.dumps escapes newlines, so a value can never start a new SSE field.
    return f"event: {kind}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


@router.get("/investigations/{investigation_id}/events", response_model=None)
async def events(
    investigation_id: InvestigationId, request: Request, user: User
) -> StreamingResponse | JSONResponse:
    """Stream the live trace as server-sent events, ending with a "done" event."""
    service = _service(request)
    row = _visible(request, user, investigation_id)
    service.check_stream_limit(user)

    async def stream() -> AsyncIterator[str]:
        # Security (SEC-103): the slot and the subscription are taken here, inside the
        # generator, so a client that leaves before the first byte holds neither.
        with service.stream_slot(user):
            subscription = service.subscribe(row)
            try:
                async for chunk in _follow(request, subscription):
                    yield chunk
            finally:
                service.unsubscribe(row, subscription)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


async def _follow(request: Request, subscription: Subscription) -> AsyncIterator[str]:
    """Yield SSE chunks until "done", a disconnect or the stream time limit."""
    loop = asyncio.get_running_loop()
    ends = loop.time() + MAX_STREAM_SECONDS
    while loop.time() < ends:
        event = await subscription.next(HEARTBEAT_SECONDS)
        if event is None:
            if await request.is_disconnected():
                return
            yield ": ping\n\n"
            continue
        kind, data = event
        yield _sse(kind, data)
        if kind == "done":
            return


@router.get("/proposals")
async def inbox(request: Request, user: User) -> dict[str, Any]:
    """List the pending proposals this user may decide."""
    return {"proposals": _service(request).inbox(user)}


@router.post("/proposals/{action_id}/approve")
async def approve(
    action_id: ActionId, body: DecisionBody, request: Request, user: User
) -> dict[str, Any]:
    """Record an approval. Nothing is executed."""
    return _service(request).decide(user, action_id, "approved", body.comment)


@router.post("/proposals/{action_id}/reject")
async def reject(
    action_id: ActionId, body: DecisionBody, request: Request, user: User
) -> dict[str, Any]:
    """Record a rejection."""
    return _service(request).decide(user, action_id, "rejected", body.comment)
