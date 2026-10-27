"""Per-investigation budget of model calls (NVIDIA free tier: 40 requests/minute).

One logical chat call counts once; its retries and a fallback do not, because the shared
rate limiter already paces every HTTP attempt.
"""

import logging

from app.llm.errors import BudgetExhausted

logger = logging.getLogger(__name__)

DEFAULT_CALL_BUDGET = 15
WARNING_RATIO = 0.8


class CallBudget:
    """Count model calls for one investigation and stop when the limit is reached.

    Attributes:
        limit: Maximum number of model calls.
        used: Calls consumed so far.
    """

    def __init__(self, limit: int = DEFAULT_CALL_BUDGET) -> None:
        """Create a budget.

        Args:
            limit: Maximum number of model calls (at least 1).

        Raises:
            ValueError: If limit is below 1.
        """
        if limit < 1:
            raise ValueError("limit must be at least 1")
        self.limit = limit
        self.used = 0
        self._warned = False

    @property
    def remaining(self) -> int:
        """Return how many calls are left."""
        return self.limit - self.used

    def consume(self) -> None:
        """Take one call from the budget.

        Logs call_budget_warning once usage reaches 80 %.

        Raises:
            BudgetExhausted: If no calls are left; nothing is consumed then.
        """
        if self.used >= self.limit:
            logger.warning(
                "model call budget exhausted",
                extra={"event": "budget_exhausted", "used": self.used, "limit": self.limit},
            )
            raise BudgetExhausted(f"model call budget of {self.limit} used up")
        self.used += 1
        if not self._warned and self.used >= self.limit * WARNING_RATIO:
            self._warned = True
            logger.warning(
                "model call budget above 80 %",
                extra={"event": "call_budget_warning", "used": self.used, "limit": self.limit},
            )
