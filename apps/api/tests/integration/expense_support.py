"""Helpers of the #115 tests: the six expense routes with a fresh (or given)
``Idempotency-Key``, the bodies, the stored rows, the stored keys and the audit
rows. The clock is frozen at :data:`NOW` (the center's today is
:data:`TODAY`, ``America/Mexico_City``)."""

import uuid
from datetime import UTC, date, datetime
from typing import Any, Final

from fastapi.testclient import TestClient
from httpx import Response

from tests.integration.auth_support import cookie_header, signed_headers
from tests.integration.conftest import run_sql
from tests.integration.corrections_support import Staff

ROOT: Final = "/api/v1/finance"
EXPENSES: Final = f"{ROOT}/expenses"
EXPENSE_PAYMENTS: Final = f"{ROOT}/expense-payments"

NOW: Final = datetime(2026, 12, 7, 18, 0, tzinfo=UTC)
TODAY: Final = date(2026, 12, 7)
"""The center's today at :data:`NOW` (noon in Mexico City)."""
GONE: Final = "00000000-0000-7000-8000-000000000000"

CREATED: Final = (201, None)
DENIED: Final = (403, "permission_denied")
REAUTH: Final = (403, "reauth_required")
NOT_FOUND: Final = (404, "not_found")
VOID: Final = (409, "expense_void")
DUPLICATE: Final = (409, "expense_payment_duplicate")
EXCEEDS: Final = (409, "expense_payment_exceeds_open")
ADJUSTMENT_EXCEEDS: Final = (409, "expense_adjustment_exceeds_open")
ALREADY_REVERSED: Final = (409, "expense_payment_already_reversed")
INVALID: Final = (422, "validation_error")

CREATED_KEYS: Final = {"id", "status"}
RECORDED_KEYS: Final = {"id", "expense_id", "status"}
REVERSED_KEYS: Final = {"id", "expense_payment_id", "status"}

EXPENSE_TABLES: Final = (
    "expenses",
    "expense_payments",
    "expense_payment_reversals",
    "expense_adjustments",
)


def new_key() -> str:
    return f"key-{uuid.uuid4().hex}"


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


def new_vendor(url: str, name: str | None = None, *, active: bool = True) -> str:
    [row] = run_sql(
        url,
        "INSERT INTO vendors (name, is_active) VALUES (:n, :a) RETURNING id",
        {"n": name or f"Proveedor {uuid.uuid4().hex[:8]}", "a": active},
    )
    return str(row.id)


def expense_body(category: Any, /, **change: Any) -> dict[str, Any]:
    """A valid expense of 1000.00, incurred and due today; a change to
    ``...`` leaves the field out."""
    made: dict[str, Any] = {
        "category_id": str(category),
        "description": "Renta de diciembre",
        "amount": "1000.00",
        "incurred_on": TODAY.isoformat(),
        "due_on": TODAY.isoformat(),
    }
    made.update(change)
    return {key: value for key, value in made.items() if value is not ...}


def payment_body(
    amount: str = "100.00",
    reference: str | None = None,
    *,
    method: str = "transfer",
    paid_on: date = TODAY,
    note: str | None = None,
) -> dict[str, Any]:
    made: dict[str, Any] = {
        "paid_on": paid_on.isoformat(),
        "amount": amount,
        "method_code": method,
    }
    if reference is not None:
        made["reference"] = reference
    if note is not None:
        made["note"] = note
    return made


def post_keyed(
    client: TestClient, who: Staff, path: str, payload: Any, key: str | None = None
) -> Response:
    client.cookies.clear()
    headers = {**signed_headers(who.cookie), "Idempotency-Key": key or new_key()}
    return client.post(path, json=payload, headers=headers)


def create(
    client: TestClient, who: Staff, payload: dict[str, Any], *, key: str | None = None
) -> Response:
    """``POST /expenses``."""
    return post_keyed(client, who, EXPENSES, payload, key)


def created(client: TestClient, who: Staff, payload: dict[str, Any]) -> str:
    response = create(client, who, payload)
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def pay(
    client: TestClient,
    who: Staff,
    expense: Any,
    payload: dict[str, Any] | None = None,
    *,
    key: str | None = None,
) -> Response:
    """``POST /expenses/{id}/payments``."""
    return post_keyed(
        client, who, f"{EXPENSES}/{expense}/payments", payload or payment_body(), key
    )


def paid(client: TestClient, who: Staff, expense: Any, amount: str, **kw: Any) -> str:
    response = pay(client, who, expense, payment_body(amount, **kw))
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def reverse(
    client: TestClient,
    who: Staff,
    payment: Any,
    reason: str = "Monto equivocado",
    *,
    key: str | None = None,
) -> Response:
    """``POST /expense-payments/{id}/reversal``."""
    return post_keyed(
        client,
        who,
        f"{EXPENSE_PAYMENTS}/{payment}/reversal",
        {"reason": reason},
        key,
    )


def adjust(
    client: TestClient,
    who: Staff,
    expense: Any,
    amount: str,
    reason: str = "Factura corregida",
    *,
    key: str | None = None,
) -> Response:
    """``POST /expenses/{id}/adjustments``."""
    return post_keyed(
        client,
        who,
        f"{EXPENSES}/{expense}/adjustments",
        {"amount": amount, "reason": reason},
        key,
    )


def read(client: TestClient, who: Staff, expense: Any) -> Response:
    client.cookies.clear()
    return client.get(f"{EXPENSES}/{expense}", headers=cookie_header(who.cookie))


def detail(client: TestClient, who: Staff, expense: Any) -> dict[str, Any]:
    response = read(client, who, expense)
    assert response.status_code == 200, response.text
    return dict(response.json())


def listing(client: TestClient, who: Staff, **params: Any) -> Response:
    client.cookies.clear()
    return client.get(EXPENSES, params=params, headers=cookie_header(who.cookie))


def listed(client: TestClient, who: Staff, **params: Any) -> list[dict[str, Any]]:
    response = listing(client, who, limit=100, **params)
    assert response.status_code == 200, response.text
    return list(response.json()["items"])


def rows(url: str, table: str, **where: Any) -> list[Any]:
    conditions = " AND ".join(f"{column} = :{column}" for column in where) or "true"
    return run_sql(
        url,
        f"SELECT * FROM {table} WHERE {conditions}"  # noqa: S608
        " ORDER BY created_at, id",
        {column: str(value) for column, value in where.items()},
    )


def figures(url: str, expense: Any) -> tuple[Any, Any]:
    """``net`` and ``paid`` of an expense, by SQL."""
    [row] = run_sql(
        url,
        "SELECT e.amount + COALESCE((SELECT sum(a.amount) FROM expense_adjustments a"
        " WHERE a.expense_id = e.id), 0) AS net, COALESCE((SELECT sum(p.amount)"
        " FROM expense_payments p WHERE p.expense_id = e.id AND NOT EXISTS (SELECT"
        " 1 FROM expense_payment_reversals r WHERE r.expense_payment_id = p.id)),"
        " 0) AS paid FROM expenses e WHERE e.id = :id",
        {"id": str(expense)},
    )
    return row.net, row.paid


def stored_keys(url: str, user_id: Any) -> list[Any]:
    """The idempotency keys this user claimed on the expense routes."""
    return run_sql(
        url,
        "SELECT * FROM idempotency_keys WHERE user_id = :u AND endpoint LIKE"
        " '%/api/v1/finance/expense%' ORDER BY created_at",
        {"u": user_id},
    )


def audit_rows(url: str, record_ids: list[Any]) -> list[Any]:
    return run_sql(
        url,
        "SELECT * FROM audit_events WHERE record_id = ANY(:ids) ORDER BY id",
        {"ids": [uuid.UUID(str(i)) for i in record_ids]},
    )
