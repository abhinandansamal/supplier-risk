"""Read one JSON object from model output and validate it, re-asking once when it is invalid.

Model output is untrusted: it is size-capped, read only with json.loads, validated with a
Pydantic model, and error messages name field paths and error types, never the output itself.
"""

import json
import logging
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ValidationError

from app.agent.errors import StepFailed
from app.agent.runtime import Run, call_model

logger = logging.getLogger(__name__)

MAX_JSON_CHARS = 64_000


class OutputInvalid(ValueError):  # noqa: N818
    """Model output was not a JSON object matching the expected model."""


def _reject_constant(name: str) -> Any:  # noqa: ANN401
    raise ValueError(f"non-standard JSON constant {name}")


def parse_model_json[M: BaseModel](text: str, model: type[M]) -> M:
    """Return the first JSON object in model text, validated as `model`.

    Code fences and text around the object are ignored (models add them despite the prompt).

    Args:
        text: Model output (reasoning already stripped by the client).
        model: Pydantic model to validate against.

    Returns:
        The validated object.

    Raises:
        OutputInvalid: If no object is found, it is too large, or it does not validate.
    """
    if len(text) > MAX_JSON_CHARS:
        raise OutputInvalid("output too long")
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise OutputInvalid("no JSON object in the output")
    try:
        value = json.loads(text[start : end + 1], parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        raise OutputInvalid("output is not valid JSON") from None
    if not isinstance(value, dict):
        raise OutputInvalid("output is not a JSON object")
    try:
        return model.model_validate(value)
    except ValidationError as exc:
        fields = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or 'object'}: {err['type']}"
            for err in exc.errors()[:10]
        )
        raise OutputInvalid(f"output does not match the schema ({fields})") from None


async def ask_json[M: BaseModel](
    run: Run, agent: str, messages: Sequence[Mapping[str, Any]], model: type[M]
) -> M:
    """Ask a model for a JSON object; on invalid output re-ask once with the validation error.

    Args:
        run: The investigation run.
        agent: Calling agent.
        messages: System prompt and user payload.
        model: Expected answer model.

    Returns:
        The validated answer.

    Raises:
        StepFailed: If the model call fails or both answers are invalid.
        StopInvestigation: On budget exhaustion, deadline or a rejected token.
    """
    history = list(messages)
    for attempt in (1, 2):
        result = await call_model(run, agent, history)
        try:
            return parse_model_json(result.content, model)
        except OutputInvalid as exc:
            run.trace.add(agent, "output_invalid", note=f"attempt_{attempt}", error_type="schema")
            logger.warning(
                "model output invalid",
                extra={"event": "output_invalid", "agent": agent, "attempt": attempt},
            )
            history = [
                *messages,
                {"role": "assistant", "content": result.content[:MAX_JSON_CHARS]},
                {
                    "role": "user",
                    "content": f"Your answer was invalid: {exc}. Reply with the JSON object only.",
                },
            ]
    raise StepFailed(agent, "OutputInvalid")
