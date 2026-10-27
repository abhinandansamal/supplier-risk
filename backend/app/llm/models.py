"""Pydantic models returned by the Meterless model client."""

from typing import Any, Literal

from pydantic import BaseModel, Field

ModelRole = Literal["default", "deep", "fast"]
RuntimeState = Literal["online", "unauthorized", "offline"]


class ToolCall(BaseModel):
    """One function call requested by the model.

    Attributes:
        id: Call id assigned by the model, echoed back in the tool result message.
        name: Function name; the agent checks it against its own tool catalogue.
        arguments: Parsed JSON object; untrusted until the tool validates it.
    """

    id: str
    name: str
    arguments: dict[str, Any]


class Usage(BaseModel):
    """Token counts reported by the runtime (zero when missing or invalid).

    Attributes:
        prompt_tokens: Tokens in the request.
        completion_tokens: Tokens in the answer.
        total_tokens: Sum reported by the runtime.
    """

    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)


class ChatResult(BaseModel):
    """Result of one logical chat call, with the Nightshift receipt for evidence.

    Attributes:
        content: Answer text with reasoning (everything up to "</think>") removed.
        tool_calls: Parsed tool calls, empty when the model answered in text.
        requested_model: Model id sent in the successful request.
        routed_model: Model that served the call according to the receipt.
        provider: Provider of the winning route (e.g. nvidia), from the receipt.
        receipt_id: Nightshift receipt id, stored per call.
        usage: Token counts.
        latency_ms: Wall time of the call including retries and backoff.
        retries: Retries before the successful attempt (fallback and empty-reply retries included).
        fallback_used: True when a deep call was served by the default model after 429s.
        cache_status: Nightshift cache status from the receipt (e.g. exact, miss), if reported.
        from_cache: True when Nightshift answered from its cache instead of calling the model.
    """

    content: str
    tool_calls: list[ToolCall]
    requested_model: str
    routed_model: str | None
    provider: str | None
    receipt_id: str | None
    usage: Usage
    latency_ms: int
    retries: int
    fallback_used: bool = False
    cache_status: str | None = None
    from_cache: bool = False


class RuntimeStatus(BaseModel):
    """Reachability of the Meterless runtime as decided by GET /v1/models.

    Attributes:
        state: online (200 with model ids), unauthorized (401/403) or offline (anything else).
        model_ids: Every listed id, including ones this app does not know.
    """

    state: RuntimeState
    model_ids: list[str] = Field(default_factory=list)
