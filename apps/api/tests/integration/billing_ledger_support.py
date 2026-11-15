"""Helpers for the ledger core tests (#90 E1-E9).

The triggers, the predicate and the view exist only in a migrated database,
so the tests use ``module_seeded_url`` (migrated to head, shared by one
module's tests; each test builds its own families and rows).

- :func:`ledger_rows`: one committed row in each ledger table, on account A,
  plus a second account B with its own student, charge and payment (for the
  composite foreign keys).
- :func:`sqlstate`, :func:`fails_with`: the SQLSTATE of a refused
  statement.
- :func:`count_statements`: the statements one call sends (the
  ``before_cursor_execute`` pattern of ``catalog_support``).
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import create_sessionmaker
from app.core.money import Money
from app.core.uow import UnitOfWork
from app.modules.billing import service as billing
from app.modules.billing.models import AllocationRelease
from app.modules.billing.schemas import AdjustmentDraft
from tests.factories import billing as factories
from tests.integration.conftest import run_sql, short_lived_engine
from tests.integration.lifecycle_support import in_uow


def sqlstate(error: DBAPIError) -> str | None:
    return getattr(error.orig, "sqlstate", None)


def fails_with(url: str, sql: str, state: str | set[str], **params: Any) -> str:
    """Run ``sql``; it must fail with SQLSTATE ``state`` (or one of a set).
    Returns the message."""
    states = {state} if isinstance(state, str) else state
    with pytest.raises(DBAPIError) as raised:
        run_sql(url, sql, params)
    assert sqlstate(raised.value) in states, raised.value
    return str(raised.value.orig)


@dataclass(frozen=True)
class LedgerRows:
    user: uuid.UUID
    family: uuid.UUID
    account: uuid.UUID
    student: uuid.UUID
    charge: uuid.UUID
    adjustment: uuid.UUID
    payment: uuid.UUID
    allocation: uuid.UUID
    refund: uuid.UUID
    reversed_payment: uuid.UUID
    reversal: uuid.UUID
    release: uuid.UUID
    other_account: uuid.UUID
    other_student: uuid.UUID
    other_charge: uuid.UUID
    other_payment: uuid.UUID

    def row_of(self, table: str) -> uuid.UUID:
        return {
            "charges": self.charge,
            "charge_adjustments": self.adjustment,
            "payments": self.payment,
            "payment_reversals": self.reversal,
            "payment_allocations": self.allocation,
            "allocation_releases": self.release,
            "refunds": self.refund,
        }[table]


def ledger_rows(url: str) -> LedgerRows:
    """Account A: a 500 charge (+100 manual adjustment), a 300 payment with
    a 200 allocation and a 50 refund, a 100 payment allocated and then
    reversed (its allocation released). Account B: a charge and a payment."""

    async def _make(session: AsyncSession) -> LedgerRows:
        user = await factories.staff_user_id(session)
        family, [student] = await factories.family_with_students(session)
        posted = await factories.charge(session, student, "500.00")
        adjustment = await billing.post_adjustment(
            session,
            AdjustmentDraft(
                charge_id=posted.charge_id,
                kind="manual",
                amount=Money("100.00"),
                reason="Material extra",
            ),
        )
        paid = await factories.payment(
            session, posted.billing_account_id, "300.00", recorded_by=user
        )
        allocated = await factories.allocation(
            session, paid, posted.charge_id, "200.00"
        )
        refunded = await factories.refund(session, paid, "50.00")
        bounced = await factories.payment(
            session, posted.billing_account_id, "100.00", recorded_by=user
        )
        await factories.allocation(session, bounced, posted.charge_id, "100.00")
        reversed_ = await factories.reversal(session, bounced)
        [release] = list(
            await session.scalars(
                select(AllocationRelease.id).where(
                    AllocationRelease.payment_reversal_id == reversed_.id
                )
            )
        )
        _, [other_student] = await factories.family_with_students(session)
        other = await factories.charge(session, other_student, "80.00")
        other_paid = await factories.payment(
            session, other.billing_account_id, "80.00", recorded_by=user
        )
        return LedgerRows(
            user=user,
            family=family,
            account=posted.billing_account_id,
            student=student,
            charge=posted.charge_id,
            adjustment=adjustment.adjustment_id,
            payment=paid.id,
            allocation=allocated.id,
            refund=refunded.id,
            reversed_payment=bounced.id,
            reversal=reversed_.id,
            release=release,
            other_account=other.billing_account_id,
            other_student=other_student,
            other_charge=other.charge_id,
            other_payment=other_paid.id,
        )

    return in_uow(url, _make)


def count_statements(
    url: str, call: Callable[[AsyncSession], Awaitable[Any]]
) -> tuple[int, Any]:
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
