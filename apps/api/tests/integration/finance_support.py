"""Shared helpers for the finance tests (#114, #115): the routes, a reset to
the migration's seed, and readers of the stored rows and their audit events."""

import uuid
from typing import Any, Final

from httpx import Response

from tests.integration.conftest import run_sql

ROOT: Final = "/api/v1/finance"
CATEGORIES: Final = f"{ROOT}/expense-categories"
VENDORS: Final = f"{ROOT}/vendors"

SEED: Final = (
    ("Renta", "rent"),
    ("Servicios", "utilities"),
    ("Insumos", "supplies"),
    ("Equipo", "equipment"),
    ("Mantenimiento", "maintenance"),
)
"""The five categories migration ``0083`` inserts."""


EXPENSE_TABLES: Final = (
    "expense_adjustments",
    "expense_payment_reversals",
    "expense_payments",
    "expenses",
)
"""The append-only expense tables (#115), children first."""


def clear_expenses(url: str) -> None:
    """No expense, payment, reversal or adjustment. The tables are append-only
    and reference each other, so every trigger of each is off meanwhile, the
    foreign keys' included (a test database runs as a superuser), and back on
    in a ``finally`` (the ``payroll_period_support.clear_adjustments``
    pattern)."""
    for table in EXPENSE_TABLES:
        run_sql(url, f"ALTER TABLE {table} DISABLE TRIGGER ALL")
    try:
        for table in EXPENSE_TABLES:
            run_sql(url, f"DELETE FROM {table}")  # noqa: S608
    finally:
        for table in EXPENSE_TABLES:
            run_sql(url, f"ALTER TABLE {table} ENABLE TRIGGER ALL")


def reset(url: str) -> None:
    """Back to the migration's state: no expense (:func:`clear_expenses`), the
    five seeded categories, no vendor. ``DELETE``, because the tables refuse
    ``TRUNCATE``."""
    clear_expenses(url)
    run_sql(url, "DELETE FROM vendors")
    run_sql(url, "DELETE FROM expense_categories")
    run_sql(
        url,
        "INSERT INTO expense_categories (name, system_key) VALUES "  # noqa: S608
        + ", ".join(f"('{name}', '{key}')" for name, key in SEED),
    )


def unique(prefix: str) -> str:
    return f"{prefix} {uuid.uuid4().hex[:10]}"


def seeded_id(url: str, key: str) -> uuid.UUID:
    [row] = run_sql(
        url, "SELECT id FROM expense_categories WHERE system_key = :k", {"k": key}
    )
    return uuid.UUID(str(row.id))


def stored(url: str, table: str, row_id: Any) -> Any:
    [row] = run_sql(
        url,
        f"SELECT * FROM {table} WHERE id = :id",  # noqa: S608
        {"id": str(row_id)},
    )
    return row


def audit_events(url: str, table: str, row_id: Any) -> list[Any]:
    return run_sql(
        url,
        "SELECT action, changes FROM audit_events"
        " WHERE record_type = :t AND record_id = :id ORDER BY occurred_at, id",
        {"t": table, "id": str(row_id)},
    )


def errors_of(response: Response) -> list[tuple[tuple[Any, ...], str]]:
    return [(tuple(e["path"]), e["code"]) for e in response.json()["errors"]]
