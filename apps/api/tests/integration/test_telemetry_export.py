"""Telemetry on the real app and database (ADR-0014, #612 AC2, AC3).

Telemetry is turned on with in-memory exporters
(``app.core.telemetry_sdk.TestExporters``) around the real app
(``build_app`` with its lifespan, so its engine is instrumented) on a
Testcontainers database. Two test-only routes are added: one runs a SQL
statement with a bound value and logs a line, and one inserts a user whose
username already exists, so an unhandled ``IntegrityError`` carries the value
in its message (asyncpg's ``DETAIL: Key (username)=(...)``).
"""

import json
import logging
from collections.abc import Iterator
from typing import Annotated, Any

import pytest
from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import telemetry
from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.logging import configure_logging
from app.core.telemetry_sdk import TestExporters
from app.core.uow import UnitOfWork
from app.main import build_app, lifespan
from tests.authz.csrf import ORIGIN_HEADER
from tests.factories.identity import persist_user
from tests.integration.auth_support import create_user
from tests.integration.conftest import settings_for
from tests.unit.conventions_app import read_log_lines

MARKER = "ZZ-PII-MARKER-7f3a"
# The username form is lower-case (users.username_format).
MARKER_USERNAME = MARKER.lower()

Sessionmaker = Annotated[async_sessionmaker[AsyncSession], Depends(get_sessionmaker)]


class NewUser(BaseModel):
    username: str


def probe_router() -> APIRouter:
    router = APIRouter()

    @router.get("/telemetry-probe/items/{item_id}")
    async def item(item_id: int, sessionmaker: Sessionmaker) -> dict[str, Any]:
        async with sessionmaker() as session:
            value = (
                await session.execute(text("SELECT :value AS v"), {"value": MARKER})
            ).scalar_one()
        logging.getLogger("tests.telemetry").info("inside the probe route")
        return {"same": value == MARKER}

    @router.post("/telemetry-probe/users")
    async def add_user(body: NewUser, sessionmaker: Sessionmaker) -> None:
        async with UnitOfWork(sessionmaker) as uow:
            await persist_user(uow.session, username=body.username)

    return router


class Exported:
    def __init__(self) -> None:
        self.spans = InMemorySpanExporter()
        self.metrics = InMemoryMetricReader()
        self.logs = InMemoryLogRecordExporter()

    def everything(self) -> str:
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
        settings_for("postgresql://unused:unused@127.0.0.1:1/unused"),
        telemetry.API_SERVICE,
        exporters=TestExporters(exported.spans, exported.metrics, exported.logs),
    )
    httpx_logger = logging.getLogger("httpx")
    level = httpx_logger.level
    # The test client's own line (the full URL it sent) is not the app's.
    httpx_logger.setLevel(logging.WARNING)
    try:
        yield exported
    finally:
        httpx_logger.setLevel(level)
        telemetry.shutdown_telemetry()


@pytest.fixture
def client(exported: Exported, seeded_url: str) -> Iterator[TestClient]:
    app = build_app(lifespan=lifespan)
    app.include_router(probe_router())
    app.dependency_overrides[get_settings] = lambda: settings_for(seeded_url)
    telemetry.instrument_app(app)
    with TestClient(app, raise_server_exceptions=False, headers=ORIGIN_HEADER) as c:
        exported.spans.clear()  # the lifespan's own statements
        yield c


def server_spans(exported: Exported) -> list[Any]:
    return [
        s
        for s in exported.spans.get_finished_spans()
        if s.kind == trace.SpanKind.SERVER
    ]


def test_a_request_yields_a_server_span_with_sql_children_and_linked_logs(
    exported, client, capsys
):
    response = client.get("/telemetry-probe/items/42")

    assert response.status_code == 200
    assert response.json() == {"same": True}
    [server] = server_spans(exported)
    assert server.name == "GET /telemetry-probe/items/{item_id}"
    assert server.attributes["http.route"] == "/telemetry-probe/items/{item_id}"
    sql = [
        s
        for s in exported.spans.get_finished_spans()
        if s.kind == trace.SpanKind.CLIENT and "db.query.text" in s.attributes
    ]
    assert sql, "no SQL span"
    for span in sql:
        assert span.context.trace_id == server.context.trace_id
        assert span.attributes["db.system.name"] == "postgresql"
    [select] = [s for s in sql if "SELECT" in s.attributes["db.query.text"]]
    # The statement keeps its placeholder; the bound value is never read.
    assert "$1" in select.attributes["db.query.text"]
    assert select.parent is not None
    # The access line and the app line carry the server span's trace id.
    trace_id = format(server.context.trace_id, "032x")
    lines = read_log_lines(capsys)
    [app_line] = [line for line in lines if line["logger"] == "tests.telemetry"]
    [access] = [line for line in lines if line["logger"] == "app.access"]
    assert app_line["trace_id"] == access["trace_id"] == trace_id
    assert MARKER not in exported.everything()


def test_planted_personal_data_reaches_no_signal(exported, client, seeded_url, capsys):
    create_user(seeded_url, username=MARKER_USERNAME)
    capsys.readouterr()
    exported.spans.clear()

    # A query string, a header and a body field (login with the marker).
    client.get(
        f"/api/v1/auth/me?q={MARKER}",
        headers={"X-Probe-Note": MARKER, "User-Agent": MARKER},
    )
    client.post(
        "/api/v1/auth/login",
        json={"username": MARKER, "password": MARKER},
        headers={"X-Probe-Note": MARKER},
    )
    # A unique violation whose driver message quotes the username.
    failed = client.post(
        f"/telemetry-probe/users?q={MARKER}", json={"username": MARKER_USERNAME}
    )

    assert failed.status_code == 500
    stdout = capsys.readouterr().out
    everything = exported.everything()
    for text_ in (everything, stdout):
        assert MARKER not in text_
        assert MARKER_USERNAME not in text_
    # The failure is still visible: its type, on the span and in the log.
    [failing] = [
        s
        for s in server_spans(exported)
        if s.attributes["http.route"] == "/telemetry-probe/users"
    ]
    events = [
        event
        for span in exported.spans.get_finished_spans()
        for event in span.events
        if event.name == "exception"
    ]
    assert "sqlalchemy.exc.IntegrityError" in {
        e.attributes["exception.type"] for e in events
    }
    assert all("exception.message" not in e.attributes for e in events)
    assert failing.status.status_code == trace.StatusCode.ERROR
    errors = [
        json.loads(line)
        for line in stdout.splitlines()
        if line.startswith("{") and json.loads(line)["level"] == "ERROR"
    ]
    assert any(
        e["exception"].splitlines()[-1] == "sqlalchemy.exc.IntegrityError"
        for e in errors
    )
    # Exported log records and metric points exist (the check above is not
    # vacuous) and carry only the scrubbed JSON line / allowed labels.
    assert exported.logs.get_finished_logs()
    assert exported.metrics.get_metrics_data() is not None
