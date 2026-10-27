"""Typed errors of the tool layer.

Messages name views, tools and setting names only; never rows, SQL text or keys.
"""


class ToolError(Exception):
    """Base class for tool-layer errors."""


class ViewNotGranted(ToolError):  # noqa: N818
    """A role key asked for a view it has no grant on, or for an unknown view."""


class SourceUnavailable(ToolError):  # noqa: N818
    """No snapshot within the allowed staleness could be served for a view.

    Attributes:
        view: The view that could not be served.
    """

    def __init__(self, view: str) -> None:
        """Create the error.

        Args:
            view: The view that could not be served.
        """
        super().__init__(f"source unavailable: {view}")
        self.view = view


class ToolForbidden(ToolError):  # noqa: N818
    """The session's role may not use this tool (for example financial_trend for a buyer)."""


class ToolBudgetExceeded(ToolError):  # noqa: N818
    """The investigation used up its evidence budget (too many tool results)."""


class SupplierNotFound(ToolError):  # noqa: N818
    """The supplier does not exist or is outside the session's scope (same message for both)."""
