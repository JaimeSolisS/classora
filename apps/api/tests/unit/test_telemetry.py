"""Telemetry (ADR-0014, #612): off is a no-op; on, the personal-data rules hold.

The "off" checks run in a fresh interpreter, so nothing another test turned on
can hide an import, a provider or a thread. The "on" checks use in-memory
exporters (``app.core.telemetry_sdk.TestExporters``), or a closed port for the
resilience check; every one shuts telemetry down again.
"""

import json
import logging
import os
import re
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from procrastinate import App
from procrastinate.testing import InMemoryConnector
from pydantic import HttpUrl

from app.core import telemetry
from app.core.config import Settings
from app.core.logging import (
    EXPORT_ERROR_INTERVAL_SECONDS,
    ExportErrorThrottle,
    configure_logging,
    scrub_traceback,
)
from app.core.telemetry_sdk import (
    ALLOWED_SPAN_ATTRIBUTES,
    EXCLUDED_URLS,
    TestExporters,
)
from tests.authz.csrf import TEST_APP_ORIGIN
from tests.unit.conventions_app import build_test_app, read_log_lines

API_ROOT = Path(__file__).resolve().parents[2]
MARKER = "ZZ-PII-MARKER-7f3a"
PLACEHOLDER_DSN = "postgresql://placeholder:placeholder@127.0.0.1:1/placeholder"


def settings(endpoint: str | None = None) -> Settings:
    return Settings(
        app_env="local",
        app_origin=TEST_APP_ORIGIN,
        database_url=PLACEHOLDER_DSN,  # type: ignore[arg-type]
        database_direct_url=PLACEHOLDER_DSN,  # type: ignore[arg-type]
        otel_exporter_otlp_endpoint=HttpUrl(endpoint) if endpoint else None,
    )


def otel_threads() -> list[str]:
    return [t.name for t in threading.enumerate() if t.name.startswith("Otel")]


# --- AC1: off is a no-op ------------------------------------------------------------

OFF_PROBE = """
import json, sys, threading
from opentelemetry import _logs, metrics, trace
from sqlalchemy import event
from app.core import telemetry
from app.core.config import get_settings
from app.core.db import create_engine
from app.main import create_app
from app.worker import worker_options

threads_before = sorted(t.name for t in threading.enumerate())
app = create_app()
stack, chain = app.build_middleware_stack(), []
while stack is not None and len(chain) < 30:
    chain.append(type(stack).__name__)
    stack = getattr(stack, "app", None)
engine = create_engine(get_settings())
print(json.dumps({
    "enabled": telemetry.is_enabled(),
    "sdk_modules": sorted(
        m for m in sys.modules
        if m.startswith(("opentelemetry.sdk", "opentelemetry.exporter",
                         "opentelemetry.instrumentation", "app.core.telemetry_sdk"))
    ),
    "providers": [
        type(trace.get_tracer_provider()).__name__,
        type(metrics.get_meter_provider()).__name__,
        type(_logs.get_logger_provider()).__name__,
    ],
    "threads_before": threads_before,
    "threads_after": sorted(t.name for t in threading.enumerate()),
    "instrumented": hasattr(app, "_original_build_middleware_stack"),
    "user_middleware": [m.cls.__name__ for m in app.user_middleware],
    "chain": chain,
    "sql_listeners": event.contains(
        engine.sync_engine, "before_cursor_execute", lambda *a: None
    ) or len(engine.sync_engine.dispatch.before_cursor_execute),
    "worker_middleware": [
        m.__name__ for m in worker_options(get_settings())["worker_middleware"]
    ],
}))
"""


def run_off_probe(**extra_env: str) -> dict[str, Any]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("OTEL_", "FASTAPI_OTEL"))
    }
    env.update(
        APP_ENV="local",
        APP_ORIGIN=TEST_APP_ORIGIN,
        DATABASE_URL=PLACEHOLDER_DSN,
        DATABASE_DIRECT_URL=PLACEHOLDER_DSN,
        **extra_env,
    )
    result = subprocess.run(  # noqa: S603 - fixed interpreter and script
        [sys.executable, "-c", OFF_PROBE],
        cwd=API_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize(
    "extra_env",
    [
        {},
        # Compose passes an empty value when .env leaves it unset.
        {"OTEL_EXPORTER_OTLP_ENDPOINT": ""},
        # FastAPI's own bridge would configure itself from these; it is off.
        {"FASTAPI_OTEL_AUTO_CONFIGURE": "true"},
    ],
    ids=["unset", "empty", "fastapi-auto-configure"],
)
def test_off_creates_no_sdk_object_thread_or_middleware(extra_env):
    probe = run_off_probe(**extra_env)

    assert probe["enabled"] is False
    # The SDK, exporters and instrumentations are never even imported.
    assert probe["sdk_modules"] == []
    # The OpenTelemetry API's global providers stay its default proxies.
    assert probe["providers"] == [
        "ProxyTracerProvider",
        "_ProxyMeterProvider",
        "ProxyLoggerProvider",
    ]
    # No export or batch thread (or any other) was started.
    assert probe["threads_after"] == probe["threads_before"]
    assert not any(name.startswith("Otel") for name in probe["threads_after"])
    # FastAPI isn't instrumented, and SQLAlchemy engines get no listener.
    assert probe["instrumented"] is False
    assert not any("OpenTelemetry" in name for name in probe["chain"])
    assert probe["sql_listeners"] == 0
    assert probe["worker_middleware"] == ["record_job_failure"]


def test_off_adds_no_middleware_compared_with_develop():
    # The stack create_app() builds on develop 9df7060, before #612.
    probe = run_off_probe()

    assert probe["user_middleware"] == [
        "SecurityHeadersMiddleware",
        "RequestContextMiddleware",
    ]
    assert probe["chain"][:6] == [
        "ServerErrorMiddleware",
        "ExceptionTelemetryMiddleware",
        "SecurityHeadersMiddleware",
        "RequestContextMiddleware",
        "ExceptionMiddleware",
        "AsyncExitStackMiddleware",
    ]


def test_configure_without_an_endpoint_returns_false_and_keeps_every_hook_inert():
    threads = otel_threads()

    assert telemetry.configure_telemetry(settings(), telemetry.API_SERVICE) is False
    assert telemetry.is_enabled() is False
    assert telemetry.worker_middleware() == []
    app = FastAPI()
    telemetry.instrument_app(app)
    assert not hasattr(app, "_original_build_middleware_stack")
    telemetry.shutdown_telemetry()  # no-op
    assert otel_threads() == threads
    assert type(trace.get_tracer_provider()).__name__ == "ProxyTracerProvider"


# --- On (in-memory exporters) -------------------------------------------------------


class Exported:
    def __init__(self) -> None:
        self.spans = InMemorySpanExporter()
        self.metrics = InMemoryMetricReader()
        self.logs = InMemoryLogRecordExporter()

    def exporters(self) -> TestExporters:
        return TestExporters(self.spans, self.metrics, self.logs)

    def span_list(self) -> list[Any]:
        return list(self.spans.get_finished_spans())

    def everything(self) -> str:
        """Every exported span, log record and metric, as one JSON text."""
        parts = [span.to_json() for span in self.spans.get_finished_spans()]
        parts += [log.to_json() for log in self.logs.get_finished_logs()]
        data = self.metrics.get_metrics_data()
        if data is not None:
            parts.append(data.to_json())
        return "\n".join(parts)


@pytest.fixture
def exported() -> Iterator[Exported]:
    configure_logging()
    exported = Exported()
    assert telemetry.configure_telemetry(
        settings(), telemetry.API_SERVICE, exporters=exported.exporters()
    )
    try:
        yield exported
    finally:
        telemetry.shutdown_telemetry()


def probe_router() -> APIRouter:
    router = APIRouter()

    @router.get("/probe/items/{item_id}")
    async def item(item_id: str) -> dict[str, str]:
        logging.getLogger("tests.telemetry").info("inside the probe route")
        return {"ok": "yes"}

    @router.get("/probe/boom")
    async def boom() -> None:
        raise RuntimeError(f"failed for {MARKER}")

    return router


@pytest.fixture
def quiet_test_client_log() -> Iterator[None]:
    """The test client's own httpx line (the full URL it sent) is not the app's."""
    httpx_logger = logging.getLogger("httpx")
    level = httpx_logger.level
    httpx_logger.setLevel(logging.WARNING)
    yield
    httpx_logger.setLevel(level)


@pytest.fixture
def traced_client(
    exported: Exported, quiet_test_client_log: None
) -> Iterator[TestClient]:
    app = build_test_app()
    app.include_router(probe_router())
    telemetry.instrument_app(app)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def test_on_a_request_yields_one_server_span_named_by_its_route(
    exported, traced_client, capsys
):
    # Path values are ids in this API; the access line keeps the path (not
    # the query string), as on stdout. The query string reaches nothing.
    response = traced_client.get(f"/probe/items/42?q={MARKER}")

    assert response.status_code == 200
    [span] = exported.span_list()
    assert span.name == "GET /probe/items/{item_id}"
    assert span.kind == trace.SpanKind.SERVER
    assert span.attributes["http.route"] == "/probe/items/{item_id}"
    assert span.attributes["http.request.method"] == "GET"
    assert span.attributes["http.response.status_code"] == 200
    assert set(span.attributes) <= ALLOWED_SPAN_ATTRIBUTES
    assert span.resource.attributes["service.name"] == "classora-api"
    assert span.resource.attributes["deployment.environment.name"] == "local"
    # The stdout lines (app and access) carry the span's ids.
    trace_id = format(span.context.trace_id, "032x")
    lines = read_log_lines(capsys)
    app_line = next(line for line in lines if line["logger"] == "tests.telemetry")
    access = next(line for line in lines if line["logger"] == "app.access")
    assert app_line["trace_id"] == access["trace_id"] == trace_id
    assert app_line["span_id"] == format(span.context.span_id, "016x")
    # The exported log record is the same JSON line, linked to the span.
    exported_lines = [
        (json.loads(str(log.log_record.body)), log.log_record.trace_id)
        for log in exported.logs.get_finished_logs()
    ]
    assert (app_line, span.context.trace_id) in exported_lines
    assert access["path"] == "/probe/items/42"
    assert MARKER not in exported.everything()
    assert "/probe/items/42" not in "".join(
        span.to_json() for span in exported.span_list()
    )


def test_on_the_http_duration_metric_is_by_route_only(exported, traced_client):
    traced_client.get(f"/probe/items/{MARKER}?q={MARKER}")

    # Metrics never carry the path or the query string.
    data = exported.metrics.get_metrics_data()
    metrics = {
        metric.name: metric
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    }
    duration = metrics["http.server.request.duration"]
    assert duration.unit == "s"
    [point] = duration.data.data_points
    assert point.attributes["http.route"] == "/probe/items/{item_id}"
    assert MARKER not in data.to_json()


def test_on_an_unhandled_error_keeps_the_type_and_drops_the_message(
    exported, traced_client, capsys
):
    response = traced_client.get("/probe/boom")

    assert response.status_code == 500
    [span] = exported.span_list()
    assert span.status.status_code == trace.StatusCode.ERROR
    assert span.status.description is None
    [event] = [e for e in span.events if e.name == "exception"]
    assert event.attributes["exception.type"] == "RuntimeError"
    assert "exception.message" not in event.attributes
    assert event.attributes["exception.stacktrace"].splitlines()[-1] == "RuntimeError"
    error = next(line for line in read_log_lines(capsys) if line["level"] == "ERROR")
    assert error["trace_id"] == format(span.context.trace_id, "032x")
    assert MARKER not in json.dumps(error)
    assert MARKER not in exported.everything()


@pytest.mark.parametrize(
    "path",
    ["/health/live", "/health/ready", "/api/v1/notifications/stream"],
)
def test_excluded_paths_match_the_patterns(path):
    patterns = EXCLUDED_URLS.split(",")

    assert any(re.match(p, f"http://testserver{path}") for p in patterns)
    assert not any(re.match(p, f"http://testserver{path}/x") for p in patterns), (
        "only the exact paths are excluded"
    )


def test_health_and_stream_routes_produce_no_span(exported):
    # The real app's routes (no lifespan: /health/ready fails, the stream
    # refuses an anonymous client; neither is traced either way).
    app = telemetry_app()
    with TestClient(app, raise_server_exceptions=False) as client:
        for path in ("/health/live", "/health/ready", "/api/v1/notifications/stream"):
            client.get(path)
        assert exported.span_list() == []
        client.get("/api/v1/auth/me")
    [span] = exported.span_list()
    assert span.name == "GET /api/v1/auth/me"


def telemetry_app() -> FastAPI:
    from app.main import build_app

    app = build_app()
    telemetry.instrument_app(app)
    return app


# --- Span scrubbing -----------------------------------------------------------------


def test_spans_keep_only_allowed_attributes_and_exception_types(exported):
    tracer = telemetry.current_state().tracer
    with tracer.start_as_current_span(
        "probe",
        attributes={
            "http.route": "/x/{id}",
            "url.full": f"http://h/x/1?q={MARKER}",
            "url.query": f"q={MARKER}",
            "url.path": f"/x/{MARKER}",
            "http.target": f"/x?q={MARKER}",
            "http.url": f"http://h/x?q={MARKER}",
            "client.address": "203.0.113.9",
            "net.peer.ip": "203.0.113.9",
            "user_agent.original": MARKER,
            "http.request.header.x_note": [MARKER],
            "http.response.header.set_cookie": [MARKER],
            "enduser.id": MARKER,
            "db.statement": f"SELECT '{MARKER}'",
        },
        record_exception=False,
    ) as span:
        span.add_event("note", {"detail": MARKER, "http.route": "/x/{id}"})
        try:
            raise ValueError(MARKER)
        except ValueError as exc:
            span.record_exception(exc, attributes={"extra": MARKER})
        span.set_status(trace.Status(trace.StatusCode.ERROR, MARKER))

    [exported_span] = exported.span_list()
    assert dict(exported_span.attributes) == {"http.route": "/x/{id}"}
    note, exception = exported_span.events
    assert dict(note.attributes) == {"http.route": "/x/{id}"}
    assert set(exception.attributes) == {
        "exception.type",
        "exception.stacktrace",
        "exception.escaped",
    }
    assert exception.attributes["exception.type"] == "ValueError"
    assert exported_span.status.description is None
    assert MARKER not in exported_span.to_json()


# --- Tracebacks without messages (E2/E3) --------------------------------------------


def formatted(exc: BaseException) -> str:
    import traceback

    return "".join(traceback.format_exception(exc))


def raise_chain() -> None:
    try:
        raise KeyError(MARKER)
    except KeyError as first:
        try:
            raise ValueError(f"during {MARKER}") from first
        except ValueError:
            raise RuntimeError(f"Key (username)=({MARKER})\nDETAIL: {MARKER}")  # noqa: B904


def test_scrub_traceback_drops_every_message_in_a_chain():
    try:
        raise_chain()
    except RuntimeError as exc:
        text = formatted(exc)
    assert MARKER in text

    scrubbed = scrub_traceback(text)

    assert MARKER not in scrubbed
    type_lines = [
        line for line in scrubbed.splitlines() if line in ("KeyError", "ValueError")
    ]
    assert type_lines == ["KeyError", "ValueError"]
    assert scrubbed.splitlines()[-1] == "RuntimeError"
    assert scrubbed.count("Traceback (most recent call last):") == 3
    assert 'File "' in scrubbed


def test_scrub_traceback_drops_an_asyncpg_style_detail_and_unknown_text():
    text = (
        "Traceback (most recent call last):\n"
        '  File "/app/x.py", line 3, in f\n'
        "    await session.flush()\n"
        "sqlalchemy.exc.IntegrityError: (asyncpg.exceptions.UniqueViolationError)"
        ' duplicate key value violates unique constraint "uq_users_username"\n'
        f"DETAIL:  Key (username)=({MARKER}) already exists.\n"
        "[SQL: the statement, with $1 placeholders]\n"
        f"  [Previous line repeated 2 more times] {MARKER}\n"
        f"{MARKER} stray text\n"
    )

    scrubbed = scrub_traceback(text)

    assert scrubbed.splitlines()[-1] == "sqlalchemy.exc.IntegrityError"
    assert MARKER not in scrubbed
    assert "uq_users_username" not in scrubbed


def test_scrub_traceback_of_an_exception_group_keeps_no_message():
    group = ExceptionGroup(f"group {MARKER}", [ValueError(MARKER), KeyError(MARKER)])
    try:
        raise group
    except ExceptionGroup as exc:
        text = formatted(exc)

    assert MARKER not in scrub_traceback(text)


# --- Export errors are throttled ----------------------------------------------------


def record(name: str) -> logging.LogRecord:
    return logging.LogRecord(name, logging.WARNING, __file__, 1, "x", None, None)


def test_export_errors_pass_at_most_once_per_interval():
    throttle = ExportErrorThrottle(EXPORT_ERROR_INTERVAL_SECONDS)

    passed = [
        throttle.filter(record(name))
        for name in (
            "opentelemetry.exporter.otlp.proto.http.trace_exporter",
            "urllib3.connectionpool",
            "opentelemetry.sdk._shared_internal",
        )
    ]

    assert passed == [True, False, False]
    assert throttle.filter(record("app.access")) is True
    assert EXPORT_ERROR_INTERVAL_SECONDS == 60.0


def test_export_errors_pass_again_after_the_interval():
    throttle = ExportErrorThrottle(0.05)

    assert throttle.filter(record("opentelemetry.x")) is True
    assert throttle.filter(record("opentelemetry.x")) is False
    time.sleep(0.06)
    assert throttle.filter(record("opentelemetry.x")) is True


# --- Jobs ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_on_each_job_run_yields_a_job_span_without_its_arguments(exported):
    job_app = App(connector=InMemoryConnector())

    # Job arguments are ids only (app.core.jobs.ids_only); the failure's
    # message carries the marker, as a driver message would.
    @job_app.task(name="probe:ok")
    async def ok(item: int) -> None:
        logging.getLogger("tests.telemetry").info("inside the job")

    @job_app.task(name="probe:fails")
    async def fails(item: int) -> None:
        raise RuntimeError(f"Key (username)=({MARKER})")

    async with job_app.open_async():
        await ok.defer_async(item=7)
        await fails.defer_async(item=8)
        await job_app.run_worker_async(
            wait=False,
            install_signal_handlers=False,
            worker_middleware=telemetry.worker_middleware(),
        )

    spans = {span.name: span for span in exported.span_list()}
    assert set(spans) == {"job probe:ok", "job probe:fails"}
    ok_span, failed = spans["job probe:ok"], spans["job probe:fails"]
    assert ok_span.kind == trace.SpanKind.CONSUMER
    assert ok_span.attributes["job.task"] == "probe:ok"
    assert ok_span.attributes["job.status"] == "succeeded"
    assert isinstance(ok_span.attributes["job.id"], int)
    assert failed.attributes["job.status"] == "failed"
    assert failed.status.status_code == trace.StatusCode.ERROR
    [event] = failed.events
    assert event.attributes["exception.type"] == "RuntimeError"
    assert MARKER not in exported.everything()
    # A log line inside the job carries the job span's trace id.
    [log] = [
        log
        for log in exported.logs.get_finished_logs()
        if json.loads(str(log.log_record.body))["logger"] == "tests.telemetry"
    ]
    assert log.log_record.trace_id == ok_span.context.trace_id
    # The duration metric is labelled by task and status only.
    data = exported.metrics.get_metrics_data().to_json()
    assert "classora.job.duration" in data
    assert MARKER not in data


def test_worker_options_put_the_job_span_outermost(exported):
    from app.core.jobs import record_job_failure
    from app.worker import worker_options

    middleware = worker_options(settings())["worker_middleware"]

    assert middleware == [telemetry.current_state().trace_job, record_job_failure]


# --- Resilience (AC5) ---------------------------------------------------------------


def closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def timed_requests(client: TestClient, count: int) -> list[float]:
    durations = []
    for n in range(count):
        started = time.perf_counter()
        response = client.get(f"/probe/items/{n}")
        durations.append(time.perf_counter() - started)
        assert response.status_code == 200
    return durations


def p95(durations: list[float]) -> float:
    return sorted(durations)[int(len(durations) * 0.95) - 1]


def test_an_unreachable_collector_never_fails_or_floods(capsys):
    configure_logging()
    off_app = build_test_app()
    off_app.include_router(probe_router())
    with TestClient(off_app) as client:
        off = timed_requests(client, 50)

    endpoint = f"http://127.0.0.1:{closed_port()}"
    assert telemetry.configure_telemetry(settings(endpoint), telemetry.API_SERVICE)
    try:
        on_app = build_test_app()
        on_app.include_router(probe_router())
        telemetry.instrument_app(on_app)
        with TestClient(on_app) as client:
            on = timed_requests(client, 50)
        state = telemetry.current_state()
        state.tracer_provider.force_flush(10_000)
        state.logger_provider.force_flush(10_000)
    finally:
        started = time.monotonic()
        telemetry.shutdown_telemetry()
        shutdown_seconds = time.monotonic() - started

    lines = read_log_lines(capsys)
    export_errors = [
        line
        for line in lines
        if line["logger"].startswith(("opentelemetry", "urllib3"))
    ]
    assert len(export_errors) <= 1
    assert shutdown_seconds < 10
    # Recorded, not a gate (#612 AC5): p95 off vs on, in milliseconds.
    print(  # noqa: T201
        f"\nAC5 p95 off={p95(off) * 1000:.2f}ms on={p95(on) * 1000:.2f}ms "
        f"export_error_lines={len(export_errors)}",
        file=sys.__stderr__,
    )
