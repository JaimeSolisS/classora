"""The OpenTelemetry SDK side of :mod:`app.core.telemetry` (ADR-0014, #612).

Imported only by :func:`app.core.telemetry.configure_telemetry` once
``OTEL_EXPORTER_OTLP_ENDPOINT`` is set; never while telemetry is off.

Personal data (ADR-0014 "Personal data", #612 E2/E3):

- **Spans** pass through :class:`ScrubbingSpanProcessor` before any exporter
  sees them. It keeps only the attributes in :data:`ALLOWED_SPAN_ATTRIBUTES`
  (route template, method, status, SQL statement text with placeholders, job
  id and task, ...), so the query string, ``url.full``/``url.path``, headers,
  client address and user agent never leave the process. The span status
  keeps its code and drops its description (SQLAlchemy puts the driver
  message there). Exception events keep ``exception.type`` and the stack
  frames (:func:`app.core.logging.scrub_traceback`) and drop
  ``exception.message``.
- **SQL**: the engines are traced with the statement text only (bound
  parameters are never read; no sqlcommenter).
- **Metrics** keep only :data:`ALLOWED_METRIC_ATTRIBUTES` (one SDK view).
- **Logs** exported over OTLP are the stdout JSON line itself
  (``app.core.logging.JsonFormatter``): the same redaction, the same
  exception scrubbing.

Exports run in the SDK's background threads (batch span and log processors,
periodic metric reader) with :data:`EXPORT_TIMEOUT_SECONDS` per request, so an
unreachable collector never slows a request; what can't be sent is dropped.
"""

import asyncio
import logging
import os
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from opentelemetry import trace
from opentelemetry._logs import SeverityNumber
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
    OTLPMetricExporter,
)
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation._semconv import (
    _OpenTelemetrySemanticConventionStability,
    _OpenTelemetryStabilitySignalType,
    _StabilityMode,
)
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy.engine import EngineTracer
from opentelemetry.metrics import Histogram, Meter
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import (
    BatchLogRecordProcessor,
    LogRecordExporter,
    SimpleLogRecordProcessor,
)
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.metrics.view import View
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Event, ReadableSpan, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
)
from opentelemetry.sdk.trace.sampling import ALWAYS_ON, ParentBased
from opentelemetry.trace import SpanKind, Status, StatusCode
from opentelemetry.util.types import Attributes
from procrastinate.exceptions import JobAborted
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.core.logging import (
    TELEMETRY_LOGGER_PREFIXES,
    JsonFormatter,
    scrub_traceback,
)

if TYPE_CHECKING:
    from fastapi import FastAPI
    from procrastinate.job_context import JobContext
    from procrastinate.middleware import AsyncCallNext
    from procrastinate.worker import Worker
    from sqlalchemy.engine import Connection, ExecutionContext

# --- Settings ----------------------------------------------------------------

EXPORT_TIMEOUT_SECONDS: Final = 3.0
"""Per export request; retries stop at this deadline."""
METRIC_EXPORT_INTERVAL_MILLIS: Final = 15_000
SHUTDOWN_TIMEOUT_SECONDS: Final = 5.0
"""Total time :meth:`Telemetry.shutdown` waits for the last flush (the API's
graceful window and the worker's stop_grace_period both leave room)."""

# Not traced: high-frequency probes and the long-lived SSE stream (an open
# span for minutes). Matched against scheme://host/path (no query string).
EXCLUDED_URLS: Final = ",".join(
    (
        r"^https?://[^/]+/health/(live|ready)$",
        r"^https?://[^/]+/api/v1/notifications/stream$",
    )
)

ALLOWED_SPAN_ATTRIBUTES: Final = frozenset(
    {
        # HTTP server spans: the route template, never the path or query.
        "http.route",
        "http.request.method",
        "http.response.status_code",
        "url.scheme",
        "network.protocol.version",
        "error.type",
        # SQL spans: statement text with placeholders, never bound values.
        "db.system.name",
        "db.namespace",
        "db.operation.name",
        "db.query.text",
        "server.address",
        "server.port",
        "network.transport",
        # Job spans.
        "job.id",
        "job.task",
        "job.status",
    }
)
"""Every other span attribute is dropped: ``url.full``, ``url.path``,
``url.query``, ``http.target``, ``client.address``, ``user_agent.original``,
``http.request.header.*``, ``enduser.*`` and anything an instrumentation adds
later."""

ALLOWED_EXCEPTION_ATTRIBUTES: Final = frozenset(
    {"exception.type", "exception.stacktrace", "exception.escaped"}
)

ALLOWED_METRIC_ATTRIBUTES: Final = frozenset(
    {
        "http.request.method",
        "http.response.status_code",
        "http.route",
        "url.scheme",
        "network.protocol.version",
        "error.type",
        "pool.name",
        "state",
        "db.system.name",
        "db.operation.name",
        "job.task",
        "job.status",
    }
)

DURATION_BUCKETS_SECONDS: Final = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
    60.0,
    300.0,
)


@dataclass(frozen=True)
class TestExporters:
    """In-memory exporters for tests: spans and logs are exported synchronously."""

    __test__ = False  # not a pytest test class

    span_exporter: SpanExporter
    metric_reader: MetricReader
    log_exporter: LogRecordExporter


# --- Spans ----------------------------------------------------------------------


def _allowed(attributes: Attributes, allowed: frozenset[str]) -> dict[str, Any]:
    return {k: v for k, v in (attributes or {}).items() if k in allowed}


def _scrub_event(original: Event) -> Event:
    if original.name == "exception":
        attributes = _allowed(original.attributes, ALLOWED_EXCEPTION_ATTRIBUTES)
        stacktrace = attributes.get("exception.stacktrace")
        if isinstance(stacktrace, str):
            attributes["exception.stacktrace"] = scrub_traceback(stacktrace)
    else:
        attributes = _allowed(original.attributes, ALLOWED_SPAN_ATTRIBUTES)
    return Event(original.name, attributes, original.timestamp)


def scrub_span(span: ReadableSpan) -> ReadableSpan:
    """A copy of ``span`` holding only what the personal-data rules allow."""
    return ReadableSpan(
        name=span.name,
        context=span.context,
        parent=span.parent,
        resource=span.resource,
        attributes=_allowed(span.attributes, ALLOWED_SPAN_ATTRIBUTES),
        events=[_scrub_event(e) for e in span.events],
        links=(),
        kind=span.kind,
        status=Status(span.status.status_code),
        start_time=span.start_time,
        end_time=span.end_time,
        instrumentation_scope=span.instrumentation_scope,
    )


class ScrubbingSpanProcessor(SpanProcessor):
    """Hands :func:`scrub_span`'s copy of each ended span to ``inner``."""

    def __init__(self, inner: SpanProcessor) -> None:
        self.inner = inner

    def on_start(self, span: Any, parent_context: Context | None = None) -> None:
        self.inner.on_start(span, parent_context=parent_context)

    def on_end(self, span: ReadableSpan) -> None:
        self.inner.on_end(scrub_span(span))

    def shutdown(self) -> None:
        self.inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self.inner.force_flush(timeout_millis)


# --- Logs -----------------------------------------------------------------------

_SEVERITY_TEXT: Final = {"WARNING": "WARN", "CRITICAL": "FATAL"}


class OtlpLogHandler(logging.Handler):
    """Sends each record over OTLP with the stdout JSON line as its body.

    The body is :class:`app.core.logging.JsonFormatter`'s output, so the
    exported log has exactly the stdout line's redaction and exception
    scrubbing; no record attribute (``extra``, ``exc_info``) is exported on
    its own. The current span context gives the record its ``trace_id``.
    Records of the exporter's own loggers are not exported (no feedback
    loop when the collector is down).
    """

    def __init__(self, logger_provider: LoggerProvider) -> None:
        super().__init__()
        self.logger_provider = logger_provider
        self.setFormatter(JsonFormatter())

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith(TELEMETRY_LOGGER_PREFIXES):
            return
        try:
            body = self.format(record)
            level = record.levelname
            self.logger_provider.get_logger(record.name).emit(
                timestamp=int(record.created * 1e9),
                context=Context(trace.set_span_in_context(trace.get_current_span())),
                severity_text=_SEVERITY_TEXT.get(level, level),
                severity_number=_severity_number(record.levelno),
                body=body,
            )
        except Exception:  # noqa: BLE001 - logging must never raise
            self.handleError(record)


def _severity_number(levelno: int) -> SeverityNumber:
    if levelno >= logging.CRITICAL:
        return SeverityNumber.FATAL
    if levelno >= logging.ERROR:
        return SeverityNumber.ERROR
    if levelno >= logging.WARNING:
        return SeverityNumber.WARN
    if levelno >= logging.INFO:
        return SeverityNumber.INFO
    return SeverityNumber.DEBUG


# --- Semantic conventions ------------------------------------------------------


def _use_stable_semantic_conventions() -> None:
    """Stable HTTP and database attribute names (``http.request.method``,
    ``http.server.request.duration`` in seconds, ``db.query.text``) instead
    of the instrumentations' old defaults, without reading
    ``OTEL_SEMCONV_STABILITY_OPT_IN`` from the environment (``app.core.config``
    is the only env reader). The instrumentation versions are pinned exactly;
    ``tests/unit/test_telemetry.py`` checks the resulting names."""
    stability = _OpenTelemetrySemanticConventionStability
    with stability._lock:
        stability._OTEL_SEMCONV_STABILITY_SIGNAL_MAPPING = {
            _OpenTelemetryStabilitySignalType.HTTP: _StabilityMode.HTTP,
            _OpenTelemetryStabilitySignalType.DATABASE: _StabilityMode.DATABASE,
            _OpenTelemetryStabilitySignalType.GEN_AI: _StabilityMode.DEFAULT,
        }
        stability._initialized = True


# --- The running telemetry -----------------------------------------------------


@dataclass
class Telemetry:
    """Providers and instruments of one process (see ``app.core.telemetry``)."""

    tracer_provider: TracerProvider
    meter_provider: MeterProvider
    logger_provider: LoggerProvider
    log_handler: OtlpLogHandler
    meter: Meter
    job_duration: Histogram
    db_duration: Histogram
    db_connections_usage: Any
    tracer: trace.Tracer = field(init=False)
    db_tracer: trace.Tracer = field(init=False)

    def __post_init__(self) -> None:
        self.tracer = self.tracer_provider.get_tracer("app.core.telemetry")
        self.db_tracer = self.tracer_provider.get_tracer(
            "opentelemetry.instrumentation.sqlalchemy"
        )

    def instrument_app(self, app: FastAPI) -> None:
        FastAPIInstrumentor.instrument_app(
            app,
            tracer_provider=self.tracer_provider,
            meter_provider=self.meter_provider,
            excluded_urls=EXCLUDED_URLS,
            # No headers captured (the instrumentation would otherwise read a
            # list from the environment); per-message send/receive spans off.
            http_capture_headers_server_request=[],
            http_capture_headers_server_response=[],
            exclude_spans=["receive", "send"],
        )

    def instrument_engine(self, engine: AsyncEngine) -> None:
        sync_engine = engine.sync_engine
        # Statement text only: sqlcommenter off, parameters never read.
        EngineTracer(  # type: ignore[no-untyped-call]
            self.db_tracer,
            sync_engine,
            self.db_connections_usage,
            enable_commenter=False,
        )
        histogram = self.db_duration

        def before(
            conn: Connection,
            cursor: Any,
            statement: str,
            parameters: Any,
            context: ExecutionContext,
            executemany: bool,
        ) -> None:
            context._classora_started = time.perf_counter()  # type: ignore[attr-defined]

        def after(
            conn: Connection,
            cursor: Any,
            statement: str,
            parameters: Any,
            context: ExecutionContext,
            executemany: bool,
        ) -> None:
            started = getattr(context, "_classora_started", None)
            if started is None:
                return
            words = statement.split(maxsplit=1)
            operation = words[0].upper() if words else "OTHER"
            histogram.record(
                time.perf_counter() - started,
                {
                    "db.system.name": "postgresql",
                    "db.operation.name": operation[:16],
                },
            )

        event.listen(sync_engine, "before_cursor_execute", before)
        event.listen(sync_engine, "after_cursor_execute", after)

    async def trace_job(
        self, call_next: AsyncCallNext, context: JobContext, worker: Worker
    ) -> Any:
        """Worker middleware: one span ``job <task>`` per run (never the args)."""
        job = context.job
        task = job.task_name
        started = time.perf_counter()
        status = "failed"
        with self.tracer.start_as_current_span(
            f"job {task}",
            kind=SpanKind.CONSUMER,
            attributes={"job.id": job.id or 0, "job.task": task},
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            try:
                result = await call_next()
            except BaseException as exc:
                if isinstance(exc, JobAborted | asyncio.CancelledError):
                    status = "aborted"
                else:
                    # Type and frames only once scrubbed (ScrubbingSpanProcessor).
                    span.record_exception(exc, escaped=True)
                    span.set_status(Status(StatusCode.ERROR))
                raise
            else:
                status = "succeeded"
                return result
            finally:
                span.set_attribute("job.status", status)
                self.job_duration.record(
                    time.perf_counter() - started,
                    {"job.task": task, "job.status": status},
                )

    def shutdown(self) -> None:
        """Flush and stop every provider, in parallel, within the timeout."""
        logging.getLogger().removeHandler(self.log_handler)
        deadline = time.monotonic() + SHUTDOWN_TIMEOUT_SECONDS
        threads = [
            threading.Thread(target=stop, name=f"telemetry-shutdown-{n}", daemon=True)
            for n, stop in enumerate(
                (
                    self.tracer_provider.shutdown,
                    self.logger_provider.shutdown,
                    lambda: self.meter_provider.shutdown(
                        timeout_millis=SHUTDOWN_TIMEOUT_SECONDS * 1000
                    ),
                )
            )
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(max(0.0, deadline - time.monotonic()))


def _resource(settings: Settings, service: str) -> Resource:
    # Resource(...) rather than Resource.create(): no detector reads
    # OTEL_RESOURCE_ATTRIBUTES or OTEL_SERVICE_NAME from the environment.
    return Resource(
        {
            "service.name": service,
            "service.instance.id": f"{socket.gethostname()}-{os.getpid()}",
            "deployment.environment.name": settings.app_env,
        }
    )


def _signal_url(settings: Settings, signal: str) -> str:
    endpoint = settings.otel_exporter_otlp_endpoint
    assert endpoint is not None  # noqa: S101 - configure_telemetry checked it
    return f"{str(endpoint).rstrip('/')}/v1/{signal}"


def start(
    settings: Settings, service: str, *, exporters: TestExporters | None = None
) -> Telemetry:
    """Create the providers, start exporting and attach the OTLP log handler."""
    _use_stable_semantic_conventions()
    resource = _resource(settings, service)

    span_processor: SpanProcessor
    log_processor: Any
    if exporters is None:
        span_processor = BatchSpanProcessor(
            OTLPSpanExporter(
                endpoint=_signal_url(settings, "traces"),
                timeout=EXPORT_TIMEOUT_SECONDS,
            )
        )
        metric_reader: MetricReader = PeriodicExportingMetricReader(
            OTLPMetricExporter(
                endpoint=_signal_url(settings, "metrics"),
                timeout=EXPORT_TIMEOUT_SECONDS,
            ),
            export_interval_millis=METRIC_EXPORT_INTERVAL_MILLIS,
            export_timeout_millis=EXPORT_TIMEOUT_SECONDS * 1000,
        )
        log_processor = BatchLogRecordProcessor(
            OTLPLogExporter(
                endpoint=_signal_url(settings, "logs"),
                timeout=EXPORT_TIMEOUT_SECONDS,
            )
        )
    else:
        span_processor = SimpleSpanProcessor(exporters.span_exporter)
        metric_reader = exporters.metric_reader
        log_processor = SimpleLogRecordProcessor(exporters.log_exporter)

    tracer_provider = TracerProvider(
        resource=resource, sampler=ParentBased(ALWAYS_ON), shutdown_on_exit=False
    )
    tracer_provider.add_span_processor(ScrubbingSpanProcessor(span_processor))

    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[metric_reader],
        views=[
            View(instrument_name="*", attribute_keys=set(ALLOWED_METRIC_ATTRIBUTES))
        ],
        shutdown_on_exit=False,
    )
    meter = meter_provider.get_meter("app.core.telemetry")

    logger_provider = LoggerProvider(
        resource=resource, shutdown_on_exit=False, meter_provider=meter_provider
    )
    logger_provider.add_log_record_processor(log_processor)
    log_handler = OtlpLogHandler(logger_provider)
    logging.getLogger().addHandler(log_handler)

    return Telemetry(
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
        logger_provider=logger_provider,
        log_handler=log_handler,
        meter=meter,
        job_duration=meter.create_histogram(
            "classora.job.duration",
            unit="s",
            description="Duration of one Procrastinate job run.",
            explicit_bucket_boundaries_advisory=list(DURATION_BUCKETS_SECONDS),
        ),
        db_duration=meter.create_histogram(
            "db.client.operation.duration",
            unit="s",
            description="Duration of one SQL statement.",
            explicit_bucket_boundaries_advisory=list(DURATION_BUCKETS_SECONDS),
        ),
        db_connections_usage=meter.create_up_down_counter(
            "db.client.connections.usage",
            unit="{connection}",
            description="Connections in each state (idle, used) per pool.",
        ),
    )
