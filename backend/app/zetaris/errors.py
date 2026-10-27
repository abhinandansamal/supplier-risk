"""Typed errors of the Zetaris REST client.

Messages name the failure, the status code and setting names only. Zetaris response bodies
(which may echo SQL or data), SQL text and API keys never go into an error, because errors
reach logs and callers.
"""


class ZetarisError(Exception):
    """Base class for Zetaris client errors.

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


class ZetarisConfigError(ZetarisError):
    """The client cannot start: a role key is missing, is the admin key, or the URL is unsafe."""


class ZetarisUnauthorized(ZetarisError):  # noqa: N818
    """Zetaris rejected the key (401 or 403): wrong key, or no grant on the object."""


class ZetarisRequestError(ZetarisError):
    """Zetaris refused the query with a non-retryable status (for example a SQL error)."""


class ZetarisUnavailable(ZetarisError):  # noqa: N818
    """429, 502-504, timeouts or connection errors persisted after the retry."""


class ZetarisBadResponse(ZetarisError):  # noqa: N818
    """The response was not the expected {"data": [[...]]} JSON, or was too large."""
