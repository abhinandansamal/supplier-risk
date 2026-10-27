"""Tests for MeterlessClient.embed(): batching, ordering and validation."""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
import respx

from app.llm.client import (
    DEFAULT_EMBED_BATCH_SIZE,
    MAX_EMBED_DIMENSIONS,
    MAX_EMBED_TEXT_CHARS,
    MeterlessClient,
)
from app.llm.errors import LLMBadResponse

EMBED_MODEL = "nvidia/nemotron-3.5-embed-1b"


def _length_embeddings(request: httpx.Request) -> httpx.Response:
    """Embed each text as [len(text)] and return the items in reverse index order."""
    texts = json.loads(request.content)["input"]
    data = [
        {"object": "embedding", "index": i, "embedding": [float(len(t))]}
        for i, t in enumerate(texts)
    ]
    return httpx.Response(200, json={"object": "list", "model": EMBED_MODEL, "data": data[::-1]})


@pytest.fixture
def embed_route(meterless: respx.MockRouter) -> respx.Route:
    return meterless.post("/embeddings")


def test_default_batch_size_is_32() -> None:
    assert DEFAULT_EMBED_BATCH_SIZE == 32


async def test_returns_vectors_in_input_order(
    embed_route: respx.Route, make_client: Callable[..., MeterlessClient]
) -> None:
    embed_route.mock(side_effect=_length_embeddings)
    vectors = await make_client().embed(["a", "bbb", "bb"])
    assert vectors == [[1.0], [3.0], [2.0]]


async def test_sends_batches_with_the_embed_model_and_no_scope(
    embed_route: respx.Route, make_client: Callable[..., MeterlessClient]
) -> None:
    embed_route.mock(side_effect=_length_embeddings)
    texts = ["a", "bb", "ccc", "dddd", "eeeee"]
    vectors = await make_client(embed_batch_size=2).embed(texts)
    assert vectors == [[1.0], [2.0], [3.0], [4.0], [5.0]]
    sent: list[dict[str, Any]] = [json.loads(c.request.content) for c in embed_route.calls]
    assert [len(s["input"]) for s in sent] == [2, 2, 1]
    assert all(s["model"] == EMBED_MODEL for s in sent)
    headers = embed_route.calls.last.request.headers
    assert headers["authorization"] == "Bearer mt-secret-VALUE-2222"
    assert headers["meterless-app"] == "supplier-risk"
    assert "meterless-scope" not in headers


async def test_empty_input_makes_no_request(
    embed_route: respx.Route, make_client: Callable[..., MeterlessClient]
) -> None:
    assert await make_client().embed([]) == []
    assert embed_route.call_count == 0


@pytest.mark.parametrize(
    "body",
    [
        {"data": [{"index": 0, "embedding": [1.0]}]},
        {"data": [{"index": 0, "embedding": "x"}, {"index": 1, "embedding": [1.0]}]},
        {"data": [{"index": 0, "embedding": [1.0]}, {"index": 0, "embedding": [1.0]}]},
        {"data": "nope"},
        [],
    ],
)
async def test_malformed_embeddings_raise_bad_response(
    embed_route: respx.Route, make_client: Callable[..., MeterlessClient], body: object
) -> None:
    embed_route.respond(200, json=body)
    with pytest.raises(LLMBadResponse):
        await make_client().embed(["a", "b"])


async def test_rejects_oversized_text_without_a_request(
    embed_route: respx.Route, make_client: Callable[..., MeterlessClient]
) -> None:
    with pytest.raises(ValueError, match="text"):
        await make_client().embed(["x" * (MAX_EMBED_TEXT_CHARS + 1)])
    assert embed_route.call_count == 0


async def test_retries_on_429(
    embed_route: respx.Route, make_client: Callable[..., MeterlessClient]
) -> None:
    embed_route.side_effect = [httpx.Response(429), _length_embeddings]
    assert await make_client().embed(["ab"]) == [[2.0]]


async def test_dimensions_must_match_across_batches(
    embed_route: respx.Route, make_client: Callable[..., MeterlessClient]
) -> None:
    def growing(request: httpx.Request) -> httpx.Response:
        texts = json.loads(request.content)["input"]
        size = embed_route.call_count
        data = [{"index": i, "embedding": [0.1] * size} for i in range(len(texts))]
        return httpx.Response(200, json={"data": data})

    embed_route.mock(side_effect=growing)
    with pytest.raises(LLMBadResponse):
        await make_client(embed_batch_size=1).embed(["a", "b"])


async def test_oversized_vectors_are_rejected(
    embed_route: respx.Route, make_client: Callable[..., MeterlessClient]
) -> None:
    vector = [0.1] * (MAX_EMBED_DIMENSIONS + 1)
    embed_route.respond(200, json={"data": [{"index": 0, "embedding": vector}]})
    with pytest.raises(LLMBadResponse):
        await make_client().embed(["a"])
