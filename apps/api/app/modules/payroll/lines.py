"""Reading and writing ``payroll_lines`` (#107 F, G; data-model §11.6, §15).

**Replacing.** :func:`lock_open_period` takes the period row ``FOR UPDATE``,
re-read, so a run that waited sees a status that changed meanwhile, and refuses
anything but ``open`` (``409 payroll_period_not_open``). :func:`replace_lines`
deletes only :data:`REBUILT_KINDS` and inserts the drafts, so #111's
``adjustment`` lines survive (#107 E7). What it reports is all the period's
lines, survivors included (#109 E2): one period has one total, the one ``GET``
shows and #110 stores. Line ids change on every run, which is why nothing may
reference a line of an open period.

**Deleting a version a line uses (#107 E8).** The period locks come first, then
the version, as in a run. :func:`lock_open_periods_of_tutor` and
:func:`lock_open_periods_of_pay_rule` take the rows oldest first; a period
that isn't ``open`` keeps its line, and the foreign key refuses the delete of
the version (:data:`FOREIGN_KEY_REFUSALS`). A terms version works the same way
(#108 F), and so does a pay item, over the not-finalized periods
(:func:`lock_unfinalized_periods_of_tutor`), so it can't race a finalize either
(#109 F6, F7).

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from collections.abc import Collection, Sequence
from datetime import date
from typing import Any, Final

from sqlalchemy import ColumnElement, Row, delete, exists, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import RecordNotFoundError
from app.core.money import Money, MoneyRangeError
from app.modules.payroll.calculation import FinalizedCourseLine, LineDraft
from app.modules.payroll.errors import (
    PayrollAmountOutOfRangeError,
    PayrollPeriodNotOpenError,
)
from app.modules.payroll.models import (
    PAY_ITEM,
    SALARY,
    SESSION_LINE_KINDS,
    PayItem,
    PayItemType,
    PayrollLine,
    PayrollPeriod,
)
from app.modules.payroll.schemas import (
    PayrollLineItem,
    PayrollPeriodLines,
    PayrollPeriodSummary,
    PayrollRecalculation,
)

REBUILT_KINDS: Final = (*SESSION_LINE_KINDS, SALARY, PAY_ITEM)
"""The kinds ``recalculate`` replaces (#107 E7, #108, #109)."""

OPEN: Final = "open"
FINALIZED: Final = "finalized"
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
    the period lock, and describe all its lines after that. A total beyond the
    money range writes nothing. Three statements, two without drafts."""
    survivors = list(
        await session.scalars(
            select(PayrollLine.amount).where(
                PayrollLine.payroll_period_id == period.id,
                PayrollLine.kind.not_in(REBUILT_KINDS),
            )
        )
    )
    try:
        total = Money.total([*(draft.amount for draft in drafts), *survivors])
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
            # render_nulls: one INSERT, whichever columns each kind leaves NULL.
            insert(PayrollLine).execution_options(render_nulls=True),
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
                    "cumulative_sessions": draft.cumulative_sessions,
                    "cohort_sessions": draft.cohort_sessions,
                    "tutor_pay_terms_id": draft.tutor_pay_terms_id,
                    "salary_amount": draft.salary_amount,
                    "salary_starts_on": draft.salary_starts_on,
                    "salary_ends_on": draft.salary_ends_on,
                    "proration_numerator": draft.proration_numerator,
                    "proration_denominator": draft.proration_denominator,
                    "proration_basis": draft.proration_basis,
                    "pay_item_id": draft.pay_item_id,
                }
                for draft in drafts
            ],
        )
    return PayrollRecalculation(line_count=len(drafts) + len(survivors), total=total)


async def period_lines(
    session: AsyncSession, period_id: uuid.UUID
) -> PayrollPeriodLines:
    """A period's lines by service date, then session, then piece, then item:
    salary after the session lines, items last (never by ``id``, which changes
    on every run). With their total (``404`` for an unknown id). A delete can
    lift lines already written beyond the money range, because signed lines can
    offset each other: that is ``409 payroll_amount_out_of_range``, as a run says,
    and the item routes are the way out (#109 QA D1). Two statements, no lock."""
    period = await session.get(PayrollPeriod, period_id)
    if period is None:
        raise RecordNotFoundError()
    lines: Sequence[Row[Any]] = (
        await session.execute(
            select(
                PayrollLine.__table__,
                PayItemType.name.label("pay_item_type_name"),
                PayItem.description.label("pay_item_description"),
            )
            .outerjoin(PayItem, PayItem.id == PayrollLine.pay_item_id)
            .outerjoin(PayItemType, PayItemType.id == PayItem.pay_item_type_id)
            .where(PayrollLine.payroll_period_id == period_id)
            .order_by(
                PayrollLine.service_date.asc().nulls_last(),
                PayrollLine.class_session_id,
                PayrollLine.salary_starts_on,
                PayrollLine.pay_item_id,
            )
        )
    ).all()
    try:
        total = Money.total(line.amount for line in lines)
    except MoneyRangeError:
        raise PayrollAmountOutOfRangeError() from None
    return PayrollPeriodLines(
        period=_summary(period),
        lines=[
            PayrollLineItem.model_validate(line, from_attributes=True) for line in lines
        ],
        total=total,
    )


async def finalized_period_dates(
    session: AsyncSession, tutor_user_id: uuid.UUID
) -> list[tuple[date, date]]:
    """``(starts_on, ends_on)`` of the tutor's ``finalized`` periods. One
    statement, no lock (#442 C2)."""
    rows = await session.execute(
        select(PayrollPeriod.starts_on, PayrollPeriod.ends_on).where(
            PayrollPeriod.tutor_user_id == tutor_user_id,
            PayrollPeriod.status == FINALIZED,
        )
    )
    return [(row.starts_on, row.ends_on) for row in rows]


async def finalized_course_lines(
    session: AsyncSession,
    tutor_user_id: uuid.UUID,
    class_session_ids: Collection[uuid.UUID],
) -> list[FinalizedCourseLine]:
    """The tutor's per-course lines of ``finalized`` periods on these sessions
    (#442 A3). One statement, none for no ids, no lock."""
    if not class_session_ids:
        return []
    rows = await session.execute(
        select(
            PayrollLine.class_session_id,
            PayrollLine.cumulative_sessions,
            PayrollLine.cohort_sessions,
            PayrollLine.rate,
        )
        .join(PayrollPeriod, PayrollPeriod.id == PayrollLine.payroll_period_id)
        .where(
            PayrollPeriod.status == FINALIZED,
            PayrollLine.tutor_user_id == tutor_user_id,
            PayrollLine.class_session_id.in_(set(class_session_ids)),
            PayrollLine.cumulative_sessions.is_not(None),
        )
    )
    return [
        FinalizedCourseLine(
            class_session_id=row.class_session_id,
            cumulative_sessions=row.cumulative_sessions,
            cohort_sessions=row.cohort_sessions,
            rate=row.rate,
        )
        for row in rows
    ]


def has_no_session_line(class_session_id: ColumnElement[Any]) -> ColumnElement[bool]:
    """``NOT EXISTS`` a session-sourced line of this session, **not executed**
    (#443 C4), on the partial unique index of those lines, in a period of any
    status. Only session kinds: an adjustment doesn't pay the session itself."""
    return ~exists().where(
        PayrollLine.class_session_id == class_session_id,
        PayrollLine.kind.in_(SESSION_LINE_KINDS),
    )


async def first_period_starts(
    session: AsyncSession, tutor_user_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, date]:
    """The start of each tutor's first period, whatever its status; a tutor
    with no periods is left out (#443 A3). One statement, none for no ids."""
    if not tutor_user_ids:
        return {}
    rows = await session.execute(
        select(PayrollPeriod.tutor_user_id, func.min(PayrollPeriod.starts_on))
        .where(PayrollPeriod.tutor_user_id.in_(set(tutor_user_ids)))
        .group_by(PayrollPeriod.tutor_user_id)
    )
    return {row[0]: row[1] for row in rows}


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


async def lock_unfinalized_periods_of_tutor(
    session: AsyncSession, tutor_user_id: uuid.UUID
) -> list[uuid.UUID]:
    """The tutor's not-finalized periods ``FOR UPDATE``, oldest first, and the
    ids of those that are ``open`` (#109 F6, F7)."""
    rows = await session.execute(
        select(PayrollPeriod.id, PayrollPeriod.status)
        .where(
            PayrollPeriod.tutor_user_id == tutor_user_id,
            PayrollPeriod.status != FINALIZED,
        )
        .order_by(PayrollPeriod.starts_on)
        .with_for_update()
    )
    return [row.id for row in rows if row.status == OPEN]


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


async def delete_lines_of_terms(
    session: AsyncSession, terms_id: uuid.UUID, period_ids: Sequence[uuid.UUID]
) -> None:
    """Delete the lines of this terms version in these locked periods."""
    if period_ids:
        await session.execute(
            delete(PayrollLine).where(
                PayrollLine.tutor_pay_terms_id == terms_id,
                PayrollLine.payroll_period_id.in_(period_ids),
            )
        )


async def delete_lines_of_pay_item(
    session: AsyncSession, item_id: uuid.UUID, period_ids: Sequence[uuid.UUID]
) -> None:
    """Delete the lines of this item in these locked periods."""
    if period_ids:
        await session.execute(
            delete(PayrollLine).where(
                PayrollLine.pay_item_id == item_id,
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
