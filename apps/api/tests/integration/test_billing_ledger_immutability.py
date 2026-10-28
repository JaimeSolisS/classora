"""Posted ledger rows never change (#90 E1; I11; data-model §5.1).

For each of the seven append-only tables, with one committed row: raw SQL
``UPDATE``, ``DELETE`` and ``TRUNCATE`` fail with SQLSTATE 42501 and the
row is unchanged; an ORM change of a loaded charge fails the same way.

These statements run as the application's database role (the local, test
and CI role is a superuser): the triggers, not privileges, refuse them.
#165 adds a least-privilege role later; the triggers stay.

``TRUNCATE`` of a table that others reference needs those tables in the same
statement, so each statement lists every ledger table with the table under
test **first**: its trigger fires first, and the message names it.

The mutable rows can change: ``billing_accounts.due_offset_days`` and
``payment_methods.is_active``; but a referenced account or method can't be
deleted (``RESTRICT``, 23001).
"""

import uuid

import pytest
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import Money
from app.modules.billing.models import APPEND_ONLY_TABLES, Charge
from tests.factories import billing as factories
from tests.integration.billing_ledger_support import (
    LedgerRows,
    fails_with,
    ledger_rows,
    sqlstate,
)
from tests.integration.conftest import run_sql
from tests.integration.lifecycle_support import in_uow

INSUFFICIENT_PRIVILEGE = "42501"
RESTRICTED = {"23001", "23503"}
"""``ON DELETE RESTRICT`` reports 23001 (``NO ACTION`` would be 23503)."""


@pytest.fixture(scope="module")
def rows(module_seeded_url: str) -> LedgerRows:
    return ledger_rows(module_seeded_url)


def _snapshot(url: str, table: str, row_id: uuid.UUID) -> str:
    [row] = run_sql(
        url,
        f"SELECT row_to_json(t)::text AS j FROM {table} t WHERE id = :id",  # noqa: S608
        {"id": row_id},
    )
    return str(row.j)


@pytest.mark.parametrize("table", APPEND_ONLY_TABLES)
def test_update_is_refused_and_the_row_is_unchanged(module_seeded_url, rows, table):
    url = module_seeded_url
    row_id = rows.row_of(table)
    before = _snapshot(url, table, row_id)

    message = fails_with(
        url,
        f"UPDATE {table} SET updated_at = now() WHERE id = :id",  # noqa: S608
        INSUFFICIENT_PRIVILEGE,
        id=row_id,
    )

    assert f"{table} is append-only" in message
    assert _snapshot(url, table, row_id) == before


@pytest.mark.parametrize("table", APPEND_ONLY_TABLES)
def test_delete_is_refused_and_the_row_is_unchanged(module_seeded_url, rows, table):
    url = module_seeded_url
    row_id = rows.row_of(table)
    before = _snapshot(url, table, row_id)

    message = fails_with(
        url,
        f"DELETE FROM {table} WHERE id = :id",  # noqa: S608
        INSUFFICIENT_PRIVILEGE,
        id=row_id,
    )

    assert f"{table} is append-only" in message
    assert _snapshot(url, table, row_id) == before


@pytest.mark.parametrize("table", APPEND_ONLY_TABLES)
def test_truncate_is_refused_by_the_tables_own_trigger(module_seeded_url, rows, table):
    url = module_seeded_url
    row_id = rows.row_of(table)
    before = _snapshot(url, table, row_id)
    # charge_discounts (#95) references charges, so it is truncated with them.
    tables = [table, *(t for t in APPEND_ONLY_TABLES if t != table), "charge_discounts"]

    message = fails_with(url, f"TRUNCATE {', '.join(tables)}", INSUFFICIENT_PRIVILEGE)

    assert f"{table} is append-only" in message
    assert _snapshot(url, table, row_id) == before


def test_an_orm_change_of_a_loaded_charge_is_refused(module_seeded_url, rows):
    url = module_seeded_url
    before = _snapshot(url, "charges", rows.charge)

    async def _change(session: AsyncSession) -> None:
        charge = await session.get(Charge, rows.charge)
        assert charge is not None
        charge.amount = Money("1.00")
        charge.list_amount = Money("1.00")
        await session.flush()

    with pytest.raises(DBAPIError) as raised:
        in_uow(url, _change)

    assert sqlstate(raised.value) == INSUFFICIENT_PRIVILEGE
    assert _snapshot(url, "charges", rows.charge) == before


def test_the_account_offset_can_change(module_seeded_url, rows):
    url = module_seeded_url
    run_sql(
        url,
        "UPDATE billing_accounts SET due_offset_days = 15 WHERE id = :id",
        {"id": rows.account},
    )
    [row] = run_sql(
        url,
        "SELECT due_offset_days FROM billing_accounts WHERE id = :id",
        {"id": rows.account},
    )
    assert row.due_offset_days == 15
    run_sql(
        url,
        "UPDATE billing_accounts SET due_offset_days = 0 WHERE id = :id",
        {"id": rows.account},
    )


def test_a_referenced_account_cannot_be_deleted(module_seeded_url, rows):
    fails_with(
        module_seeded_url,
        "DELETE FROM billing_accounts WHERE id = :id",
        RESTRICTED,
        id=rows.account,
    )


def test_a_method_can_be_deactivated_but_a_used_one_not_deleted(
    module_seeded_url, rows
):
    url = module_seeded_url
    code = f"test_{uuid.uuid4().hex[:8]}"
    run_sql(url, "INSERT INTO payment_methods (code) VALUES (:c)", {"c": code})
    run_sql(
        url,
        "UPDATE payment_methods SET is_active = false WHERE code = :c",
        {"c": code},
    )
    [row] = run_sql(
        url, "SELECT is_active FROM payment_methods WHERE code = :c", {"c": code}
    )
    assert row.is_active is False

    async def _pay(session: AsyncSession) -> None:
        await factories.payment(
            session, rows.account, "10.00", recorded_by=rows.user, method=code
        )

    in_uow(url, _pay)

    fails_with(
        url,
        "DELETE FROM payment_methods WHERE code = :c",
        RESTRICTED,
        c=code,
    )
