"""Control-flow errors of the agent graph.

StopInvestigation ends the whole investigation as "incomplete" (budget, deadline, rejected
token). StepFailed ends one step; the step then uses its documented fallback.
"""

from typing import Literal

StopReason = Literal["budget_exhausted", "deadline", "model_unauthorized", "internal_error"]


class StopInvestigation(Exception):  # noqa: N818
    """The investigation cannot go on; it ends as incomplete with what was checked.

    Attributes:
        reason: Why it stopped.
    """

    def __init__(self, reason: StopReason) -> None:
        """Create the error.

        Args:
            reason: Why it stopped.
        """
        super().__init__(reason)
        self.reason: StopReason = reason


class StepFailed(Exception):  # noqa: N818
    """One agent step failed after its retries and fallback; the step degrades.

    Attributes:
        agent: The agent whose step failed.
        error_type: Short error class name (never model output).
    """

    def __init__(self, agent: str, error_type: str) -> None:
        """Create the error.

        Args:
            agent: The agent whose step failed.
            error_type: Short error class name.
        """
        super().__init__(f"{agent} step failed: {error_type}")
        self.agent = agent
        self.error_type = error_type
