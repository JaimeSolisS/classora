"""Helpers of the #116 tests: the template routes, bodies, the job run, SQL
readers and a direct insert for job tests that need a template outside the
create window. The clock is frozen by each module (the center is in
``America/Mexico_City``)."""

import asyncio
import uuid
from datetime import date
from decimal import Decimal
from typing import Any, Final

from fastapi.testclient import TestClient
from httpx import Response

from app.workflows.recurring_expenses import (
    RecurringExpenseRunResult,
    run_generate_recurring_expenses,
)
from tests.integration.auth_support import SetCookie, cookie_header, signed_headers
from tests.integration.conftest import run_sql, settings_for

ROOT: Final = "/api/v1/finance"
TEMPLATES: Final = f"{ROOT}/expense-templates"
EXPENSES: Final = f"{ROOT}/expenses"
TASK: Final = "finance:generate_recurring_expenses"


def template_path(template_id: Any) -> str:
    return f"{TEMPLATES}/{template_id}"


def code(response: Response) -> tuple[int, str | None]:
    if response.status_code < 400:
        return response.status_code, None
    return response.status_code, response.json().get("code")


def field_errors(response: Response) -> list[tuple[tuple[Any, ...], str]]:
    return [(tuple(e["path"]), e["code"]) for e in response.json()["errors"]]


def category_of(url: str, key: str = "rent") -> str:
    [row] = run_sql(
        url, "SELECT id FROM expense_categories WHERE system_key = :k", {"k": key}
    )
    return str(row.id)


def new_category(url: str, name: str | None = None, *, active: bool = True) -> str:
    [row] = run_sql(
        url,
        "INSERT INTO expense_categories (name, is_active) VALUES (:n, :a) RETURNING id",
        {"n": name or f"Categoría {uuid.uuid4().hex[:8]}", "a": active},
    )
    return str(row.id)


def new_vendor(url: str, name: str | None = None, *, active: bool = True) -> str:
    [row] = run_sql(
        url,
        "INSERT INTO vendors (name, is_active) VALUES (:n, :a) RETURNING id",
        {"n": name or f"Proveedor {uuid.uuid4().hex[:8]}", "a": active},
    )
    return str(row.id)


def set_active(url: str, table: str, row_id: Any, active: bool) -> None:
    run_sql(
        url,
        f"UPDATE {table} SET is_active = :a WHERE id = :i",  # noqa: S608
        {"a": active, "i": str(row_id)},
    )


def template_body(category: Any, starts_on: date, /, **change: Any) -> dict[str, Any]:
    """A valid monthly template of 8000.00 due 5 days after each period; a
    change to ``...`` leaves the field out."""
    made: dict[str, Any] = {
        "name": f"Renta {uuid.uuid4().hex[:8]}",
        "category_id": str(category),
        "amount": "8000.00",
        "frequency": "monthly",
        "starts_on": starts_on.isoformat(),
        "due_offset_days": 5,
    }
    made.update(change)
    return {key: value for key, value in made.items() if value is not ...}


def create(
    client: TestClient,
    cookie: SetCookie,
    body: Any,
    *,
    headers: dict[str, str] | None = None,
) -> Response:
    client.cookies.clear()
    return client.post(
        TEMPLATES, json=body, headers={**signed_headers(cookie), **(headers or {})}
    )


def created(client: TestClient, cookie: SetCookie, body: Any) -> dict[str, Any]:
    response = create(client, cookie, body)
    assert response.status_code == 201, response.text
    item: dict[str, Any] = response.json()
    return item


def change(
    client: TestClient, cookie: SetCookie, template_id: Any, body: Any
) -> Response:
    client.cookies.clear()
    return client.patch(
        template_path(template_id), json=body, headers=signed_headers(cookie)
    )


def changed(
    client: TestClient, cookie: SetCookie, template_id: Any, body: Any
) -> dict[str, Any]:
    response = change(client, cookie, template_id, body)
    assert response.status_code == 200, response.text
    item: dict[str, Any] = response.json()
    return item


def read(client: TestClient, cookie: SetCookie, template_id: Any) -> Response:
    client.cookies.clear()
    return client.get(template_path(template_id), headers=cookie_header(cookie))


def listing(client: TestClient, cookie: SetCookie, **params: Any) -> Response:
    client.cookies.clear()
    return client.get(TEMPLATES, params=params, headers=cookie_header(cookie))


def listed(client: TestClient, cookie: SetCookie, **params: Any) -> list[Any]:
    response = listing(client, cookie, **params)
    assert response.status_code == 200, response.text
    return list(response.json())


def run_job(url: str) -> RecurringExpenseRunResult:
    """One run of ``finance:generate_recurring_expenses`` on ``url`` (its own
    direct engine, as the worker)."""
    return asyncio.run(run_generate_recurring_expenses(settings_for(url)))


def generated(url: str, template_id: Any) -> list[Any]:
    """The template's expenses, oldest period first."""
    return run_sql(
        url,
        "SELECT * FROM expenses WHERE expense_template_id = :t ORDER BY period_start",
        {"t": str(template_id)},
    )


def template_row(url: str, template_id: Any) -> Any:
    [row] = run_sql(
        url, "SELECT * FROM expense_templates WHERE id = :i", {"i": str(template_id)}
    )
    return row


def set_next(url: str, template_id: Any, next_period_start: date) -> None:
    """Moves ``next_period_start`` back by SQL (a simulated stale row)."""
    run_sql(
        url,
        "UPDATE expense_templates SET next_period_start = :n WHERE id = :i",
        {"n": next_period_start, "i": str(template_id)},
    )


def insert_template(url: str, category: Any, starts_on: date, **change: Any) -> str:
    """A template by SQL, for job tests whose anchor is outside the create
    window. ``next_period_start`` defaults to ``starts_on``."""
    row = {
        "name": f"Renta {uuid.uuid4().hex[:8]}",
        "category_id": str(category),
        "vendor_id": None,
        "amount": Decimal("8000.00"),
        "frequency": "monthly",
        "starts_on": starts_on,
        "ends_on": None,
        "due_offset_days": 5,
        "next_period_start": starts_on,
        "is_active": True,
        **change,
    }
    columns = ", ".join(row)
    binds = ", ".join(f":{name}" for name in row)
    [made] = run_sql(
        url,
        f"INSERT INTO expense_templates ({columns}) VALUES ({binds})"  # noqa: S608
        " RETURNING id",
        row,
    )
    return str(made.id)


def queued(url: str) -> int:
    """Queued runs of the job (``todo``)."""
    [row] = run_sql(
        url,
        "SELECT count(*) AS n FROM procrastinate_jobs"
        " WHERE task_name = :t AND status = 'todo'",
        {"t": TASK},
    )
    return int(row.n)


def clear_queue(url: str) -> None:
    run_sql(url, "DELETE FROM procrastinate_jobs WHERE task_name = :t", {"t": TASK})
