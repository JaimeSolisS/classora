"""Withdrawals: unused sessions or package units to a credit or a refund
(#101 B, C; spec §6 "withdrawals … calculate unused sessions/hours and apply
policy-based refunds or account credits", W4.4, W4.5; ADR-0006 E5, E9,
E10; ``_docs/data-model.md`` §3.23).

**The rule (S1, pure: :func:`withdrawal_credit`).** A withdrawal credits
only its **lines**: the enrollment fee that pays for the period, the posted
``tuition_period`` charges of a plan linked to it whose period ends on or
after the withdrawal's local date, or a package's ``package_purchase``
charge. Never a ``session`` or ``manual`` charge (#92 J4 credits the group
session charges; #99/#100 handle the rest). Per line::

    credit = net × U / D          (Money.prorate: one rounding, half away)

- fee: ``D`` = the owner's counted sessions (materialized plus projected,
  not cancelled or skipped) after the fee period's ``enrolled_at``, ``U``
  = the same after the withdrawal instant;
- linked tuition period: the same counts inside the period's local window;
- package: ``U`` = the remaining units, ``D`` = the units granted.

Policies, in this order: an open-ended group fee → ``open_ended_fee``
(``0.00``, #92 J3: charged in full); ``D = 0`` → ``nothing_delivered``
(the whole net); ``U = 0`` → ``fully_used`` (``0.00``); else
``pro_rata``. ``U`` is clamped to ``[0, D]``. Examples (B1): a 10-session
fee of 3,000.00 with 6 unused → 1,800.00; a fee prorated at enrollment to
2,100.00 (7 of 10), then one session cancelled by the center and 2 held →
``D = 6``, ``U = 4`` → 1,400.00; a 10-session package with 6 left on a net
of 2,500.00 → 1,500.00; a 600-minute package with 270 left on 1,000.00 →
450.00. Staff may override a line's credit from ``0.00`` to its net.

**Freed cash (S4, pure: :func:`plan_freed_cash`).** A credit lowers the
charge's net; when the valid allocations are above the new net, E5
releases the newest ones (``ledger.post_adjustment``) and the excess
becomes its payment's account credit. :func:`plan_freed_cash` predicts that
excess per charge and per payment; a ``refund`` outcome pays exactly it
back (one ``refunds`` row per source payment) and the confirm checks the
real releases against it.

**Rows** (this module, in the caller's Unit of Work; never commits):
:func:`insert_withdrawal` (the append-only ``withdrawals`` row, the
caller's actor), :func:`record_package_refund` (the ``refund`` units row
``−remaining``, one per package) and the reads behind the routes.
:func:`withdrawal_subject_columns` is the unexecuted subject select the #92
classification joins (``withdrawal_settled``).
"""

import hashlib
import json
import uuid
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Final, Literal, cast

from sqlalchemy import Select, func, select, type_coerce
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import current_actor_id
from app.core.money import Money, MoneyType, money_sum
from app.modules.billing.balances import _released
from app.modules.billing.errors import PackageNotWithdrawableError
from app.modules.billing.models import (
    PACKAGE_REFUND_KEY,
    AllocationRelease,
    BillingAccount,
    Charge,
    ChargeAdjustment,
    Package,
    PackageCreditTransaction,
    Payment,
    PaymentAllocation,
    Refund,
    Withdrawal,
)

type CreditPolicy = Literal[
    "pro_rata", "nothing_delivered", "open_ended_fee", "fully_used"
]
type WithdrawalOutcome = Literal["refund", "account_credit"]
type SubjectKind = Literal["group_enrollment", "cohort_enrollment", "package"]

WITHDRAWAL_KEY: Final = "withdrawal:{charge_id}:{withdrawal_id}"
"""The ``source_key`` of a withdrawal's credit on one line (data-model
§3.10)."""
SUBJECT_COLUMNS: Final[Mapping[SubjectKind, str]] = {
    "group_enrollment": "group_enrollment_id",
    "cohort_enrollment": "cohort_enrollment_id",
    "package": "package_id",
}


# --- The pure rules (B1, B2, B7) -----------------------------------------------------


@dataclass(frozen=True)
class LineCredit:
    """The computed credit of one line and the policy that gave it (S1)."""

    policy: CreditPolicy
    credit: Money


def withdrawal_credit(
    net: Money, unused: int, denominator: int, *, open_ended: bool = False
) -> LineCredit:
    """S1 for one line (module docstring): ``open_ended_fee`` → ``0.00``;
    ``D = 0`` → ``nothing_delivered`` (the net); ``U`` clamped to ``[0,
    D]``; ``U = 0`` → ``fully_used`` (``0.00``); else ``pro_rata`` =
    ``net.prorate(U, D)``. A net of ``0.00`` credits ``0.00``."""
    if open_ended:
        return LineCredit("open_ended_fee", Money.zero())
    if denominator <= 0:
        return LineCredit("nothing_delivered", max(net, Money.zero()))
    unused = max(0, min(unused, denominator))
    if unused == 0:
        return LineCredit("fully_used", Money.zero())
    if not net.is_positive():
        return LineCredit("pro_rata", Money.zero())
    return LineCredit("pro_rata", net.prorate(unused, denominator))


@dataclass(frozen=True)
class AllocationFact:
    """One valid allocation of a charge, with its payment's date (the order
    of E5's releases is the allocation's ``created_at``, ``id``, newest
    first; refunds go oldest ``received_on`` first)."""

    allocation_id: uuid.UUID
    payment_id: uuid.UUID
    amount: Money
    created_at: datetime
    received_on: date


@dataclass(frozen=True)
class ChargeAfter:
    """A charge with its net after the planned credit and its valid
    allocations (any order)."""

    charge_id: uuid.UUID
    new_net: Money
    allocations: Sequence[AllocationFact]


@dataclass(frozen=True)
class FreedCash:
    """What E5 releases (B2): per charge and per payment (payments oldest
    ``received_on`` first), and the total."""

    by_charge: Mapping[uuid.UUID, Money]
    by_payment: Mapping[uuid.UUID, Money]
    total: Money


def plan_freed_cash(lines_after: Sequence[ChargeAfter]) -> FreedCash:
    """B2 (pure): per charge, ``max(0, Σ valid allocations − new net)``,
    taken from the newest allocation first (``created_at``, ``id``) and
    attributed to its payment, exactly as ``post_adjustment`` releases and
    re-inserts."""
    by_charge: dict[uuid.UUID, Money] = {}
    by_payment: dict[uuid.UUID, Money] = {}
    received: dict[uuid.UUID, date] = {}
    for line in lines_after:
        valid = sorted(
            line.allocations,
            key=lambda a: (a.created_at, a.allocation_id),
            reverse=True,
        )
        excess = Money.total(a.amount for a in valid) - line.new_net
        if not excess.is_positive():
            continue
        by_charge[line.charge_id] = by_charge.get(line.charge_id, Money.zero()) + excess
        left = excess
        for allocation in valid:
            if not left.is_positive():
                break
            take = min(allocation.amount, left)
            by_payment[allocation.payment_id] = (
                by_payment.get(allocation.payment_id, Money.zero()) + take
            )
            received[allocation.payment_id] = allocation.received_on
            left -= take
    ordered = dict(
        sorted(by_payment.items(), key=lambda item: (received[item[0]], str(item[0])))
    )
    return FreedCash(
        by_charge=by_charge,
        by_payment=ordered,
        total=Money.total(by_charge.values()),
    )


def _canonical(value: Any) -> Any:
    if isinstance(value, Money):
        return str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k): _canonical(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_canonical(v) for v in value]
    return value


def canonical_json(value: Any) -> str:
    """The canonical JSON of ``value`` (B7): keys sorted, no spaces, Money
    and decimals as their strings, ids and dates as text."""
    return json.dumps(
        _canonical(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def fingerprint(value: Any) -> str:
    """SHA-256 hex of :func:`canonical_json` (B7)."""
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


# --- Reads in the caller's Unit of Work ----------------------------------------------


def withdrawal_subject_columns() -> Select[
    uuid.UUID, uuid.UUID | None, uuid.UUID | None, uuid.UUID | None
]:
    """``SELECT id, group_enrollment_id, cohort_enrollment_id, package_id
    FROM withdrawals``, **not executed** (the #92 ``withdrawal_settled``
    classification joins it)."""
    return select(
        Withdrawal.id,
        Withdrawal.group_enrollment_id,
        Withdrawal.cohort_enrollment_id,
        Withdrawal.package_id,
    )


async def withdrawal_of_subject(
    session: AsyncSession, kind: SubjectKind, subject_id: uuid.UUID
) -> uuid.UUID | None:
    """The withdrawal of a subject, or ``None``. One statement."""
    column = getattr(Withdrawal, SUBJECT_COLUMNS[kind])
    found: uuid.UUID | None = await session.scalar(
        select(Withdrawal.id).where(column == subject_id)
    )
    return found


@dataclass(frozen=True)
class LineCharge:
    """A charge a withdrawal may credit, with its net and valid
    allocations."""

    charge_id: uuid.UUID
    billing_account_id: uuid.UUID
    student_id: uuid.UUID
    kind: str
    service_date: date
    service_period_end: date | None
    net: Money
    posted_at: datetime
    allocations: tuple[AllocationFact, ...] = ()
    withdrawal_credited: bool = False
    """A withdrawal already credited it (the line of another period, J5):
    it is never a line again."""

    @property
    def allocated(self) -> Money:
        return Money.total(a.amount for a in self.allocations)


def _net() -> Any:
    adjustments = (
        select(money_sum(ChargeAdjustment.amount))
        .where(ChargeAdjustment.charge_id == Charge.id)
        .correlate(Charge)
        .scalar_subquery()
    )
    return type_coerce(Charge.amount + adjustments, MoneyType()).label("net")


async def line_charges(
    session: AsyncSession, charge_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, LineCharge]:
    """These charges with their net and valid allocations (two statements,
    none for no ids; no lock: the confirm re-reads under the account
    lock)."""
    if not charge_ids:
        return {}
    rows = await session.execute(
        select(
            Charge.id,
            Charge.billing_account_id,
            Charge.student_id,
            Charge.kind,
            Charge.service_date,
            Charge.service_period_end,
            Charge.posted_at,
            _net(),
            select(ChargeAdjustment.id)
            .where(
                ChargeAdjustment.charge_id == Charge.id,
                ChargeAdjustment.kind == "withdrawal",
            )
            .correlate(Charge)
            .exists()
            .label("withdrawal_credited"),
        ).where(Charge.id.in_(set(charge_ids)))
    )
    found = {
        row.id: LineCharge(
            charge_id=row.id,
            billing_account_id=row.billing_account_id,
            student_id=row.student_id,
            kind=row.kind,
            service_date=row.service_date,
            service_period_end=row.service_period_end,
            net=Money(row.net),
            posted_at=row.posted_at,
            withdrawal_credited=bool(row.withdrawal_credited),
        )
        for row in rows
    }
    allocations = await valid_allocations(session, found)
    return {
        charge_id: replace(line, allocations=tuple(allocations.get(charge_id, ())))
        for charge_id, line in found.items()
    }


async def valid_allocations(
    session: AsyncSession, charge_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, list[AllocationFact]]:
    """The valid allocations of these charges, with their payment's date.
    One statement (none for no ids)."""
    if not charge_ids:
        return {}
    rows = await session.execute(
        select(
            PaymentAllocation.id,
            PaymentAllocation.charge_id,
            PaymentAllocation.payment_id,
            PaymentAllocation.amount,
            PaymentAllocation.created_at,
            Payment.received_on,
        )
        .join(Payment, Payment.id == PaymentAllocation.payment_id)
        .where(
            PaymentAllocation.charge_id.in_(set(charge_ids)),
            ~_released(PaymentAllocation.id),
        )
        .order_by(PaymentAllocation.created_at, PaymentAllocation.id)
    )
    found: dict[uuid.UUID, list[AllocationFact]] = {}
    for row in rows:
        found.setdefault(row.charge_id, []).append(
            AllocationFact(
                allocation_id=row.id,
                payment_id=row.payment_id,
                amount=Money(row.amount),
                created_at=row.created_at,
                received_on=row.received_on,
            )
        )
    return found


async def tuition_period_charge_ids(
    session: AsyncSession, plan_ids: Collection[uuid.UUID], *, ends_from: date
) -> list[uuid.UUID]:
    """The ``tuition_period`` charges of these plans whose period ends on or
    after ``ends_from`` (#101 B4), oldest first. One statement (none for no
    plan)."""
    if not plan_ids:
        return []
    rows = await session.scalars(
        select(Charge.id)
        .where(
            Charge.kind == "tuition_period",
            Charge.tuition_plan_id.in_(set(plan_ids)),
            Charge.service_period_end >= ends_from,
        )
        .order_by(Charge.service_date, Charge.id)
    )
    return list(rows)


@dataclass(frozen=True)
class PackageFacts:
    """A package for a withdrawal (#101 S7): its account, student, unit,
    units, dates, its purchase charge and which closing rows it has."""

    id: uuid.UUID
    billing_account_id: uuid.UUID
    student_id: uuid.UUID
    family_id: uuid.UUID
    unit: str
    units_granted: int
    remaining: int
    subject_id: uuid.UUID | None
    purchased_on: date
    expires_on: date
    purchase_charge_id: uuid.UUID | None
    has_reversal: bool
    has_expire: bool
    has_refund: bool

    def not_withdrawable(self, today: date) -> str | None:
        """S7: why the package can't be withdrawn (``cancelled`` →
        ``withdrawn`` → ``expired`` → ``exhausted``), or ``None``."""
        if self.has_reversal:
            return "cancelled"
        if self.has_refund:
            return "withdrawn"
        if self.has_expire or self.expires_on < today:
            return "expired"
        if self.remaining <= 0:
            return "exhausted"
        return None


def _kind_exists(kind: str) -> Any:
    return (
        select(PackageCreditTransaction.id)
        .where(
            PackageCreditTransaction.package_id == Package.id,
            PackageCreditTransaction.kind == kind,
        )
        .correlate(Package)
        .exists()
    )


async def package_facts(
    session: AsyncSession, package_id: uuid.UUID
) -> PackageFacts | None:
    """The :class:`PackageFacts` of one package (``None``: unknown). One
    statement; re-read (the confirm calls it under the account lock)."""
    remaining = (
        select(func.coalesce(func.sum(PackageCreditTransaction.units), 0))
        .where(PackageCreditTransaction.package_id == Package.id)
        .correlate(Package)
        .scalar_subquery()
    )
    purchase = (
        select(Charge.id)
        .where(Charge.kind == "package_purchase", Charge.package_id == Package.id)
        .correlate(Package)
        .scalar_subquery()
    )
    row = (
        await session.execute(
            select(
                Package.id,
                Package.billing_account_id,
                Package.student_id,
                BillingAccount.family_id,
                Package.unit,
                Package.units_granted,
                Package.subject_id,
                Package.purchased_on,
                Package.expires_on,
                remaining.label("remaining"),
                purchase.label("purchase_charge_id"),
                _kind_exists("reversal").label("has_reversal"),
                _kind_exists("expire").label("has_expire"),
                _kind_exists("refund").label("has_refund"),
            )
            .join(BillingAccount, BillingAccount.id == Package.billing_account_id)
            .where(Package.id == package_id)
        )
    ).first()
    if row is None:
        return None
    return PackageFacts(
        id=row.id,
        billing_account_id=row.billing_account_id,
        student_id=row.student_id,
        family_id=row.family_id,
        unit=row.unit,
        units_granted=row.units_granted,
        remaining=int(row.remaining),
        subject_id=row.subject_id,
        purchased_on=row.purchased_on,
        expires_on=row.expires_on,
        purchase_charge_id=row.purchase_charge_id,
        has_reversal=bool(row.has_reversal),
        has_expire=bool(row.has_expire),
        has_refund=bool(row.has_refund),
    )


# --- Writes (C2 steps 8-11) ----------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class WithdrawalDraft:
    """A ``withdrawals`` row to insert (exactly one subject)."""

    billing_account_id: uuid.UUID
    student_id: uuid.UUID
    kind: SubjectKind
    subject_id: uuid.UUID
    outcome: WithdrawalOutcome
    amount: Money
    refund_amount: Money
    withdrawn_at: datetime
    reason: str
    preview: Mapping[str, Any] = field(default_factory=dict)


async def insert_withdrawal(session: AsyncSession, draft: WithdrawalDraft) -> uuid.UUID:
    """Insert the withdrawal (``confirmed_by_user_id`` = the Unit of Work's
    actor; audited ``create``). A unique-subject violation is the caller's
    race (it holds the locks, so it can't happen) and propagates. Never
    commits."""
    actor = current_actor_id(session)
    if actor is None:
        raise ValueError("a withdrawal needs the Unit of Work's actor")
    subject = {SUBJECT_COLUMNS[draft.kind]: draft.subject_id}
    row = Withdrawal(
        billing_account_id=draft.billing_account_id,
        student_id=draft.student_id,
        outcome=draft.outcome,
        amount=draft.amount,
        refund_amount=draft.refund_amount,
        withdrawn_at=draft.withdrawn_at,
        reason=draft.reason,
        preview=json.loads(canonical_json(draft.preview)),
        confirmed_by_user_id=actor,
        **subject,
    )
    session.add(row)
    await session.flush()
    return row.id


async def record_package_refund(
    session: AsyncSession, package_id: uuid.UUID, withdrawal_id: uuid.UUID
) -> tuple[uuid.UUID, int]:
    """The ``refund`` units row of a package withdrawal: ``units =
    −remaining`` (S7), read under the caller's account lock. Remaining 0 →
    :class:`PackageNotWithdrawableError` (``exhausted``); a second ``refund``
    (``uq_package_credit_transactions_refund``) →
    :class:`PackageNotWithdrawableError` (``withdrawn``). Returns the row id
    and the units refunded. Never commits."""
    remaining = int(
        await session.scalar(
            select(func.coalesce(func.sum(PackageCreditTransaction.units), 0)).where(
                PackageCreditTransaction.package_id == package_id
            )
        )
        or 0
    )
    if remaining <= 0:
        raise PackageNotWithdrawableError("exhausted")
    row = PackageCreditTransaction(
        package_id=package_id,
        kind="refund",
        units=-remaining,
        withdrawal_id=withdrawal_id,
    )
    try:
        async with session.begin_nested():
            session.add(row)
    except IntegrityError as exc:  # pragma: no cover - the lock serializes it
        if PACKAGE_REFUND_KEY not in str(exc.orig):
            raise
        raise PackageNotWithdrawableError("withdrawn") from None
    return row.id, remaining


async def freed_by_adjustments(
    session: AsyncSession,
    adjustment_ids: Collection[uuid.UUID],
    reinserted_ids: Collection[uuid.UUID],
) -> dict[uuid.UUID, Money]:
    """The money these adjustments' E5 releases freed, per payment: Σ the
    released allocations minus the re-inserted ones (two statements, none
    for no adjustment)."""
    if not adjustment_ids:
        return {}
    freed: dict[uuid.UUID, Money] = {}
    rows = await session.execute(
        select(PaymentAllocation.payment_id, PaymentAllocation.amount)
        .join(
            AllocationRelease, AllocationRelease.allocation_id == PaymentAllocation.id
        )
        .where(AllocationRelease.charge_adjustment_id.in_(set(adjustment_ids)))
    )
    for row in rows:
        freed[row.payment_id] = freed.get(row.payment_id, Money.zero()) + Money(
            row.amount
        )
    if reinserted_ids:
        back = await session.execute(
            select(PaymentAllocation.payment_id, PaymentAllocation.amount).where(
                PaymentAllocation.id.in_(set(reinserted_ids))
            )
        )
        for row in back:
            freed[row.payment_id] = freed.get(row.payment_id, Money.zero()) - Money(
                row.amount
            )
    return {k: v for k, v in freed.items() if v.is_positive()}


async def adjustment_ids_by_key(
    session: AsyncSession, source_keys: Collection[str]
) -> dict[str, uuid.UUID]:
    """The adjustments with these ``source_key``s (one statement, none for
    no key)."""
    if not source_keys:
        return {}
    rows = await session.execute(
        select(ChargeAdjustment.source_key, ChargeAdjustment.id).where(
            ChargeAdjustment.source_key.in_(set(source_keys))
        )
    )
    return {row.source_key: row.id for row in rows if row.source_key is not None}


# --- Reads behind the routes (D3) ----------------------------------------------------


@dataclass(frozen=True)
class WithdrawalRow:
    """One withdrawal as stored, with its family and its refund ids."""

    id: uuid.UUID
    billing_account_id: uuid.UUID
    family_id: uuid.UUID
    student_id: uuid.UUID
    group_enrollment_id: uuid.UUID | None
    cohort_enrollment_id: uuid.UUID | None
    package_id: uuid.UUID | None
    outcome: WithdrawalOutcome
    amount: Money
    refund_amount: Money
    withdrawn_at: datetime
    reason: str
    preview: dict[str, Any]
    confirmed_by_user_id: uuid.UUID
    created_at: datetime
    refund_ids: tuple[uuid.UUID, ...] = ()


def _withdrawal_query() -> Select[Any]:
    refunds = (
        select(func.array_agg(Refund.id))
        .where(Refund.withdrawal_id == Withdrawal.id)
        .correlate(Withdrawal)
        .scalar_subquery()
    )
    return select(
        Withdrawal,
        BillingAccount.family_id,
        refunds.label("refund_ids"),
    ).join(BillingAccount, BillingAccount.id == Withdrawal.billing_account_id)


def _withdrawal_row(found: Any) -> WithdrawalRow:
    row: Withdrawal = found[0]
    return WithdrawalRow(
        id=row.id,
        billing_account_id=row.billing_account_id,
        family_id=found.family_id,
        student_id=row.student_id,
        group_enrollment_id=row.group_enrollment_id,
        cohort_enrollment_id=row.cohort_enrollment_id,
        package_id=row.package_id,
        outcome=cast(WithdrawalOutcome, row.outcome),
        amount=Money(row.amount),
        refund_amount=Money(row.refund_amount),
        withdrawn_at=row.withdrawn_at,
        reason=row.reason,
        preview=dict(row.preview),
        confirmed_by_user_id=row.confirmed_by_user_id,
        created_at=row.created_at,
        refund_ids=tuple(sorted(found.refund_ids or ())),
    )


async def get_withdrawal(
    session: AsyncSession, withdrawal_id: uuid.UUID
) -> WithdrawalRow | None:
    """One withdrawal (``None``: unknown). One statement."""
    found = (
        await session.execute(_withdrawal_query().where(Withdrawal.id == withdrawal_id))
    ).first()
    return None if found is None else _withdrawal_row(found)


async def list_withdrawals(
    session: AsyncSession,
    *,
    family_id: uuid.UUID | None = None,
    student_id: uuid.UUID | None = None,
    group_enrollment_id: uuid.UUID | None = None,
    cohort_enrollment_id: uuid.UUID | None = None,
    package_id: uuid.UUID | None = None,
    limit: int,
    offset: int,
) -> tuple[list[WithdrawalRow], int]:
    """A page of withdrawals, newest first (``created_at``, ``id``), with the
    total. One statement (a window count), plus a count past the end."""
    conditions: list[Any] = []
    for column, value in (
        (BillingAccount.family_id, family_id),
        (Withdrawal.student_id, student_id),
        (Withdrawal.group_enrollment_id, group_enrollment_id),
        (Withdrawal.cohort_enrollment_id, cohort_enrollment_id),
        (Withdrawal.package_id, package_id),
    ):
        if value is not None:
            conditions.append(column == value)
    rows = (
        await session.execute(
            _withdrawal_query()
            .add_columns(func.count().over().label("total"))
            .where(*conditions)
            .order_by(Withdrawal.created_at.desc(), Withdrawal.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    if rows:
        return [_withdrawal_row(row) for row in rows], int(rows[0].total)
    total = await session.scalar(
        select(func.count(Withdrawal.id))
        .join(BillingAccount, BillingAccount.id == Withdrawal.billing_account_id)
        .where(*conditions)
    )
    return [], int(total or 0)
