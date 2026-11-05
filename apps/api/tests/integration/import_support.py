"""Helpers for the import tests (#122: ``test_imports_pipeline.py``,
``test_imports_students.py``, ``test_imports_tutors.py``).

The routes run with real logins on one seeded database per module; rows are
scoped with unique names. The task runs in-process through
:func:`run_imports` with its own direct engine, as the worker does (the
module's tests never start a worker, so nothing else commits a job).
"""

import asyncio
import uuid
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from fastapi.testclient import TestClient
from httpx import Response

from app.core.authz import Principal
from app.modules.administration.imports.tasks import ImportRunResult, run_imports
from app.modules.identity.permissions import resolve_permissions
from tests.integration.auth_support import CreatedUser, SetCookie, signed_headers
from tests.integration.catalog_support import get
from tests.integration.conftest import run_sql, settings_for

IMPORTS = "/api/v1/imports"
TASK = "administration:run_imports"

STUDENT_HEADER = (
    "given_names",
    "family_names",
    "birthdate",
    "email",
    "phone",
    "school_name",
    "school_grade",
    "education_level",
    "contact_name",
    "contact_relationship",
    "contact_phone",
    "contact2_name",
    "contact2_relationship",
    "contact2_phone",
)


def unique(prefix: str = "Imp") -> str:
    """A family name no other test uses (letters only)."""
    letters = "".join(chr(ord("a") + int(c, 16) % 26) for c in uuid.uuid4().hex[:10])
    return f"{prefix}{letters}"


def job_path(job_id: Any, suffix: str = "") -> str:
    return f"{IMPORTS}/{job_id}{suffix}"


def csv_text(
    header: Sequence[str],
    rows: Iterable[Mapping[str, str]],
    *,
    delimiter: str = ",",
    bom: bool = False,
    newline: str = "\n",
) -> str:
    def cell(value: str) -> str:
        if any(ch in value for ch in (delimiter, '"', "\n", "\r")):
            return '"' + value.replace('"', '""') + '"'
        return value

    lines = [delimiter.join(header)]
    lines.extend(delimiter.join(cell(row.get(c, "")) for c in header) for row in rows)
    return ("﻿" if bom else "") + newline.join(lines) + newline


def student(family: str, given: str = "Ana", **change: str) -> dict[str, str]:
    row = {
        "given_names": given,
        "family_names": family,
        "birthdate": "2012-03-04",
        "contact_name": "María López",
        "contact_relationship": "Madre",
        "contact_phone": "55 1234 5678",
    }
    row.update(change)
    return row


def students_csv(rows: Iterable[Mapping[str, str]], **options: Any) -> str:
    return csv_text(STUDENT_HEADER, rows, **options)


def tutors_csv(rows: Iterable[Mapping[str, str]], **options: Any) -> str:
    return csv_text(("username", "display_name", "email", "phone"), rows, **options)


def post_import(
    client: TestClient,
    cookie: SetCookie,
    kind: str,
    csv: str,
    file_name: str | None = "alumnos.csv",
) -> Response:
    client.cookies.clear()
    body: dict[str, Any] = {"kind": kind, "csv": csv}
    if file_name is not None:
        body["file_name"] = file_name
    return client.post(IMPORTS, json=body, headers=signed_headers(cookie))


def post_raw(
    client: TestClient, cookie: SetCookie, content: Any, **headers: str
) -> Response:
    client.cookies.clear()
    return client.post(
        IMPORTS,
        content=content,
        headers={
            **signed_headers(cookie),
            "Content-Type": "application/json",
            **headers,
        },
    )


def action(
    client: TestClient,
    cookie: SetCookie,
    job_id: Any,
    name: str,
    version: int | None = None,
) -> Response:
    """``POST …/commit`` or ``…/discard`` with the job's current version."""
    if version is None:
        version = get(client, cookie, job_path(job_id)).json()["version"]
    client.cookies.clear()
    return client.post(
        job_path(job_id, f"/{name}"),
        json={"version": version},
        headers=signed_headers(cookie),
    )


def rows_of(client: TestClient, cookie: SetCookie, job_id: Any, **params: Any) -> Any:
    response = get(client, cookie, job_path(job_id, "/rows"), limit=100, **params)
    assert response.status_code == 200, response.text
    return response.json()


def run_job(url: str) -> ImportRunResult:
    return asyncio.run(run_imports(settings_for(url)))


def queued_runs(url: str) -> int:
    return int(
        run_sql(
            url,
            "SELECT count(*) AS n FROM procrastinate_jobs"
            " WHERE task_name = :t AND status = 'todo'",
            {"t": TASK},
        )[0].n
    )


def clear_queue(url: str) -> None:
    run_sql(url, "DELETE FROM procrastinate_jobs WHERE task_name = :t", {"t": TASK})


def job_row(url: str, job_id: Any) -> Any:
    [row] = run_sql(url, "SELECT * FROM import_jobs WHERE id = :i", {"i": str(job_id)})
    return row


def stored_rows(url: str, job_id: Any) -> list[Any]:
    return run_sql(
        url,
        'SELECT row_number, status, "values", errors, warnings, record_id'
        " FROM import_rows WHERE job_id = :i ORDER BY row_number",
        {"i": str(job_id)},
    )


def students_named(url: str, family: str) -> list[Any]:
    return run_sql(
        url,
        "SELECT * FROM students WHERE family_names = :f ORDER BY given_names",
        {"f": family},
    )


def principal_of(user: CreatedUser, *roles: str) -> Principal:
    return Principal(
        user_id=user.id,
        roles=frozenset(roles),
        permissions=resolve_permissions("active", roles, ()),
    )


def codes(issues: Iterable[Mapping[str, Any]]) -> set[tuple[Any, str]]:
    return {(issue["column"], issue["code"]) for issue in issues}
