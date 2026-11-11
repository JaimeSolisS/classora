"""Structured JSON logging (architecture §9.3).

Every log line is one JSON object with ``timestamp``, ``level``, ``logger``,
``message``, ``request_id``, ``trace_id`` and ``span_id``. ``request_id`` comes
from a context variable set by the request middleware
(``app.core.middleware``); it is ``null`` outside a request. ``trace_id`` and
``span_id`` are the current OpenTelemetry span's (hex), or ``null`` when there
is none, which is always the case while telemetry is off (ADR-0014,
``app.core.telemetry``). Fields passed with ``extra=`` become top-level keys,
and the values of sensitive keys (:data:`SENSITIVE_KEY_FRAGMENTS`) are replaced
with ``"[REDACTED]"`` at any depth.

A logged exception (``exc_info``) keeps its type and stack frames only:
:func:`scrub_traceback` drops every exception **message** (#612 E2/E3),
because driver and validation messages quote submitted values (for example
asyncpg's ``Key (username)=(...)``). The ``request_id``/``trace_id`` leads to
the request instead.

Rule: **never interpolate secrets, tokens, credentials or personal data into the
log message**. Only ``extra`` keys are redacted; the free-text ``message`` is
written as is. Pass data as ``extra`` fields with descriptive key names.

Only the stdlib ``logging`` module is used (no structlog).
"""

import json
import logging
import re
import sys
import time
import traceback
from collections.abc import Mapping
from contextvars import ContextVar
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, TextIO

from opentelemetry import trace

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


def current_trace_ids() -> tuple[str | None, str | None]:
    """``(trace_id, span_id)`` of the current span as hex, or ``(None, None)``.

    Reads the OpenTelemetry API's context only: with telemetry off there is
    never a valid span, so both are ``None``.
    """
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return None, None
    return format(context.trace_id, "032x"), format(context.span_id, "016x")


# --- Exceptions without messages (#612 E2/E3) --------------------------------

_TRACEBACK_HEADER = "Traceback (most recent call last):"
_CHAIN_SEPARATORS = frozenset(
    {
        "The above exception was the direct cause of the following exception:",
        "During handling of the above exception, another exception occurred:",
    }
)
_FRAME_LINE = re.compile(r'  File "[^"]*", line \d+, in \S.*')
_EXCEPTION_TYPE = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*")
UNKNOWN_EXCEPTION_TYPE = "<exception>"


def scrub_traceback(text: str) -> str:
    """``text`` (a formatted Python traceback) without any exception message.

    Keeps the ``Traceback`` headers, the chaining lines, each ``File ...``
    frame line with its indented source and caret lines, and of each
    ``Type: message`` line only ``Type``. Everything after that type line up
    to the next header (the rest of a multi-line message, such as asyncpg's
    ``DETAIL:`` line) is dropped, as is any text that is not inside a
    recognised traceback block. Used for the stdout log, the OTLP log and the
    span exception events alike.
    """
    kept: list[str] = []
    state = "outside"  # outside | traceback | frame | message
    for line in text.splitlines():
        if line == _TRACEBACK_HEADER:
            kept.append(line)
            state = "traceback"
        elif line in _CHAIN_SEPARATORS:
            kept.extend(["", line, ""])
            state = "outside"
        elif state in ("traceback", "frame") and _FRAME_LINE.fullmatch(line):
            kept.append(line)
            state = "frame"
        elif state == "frame" and line.startswith("    "):
            # The frame's source line, carets, or "...<N lines>...".
            kept.append(line)
        elif state == "frame" and line.startswith("  "):
            # "  [Previous line repeated N more times]".
            continue
        elif state == "frame":
            match = _EXCEPTION_TYPE.match(line)
            rest = line[match.end() :] if match else "?"
            if match and (rest == "" or rest.startswith(":")):
                kept.append(match.group(0))
            else:
                kept.append(UNKNOWN_EXCEPTION_TYPE)
            state = "message"
        # Anything else (message lines, blank lines) is dropped.
    return "\n".join(kept)


def exception_type_name(exc_type: type[BaseException]) -> str:
    """``module.QualName`` of an exception class (``builtins`` omitted)."""
    module = exc_type.__module__
    if module in ("builtins", "__main__"):
        return exc_type.__qualname__
    return f"{module}.{exc_type.__qualname__}"


def format_exception_without_message(
    exc_type: type[BaseException] | None,
    exc: BaseException | None,
    tb: TracebackType | None,
) -> str:
    """The traceback of ``exc`` (types and frames only, see :func:`scrub_traceback`)."""
    if exc_type is None:
        return ""
    scrubbed = scrub_traceback("".join(traceback.format_exception(exc_type, exc, tb)))
    return scrubbed or exception_type_name(exc_type)


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
        payload["trace_id"], payload["span_id"] = current_trace_ids()
        for key, value in record.__dict__.items():
            if key in _RECORD_ATTRIBUTES or key in _IGNORED_EXTRA or key in payload:
                continue
            payload[key] = REDACTED if is_sensitive_key(key) else redact(value)
        if record.exc_info:
            payload["exception"] = format_exception_without_message(*record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        return json.dumps(payload, default=str)


TELEMETRY_LOGGER_PREFIXES: tuple[str, ...] = ("opentelemetry", "urllib3")
EXPORT_ERROR_INTERVAL_SECONDS = 60.0


class ExportErrorThrottle(logging.Filter):
    """At most one line per :data:`EXPORT_ERROR_INTERVAL_SECONDS` from the
    telemetry exporter's own loggers (``opentelemetry.*`` and the HTTP client,
    ``urllib3.*``), so an unreachable collector can't flood the log (#612).
    Every other record passes."""

    def __init__(self, interval_seconds: float) -> None:
        super().__init__()
        self.interval_seconds = interval_seconds
        self._last: float | None = None

    def filter(self, record: logging.LogRecord) -> bool:
        if not record.name.startswith(TELEMETRY_LOGGER_PREFIXES):
            return True
        now = time.monotonic()
        if self._last is not None and now - self._last < self.interval_seconds:
            return False
        self._last = now
        return True


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
    handler.addFilter(ExportErrorThrottle(EXPORT_ERROR_INTERVAL_SECONDS))
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
