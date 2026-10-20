"""Helpers for the academic catalog tests (#32; periods and closures #38;
classrooms #39).

Real logins through the real app (``auth_support.auth_client``); every
request sends its cookie (and, when unsafe, its CSRF token and ``Origin``) by
hand, so the client's own cookie jar never carries a session from one call to
the next.
"""

import asyncio
import itertools
import random
import uuid
from typing import Any

from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import event

from app.core.db import create_sessionmaker
from app.core.uow import UnitOfWork
from tests.integration.auth_support import SetCookie, cookie_header, signed_headers
from tests.integration.conftest import run_sql, run_sql_async, short_lived_engine

LEVELS = "/api/v1/education-levels"
SUBJECTS = "/api/v1/subjects"
PERIODS = "/api/v1/academic-periods"
CLOSURES = "/api/v1/closures"
CLOSURE_CHECK = "/api/v1/closures/check"
CLASSROOMS = "/api/v1/classrooms"

# Years no other test of the process uses (#38): the period and closure
# tables of a module database are shared, so each test takes its own decade.
_YEARS = itertools.count(2200 + random.randrange(0, 5000, 100), 10)  # noqa: S311


def unique_year() -> int:
    """The first year of a decade no other test of this process has used: a
    test may use this year and the next nine."""
    return next(_YEARS)


def unique_name(prefix: str = "Nivel") -> str:
    """A name no other test uses (the catalog tables are shared)."""
    return f"{prefix} {uuid.uuid4().hex[:10]}"


def level_path(level_id: Any) -> str:
    return f"{LEVELS}/{level_id}"


def subject_path(subject_id: Any) -> str:
    return f"{SUBJECTS}/{subject_id}"


def get(client: TestClient, cookie: SetCookie, path: str, **params: Any) -> Response:
    client.cookies.clear()
    return client.get(path, params=params, headers=cookie_header(cookie))


def post(client: TestClient, cookie: SetCookie, path: str, body: Any) -> Response:
    client.cookies.clear()
    return client.post(path, json=body, headers=signed_headers(cookie))


def patch(client: TestClient, cookie: SetCookie, path: str, body: Any) -> Response:
    client.cookies.clear()
    return client.patch(path, json=body, headers=signed_headers(cookie))


def create_level(
    client: TestClient, cookie: SetCookie, name: str | None = None, **body: Any
) -> dict[str, Any]:
    """``POST /education-levels`` that must succeed; returns the detail."""
    response = post(client, cookie, LEVELS, {"name": name or unique_name(), **body})
    assert response.status_code == 201, response.text
    detail: dict[str, Any] = response.json()
    return detail


def create_subject(
    client: TestClient, cookie: SetCookie, level_id: Any, name: str | None = None
) -> dict[str, Any]:
    """``POST /education-levels/{id}/subjects`` that must succeed."""
    response = post(
        client,
        cookie,
        f"{level_path(level_id)}/subjects",
        {"name": name or unique_name("Materia")},
    )
    assert response.status_code == 201, response.text
    detail: dict[str, Any] = response.json()
    return detail


def level_row(url: str, level_id: Any) -> Any:
    [row] = run_sql(
        url,
        "SELECT name, sort_order, is_active, version, seed_key"
        " FROM education_levels WHERE id = :id",
        {"id": level_id},
    )
    return row


def subject_row(url: str, subject_id: Any) -> Any:
    [row] = run_sql(
        url,
        "SELECT education_level_id, name, is_active, version"
        " FROM subjects WHERE id = :id",
        {"id": subject_id},
    )
    return row


def catalog_event_count(url: str) -> int:
    [row] = run_sql(
        url,
        "SELECT count(*) AS n FROM audit_events"
        " WHERE record_type IN ('education_levels', 'subjects')",
    )
    return int(row.n)


async def insert_level(
    dsn: str,
    name: str,
    sort_order: int = 10,
    *,
    is_active: bool = True,
    seed_key: str | None = None,
) -> str:
    """A raw ``education_levels`` row (no audit event); returns its id."""
    rows = await run_sql_async(
        dsn,
        "INSERT INTO education_levels (name, sort_order, is_active, seed_key)"
        " VALUES (:name, :sort_order, :is_active, :seed_key) RETURNING id",
        {
            "name": name,
            "sort_order": sort_order,
            "is_active": is_active,
            "seed_key": seed_key,
        },
    )
    return str(rows[0].id)


async def insert_subject(
    dsn: str, level_id: str, name: str, *, is_active: bool = True
) -> str:
    """A raw ``subjects`` row (no audit event); returns its id."""
    rows = await run_sql_async(
        dsn,
        "INSERT INTO subjects (education_level_id, name, is_active)"
        " VALUES (:level_id, :name, :is_active) RETURNING id",
        {"level_id": level_id, "name": name, "is_active": is_active},
    )
    return str(rows[0].id)


def count_statements(url: str, call: Any) -> tuple[int, Any]:
    """Runs ``call(session)`` in a Unit of Work; returns the number of SQL
    statements it sent and its result."""
    seen: list[str] = []

    async def _run() -> tuple[int, Any]:
        engine = short_lived_engine(url)

        def record(conn, cursor, statement, parameters, context, executemany) -> None:  # type: ignore[no-untyped-def]
            seen.append(statement)

        event.listen(engine.sync_engine, "before_cursor_execute", record)
        try:
            async with UnitOfWork(create_sessionmaker(engine)) as uow:
                seen.clear()
                result = await call(uow.session)
                return len(seen), result
        finally:
            await engine.dispose()

    return asyncio.run(_run())


def period_path(period_id: Any) -> str:
    return f"{PERIODS}/{period_id}"


def closure_path(closure_id: Any) -> str:
    return f"{CLOSURES}/{closure_id}"


def create_period(
    client: TestClient,
    cookie: SetCookie,
    starts_on: str,
    ends_on: str,
    period_type: str = "school_year",
    name: str | None = None,
) -> dict[str, Any]:
    """``POST /academic-periods`` that must succeed; returns the detail."""
    response = post(
        client,
        cookie,
        PERIODS,
        {
            "name": name or unique_name("Periodo"),
            "period_type": period_type,
            "starts_on": starts_on,
            "ends_on": ends_on,
        },
    )
    assert response.status_code == 201, response.text
    detail: dict[str, Any] = response.json()
    return detail


def create_closure(
    client: TestClient,
    cookie: SetCookie,
    starts_on: str,
    ends_on: str | None = None,
    closure_type: str = "holiday",
    name: str = "Cierre",
) -> dict[str, Any]:
    """``POST /closures`` that must succeed; returns the detail."""
    response = post(
        client,
        cookie,
        CLOSURES,
        {
            "name": name,
            "closure_type": closure_type,
            "starts_on": starts_on,
            "ends_on": ends_on or starts_on,
        },
    )
    assert response.status_code == 201, response.text
    detail: dict[str, Any] = response.json()
    return detail


def range_event_count(url: str) -> int:
    """Audit events of the period and closure tables."""
    [row] = run_sql(
        url,
        "SELECT count(*) AS n FROM audit_events"
        " WHERE record_type IN ('academic_periods', 'closures')",
    )
    return int(row.n)


def classroom_path(classroom_id: Any) -> str:
    return f"{CLASSROOMS}/{classroom_id}"


def window(weekday: int, starts_at: str, ends_at: str) -> dict[str, Any]:
    """One availability window as the API takes and returns it."""
    return {"weekday": weekday, "starts_at": starts_at, "ends_at": ends_at}


def create_classroom(
    client: TestClient,
    cookie: SetCookie,
    name: str | None = None,
    capacity: int = 12,
    availability: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """``POST /classrooms`` that must succeed; returns the detail."""
    body: dict[str, Any] = {"name": name or unique_name("Aula"), "capacity": capacity}
    if availability is not None:
        body["availability"] = availability
    response = post(client, cookie, CLASSROOMS, body)
    assert response.status_code == 201, response.text
    detail: dict[str, Any] = response.json()
    return detail


def classroom_event_count(url: str) -> int:
    """Audit events of the classroom and window tables."""
    [row] = run_sql(
        url,
        "SELECT count(*) AS n FROM audit_events WHERE record_type IN"
        " ('classrooms', 'classroom_availability_windows')",
    )
    return int(row.n)


def window_events(url: str, classroom_id: Any) -> list[tuple[str, dict[str, Any]]]:
    """``(action, changes)`` of the audit events of one room's windows, in
    order."""
    rows = run_sql(
        url,
        "SELECT action, changes FROM audit_events"
        " WHERE record_type = 'classroom_availability_windows'"
        " AND coalesce(changes->'classroom_id'->>'new',"
        " changes->'classroom_id'->>'old') = :id"
        " ORDER BY occurred_at, id",
        {"id": str(classroom_id)},
    )
    return [(row.action, row.changes) for row in rows]
