"""Structured JSON logging (architecture §9.3).

Every log line is one JSON object with ``timestamp``, ``level``, ``logger``,
``message`` and ``request_id``. ``request_id`` comes from a context variable set
by the request middleware (``app.core.middleware``); it is ``null`` outside a
request. Fields passed with ``extra=`` become top-level keys, and the values of
sensitive keys (:data:`SENSITIVE_KEY_FRAGMENTS`) are replaced with
``"[REDACTED]"`` at any depth.

Rule: **never interpolate secrets, tokens, credentials or personal data into the
log message**. Only ``extra`` keys are redacted; the free-text ``message`` is
written as is. Pass data as ``extra`` fields with descriptive key names.

Only the stdlib ``logging`` module is used (no structlog).
"""

import json
import logging
import sys
from collections.abc import Mapping
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any, TextIO

# A key is sensitive when its lowercased name, with "-" turned into "_",
# contains any of these fragments.
SENSITIVE_KEY_FRAGMENTS: tuple[str, ...] = (
    "password",
    "passwd",
    "secret",
    "token",
    "authorization",
    "cookie",
    "api_key",
    "apikey",
    "credential",
    "dsn",
    "database_url",
    "database_direct_url",
)
REDACTED = "[REDACTED]"

LOG_LEVEL = logging.INFO
HANDLER_NAME = "classora-json"

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

# Attributes every LogRecord has; anything else on a record came from ``extra``.
_RECORD_ATTRIBUTES = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__
) | {"message", "asctime"}
# uvicorn adds an ANSI-coloured copy of its message; it is noise in JSON.
_IGNORED_EXTRA = frozenset({"color_message"})


def get_request_id() -> str | None:
    """The current request's ID, or ``None`` outside a request."""
    return request_id_var.get()


def is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(fragment in normalized for fragment in SENSITIVE_KEY_FRAGMENTS)


def redact(value: Any) -> Any:
    """Copy of ``value`` with sensitive dict keys redacted at any depth."""
    if isinstance(value, Mapping):
        return {
            key: REDACTED if is_sensitive_key(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple | set | frozenset):
        return [redact(item) for item in value]
    return value


class JsonFormatter(logging.Formatter):
    """Formats a record as one JSON object on one line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": get_request_id(),
        }
        for key, value in record.__dict__.items():
            if key in _RECORD_ATTRIBUTES or key in _IGNORED_EXTRA or key in payload:
                continue
            payload[key] = REDACTED if is_sensitive_key(key) else redact(value)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        return json.dumps(payload, default=str)


class _StdoutHandler(logging.StreamHandler[TextIO]):
    """Writes to the *current* ``sys.stdout`` (test capture replaces it)."""

    def __init__(self) -> None:
        super().__init__(sys.stdout)

    def emit(self, record: logging.LogRecord) -> None:
        self.stream = sys.stdout
        super().emit(record)


def configure_logging() -> None:
    """Send all logs, including uvicorn's, as JSON lines to stdout. Idempotent.

    uvicorn's own access log is disabled: ``app.access`` (see
    ``app.core.middleware``) writes one access line per request instead.
    """
    root = logging.getLogger()
    for existing in list(root.handlers):
        if existing.name == HANDLER_NAME:
            root.removeHandler(existing)
    handler = _StdoutHandler()
    handler.name = HANDLER_NAME
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(LOG_LEVEL)

    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
        uvicorn_logger.setLevel(LOG_LEVEL)
    access = logging.getLogger("uvicorn.access")
    access.handlers.clear()
    access.propagate = False
    access.disabled = True

    # Python warnings would otherwise be written to stderr as plain text.
    logging.captureWarnings(True)
