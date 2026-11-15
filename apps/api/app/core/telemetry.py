"""OpenTelemetry for the API and the worker (ADR-0014, #612).

**Off by default.** Telemetry is on only when ``OTEL_EXPORTER_OTLP_ENDPOINT``
(``Settings.otel_exporter_otlp_endpoint``) is set. While it is off, this module
is all that runs: :func:`configure_telemetry` returns at once, the
OpenTelemetry SDK is never imported, and every hook below is a no-op. No
provider, exporter, processor, instrumentation, middleware or thread exists,
and the OpenTelemetry API's global providers stay its defaults. Log lines
carry ``"trace_id": null`` (``app.core.logging``).

When it is on, :mod:`app.core.telemetry_sdk` (imported only then) sets up:

- traces, metrics and logs exported over OTLP/HTTP to the endpoint, from
  background batch processors, so no request waits on an export;
- FastAPI instrumentation (:func:`instrument_app`), without the health probes
  and the live-notification stream;
- SQLAlchemy instrumentation of every engine ``app.core.db`` creates
  (:func:`instrument_engine`): statement text with placeholders, never bound
  values;
- one span per Procrastinate job (:func:`worker_middleware`);
- the personal-data rules: spans keep an allow-list of attributes, exception
  events keep the type and frames only, and logs exported over OTLP are the
  same redacted JSON lines as stdout.

The providers are kept here, not installed as the API's globals, so a test can
configure and shut telemetry down more than once.
"""

from typing import TYPE_CHECKING, Any, Final

from app.core.config import Settings

if TYPE_CHECKING:
    from fastapi import FastAPI
    from procrastinate.middleware import WorkerMiddleware
    from sqlalchemy.ext.asyncio import AsyncEngine

    from app.core.telemetry_sdk import Telemetry, TestExporters

API_SERVICE: Final = "classora-api"
WORKER_SERVICE: Final = "classora-worker"

_state: Telemetry | None = None


def is_enabled() -> bool:
    """``True`` once :func:`configure_telemetry` has turned telemetry on."""
    return _state is not None


def configure_telemetry(
    settings: Settings,
    service: str,
    *,
    exporters: TestExporters | None = None,
) -> bool:
    """Turn telemetry on if ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set. Idempotent.

    Returns whether telemetry is on. With no endpoint (and no test
    ``exporters``) it returns ``False`` immediately and creates nothing.
    ``exporters`` replaces the OTLP exporters with in-memory ones (tests only).
    """
    global _state
    if _state is not None:
        return True
    if settings.otel_exporter_otlp_endpoint is None and exporters is None:
        return False
    from app.core import telemetry_sdk

    _state = telemetry_sdk.start(settings, service, exporters=exporters)
    return True


def instrument_app(app: FastAPI) -> None:
    """Trace and measure ``app``'s requests. No-op while telemetry is off."""
    if _state is not None:
        _state.instrument_app(app)


def instrument_engine(engine: AsyncEngine) -> None:
    """Trace ``engine``'s SQL statements. No-op while telemetry is off."""
    if _state is not None:
        _state.instrument_engine(engine)


def worker_middleware() -> list[WorkerMiddleware]:
    """The job-tracing worker middleware, or ``[]`` while telemetry is off."""
    if _state is None:
        return []
    return [_state.trace_job]


def shutdown_telemetry() -> None:
    """Flush and stop telemetry (bounded; see ``telemetry_sdk``). No-op when off."""
    global _state
    state, _state = _state, None
    if state is not None:
        state.shutdown()


def current_state() -> Any:
    """The running telemetry (tests only), or ``None``."""
    return _state
