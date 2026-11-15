"""The billing side of schedule reconciliation (#99 A2, A3, C3, D4-D6;
ADR-0006 E2, E3; data-model §3.10, §3.22a, §3.22b, §6).

``app.workflows.schedule_reconciliation`` composes it with ``scheduling``;
this module never imports ``scheduling``.

**Natural keys** (data-model §3.10; the ``charge_adjustments.source_key``
of each generated correction):

- :data:`CANCELLATION_CREDIT_KEY` ``cancellation_credit:<charge_id>``: one
  cancellation credit per charge;
- :data:`CLOSURE_CREDIT_KEY` ``closure_credit:<charge_id>:<n>``: ``n`` = the
  number of ``closure_credit`` rows of the charge + 1, read under the
  account lock; odd ``n`` is a credit (negative), even ``n`` a restoration
  (positive, exactly minus the credit before it; Q3);
- :data:`DURATION_CHANGE_KEY` ``duration_change:<charge_id>:<version>``:
  the session ``version`` the repricing saw (Q1: edits between two runs
  coalesce into one).

**Reads.** The unexecuted :func:`session_charge_reconciliation_columns`
(every ``session`` charge with its net, closure total and billed minutes)
and :func:`cancellation_decision_key_columns` (the decided keys) are joined
by the workflow's candidate query (ADR-0002). :func:`session_charge_of_key`
finds a key's charge (no lock); :func:`reconciliation_charge` reads
everything the job decides with in one statement, **after** the caller
took :func:`~app.modules.billing.ledger.lock_billing_account` (the last
lock).

**Writes** (the caller's Unit of Work; no commit):
:func:`record_cancellation_decision` and :func:`record_repricing` insert
one append-only row inside a savepoint; a violation of the row's natural
key (a concurrent worker) rolls the savepoint back, with its audit event,
and returns ``False``. Adjustments go through
:func:`~app.modules.billing.ledger.post_adjustment` (I1, E5 releases).
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final

from sqlalchemy import Select, func, literal, select, type_coerce
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.money import Money, MoneyType, money_sum
from app.modules.billing.models import (
    CANCELLATION_DECISION_KEY,
    REPRICING_KEY,
    BillingAccount,
    Charge,
    ChargeAdjustment,
    ChargeDurationRepricing,
    SessionCancellationDecision,
)

CANCELLATION_CREDIT_KEY: Final = "cancellation_credit:{charge_id}"
CLOSURE_CREDIT_KEY: Final = "closure_credit:{charge_id}:{n}"
DURATION_CHANGE_KEY: Final = "duration_change:{charge_id}:{version}"
CANCELLATION_CREDIT: Final = "cancellation_credit"
CLOSURE_CREDIT: Final = "closure_credit"
DURATION_CHANGE: Final = "duration_change"


def _adjustments_of(charge: Any, *, kind: str | None = None) -> Any:
    """``Σ amount`` of a charge's adjustments (of one kind), as a correlated
    scalar subquery."""
    query = select(money_sum(ChargeAdjustment.amount)).where(
        ChargeAdjustment.charge_id == charge.id
    )
    if kind is not None:
        query = query.where(ChargeAdjustment.kind == kind)
    return query.correlate(charge).scalar_subquery()


def _closure_count(charge: Any) -> Any:
    return (
        select(func.count(ChargeAdjustment.id))
        .where(
            ChargeAdjustment.charge_id == charge.id,
            ChargeAdjustment.kind == CLOSURE_CREDIT,
        )
        .correlate(charge)
        .scalar_subquery()
    )


def _billed_minutes(charge: Any) -> Any:
    """The minutes of the charge's latest repricing, else its snapshot."""
    latest = (
        select(ChargeDurationRepricing.scheduled_minutes)
        .where(ChargeDurationRepricing.charge_id == charge.id)
        .order_by(
            ChargeDurationRepricing.created_at.desc(),
            ChargeDurationRepricing.id.desc(),
        )
        .limit(1)
        .correlate(charge)
        .scalar_subquery()
    )
    return func.coalesce(latest, charge.scheduled_minutes)


def _money(expression: Any, name: str) -> Any:
    return type_coerce(expression, MoneyType()).label(name)


def session_charge_reconciliation_columns() -> Select[Any]:
    """C3: ``SELECT charge_id, class_session_id, student_id, amount,
    charge_minutes (the snapshot), net, closure_total, billed_minutes`` of
    every ``session`` charge, **not executed**: the reconciliation job's
    candidate query joins it on ``(class_session_id, student_id)``."""
    return select(
        Charge.id.label("charge_id"),
        Charge.class_session_id,
        Charge.student_id,
        Charge.amount,
        Charge.scheduled_minutes.label("charge_minutes"),
        _money(Charge.amount + _adjustments_of(Charge), "net"),
        _money(_adjustments_of(Charge, kind=CLOSURE_CREDIT), "closure_total"),
        _billed_minutes(Charge).label("billed_minutes"),
    ).where(Charge.kind == "session")


def cancellation_decision_key_columns() -> Select[uuid.UUID, uuid.UUID]:
    """``SELECT class_session_id, student_id`` of the decided cancellations,
    **not executed**: the candidate query anti-joins it (Q5)."""
    return select(
        SessionCancellationDecision.class_session_id,
        SessionCancellationDecision.student_id,
    )


@dataclass(frozen=True)
class ReconciliationCharge:
    """A session charge as the reconciliation decides with it, read under
    the account lock."""

    charge_id: uuid.UUID
    billing_account_id: uuid.UUID
    family_id: uuid.UUID
    student_id: uuid.UUID
    amount: Money
    scheduled_minutes: int
    """The snapshot of the minutes the charge was posted for."""
    service_date: date
    education_level_id: uuid.UUID | None
    posted_at: datetime
    net: Money
    closure_total: Money
    """``Σ closure_credit``: ``0.00`` or minus the credited net."""
    closure_count: int
    duration_total: Money
    """``Σ duration_change``."""
    billed_minutes: int

    @property
    def billed_amount(self) -> Money:
        """The amount billed for the duration (Q2): ``amount`` + Σ
        ``duration_change``; other adjustments are not part of it."""
        return self.amount + self.duration_total


async def session_charge_of_key(
    session: AsyncSession, class_session_id: uuid.UUID, student_id: uuid.UUID
) -> tuple[uuid.UUID, uuid.UUID] | None:
    """``(charge_id, billing_account_id)`` of the key's ``session`` charge,
    or ``None``. One statement, no lock."""
    row = (
        await session.execute(
            select(Charge.id, Charge.billing_account_id).where(
                Charge.kind == "session",
                Charge.class_session_id == class_session_id,
                Charge.student_id == student_id,
            )
        )
    ).first()
    return None if row is None else (row.id, row.billing_account_id)


async def reconciliation_charge(
    session: AsyncSession, charge_id: uuid.UUID
) -> ReconciliationCharge:
    """One statement; the caller holds the account lock. An unknown id is a
    :class:`ValueError`."""
    row = (
        await session.execute(
            select(
                Charge.id,
                Charge.billing_account_id,
                BillingAccount.family_id,
                Charge.student_id,
                Charge.amount,
                Charge.scheduled_minutes,
                Charge.service_date,
                Charge.education_level_id,
                Charge.posted_at,
                _money(Charge.amount + _adjustments_of(Charge), "net"),
                _money(_adjustments_of(Charge, kind=CLOSURE_CREDIT), "closure_total"),
                _closure_count(Charge).label("closure_count"),
                _money(_adjustments_of(Charge, kind=DURATION_CHANGE), "duration_total"),
                _billed_minutes(Charge).label("billed_minutes"),
            )
            .join(BillingAccount, BillingAccount.id == Charge.billing_account_id)
            .where(Charge.id == charge_id, Charge.kind == "session")
        )
    ).first()
    if row is None:
        raise ValueError("unknown session charge")
    return ReconciliationCharge(
        charge_id=row.id,
        billing_account_id=row.billing_account_id,
        family_id=row.family_id,
        student_id=row.student_id,
        amount=Money(row.amount),
        scheduled_minutes=row.scheduled_minutes,
        service_date=row.service_date,
        education_level_id=row.education_level_id,
        posted_at=row.posted_at,
        net=Money(row.net),
        closure_total=Money(row.closure_total),
        closure_count=row.closure_count,
        duration_total=Money(row.duration_total),
        billed_minutes=row.billed_minutes,
    )


async def has_cancellation_decision(
    session: AsyncSession, class_session_id: uuid.UUID, student_id: uuid.UUID
) -> bool:
    """Whether the key has a decision (one statement)."""
    found = await session.scalar(
        select(literal(1)).where(
            SessionCancellationDecision.class_session_id == class_session_id,
            SessionCancellationDecision.student_id == student_id,
        )
    )
    return found is not None


@dataclass(frozen=True)
class CancellationDecisionDraft:
    """One ``session_cancellation_decisions`` row (A2)."""

    class_session_id: uuid.UUID
    student_id: uuid.UUID
    initiated_by: str
    change_request_id: uuid.UUID | None
    classification: str
    makeup_allowed: bool
    classified_at: datetime
    rule_id: uuid.UUID
    outcome: str
    result: str
    charge_id: uuid.UUID | None = None
    charge_adjustment_id: uuid.UUID | None = None


def _violates(error: IntegrityError, name: str) -> bool:
    return name in str(error.orig)


async def record_cancellation_decision(
    session: AsyncSession, draft: CancellationDecisionDraft
) -> bool:
    """Insert the decision in a savepoint (D4). ``False`` when the key was
    decided meanwhile (``uq_session_cancellation_decisions_key``): the
    savepoint, with its audit event, is rolled back."""
    row = SessionCancellationDecision(
        class_session_id=draft.class_session_id,
        student_id=draft.student_id,
        initiated_by=draft.initiated_by,
        change_request_id=draft.change_request_id,
        classification=draft.classification,
        makeup_allowed=draft.makeup_allowed,
        classified_at=draft.classified_at,
        rule_id=draft.rule_id,
        outcome=draft.outcome,
        result=draft.result,
        charge_id=draft.charge_id,
        charge_adjustment_id=draft.charge_adjustment_id,
        decided_at=clock.now(),
    )
    try:
        async with session.begin_nested():
            session.add(row)
    except IntegrityError as exc:
        if not _violates(exc, CANCELLATION_DECISION_KEY):
            raise
        return False
    return True


async def has_repricing(
    session: AsyncSession, charge_id: uuid.UUID, session_version: int
) -> bool:
    """Whether the charge was repriced for that session version (one
    statement)."""
    found = await session.scalar(
        select(literal(1)).where(
            ChargeDurationRepricing.charge_id == charge_id,
            ChargeDurationRepricing.session_version == session_version,
        )
    )
    return found is not None


@dataclass(frozen=True)
class RepricingDraft:
    """One ``charge_duration_repricings`` row (A3)."""

    charge_id: uuid.UUID
    class_session_id: uuid.UUID
    session_version: int
    previous_minutes: int
    scheduled_minutes: int
    target_amount: Money
    delta_amount: Money
    method: str
    clamped: bool
    charge_adjustment_id: uuid.UUID | None


async def record_repricing(session: AsyncSession, draft: RepricingDraft) -> bool:
    """Insert the repricing in a savepoint (D6). ``False`` when the charge
    was repriced for that version meanwhile (the savepoint is rolled
    back)."""
    row = ChargeDurationRepricing(
        charge_id=draft.charge_id,
        class_session_id=draft.class_session_id,
        session_version=draft.session_version,
        previous_minutes=draft.previous_minutes,
        scheduled_minutes=draft.scheduled_minutes,
        target_amount=draft.target_amount,
        delta_amount=draft.delta_amount,
        method=draft.method,
        clamped=draft.clamped,
        charge_adjustment_id=draft.charge_adjustment_id,
    )
    try:
        async with session.begin_nested():
            session.add(row)
    except IntegrityError as exc:
        if not _violates(exc, REPRICING_KEY):
            raise
        return False
    return True
