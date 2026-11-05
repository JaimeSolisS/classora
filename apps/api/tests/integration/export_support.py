"""Helpers for the export tests (#124: ``test_exports_api.py``,
``test_exports_worker.py``, ``test_exports_datasets.py``).

The routes run with real logins on one seeded database per module. The task
runs in-process through :func:`run_exports` with its own direct engine, as the
worker does (the modules never start a worker, so nothing else builds a job).
"""

import asyncio
import csv
import io
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from fastapi.testclient import TestClient
from httpx import Response

from app.modules.reporting.tasks import ExportRunResult, run_exports
from tests.integration.auth_support import (
    CreatedUser,
    SetCookie,
    create_user,
    signed_headers,
)
from tests.integration.catalog_support import get
from tests.integration.conftest import run_sql, settings_for
from tests.integration.invitation_support import reauthenticate, sign_in

EXPORTS: Final = "/api/v1/exports"
DATASETS: Final = f"{EXPORTS}/datasets"
TASK: Final = "reporting:run_exports"
STAFF: Final = ("owner", "administrator", "academic_coordinator", "finance_staff")
FAMILY_AND_TUTOR: Final = ("tutor", "student", "guardian")


@dataclass(frozen=True)
class Actor:
    user: CreatedUser
    cookie: SetCookie


def actor(
    url: str, client: TestClient, role: str, *, reauth: bool = False, **kw: Any
) -> Actor:
    """A new user with ``role``, signed in (and reauthenticated)."""
    user = create_user(url, roles=(role,), **kw)
    cookie = sign_in(client, user.username)
    if reauth:
        reauthenticate(client, cookie)
    return Actor(user, cookie)


def export_path(job_id: Any, suffix: str = "") -> str:
    return f"{EXPORTS}/{job_id}{suffix}"


def post_export(client: TestClient, who: Actor, body: Any) -> Response:
    client.cookies.clear()
    return client.post(EXPORTS, json=body, headers=signed_headers(who.cookie))


def requested(
    client: TestClient, who: Actor, body: Mapping[str, Any]
) -> dict[str, Any]:
    response = post_export(client, who, body)
    assert response.status_code == 202, response.text
    result: dict[str, Any] = response.json()
    return result


def expenses_body(fmt: str = "csv", **change: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"dataset": "expenses", "format": fmt, "filters": {}}
    body.update(change)
    return body


def students_body(fmt: str = "csv", **change: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"dataset": "students", "format": fmt}
    body.update(change)
    return body


def sessions_body(start: str, end: str, fmt: str = "csv", **change: Any) -> dict:
    body: dict[str, Any] = {
        "dataset": "class_sessions",
        "format": fmt,
        "filters": {"from": start, "to": end},
    }
    body.update(change)
    return body


def read(client: TestClient, who: Actor, path: str, **params: Any) -> Response:
    return get(client, who.cookie, path, **params)


def download(client: TestClient, who: Actor, job_id: Any) -> Response:
    return read(client, who, export_path(job_id, "/download"))


def run_job(url: str) -> ExportRunResult:
    return asyncio.run(run_exports(settings_for(url)))


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
    [row] = run_sql(url, "SELECT * FROM export_jobs WHERE id = :i", {"i": str(job_id)})
    return row


def file_of(url: str, job_id: Any) -> bytes | None:
    rows = run_sql(
        url, "SELECT content FROM export_files WHERE job_id = :i", {"i": str(job_id)}
    )
    return bytes(rows[0].content) if rows else None


def notifications_of(url: str, user: CreatedUser, notification_type: str) -> list:
    return run_sql(
        url,
        "SELECT params FROM notifications"
        " WHERE recipient_user_id = :u AND notification_type = :t ORDER BY id",
        {"u": user.id, "t": notification_type},
    )


def audit_of(url: str, job_id: Any) -> list[Any]:
    return run_sql(
        url,
        "SELECT action, actor_user_id, changes FROM audit_events"
        " WHERE record_type = 'export_jobs' AND record_id = :i ORDER BY id",
        {"i": str(job_id)},
    )


def csv_table(data: bytes) -> list[list[str]]:
    assert data.startswith(b"\xef\xbb\xbf")
    return list(csv.reader(io.StringIO(data.decode("utf-8-sig"), newline="")))


def set_language(url: str, user: CreatedUser, language: str) -> None:
    run_sql(
        url,
        "INSERT INTO user_preferences (user_id, language) VALUES (:u, :l)"
        " ON CONFLICT (user_id) DO UPDATE SET language = EXCLUDED.language",
        {"u": user.id, "l": language},
    )


def set_roles(url: str, user: CreatedUser, *roles: str) -> None:
    """Replace the user's roles (a permission change between steps)."""
    run_sql(url, "DELETE FROM user_roles WHERE user_id = :u", {"u": user.id})
    for role in roles:
        run_sql(
            url,
            "INSERT INTO user_roles (user_id, role_id)"
            " SELECT :u, id FROM roles WHERE code = :r",
            {"u": user.id, "r": role},
        )


def unique(prefix: str = "Exp") -> str:
    """A name no other test uses (letters only)."""
    letters = "".join(chr(ord("a") + int(c, 16) % 26) for c in uuid.uuid4().hex[:10])
    return f"{prefix}{letters}"


def new_expense(
    client: TestClient, who: Actor, url: str, vendor: str, **change: Any
) -> str:
    """An expense of ``vendor`` (rent category), incurred yesterday."""
    from datetime import date, timedelta

    from tests.integration.expense_support import category_of, created, expense_body

    day = (date.today() - timedelta(days=1)).isoformat()
    body = expense_body(
        category_of(url), vendor_id=vendor, incurred_on=day, due_on=day, **change
    )
    return created(client, who, body)  # type: ignore[arg-type]


def insert_job(url: str, user: CreatedUser, dataset: str, **parameters: Any) -> str:
    """A ``queued`` job written directly (a dataset the route doesn't know)."""
    import json

    body = {
        "dataset": dataset,
        "format": "csv",
        "columns": None,
        "group_by": None,
        "filters": {},
        **parameters,
    }
    [row] = run_sql(
        url,
        "INSERT INTO export_jobs (requested_by_user_id, dataset, format,"
        " parameters, language, file_name)"
        " VALUES (:u, :d, 'csv', CAST(:p AS jsonb), 'es', :f) RETURNING id",
        {
            "u": user.id,
            "d": dataset,
            "p": json.dumps(body),
            "f": f"classora-{dataset}-20261105-1000.csv",
        },
    )
    return str(row.id)
