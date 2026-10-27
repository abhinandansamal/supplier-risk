"""Typed errors of the Meterless model client.

Messages name the failure and the status code only. Upstream response bodies, prompts, tool
arguments and the runtime token never go into an error, because errors reach logs and callers.
Names follow the feature spec (BudgetExhausted, LLMUnauthorized, ...) rather than an Error suffix.
"""


class LLMError(Exception):
    """Base class for model client errors.

    Attributes:
        status_code: HTTP status of the last response, when there was one.
    """

    def __init__(self, message: str, status_code: int | None = None) -> None:
        """Create the error.

        Args:
            message: Short description without upstream content.
            status_code: HTTP status of the last response, if any.
        """
        super().__init__(message)
        self.status_code = status_code


class LLMUnauthorized(LLMError):  # noqa: N818
    """The runtime rejected the token (401 or 403); retrying cannot help."""


class LLMRateLimited(LLMError):  # noqa: N818
    """The runtime still answered 429 after all attempts."""


class LLMUnavailable(LLMError):  # noqa: N818
    """5xx answers, timeouts or connection errors persisted after all attempts."""


class LLMRequestError(LLMError):
    """The runtime refused the request with a non-retryable status (3xx or 4xx)."""


class LLMBadResponse(LLMError):  # noqa: N818
    """The response was not the expected OpenAI-compatible JSON."""


class ToolCallParseError(LLMBadResponse):
    """A tool call's arguments were not a JSON object (never evaluated as code)."""


class BudgetExhausted(LLMError):  # noqa: N818
    """The per-investigation model call budget is used up."""
