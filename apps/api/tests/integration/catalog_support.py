"""Helpers for the academic catalog tests (#32).

Real logins through the real app (``auth_support.auth_client``); every
request sends its cookie (and, when unsafe, its CSRF token and ``Origin``) by
hand, so the client's own cookie jar never carries a session from one call to
the next.
"""

import asyncio
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
