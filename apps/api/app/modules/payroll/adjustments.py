"""Reading and writing ``payroll_adjustments`` (#111; ADR-0017 E11 and its #111
notes; data-model §11.9, §13, §15).

The job ``payroll:post_adjustments`` (:mod:`app.workflows.payroll_adjustments`)
composes the reads here with ``academics`` and ``scheduling``; this module only
knows payroll's tables.

**Unexecuted predicates (#443 C4):** :func:`has_no_adjustment` ("settled", #111
E7), :func:`has_no_adjustment_keyed`, :func:`paid_after_lock` and
:func:`revision_unpaid`. The reads that select candidates take no lock (ADR-0002,
#443 D3).

**Paid so far (#111 E2):** :func:`paid_so_far` reads, for each session, its
origin (the finalized session line, or the ``adjustment`` line of its
``late_approval``), with the snapshot it was priced on (and, for a per-course
origin, its kind, amount, ``k``, ``N`` and rule, M7-08d E2), and every automatic
adjustment of the session. ``manual`` ones are left out: a person's correction is
never undone by the job.

**Writing (#111 B4):** :func:`post_adjustment` inserts the adjustment in a
savepoint and then its one ``adjustment`` line, under the caller's lock of the
target period. A ``source_key`` or first-payment conflict rolls the savepoint
back and answers ``None``: someone else paid it.

**A person's adjustments (M7-08c):** :func:`post_manual_adjustment` writes a
``manual`` one the same way (a first-payment conflict answers ``None``), and
:func:`post_carry_forward` the two rows of a carry, ``+X`` in the negative
period and ``-X`` in the next open one, the second naming the first
(``paired_adjustment_id``). :func:`settled_session_ids` is what keeps a settled
session out of a run (M7-08c B1), :func:`settled_by_tutor` and
:func:`line_period_status` are the reference checks of a ``manual`` one.

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Final

from sqlalchemy import ColumnElement, and_, exists, insert, literal, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import Money
from app.modules.payroll.calculation import LineDraft, OriginSnapshot, SalaryCover
from app.modules.payroll.lines import FINALIZED, OPEN
from app.modules.payroll.models import (
    ADJUSTMENT,
    ADJUSTMENT_FIRST_PAYMENT,
    ADJUSTMENT_SOURCE_KEY,
    AUTOMATIC_ADJUSTMENT_KINDS,
    CARRY_FORWARD,
    LATE_APPROVAL,
    MANUAL,
    SALARY,
    SESSION_LINE_KINDS,
    PayrollAdjustment,
    PayrollLine,
    PayrollPeriod,
)

APPROVAL_KEY: Final = "approval:"
CANCELLATION_KEY: Final = "cancellation:"
CONFLICTS: Final = (ADJUSTMENT_SOURCE_KEY, ADJUSTMENT_FIRST_PAYMENT)


def approval_key(approval_id: uuid.UUID) -> str:
    """``approval:<id>``: one key per revision (#111 E2)."""
    return f"{APPROVAL_KEY}{approval_id}"


def cancellation_key(class_session_id: uuid.UUID) -> str:
    """``cancellation:<class_session_id>`` (#443 D8)."""
    return f"{CANCELLATION_KEY}{class_session_id}"


# --- Unexecuted predicates -----------------------------------------------------------


def has_no_adjustment(class_session_id: ColumnElement[Any]) -> ColumnElement[bool]:
    """``NOT EXISTS`` an adjustment naming this session, of any kind: the work is
    settled (#111 E7) and never listed, blocking or paid a second time."""
    return ~exists().where(PayrollAdjustment.class_session_id == class_session_id)


def has_no_adjustment_keyed(key: ColumnElement[Any]) -> ColumnElement[bool]:
    """``NOT EXISTS`` an adjustment with this ``source_key``."""
    return ~exists().where(PayrollAdjustment.source_key == key)


def paid_after_lock(class_session_id: ColumnElement[Any]) -> ColumnElement[bool]:
    """The session was paid in a finalized way: a session-sourced line of a
    finalized period, or a ``late_approval`` (#111 E2)."""
    return or_(
        exists(
            select(PayrollLine.id)
            .join(PayrollPeriod, PayrollPeriod.id == PayrollLine.payroll_period_id)
            .where(
                PayrollLine.class_session_id == class_session_id,
                PayrollLine.kind.in_(SESSION_LINE_KINDS),
                PayrollPeriod.status == FINALIZED,
            )
        ),
        exists().where(
            PayrollAdjustment.class_session_id == class_session_id,
            PayrollAdjustment.kind == LATE_APPROVAL,
        ),
    )


def revision_unpaid(
    class_session_id: ColumnElement[Any], approval_id: ColumnElement[Any]
) -> ColumnElement[bool]:
    """Neither a session line nor an adjustment of the session names this
    revision (#111 B3)."""
    return and_(
        ~exists().where(
            PayrollLine.class_session_id == class_session_id,
            PayrollLine.kind.in_(SESSION_LINE_KINDS),
            PayrollLine.approval_id == approval_id,
        ),
        ~exists().where(
            PayrollAdjustment.class_session_id == class_session_id,
            PayrollAdjustment.approval_id == approval_id,
        ),
    )


def keyed(prefix: str, value: ColumnElement[Any]) -> ColumnElement[Any]:
    """``'<prefix>' || value::text``, for :func:`has_no_adjustment_keyed`."""
    return literal(prefix) + value.cast(PayrollAdjustment.source_key.type)


# --- Reads ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FinalizedSpan:
    """A finalized period's dates."""

    id: uuid.UUID
    starts_on: date
    ends_on: date


def finalized_span_of(spans: Sequence[FinalizedSpan], on: date) -> FinalizedSpan | None:
    """The finalized period holding ``on``, if any."""
    for span in spans:
        if span.starts_on <= on <= span.ends_on:
            return span
    return None


async def finalized_spans_of_tutors(
    session: AsyncSession, tutor_user_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, list[FinalizedSpan]]:
    """Each tutor's finalized periods. One statement, none for no ids, no lock."""
    spans: dict[uuid.UUID, list[FinalizedSpan]] = {}
    if tutor_user_ids:
        rows = await session.execute(
            select(
                PayrollPeriod.tutor_user_id,
                PayrollPeriod.id,
                PayrollPeriod.starts_on,
                PayrollPeriod.ends_on,
            ).where(
                PayrollPeriod.tutor_user_id.in_(set(tutor_user_ids)),
                PayrollPeriod.status == FINALIZED,
            )
        )
        for row in rows:
            spans.setdefault(row.tutor_user_id, []).append(
                FinalizedSpan(row.id, row.starts_on, row.ends_on)
            )
    return spans


async def salary_covers_of_tutors(
    session: AsyncSession, tutor_user_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, list[SalaryCover]]:
    """The dates each tutor's finalized salary lines paid (#111 E4), with the
    database time of that period's finalize: ``updated_at``, which nothing writes
    after it (the migration 0073 guard), on the same clock as a rate version's
    ``created_at``. One statement, none for no ids, no lock."""
    covers: dict[uuid.UUID, list[SalaryCover]] = {}
    if tutor_user_ids:
        rows = await session.execute(
            select(
                PayrollLine.tutor_user_id,
                PayrollLine.salary_starts_on,
                PayrollLine.salary_ends_on,
                PayrollPeriod.updated_at,
            )
            .join(PayrollPeriod, PayrollPeriod.id == PayrollLine.payroll_period_id)
            .where(
                PayrollLine.tutor_user_id.in_(set(tutor_user_ids)),
                PayrollLine.kind == SALARY,
                PayrollPeriod.status == FINALIZED,
            )
        )
        for row in rows:
            covers.setdefault(row.tutor_user_id, []).append(
                SalaryCover(row.salary_starts_on, row.salary_ends_on, row.updated_at)
            )
    return covers


@dataclass(frozen=True)
class PaidSession:
    """What a session was paid in a finalized way (#111 E2)."""

    class_session_id: uuid.UUID
    tutor_user_id: uuid.UUID
    origin_line_id: uuid.UUID
    origin: OriginSnapshot
    paid: tuple[Money, ...]
    """The origin line's amount if it is a session line, and every automatic
    adjustment of the session."""
    approval_ids: frozenset[uuid.UUID] = field(default_factory=frozenset)


async def paid_so_far(
    session: AsyncSession, class_session_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, PaidSession]:
    """Each session's origin and what was paid so far. Two statements, none for
    no ids. A session with neither origin is left out."""
    if not class_session_ids:
        return {}
    ids = set(class_session_ids)
    lines = (
        await session.execute(
            select(
                PayrollLine.id,
                PayrollLine.class_session_id,
                PayrollLine.tutor_user_id,
                PayrollLine.amount,
                PayrollLine.approval_id,
                PayrollLine.rate_model,
                PayrollLine.rate,
                PayrollLine.compensation_rate_id,
                PayrollLine.kind,
                PayrollLine.cumulative_sessions,
                PayrollLine.cohort_sessions,
                PayrollLine.session_pay_rule_id,
            )
            .join(PayrollPeriod, PayrollPeriod.id == PayrollLine.payroll_period_id)
            .where(
                PayrollLine.class_session_id.in_(ids),
                PayrollLine.kind.in_(SESSION_LINE_KINDS),
                PayrollPeriod.status == FINALIZED,
            )
        )
    ).all()
    adjustments = (
        await session.execute(
            select(
                PayrollAdjustment.class_session_id,
                PayrollAdjustment.tutor_user_id,
                PayrollAdjustment.kind,
                PayrollAdjustment.amount,
                PayrollAdjustment.approval_id,
                PayrollAdjustment.rate_model,
                PayrollAdjustment.rate,
                PayrollAdjustment.compensation_rate_id,
                PayrollAdjustment.line_kind,
                PayrollAdjustment.cumulative_sessions,
                PayrollAdjustment.cohort_sessions,
                PayrollAdjustment.session_pay_rule_id,
                PayrollLine.id.label("line_id"),
            )
            .join(
                PayrollLine,
                and_(
                    PayrollLine.payroll_adjustment_id == PayrollAdjustment.id,
                    PayrollLine.kind == ADJUSTMENT,
                ),
            )
            .where(
                PayrollAdjustment.class_session_id.in_(ids),
                PayrollAdjustment.kind.in_(AUTOMATIC_ADJUSTMENT_KINDS),
            )
            .order_by(PayrollAdjustment.created_at, PayrollAdjustment.id)
        )
    ).all()
    found: dict[uuid.UUID, PaidSession] = {}
    for line in lines:
        found[line.class_session_id] = PaidSession(
            class_session_id=line.class_session_id,
            tutor_user_id=line.tutor_user_id,
            origin_line_id=line.id,
            origin=OriginSnapshot(
                line.rate_model,
                line.rate,
                line.compensation_rate_id,
                kind=line.kind,
                line_amount=line.amount,
                cumulative_sessions=line.cumulative_sessions,
                cohort_sessions=line.cohort_sessions,
                session_pay_rule_id=line.session_pay_rule_id,
            ),
            paid=(line.amount,),
            approval_ids=frozenset(
                () if line.approval_id is None else (line.approval_id,)
            ),
        )
    for row in adjustments:
        known = found.get(row.class_session_id)
        if known is None:
            if row.kind != LATE_APPROVAL:  # pragma: no cover - P13: it comes first
                continue
            known = PaidSession(
                class_session_id=row.class_session_id,
                tutor_user_id=row.tutor_user_id,
                origin_line_id=row.line_id,
                origin=OriginSnapshot(
                    row.rate_model,
                    row.rate,
                    row.compensation_rate_id,
                    kind=row.line_kind,
                    line_amount=row.amount,
                    cumulative_sessions=row.cumulative_sessions,
                    cohort_sessions=row.cohort_sessions,
                    session_pay_rule_id=row.session_pay_rule_id,
                ),
                paid=(),
            )
        found[row.class_session_id] = PaidSession(
            class_session_id=known.class_session_id,
            tutor_user_id=known.tutor_user_id,
            origin_line_id=known.origin_line_id,
            origin=known.origin,
            paid=(*known.paid, row.amount),
            approval_ids=known.approval_ids
            | frozenset(() if row.approval_id is None else (row.approval_id,)),
        )
    return found


async def earliest_open_period(
    session: AsyncSession, tutor_user_id: uuid.UUID, *, after: date | None = None
) -> uuid.UUID | None:
    """The tutor's earliest ``open`` period, starting after ``after`` if given
    (M7-08c E3), read with no lock: the caller locks and re-reads it (#111 E6).
    One statement."""
    query = select(PayrollPeriod.id).where(
        PayrollPeriod.tutor_user_id == tutor_user_id,
        PayrollPeriod.status == OPEN,
    )
    if after is not None:
        query = query.where(PayrollPeriod.starts_on > after)
    return await session.scalar(query.order_by(PayrollPeriod.starts_on).limit(1))


# --- Writing -------------------------------------------------------------------------


@dataclass(frozen=True)
class AdjustmentDraft:
    """An automatic adjustment before it is written (#111 A1, A2)."""

    kind: str
    tutor_user_id: uuid.UUID
    amount: Money
    source_key: str
    class_session_id: uuid.UUID
    priced_on: LineDraft
    """The draft it was priced on: its kind, session fields and rate snapshot."""
    approval_id: uuid.UUID | None = None
    origin_line_id: uuid.UUID | None = None
    origin_period_id: uuid.UUID | None = None


async def post_adjustment(
    session: AsyncSession, draft: AdjustmentDraft, target_period_id: uuid.UUID
) -> uuid.UUID | None:
    """The adjustment, audited with the Unit of Work's actor, and its line in the
    locked target period. ``None`` when another writer paid it first. Two
    statements and the audit row, more with a savepoint."""
    priced = draft.priced_on
    adjustment = PayrollAdjustment(
        id=uuid.uuid7(),
        kind=draft.kind,
        tutor_user_id=draft.tutor_user_id,
        target_period_id=target_period_id,
        amount=draft.amount,
        origin_line_id=draft.origin_line_id,
        origin_period_id=draft.origin_period_id,
        class_session_id=draft.class_session_id,
        approval_id=draft.approval_id,
        source_key=draft.source_key,
        line_kind=priced.kind,
        service_date=priced.service_date,
        class_kind=priced.class_kind,
        minutes=priced.minutes,
        rate_model=priced.rate_model,
        rate=priced.rate,
        compensation_rate_id=priced.compensation_rate_id,
        session_pay_rule_id=priced.session_pay_rule_id,
        cumulative_sessions=priced.cumulative_sessions,
        cohort_sessions=priced.cohort_sessions,
    )
    try:
        async with session.begin_nested():
            session.add(adjustment)
    except IntegrityError as exc:
        if any(name in str(exc.orig) for name in CONFLICTS):
            return None
        raise
    await session.execute(
        insert(PayrollLine).values(
            payroll_period_id=target_period_id,
            tutor_user_id=draft.tutor_user_id,
            kind=ADJUSTMENT,
            amount=draft.amount,
            payroll_adjustment_id=adjustment.id,
        )
    )
    return adjustment.id


# --- A person's adjustments (M7-08c) -------------------------------------------------


async def settled_session_ids(
    session: AsyncSession, class_session_ids: Collection[uuid.UUID]
) -> set[uuid.UUID]:
    """The sessions among these that an adjustment names, of any kind: settled
    (#111 E7), so a run drafts no line for them (M7-08c B1), as the unresolved
    reads leave them out (:func:`has_no_adjustment`). One statement, none for no
    ids, no lock."""
    if not class_session_ids:
        return set()
    return set(
        await session.scalars(
            select(PayrollAdjustment.class_session_id)
            .where(PayrollAdjustment.class_session_id.in_(set(class_session_ids)))
            .distinct()
        )
    )


async def settled_by_tutor(
    session: AsyncSession, tutor_user_id: uuid.UUID, class_session_id: uuid.UUID
) -> bool:
    """Whether an adjustment of this tutor already names this session. One
    statement."""
    found = await session.scalar(
        select(PayrollAdjustment.id)
        .where(
            PayrollAdjustment.tutor_user_id == tutor_user_id,
            PayrollAdjustment.class_session_id == class_session_id,
        )
        .limit(1)
    )
    return found is not None


async def line_period_status(
    session: AsyncSession, line_id: uuid.UUID, tutor_user_id: uuid.UUID
) -> str | None:
    """The status of the period holding this tutor's line, or ``None`` for no
    such line of this tutor. One statement; the caller holds the tutor's
    not-finalized periods ``FOR UPDATE``, so the answer can't change."""
    status: str | None = await session.scalar(
        select(PayrollPeriod.status)
        .join(PayrollLine, PayrollLine.payroll_period_id == PayrollPeriod.id)
        .where(PayrollLine.id == line_id, PayrollLine.tutor_user_id == tutor_user_id)
    )
    return status


@dataclass(frozen=True)
class PostedAdjustment:
    """A written adjustment: no amount, no reason (M7-08c E6)."""

    id: uuid.UUID
    target_period_id: uuid.UUID
    created_at: datetime


async def _written(
    session: AsyncSession, adjustment: PayrollAdjustment
) -> PostedAdjustment | None:
    """The adjustment in a savepoint, then its line. ``None`` on a first-payment
    conflict. The audit row comes with the Unit of Work's actor."""
    try:
        async with session.begin_nested():
            session.add(adjustment)
    except IntegrityError as exc:
        if ADJUSTMENT_FIRST_PAYMENT in str(exc.orig):
            return None
        raise
    await session.execute(
        insert(PayrollLine).values(
            payroll_period_id=adjustment.target_period_id,
            tutor_user_id=adjustment.tutor_user_id,
            kind=ADJUSTMENT,
            amount=adjustment.amount,
            payroll_adjustment_id=adjustment.id,
        )
    )
    return PostedAdjustment(
        adjustment.id, adjustment.target_period_id, adjustment.created_at
    )


@dataclass(frozen=True)
class ManualDraft:
    """A ``manual`` adjustment before it is written (M7-08c E1): on a line, on
    unresolved work, or free."""

    tutor_user_id: uuid.UUID
    amount: Money
    reason: str
    created_by_user_id: uuid.UUID
    origin_line_id: uuid.UUID | None = None
    class_session_id: uuid.UUID | None = None


async def post_manual_adjustment(
    session: AsyncSession, draft: ManualDraft, target_period_id: uuid.UUID
) -> PostedAdjustment | None:
    """The ``manual`` adjustment and its line in the locked target period.
    ``None`` when the session already has its first payment (the index,
    #111 A3). Two statements and the audit row, more with a savepoint."""
    return await _written(
        session,
        PayrollAdjustment(
            id=uuid.uuid7(),
            kind=MANUAL,
            tutor_user_id=draft.tutor_user_id,
            target_period_id=target_period_id,
            amount=draft.amount,
            reason=draft.reason,
            origin_line_id=draft.origin_line_id,
            class_session_id=draft.class_session_id,
            created_by_user_id=draft.created_by_user_id,
        ),
    )


async def post_carry_forward(
    session: AsyncSession,
    *,
    tutor_user_id: uuid.UUID,
    period_id: uuid.UUID,
    target_period_id: uuid.UUID,
    amount: Money,
    created_by_user_id: uuid.UUID,
) -> tuple[PostedAdjustment, PostedAdjustment]:
    """``+amount`` in ``period_id`` and ``-amount`` in ``target_period_id``,
    both with ``origin_period_id`` = ``period_id``, the second naming the first
    (M7-08c E3). The caller holds both periods ``FOR UPDATE``. Four statements
    and two audit rows, more with the savepoints."""
    rows = []
    paired: uuid.UUID | None = None
    for target, signed in ((period_id, amount), (target_period_id, -amount)):
        written = await _written(
            session,
            PayrollAdjustment(
                id=uuid.uuid7(),
                kind=CARRY_FORWARD,
                tutor_user_id=tutor_user_id,
                target_period_id=target,
                amount=signed,
                origin_period_id=period_id,
                paired_adjustment_id=paired,
                created_by_user_id=created_by_user_id,
            ),
        )
        if written is None:  # pragma: no cover - a carry names no session
            raise RuntimeError("a carry-forward can't conflict")
        paired = written.id
        rows.append(written)
    return rows[0], rows[1]
