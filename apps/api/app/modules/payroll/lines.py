"""Reading and writing ``payroll_lines`` (#107 F, G; data-model §11.6, §15).

**Replacing.** :func:`lock_open_period` takes the period row ``FOR UPDATE``,
re-read, so a run that waited sees a status that changed meanwhile, and refuses
anything but ``open`` (``409 payroll_period_not_open``). :func:`replace_lines`
deletes only :data:`REBUILT_KINDS` and inserts the drafts: #108 and #109 extend
the tuple, and #111's ``adjustment`` lines survive (#107 E7). Line ids change
on every run, which is why nothing may reference a line of an open period.

**Deleting a version a line uses (#107 E8).** The period locks come first, then
the version, as in a run. :func:`lock_open_periods_of_tutor` and
:func:`lock_open_periods_of_pay_rule` take the rows oldest first; a period
that isn't ``open`` keeps its line, and the foreign key refuses the delete of
the version (:data:`FOREIGN_KEY_REFUSALS`).

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from collections.abc import Sequence
from typing import Final

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import RecordNotFoundError
from app.core.money import Money, MoneyRangeError
from app.modules.payroll.calculation import LineDraft
from app.modules.payroll.errors import (
    PayrollAmountOutOfRangeError,
    PayrollPeriodNotOpenError,
)
from app.modules.payroll.models import SESSION_LINE_KINDS, PayrollLine, PayrollPeriod
from app.modules.payroll.schemas import (
    PayrollLineItem,
    PayrollPeriodLines,
    PayrollPeriodSummary,
    PayrollRecalculation,
)

REBUILT_KINDS: Final = SESSION_LINE_KINDS
"""The kinds ``recalculate`` replaces (#107 E7)."""

OPEN: Final = "open"
FOREIGN_KEY_REFUSALS: Final = frozenset({"23001", "23503"})
"""``restrict_violation``, the value on this PostgreSQL, and
``foreign_key_violation``."""


def _summary(period: PayrollPeriod) -> PayrollPeriodSummary:
    return PayrollPeriodSummary.model_validate(period, from_attributes=True)


async def lock_open_period(
    session: AsyncSession, period_id: uuid.UUID
) -> PayrollPeriodSummary:
    """The period ``FOR UPDATE``, re-read: ``404`` → ``409
    payroll_period_not_open`` with ``details.status``."""
    period = await session.scalar(
        select(PayrollPeriod)
        .where(PayrollPeriod.id == period_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if period is None:
        raise RecordNotFoundError()
    if period.status != OPEN:
        raise PayrollPeriodNotOpenError(period.status)
    return _summary(period)


async def replace_lines(
    session: AsyncSession,
    period: PayrollPeriodSummary,
    drafts: Sequence[LineDraft],
) -> PayrollRecalculation:
    """Replace the period's :data:`REBUILT_KINDS` lines with ``drafts``, under
    the period lock. A total beyond the money range writes nothing. Two
    statements, one without drafts."""
    try:
        total = Money.total(draft.amount for draft in drafts)
    except MoneyRangeError:
        raise PayrollAmountOutOfRangeError() from None
    await session.execute(
        delete(PayrollLine).where(
            PayrollLine.payroll_period_id == period.id,
            PayrollLine.kind.in_(REBUILT_KINDS),
        )
    )
    if drafts:
        await session.execute(
            insert(PayrollLine),
            [
                {
                    "payroll_period_id": period.id,
                    "tutor_user_id": period.tutor_user_id,
                    "kind": draft.kind,
                    "amount": draft.amount,
                    "class_session_id": draft.class_session_id,
                    "approval_id": draft.approval_id,
                    "service_date": draft.service_date,
                    "class_kind": draft.class_kind,
                    "minutes": draft.minutes,
                    "rate_model": draft.rate_model,
                    "rate": draft.rate,
                    "compensation_rate_id": draft.compensation_rate_id,
                    "session_pay_rule_id": draft.session_pay_rule_id,
                }
                for draft in drafts
            ],
        )
    return PayrollRecalculation(line_count=len(drafts), total=total)


async def period_lines(
    session: AsyncSession, period_id: uuid.UUID
) -> PayrollPeriodLines:
    """A period's lines by service date, then session, and their total
    (``404`` for an unknown id). Two statements, no lock."""
    period = await session.get(PayrollPeriod, period_id)
    if period is None:
        raise RecordNotFoundError()
    lines = list(
        await session.scalars(
            select(PayrollLine)
            .where(PayrollLine.payroll_period_id == period_id)
            .order_by(PayrollLine.service_date, PayrollLine.class_session_id)
        )
    )
    return PayrollPeriodLines(
        period=_summary(period),
        lines=[
            PayrollLineItem.model_validate(line, from_attributes=True) for line in lines
        ],
        total=Money.total(line.amount for line in lines),
    )


async def lock_open_periods_of_tutor(
    session: AsyncSession, tutor_user_id: uuid.UUID
) -> list[uuid.UUID]:
    """The tutor's ``open`` periods ``FOR UPDATE``, oldest first."""
    return list(
        await session.scalars(
            select(PayrollPeriod.id)
            .where(
                PayrollPeriod.tutor_user_id == tutor_user_id,
                PayrollPeriod.status == OPEN,
            )
            .order_by(PayrollPeriod.starts_on)
            .with_for_update()
        )
    )


async def lock_open_periods_of_pay_rule(
    session: AsyncSession, rule_id: uuid.UUID
) -> list[uuid.UUID]:
    """The ``open`` periods holding a line of this rule ``FOR UPDATE``, oldest
    first, whoever's they are."""
    return list(
        await session.scalars(
            select(PayrollPeriod.id)
            .where(
                PayrollPeriod.status == OPEN,
                PayrollPeriod.id.in_(
                    select(PayrollLine.payroll_period_id).where(
                        PayrollLine.session_pay_rule_id == rule_id
                    )
                ),
            )
            .order_by(PayrollPeriod.starts_on, PayrollPeriod.id)
            .with_for_update()
        )
    )


async def delete_lines_of_rate(
    session: AsyncSession, rate_id: uuid.UUID, period_ids: Sequence[uuid.UUID]
) -> None:
    """Delete the lines of this rate version in these locked periods."""
    if period_ids:
        await session.execute(
            delete(PayrollLine).where(
                PayrollLine.compensation_rate_id == rate_id,
                PayrollLine.payroll_period_id.in_(period_ids),
            )
        )


async def delete_lines_of_pay_rule(
    session: AsyncSession, rule_id: uuid.UUID, period_ids: Sequence[uuid.UUID]
) -> None:
    """Delete the lines of this rule version in these locked periods."""
    if period_ids:
        await session.execute(
            delete(PayrollLine).where(
                PayrollLine.session_pay_rule_id == rule_id,
                PayrollLine.payroll_period_id.in_(period_ids),
            )
        )
