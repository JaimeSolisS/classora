"""A tutor's finalized earnings, as rows (#113 C; ADR-0017 E12, E14, E18).

Only ``finalized`` periods are read (#113 E3): an ``open`` or ``prepared``
period is never listed and its id is "not found". Empty finalized periods are
listed, at ``0.00`` with no payslip. ``app.workflows.payroll_earnings`` applies
the record policy and turns these rows into responses; the lines come from
:func:`~app.modules.payroll.payslips.earnings_lines`, the payslip's own
projection (E4).

Every read is lock-free (#443 D3) and runs in the caller's Unit of Work.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import Money
from app.core.pagination import OffsetPage, OffsetParams
from app.modules.payroll.models import PayrollLine, PayrollPeriod, Payslip
from app.modules.payroll.payments import last_paid_on, paid_sum
from app.modules.payroll.schemas import PayFrequency

FINALIZED: Final = "finalized"


@dataclass(frozen=True)
class EarningsPeriodRow:
    """One finalized period of a tutor: its stored total, what was paid
    (payments without a reversal), its line count and its payslip, if any."""

    id: uuid.UUID
    starts_on: date
    ends_on: date
    pay_frequency: PayFrequency
    finalized_at: datetime
    total: Money
    paid_total: Money
    last_paid_on: date | None
    line_count: int
    payslip_id: uuid.UUID | None
    payslip_number: str | None


def _columns() -> tuple[Any, ...]:
    line_count = (
        select(func.count(PayrollLine.id))
        .where(PayrollLine.payroll_period_id == PayrollPeriod.id)
        .scalar_subquery()
    )
    return (
        PayrollPeriod.id,
        PayrollPeriod.starts_on,
        PayrollPeriod.ends_on,
        PayrollPeriod.pay_frequency,
        PayrollPeriod.finalized_at,
        PayrollPeriod.total_amount,
        paid_sum(PayrollPeriod.id).label("paid"),
        last_paid_on(PayrollPeriod.id).label("last_paid_on"),
        line_count.label("line_count"),
        Payslip.id.label("payslip_id"),
        Payslip.number.label("payslip_number"),
    )


def _row(row: Any) -> EarningsPeriodRow:
    return EarningsPeriodRow(
        id=row.id,
        starts_on=row.starts_on,
        ends_on=row.ends_on,
        pay_frequency=row.pay_frequency,
        finalized_at=row.finalized_at,
        total=row.total_amount,
        paid_total=Money(row.paid),
        last_paid_on=row.last_paid_on,
        line_count=row.line_count,
        payslip_id=row.payslip_id,
        payslip_number=row.payslip_number,
    )


def _finalized_of(tutor_user_id: uuid.UUID) -> tuple[Any, ...]:
    return (
        PayrollPeriod.tutor_user_id == tutor_user_id,
        PayrollPeriod.status == FINALIZED,
    )


async def list_finalized_periods(
    session: AsyncSession, tutor_user_id: uuid.UUID, page: OffsetParams
) -> OffsetPage[EarningsPeriodRow]:
    """The tutor's finalized periods, newest ``ends_on`` first. Two statements,
    whatever the page size."""
    where = _finalized_of(tutor_user_id)
    count = await session.scalar(
        select(func.count()).select_from(PayrollPeriod).where(*where)
    )
    rows = await session.execute(
        select(*_columns())
        .outerjoin(Payslip, Payslip.payroll_period_id == PayrollPeriod.id)
        .where(*where)
        .order_by(PayrollPeriod.ends_on.desc(), PayrollPeriod.id)
        .limit(page.limit)
        .offset(page.offset)
    )
    return OffsetPage[EarningsPeriodRow](
        items=[_row(row) for row in rows],
        total=count or 0,
        limit=page.limit,
        offset=page.offset,
    )


async def finalized_period(
    session: AsyncSession, tutor_user_id: uuid.UUID, period_id: uuid.UUID
) -> EarningsPeriodRow | None:
    """The tutor's finalized period ``period_id``, or ``None`` (another tutor's,
    not finalized, or unknown). One statement."""
    row = (
        await session.execute(
            select(*_columns())
            .outerjoin(Payslip, Payslip.payroll_period_id == PayrollPeriod.id)
            .where(PayrollPeriod.id == period_id, *_finalized_of(tutor_user_id))
        )
    ).first()
    return None if row is None else _row(row)


async def has_periods(session: AsyncSession, tutor_user_id: uuid.UUID) -> bool:
    """Whether the user has a payroll period of any status: someone payroll
    has paid, or will (#113 C1, the leaver who lost the tutor role). One
    statement."""
    found = await session.scalar(
        select(exists().where(PayrollPeriod.tutor_user_id == tutor_user_id))
    )
    return bool(found)
