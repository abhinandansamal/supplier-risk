"""Structured JSON logging with context ids and secret redaction.

setup_logging() is called once at startup. Every handler writes one JSON object per line and
passes records through RedactionFilter first.
"""

import json
import logging
import logging.handlers
import re
import sys
from collections.abc import Iterable
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.settings import Settings

REDACTED = "[REDACTED]"
MAX_BYTES = 10 * 1024 * 1024
BACKUP_COUNT = 5

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
investigation_id_var: ContextVar[str | None] = ContextVar("investigation_id", default=None)

# Security: patterns are anchored on a keyword and bounded ({0,40}, character classes that
# exclude the delimiter), so matching stays linear on adversarial input.
_AUTH_HEADER = re.compile(r"""(?i)(authorization["']?\s*[:=]\s*["']?)[^\r\n"']*""")
_COOKIE_HEADER = re.compile(r"""(?i)((?:set-)?cookie["']?\s*[:=]\s*["']?)[^\r\n"']*""")
_URL_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^\s/?#]*@")
_KEY_VALUE = re.compile(
    r"""(?i)([\w.-]{0,40}?(?:password|passwd|pwd|passphrase|secret(?:[_-]?key)?|token|"""
    r"""(?:api|private|access)[_-]?key|credentials?)["']?\s*[:=]\s*["']?)[^\s"'&;,}]+"""
)
_BEARER = re.compile(r"""(?i)\bbearer\s+[^\s"',;}]+""")
_NVAPI = re.compile(r"nvapi-[A-Za-z0-9_-]+")
_SENSITIVE_KEY = re.compile(
    r"(?i)(authorization|cookie|set-cookie|password|passwd|secret|token|"
    r"[\w-]*(api[_-]?key|_token|-token|_secret))"
)
_STANDARD_ATTRS = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message"}
_CORE_FIELDS = {
    "timestamp",
    "level",
    "logger",
    "message",
    "event",
    "request_id",
    "investigation_id",
}
_HANDLER_MARK = "_supplier_risk"


class Redactor:
    """Remove secrets from text and nested values."""

    def __init__(self, secrets: Iterable[str]) -> None:
        """Create a redactor.

        Args:
            secrets: Literal secret values to remove (empty strings are ignored).
        """
        # Longest first, so a URL secret is replaced before its password part.
        self._secrets = sorted({s for s in secrets if s}, key=len, reverse=True)

    def redact_text(self, text: str) -> str:
        """Return text with secrets removed.

        Covers literal secrets, Authorization and Cookie headers, URL user:password@, key=value
        pairs for password/token/api_key style names, bearer tokens and nvapi keys.
        """
        for secret in self._secrets:
            text = text.replace(secret, REDACTED)
        text = _AUTH_HEADER.sub(rf"\1{REDACTED}", text)
        text = _COOKIE_HEADER.sub(rf"\1{REDACTED}", text)
        text = _URL_USERINFO.sub(rf"\1{REDACTED}@", text)
        text = _KEY_VALUE.sub(rf"\1{REDACTED}", text)
        text = _BEARER.sub(f"Bearer {REDACTED}", text)
        return _NVAPI.sub(REDACTED, text)

    def redact_value(self, value: object, key: str | None = None) -> Any:  # noqa: ANN401
        """Return a JSON-safe copy of value with secrets removed.

        Args:
            value: Any logged value; unknown objects are converted with str().
            key: Name of the field or dict key holding the value; sensitive names are masked.
        """
        if key is not None and _SENSITIVE_KEY.fullmatch(key):
            return REDACTED
        if isinstance(value, str):
            return self.redact_text(value)
        if value is None or isinstance(value, bool | int | float):
            return value
        if isinstance(value, dict):
            return {str(k): self.redact_value(v, key=str(k)) for k, v in value.items()}
        if isinstance(value, list | tuple | set | frozenset):
            return [self.redact_value(v) for v in value]
        return self.redact_text(str(value))


class RedactionFilter(logging.Filter):
    """Rewrite each record so no secret survives in message, extras or exception text."""

    def __init__(self, redactor: Redactor) -> None:
        """Create the filter.

        Args:
            redactor: The redactor holding this process's secret values.
        """
        super().__init__()
        self._redactor = redactor

    def filter(self, record: logging.LogRecord) -> bool:
        """Redact the record in place and always let it through."""
        try:
            message = record.getMessage()
        except (TypeError, ValueError):
            message = str(record.msg)
        record.msg = self._redactor.redact_text(message)
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = self._redactor.redact_text(record.exc_text)
        if record.stack_info:
            record.stack_info = self._redactor.redact_text(record.stack_info)
        for name in [n for n in record.__dict__ if n not in _STANDARD_ATTRS]:
            setattr(record, name, self._redactor.redact_value(record.__dict__[name], key=name))
        return True


class JsonFormatter(logging.Formatter):
    """Format a record as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        """Return the record as a JSON line with the standard fields plus extras."""
        payload: dict[str, Any] = {
            name: value
            for name, value in record.__dict__.items()
            if name not in _STANDARD_ATTRS and name not in _CORE_FIELDS
        }
        payload.update(
            timestamp=datetime.fromtimestamp(record.created, UTC).isoformat(),
            level=record.levelname,
            logger=record.name,
            message=record.getMessage(),
            event=record.__dict__.get("event", "log"),
            request_id=request_id_var.get(),
            investigation_id=investigation_id_var.get(),
        )
        if record.exc_info or record.exc_text:
            payload["exception"] = record.exc_text or self.formatException(record.exc_info)  # type: ignore[arg-type]
        if record.stack_info:
            payload["stack"] = record.stack_info
        return json.dumps(payload, default=str, ensure_ascii=False)


def teardown_logging() -> None:
    """Remove and close the handlers installed by setup_logging (used by tests and re-setup)."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, _HANDLER_MARK, False):
            root.removeHandler(handler)
            handler.close()


def setup_logging(settings: Settings) -> None:
    """Configure console and rotating file logging; safe to call more than once.

    Args:
        settings: Provides log_level, log_dir and the secrets to redact.
    """
    teardown_logging()
    log_dir = Path(settings.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    redactor = Redactor(settings.secret_values())

    def rotating(name: str) -> logging.Handler:
        return logging.handlers.RotatingFileHandler(
            log_dir / name, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
        )

    errors = rotating("errors.jsonl")
    errors.setLevel(logging.WARNING)
    for handler in (logging.StreamHandler(sys.stdout), rotating("app.jsonl"), errors):
        handler.setFormatter(JsonFormatter())
        # Security: the filter runs on every handler, so no output path skips redaction.
        handler.addFilter(RedactionFilter(redactor))
        setattr(handler, _HANDLER_MARK, True)
        logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(settings.log_level)

    # Route uvicorn through our handlers; our middleware writes the access line itself.
    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    logging.getLogger("uvicorn.access").handlers.clear()
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
