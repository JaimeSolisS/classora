"""A period's review: its reads and status moves (#110; ADR-0017 E10, E12;
data-model §11.4, §13, §15).

:mod:`app.workflows.payroll_workflow` decides, in the caller's Unit of Work and
under the period lock (:func:`~app.modules.payroll.lines.lock_period`); this
module only reads and writes ``payroll_periods``. :func:`mark_prepared`,
:func:`mark_returned` and :func:`mark_finalized` write the row the lock already
loaded, so each is one ``UPDATE``. The database keeps the fields consistent with
the status (``prepared_fields``, ``finalized_fields``, ``returned_fields``) and
a finalized row as it is (the guard).

**Readers take no lock (#443 D3):** :func:`period_row`, :func:`list_periods` and
:func:`period_rows`, so a page never waits for a finalize, and never delays one.

:func:`earliest_unfinalized_before` needs none either: a period never leaves
``finalized``, so a stale read can only refuse. Two finalizes of one tutor can't
both pass in the wrong order (#110 E2).

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from collections.abc import Collection, Sequence
from dataclasses import dataclass, fields
from datetime import datetime
from decimal import Decimal
from typing import Any, Final

from sqlalchemy import Numeric, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import RecordNotFoundError
from app.core.money import Money, MoneyRangeError
from app.core.pagination import OffsetPage, OffsetParams
from app.modules.payroll.calculation import LineDraft
from app.modules.payroll.errors import PayrollAmountOutOfRangeError
from app.modules.payroll.lines import FINALIZED, OPEN, REBUILT_KINDS
from app.modules.payroll.models import PayrollLine, PayrollPeriod
from app.modules.payroll.payments import PAID, PARTIALLY_PAID, paid_state
from app.modules.payroll.payments import paid_sum as paid_sum_of
from app.modules.payroll.schemas import PaidState, PayrollPeriodFacts, PayrollPeriodRow

PREPARED: Final = "prepared"

_DRAFT_COLUMNS: Final = [
    getattr(PayrollLine, field.name) for field in fields(LineDraft)
]


def _facts(period: PayrollPeriod) -> PayrollPeriodFacts:
    return PayrollPeriodFacts.model_validate(period, from_attributes=True)


async def _locked_row(session: AsyncSession, period_id: uuid.UUID) -> PayrollPeriod:
    """The row :func:`~app.modules.payroll.lines.lock_period` loaded, from the
    session, with no statement."""
    period = await session.get(PayrollPeriod, period_id)
    if period is None:  # pragma: no cover - the caller holds its lock
        raise RecordNotFoundError()
    return period


async def mark_prepared(
    session: AsyncSession, period_id: uuid.UUID, *, by: uuid.UUID, at: datetime
) -> PayrollPeriodFacts:
    """``open`` → ``prepared``, clearing what a return left."""
    period = await _locked_row(session, period_id)
    period.status = PREPARED
    period.prepared_at = at
    period.prepared_by_user_id = by
    period.returned_at = None
    period.returned_by_user_id = None
    period.return_reason = None
    await session.flush()
    return _facts(period)


async def mark_returned(
    session: AsyncSession,
    period_id: uuid.UUID,
    *,
    by: uuid.UUID,
    at: datetime,
    reason: str,
) -> PayrollPeriodFacts:
    """``prepared`` → ``open``, with who returned it and why. Lines stay."""
    period = await _locked_row(session, period_id)
    period.status = OPEN
    period.prepared_at = None
    period.prepared_by_user_id = None
    period.returned_at = at
    period.returned_by_user_id = by
    period.return_reason = reason
    await session.flush()
    return _facts(period)


async def mark_finalized(
    session: AsyncSession,
    period_id: uuid.UUID,
    *,
    by: uuid.UUID,
    at: datetime,
    total: Money,
) -> PayrollPeriodFacts:
    """``prepared`` → ``finalized``. Nothing writes this row again."""
    period = await _locked_row(session, period_id)
    period.status = FINALIZED
    period.total_amount = total
    period.finalized_at = at
    period.finalized_by_user_id = by
    await session.flush()
    return _facts(period)


async def period_row(session: AsyncSession, period_id: uuid.UUID) -> PayrollPeriodFacts:
    """The period, or ``404``. One statement, no lock."""
    period = await session.scalar(
        select(PayrollPeriod)
        .where(PayrollPeriod.id == period_id)
        .execution_options(populate_existing=True)
    )
    if period is None:
        raise RecordNotFoundError()
    return _facts(period)


async def period_rows(
    session: AsyncSession, period_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, PayrollPeriodFacts]:
    """These periods by id; an unknown one is left out. One statement, none for
    no ids, no lock."""
    if not period_ids:
        return {}
    periods = await session.scalars(
        select(PayrollPeriod)
        .where(PayrollPeriod.id.in_(set(period_ids)))
        .execution_options(populate_existing=True)
    )
    return {period.id: _facts(period) for period in periods}


@dataclass(frozen=True)
class EarlierPeriod:
    """The tutor's earliest period before a given one that isn't finalized."""

    id: uuid.UUID
    status: str


async def earliest_unfinalized_before(
    session: AsyncSession, period: PayrollPeriodFacts
) -> EarlierPeriod | None:
    """The oldest not-finalized period of the tutor before ``period``, empty
    ones included (#110 E2). One statement, no lock."""
    row = (
        await session.execute(
            select(PayrollPeriod.id, PayrollPeriod.status)
            .where(
                PayrollPeriod.tutor_user_id == period.tutor_user_id,
                PayrollPeriod.starts_on < period.starts_on,
                PayrollPeriod.status != FINALIZED,
            )
            .order_by(PayrollPeriod.starts_on)
            .limit(1)
        )
    ).first()
    return None if row is None else EarlierPeriod(row.id, row.status)


async def stored_lines(session: AsyncSession, period_id: uuid.UUID) -> list[LineDraft]:
    """All the period's lines as drafts (#110 D3), in no order. Under the period
    lock of a ``prepared`` period they can't change. One statement."""
    rows = await session.execute(
        select(*_DRAFT_COLUMNS).where(PayrollLine.payroll_period_id == period_id)
    )
    return [LineDraft(**row._mapping) for row in rows]


def rebuilt_only(lines: Sequence[LineDraft]) -> list[LineDraft]:
    """The kinds a run rebuilds: drift compares these, and what survives a run
    (#111's ``adjustment``) stays in the total only (#110 D3)."""
    return [line for line in lines if line.kind in REBUILT_KINDS]


def lines_total(lines: Sequence[LineDraft]) -> Money:
    """The total of **all** these lines, the figure ``GET …/lines`` shows (#110
    D4). Beyond the money range: ``409 payroll_amount_out_of_range``."""
    try:
        return Money.total(line.amount for line in lines)
    except MoneyRangeError:
        raise PayrollAmountOutOfRangeError() from None


def _row_total(value: Decimal | None) -> Money | None:
    """A sum beyond the money range is ``None``: one bad period can't take the
    list down (#110 F2)."""
    try:
        return Money.zero() if value is None else Money(value)
    except MoneyRangeError:
        return None


def _paid_state_condition(state: PaidState, paid: Any) -> Any:
    """The list filter of #112 D2, the SQL twin of :func:`paid_state`: only a
    ``finalized`` period has a paid state."""
    total = PayrollPeriod.total_amount
    finalized = PayrollPeriod.status == FINALIZED
    if state == PAID:
        return finalized & (paid >= total)
    if state == PARTIALLY_PAID:
        return finalized & (paid > 0) & (paid < total)
    return finalized & (paid == 0) & (total > Money.zero())


def _row(row: Any) -> PayrollPeriodRow:
    """A list row; ``paid_total`` and ``paid_state`` are ``None`` unless
    ``finalized``."""
    period: PayrollPeriod = row.PayrollPeriod
    paid_total: Money | None = None
    state: PaidState | None = None
    if period.status == FINALIZED and period.total_amount is not None:
        paid_total = Money(row.paid)
        state = paid_state(period.total_amount, paid_total)
    return PayrollPeriodRow(
        **_facts(period).model_dump(),
        line_count=row.line_count,
        total=_row_total(row.total),
        paid_total=paid_total,
        paid_state=state,
    )


async def list_periods(
    session: AsyncSession,
    *,
    page: OffsetParams,
    visible: Collection[str] | None,
    status: str | None,
    tutor_user_id: uuid.UUID | None,
    paid: PaidState | None = None,
) -> OffsetPage[PayrollPeriodRow]:
    """Periods, newest ``ends_on`` first, then ``id``, with their line count,
    total and, when finalized, what was paid (#112 D2). ``visible`` narrows the
    statuses a caller reads (#110 E8); ``paid`` keeps the finalized periods in
    that paid state. Two statements, whatever the page size, no lock."""
    paid_sum = paid_sum_of(PayrollPeriod.id)
    conditions: list[Any] = []
    if paid is not None:
        conditions.append(_paid_state_condition(paid, paid_sum))
    if visible is not None:
        conditions.append(PayrollPeriod.status.in_(visible))
    if status is not None:
        conditions.append(PayrollPeriod.status == status)
    if tutor_user_id is not None:
        conditions.append(PayrollPeriod.tutor_user_id == tutor_user_id)
    count = await session.scalar(
        select(func.count()).select_from(PayrollPeriod).where(*conditions)
    )
    rows = await session.execute(
        select(
            PayrollPeriod,
            func.count(PayrollLine.id).label("line_count"),
            func.sum(cast(PayrollLine.amount, Numeric)).label("total"),
            paid_sum.label("paid"),
        )
        .outerjoin(PayrollLine, PayrollLine.payroll_period_id == PayrollPeriod.id)
        .where(*conditions)
        .group_by(PayrollPeriod.id)
        .order_by(PayrollPeriod.ends_on.desc(), PayrollPeriod.id)
        .limit(page.limit)
        .offset(page.offset)
        .execution_options(populate_existing=True)
    )
    return OffsetPage[PayrollPeriodRow](
        items=[_row(row) for row in rows],
        total=count or 0,
        limit=page.limit,
        offset=page.offset,
    )
