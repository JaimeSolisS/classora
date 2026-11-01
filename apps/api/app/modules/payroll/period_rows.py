"""Reading and opening ``payroll_periods`` rows (#106 D, E10; data-model §15).

**The lock.** :data:`PAYROLL_PERIODS_LOCK_KEY` is one transaction-scoped
advisory lock, taken first by each per-tutor unit of ``payroll:open_periods``
and by the terms insert and delete (:mod:`~app.modules.payroll.rates`). The
terms refusal reads periods and the job reads terms, and a first write has no
period row to lock, so neither side can miss the other. It comes before
``payroll_periods FOR UPDATE``, which stays the last business lock of payroll.
A terms write and a job unit are both short, and terms writes are rare.

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from datetime import date
from typing import Final

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.payroll.models import (
    PERIOD_START_KEY,
    PayrollPeriod,
    TutorPayTerms,
)
from app.modules.payroll.periods import PeriodSpan, TermsVersion, next_period
from app.modules.payroll.settings import biweekly_anchor, payroll_starts_on

PAYROLL_PERIODS_LOCK_KEY: Final = int.from_bytes(b"clsPyPrd", "big")
"""The ``pg_advisory_xact_lock`` key of terms writes and the job's units (the
bytes ``b"clsPyPrd"``). It ends with the transaction, so it works through
PgBouncer in transaction mode."""


async def lock_payroll_periods(session: AsyncSession) -> None:
    """Take the advisory lock (re-entrant within a transaction)."""
    await session.execute(select(func.pg_advisory_xact_lock(PAYROLL_PERIODS_LOCK_KEY)))


async def terms_versions(
    session: AsyncSession, tutor_user_id: uuid.UUID
) -> list[TermsVersion]:
    """The tutor's terms versions, oldest first."""
    rows = await session.execute(
        select(TutorPayTerms.effective_from, TutorPayTerms.pay_frequency)
        .where(TutorPayTerms.tutor_user_id == tutor_user_id)
        .order_by(TutorPayTerms.effective_from)
    )
    return [TermsVersion(row.effective_from, row.pay_frequency) for row in rows]


async def generated_periods(
    session: AsyncSession, tutor_user_id: uuid.UUID
) -> list[PeriodSpan]:
    """The tutor's periods, oldest first."""
    rows = await session.execute(
        select(
            PayrollPeriod.starts_on,
            PayrollPeriod.ends_on,
            PayrollPeriod.pay_frequency,
            PayrollPeriod.is_first_period,
        )
        .where(PayrollPeriod.tutor_user_id == tutor_user_id)
        .order_by(PayrollPeriod.starts_on)
    )
    return [
        PeriodSpan(row.starts_on, row.ends_on, row.pay_frequency, row.is_first_period)
        for row in rows
    ]


async def tutors_with_terms(session: AsyncSession) -> list[uuid.UUID]:
    """Every tutor with a terms row, whatever their role or state: periods
    follow terms only (#106 E8)."""
    return list(
        await session.scalars(
            select(TutorPayTerms.tutor_user_id)
            .distinct()
            .order_by(TutorPayTerms.tutor_user_id)
        )
    )


async def open_due_periods(
    session: AsyncSession, tutor_user_id: uuid.UUID, *, today: date
) -> int:
    """Open every period of this tutor due by ``today``, oldest first, and
    return how many were inserted. The order is the advisory lock →
    ``payroll_settings FOR SHARE`` → the terms → the last period → the inserts,
    so a rerun, a retry and a concurrent run all leave the same rows."""
    await lock_payroll_periods(session)
    floor = await payroll_starts_on(session, lock=True)
    if floor is None:
        return 0
    anchor = await biweekly_anchor(session)
    terms = await terms_versions(session, tutor_user_id)
    last = await _last_ends_on(session, tutor_user_id)
    opened = 0
    while True:
        span = next_period(
            previous_ends_on=last,
            terms=terms,
            floor=floor,
            anchor=anchor,
            today=today,
        )
        if span is None:
            return opened
        inserted = await session.execute(
            insert(PayrollPeriod)
            .values(
                tutor_user_id=tutor_user_id,
                starts_on=span.starts_on,
                ends_on=span.ends_on,
                pay_frequency=span.pay_frequency,
                is_first_period=span.is_first_period,
            )
            .on_conflict_do_nothing(constraint=PERIOD_START_KEY)
            .returning(PayrollPeriod.id)
        )
        opened += 0 if inserted.first() is None else 1
        latest = await _last_ends_on(session, tutor_user_id)
        if latest == last:  # pragma: no cover - the lock leaves nothing to race
            return opened
        last = latest


async def _last_ends_on(session: AsyncSession, tutor_user_id: uuid.UUID) -> date | None:
    return await session.scalar(
        select(func.max(PayrollPeriod.ends_on)).where(
            PayrollPeriod.tutor_user_id == tutor_user_id
        )
    )
