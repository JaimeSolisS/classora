"""Recording payments and allocating them (#96 B; spec §6, W4.3, W4.4;
ADR-0006 E5, E6, E8, E12; data-model §3.12, §3.14, §4.1, §6).

**Primitives.** :func:`record_payment` records an external payment and
allocates it in the same transaction; :func:`allocate_account_credit`
allocates account credit later. Both run in the caller's Unit of Work,
flush and never commit, and need the Unit of Work's audit actor (the
recording staff member: ``recorded_by_user_id`` is required, so a job can't
call them). Every row they write is audited (``create``).

**Lock order (data-model §6).** The API claims its idempotency key first
(#88); then :func:`~app.modules.billing.ledger.lock_billing_account` (the
account row ``FOR UPDATE``, the last business lock); a recorded payment's
receipt (#98) takes its ``number_series`` row after it. Everything an
allocation depends on is read **after** the lock, so a concurrent
payment, credit allocation or adjustment
(:func:`~app.modules.billing.ledger.post_adjustment`, which takes the same
lock) is either fully seen or waits:

- **I2** (per charge, Σ valid allocations ≤ net): each line is at most the
  charge's ``open`` read under the lock;
- **I3** (per payment, Σ valid allocations + Σ refunds ≤ amount, and no
  allocation on a reversed payment): a new payment's lines add up to at
  most its amount; credit is drawn only from the remainders of payments
  without a reversal (:func:`~app.modules.billing.balances.payment_remainders`),
  read under the lock.

**Allocation modes** (M2-M4; the pure :func:`plan_allocations`):

- ``oldest_first``: the charges with ``open > 0`` ordered by **effective
  due date**, then ``service_date``, ``posted_at``, ``id`` (overdue charges
  first; every student on the account, a moved-out student included), each
  filled up to its ``open`` until the money runs out;
- ``explicit``: the given lines, all or nothing: a charge not on the
  account → ``422 allocation_charge_not_found``; a line above the charge's
  ``open`` → ``409 allocation_exceeds_open``; lines above the available
  money → ``409 insufficient_account_credit`` (a payment's own lines are
  checked against its amount when the draft is built);
- ``none``: nothing allocated.

What a payment doesn't allocate **is** account credit (E5): no row is
written for it. A credit allocation draws from the payments with a
remainder, **oldest payment first** (``received_on``, ``created_at``,
``id``), so one target line can become several allocation rows (M4).

There is no "un-allocate": an allocation ends only by a release (a #100
reversal or a negative adjustment, #90 E5).
"""

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select, type_coerce
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import current_actor_id
from app.core.money import Money, MoneyType
from app.core.pagination import OffsetParams
from app.modules.billing.balances import (
    _refunded,
    _released,
    _reversed,
    _valid_allocated,
    account_credit,
    charge_balances,
    payment_remainder_column,
    payment_remainders,
)
from app.modules.billing.errors import (
    AllocationChargeNotFoundError,
    AllocationExceedsOpenError,
    InsufficientAccountCreditError,
    NothingToAllocateError,
)
from app.modules.billing.ledger import lock_billing_account
from app.modules.billing.models import (
    Charge,
    Payment,
    PaymentAllocation,
    PaymentMethod,
    Receipt,
)
from app.modules.billing.receipts import issue_receipt
from app.modules.billing.schemas import (
    AllocationRequest,
    ChargeBalance,
    CreditAllocation,
    CreditAllocationRequest,
    PaymentAllocationRow,
    PaymentDraft,
    PaymentMethodRow,
    PaymentPage,
    PaymentRow,
    PlannedAllocation,
    RecordedAllocation,
    RecordedPayment,
)

# --- The pure planner (B3) -----------------------------------------------------------


def _oldest_first_key(charge: ChargeBalance) -> tuple[Any, ...]:
    return (
        charge.effective_due_date,
        charge.service_date,
        charge.posted_at,
        charge.charge_id,
    )


def plan_allocations(
    charges: Sequence[ChargeBalance], available: Money, request: AllocationRequest
) -> list[PlannedAllocation]:
    """The allocation lines for ``request`` (#96 B3; no database).

    ``charges`` are the account's charges with their ``open`` amounts (read
    under the account lock); ``available`` is the money to allocate.
    ``oldest_first`` fills the open charges in the M2 order up to
    ``available``; ``explicit`` returns the lines in request order after
    checking them (:class:`AllocationChargeNotFoundError`,
    :class:`AllocationExceedsOpenError` on the first line above its charge's
    ``open``, :class:`InsufficientAccountCreditError` when they add up to
    more than ``available``); ``none`` returns ``[]``."""
    if request.mode == "none":
        return []
    if request.mode == "oldest_first":
        remaining = available
        planned: list[PlannedAllocation] = []
        for charge in sorted(
            (c for c in charges if c.open.is_positive()), key=_oldest_first_key
        ):
            if not remaining.is_positive():
                break
            amount = min(charge.open, remaining)
            planned.append(PlannedAllocation(charge_id=charge.charge_id, amount=amount))
            remaining -= amount
        return planned
    by_id = {charge.charge_id: charge for charge in charges}
    missing = [
        index for index, line in enumerate(request.lines) if line.charge_id not in by_id
    ]
    if missing:
        raise AllocationChargeNotFoundError(missing)
    for line in request.lines:
        open_amount = by_id[line.charge_id].open
        if line.amount > open_amount:
            raise AllocationExceedsOpenError(line.charge_id, open_amount)
    if request.total > available:
        raise InsufficientAccountCreditError(available)
    return [
        PlannedAllocation(charge_id=line.charge_id, amount=line.amount)
        for line in request.lines
    ]


def split_across_payments(
    planned: Sequence[PlannedAllocation],
    sources: Sequence[tuple[uuid.UUID, Money]],
) -> list[tuple[uuid.UUID, PlannedAllocation]]:
    """``(payment_id, line)`` rows for a credit allocation (#96 M4; no
    database): each planned line in order, drawn from ``sources``
    (``(payment_id, remainder)``, oldest first), so a line can be split
    across payments. The lines must add up to at most Σ remainders
    (:class:`ValueError` otherwise: a programming error)."""
    rows: list[tuple[uuid.UUID, PlannedAllocation]] = []
    pending = [[payment_id, remainder] for payment_id, remainder in sources]
    source = 0
    for line in planned:
        needed = line.amount
        while needed.is_positive():
            if source >= len(pending):
                raise ValueError("the lines add up to more than the account credit")
            payment_id, remainder = pending[source]
            assert isinstance(payment_id, uuid.UUID)  # noqa: S101 - typing
            assert isinstance(remainder, Money)  # noqa: S101 - typing
            if not remainder.is_positive():
                source += 1
                continue
            amount = min(needed, remainder)
            rows.append(
                (payment_id, PlannedAllocation(charge_id=line.charge_id, amount=amount))
            )
            pending[source][1] = remainder - amount
            needed -= amount
    return rows


# --- Primitives (B1, B2) -------------------------------------------------------------


def _actor(session: AsyncSession) -> uuid.UUID:
    actor = current_actor_id(session)
    if actor is None:
        raise ValueError("recording a payment needs the Unit of Work's actor")
    return actor


def _allocation_rows(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    actor: uuid.UUID,
    rows: Sequence[tuple[uuid.UUID, PlannedAllocation]],
) -> list[PaymentAllocation]:
    allocations = [
        PaymentAllocation(
            payment_id=payment_id,
            charge_id=line.charge_id,
            billing_account_id=account_id,
            amount=line.amount,
            created_by_user_id=actor,
        )
        for payment_id, line in rows
    ]
    session.add_all(allocations)
    return allocations


def _recorded(
    allocations: Sequence[PaymentAllocation], students: dict[uuid.UUID, uuid.UUID]
) -> tuple[RecordedAllocation, ...]:
    return tuple(
        RecordedAllocation(
            allocation_id=row.id,
            payment_id=row.payment_id,
            charge_id=row.charge_id,
            student_id=students[row.charge_id],
            amount=row.amount,
        )
        for row in allocations
    )


async def record_payment(session: AsyncSession, draft: PaymentDraft) -> RecordedPayment:
    """Record a payment and allocate it (#96 B1).

    The account row ``FOR UPDATE`` → the account's charges with their
    ``open`` amounts (under the lock) → the plan (:func:`plan_allocations`;
    errors raise before any write) → the ``payments`` row
    (``recorded_by_user_id`` = the actor) and its ``payment_allocations``
    rows → flush → the account credit afterwards → the receipt
    (:func:`~app.modules.billing.receipts.issue_receipt`, #98 C1: its
    ``number_series`` row is the last lock). The part not allocated is
    account credit. Never commits. An unknown account is a
    :class:`ValueError`."""
    actor = _actor(session)
    await lock_billing_account(session, draft.billing_account_id)
    charges = await charge_balances(session, draft.billing_account_id)
    planned = plan_allocations(charges, draft.amount, draft.allocation)
    payment = Payment(
        billing_account_id=draft.billing_account_id,
        amount=draft.amount,
        received_on=draft.received_on,
        payment_method_code=draft.payment_method_code,
        reference=draft.reference,
        payer_name=draft.payer_name,
        note=draft.note,
        recorded_by_user_id=actor,
    )
    session.add(payment)
    await session.flush()
    allocations = _allocation_rows(
        session,
        account_id=draft.billing_account_id,
        actor=actor,
        rows=[(payment.id, line) for line in planned],
    )
    await session.flush()
    allocated = Money.total(line.amount for line in planned)
    students = {c.charge_id: c.student_id for c in charges}
    credit_after = await account_credit(session, draft.billing_account_id)
    # Last (#98 C1, P4): the receipt's number_series row is the last lock.
    receipt = await issue_receipt(session, payment.id)
    return RecordedPayment(
        payment_id=payment.id,
        allocations=_recorded(allocations, students),
        unallocated=draft.amount - allocated,
        account_credit_after=credit_after,
        receipt_id=receipt.receipt_id,
        receipt_number=receipt.number,
    )


async def allocate_account_credit(
    session: AsyncSession, request: CreditAllocationRequest
) -> CreditAllocation:
    """Allocate account credit to open charges (#96 B2, M4).

    The account row ``FOR UPDATE`` → the charges and the payment remainders
    (under the lock) → no credit → :class:`NothingToAllocateError` → the
    plan: ``oldest_first`` up to ``amount`` (default: the whole credit;
    more than the credit → :class:`InsufficientAccountCreditError`), no
    open charge → :class:`NothingToAllocateError`; ``explicit`` checked by
    :func:`plan_allocations` → each line split across the payments, oldest
    first → the rows (``created_by_user_id`` = the actor) → flush. Never
    commits; nothing is written on an error."""
    actor = _actor(session)
    account_id = request.billing_account_id
    await lock_billing_account(session, account_id)
    charges = await charge_balances(session, account_id)
    sources = await payment_remainders(session, account_id)
    credit = Money.total(remainder for _, remainder in sources)
    if not credit.is_positive():
        raise NothingToAllocateError()
    available = credit
    if request.amount is not None:
        if request.amount > credit:
            raise InsufficientAccountCreditError(credit)
        available = request.amount
    planned = plan_allocations(charges, available, request.allocation)
    if not planned:
        raise NothingToAllocateError()
    allocations = _allocation_rows(
        session,
        account_id=account_id,
        actor=actor,
        rows=split_across_payments(planned, sources),
    )
    await session.flush()
    allocated = Money.total(line.amount for line in planned)
    students = {c.charge_id: c.student_id for c in charges}
    return CreditAllocation(
        allocations=_recorded(allocations, students),
        allocated_total=allocated,
        account_credit_after=credit - allocated,
    )


# --- Payment methods (B6) ------------------------------------------------------------


async def payment_methods_list(session: AsyncSession) -> list[PaymentMethodRow]:
    """Every payment method, by ``code`` (one statement, no lock)."""
    rows = await session.execute(
        select(PaymentMethod.code, PaymentMethod.is_active).order_by(PaymentMethod.code)
    )
    return [PaymentMethodRow(code=row.code, is_active=row.is_active) for row in rows]


async def active_payment_method(session: AsyncSession, code: str) -> bool:
    """Whether ``code`` is an active payment method (one statement, no
    lock: the foreign key ``RESTRICT`` and M8-04's edit path cover a
    concurrent change)."""
    found = await session.scalar(
        select(PaymentMethod.is_active).where(PaymentMethod.code == code)
    )
    return bool(found)


# --- Payment reads (C6) --------------------------------------------------------------


def _payment_columns() -> Any:
    allocated = type_coerce(
        _valid_allocated(PaymentAllocation.payment_id == Payment.id), MoneyType()
    )
    refunded = type_coerce(_refunded(), MoneyType())
    return select(
        Payment.id,
        Payment.amount,
        Payment.received_on,
        Payment.payment_method_code,
        Payment.reference,
        Payment.payer_name,
        Payment.note,
        Payment.recorded_by_user_id,
        Payment.created_at,
        allocated.label("allocated_amount"),
        refunded.label("refunded_amount"),
        type_coerce(payment_remainder_column(), MoneyType()).label("unallocated"),
        _reversed().label("is_reversed"),
        Receipt.id.label("receipt_id"),
        Receipt.number.label("receipt_number"),
    ).outerjoin_from(Payment, Receipt, Receipt.payment_id == Payment.id)


def _payment_row(row: Any) -> PaymentRow:
    return PaymentRow(
        id=row.id,
        amount=row.amount,
        received_on=row.received_on,
        payment_method_code=row.payment_method_code,
        reference=row.reference,
        payer_name=row.payer_name,
        note=row.note,
        recorded_by_user_id=row.recorded_by_user_id,
        recorded_at=row.created_at,
        allocated_amount=row.allocated_amount,
        refunded_amount=row.refunded_amount,
        unallocated_amount=row.unallocated,
        is_reversed=bool(row.is_reversed),
        receipt_id=row.receipt_id,
        receipt_number=row.receipt_number,
    )


async def list_payments(
    session: AsyncSession, account_id: uuid.UUID, *, page: OffsetParams
) -> PaymentPage:
    """A page of the account's payments, newest ``received_on`` first (then
    ``created_at``, ``id``), with their derived amounts (#96 C6). One
    statement with a window total, one more for the total when the page is
    past the end. No lock."""
    rows = (
        await session.execute(
            _payment_columns()
            .add_columns(func.count().over().label("total"))
            .where(Payment.billing_account_id == account_id)
            .order_by(
                Payment.received_on.desc(), Payment.created_at.desc(), Payment.id.desc()
            )
            .limit(page.limit)
            .offset(page.offset)
        )
    ).all()
    if rows:
        total = int(rows[0].total)
    elif page.offset == 0:
        total = 0
    else:
        total = int(
            await session.scalar(
                select(func.count())
                .select_from(Payment)
                .where(Payment.billing_account_id == account_id)
            )
            or 0
        )
    return PaymentPage(items=tuple(_payment_row(row) for row in rows), total=total)


async def get_payment(
    session: AsyncSession, account_id: uuid.UUID, payment_id: uuid.UUID
) -> tuple[PaymentRow, list[PaymentAllocationRow]] | None:
    """One payment of the account with all its allocations, oldest first
    (released ones with ``is_valid = False``), or ``None`` when the account
    has no such payment (#96 C6). Two statements, no lock."""
    row = (
        await session.execute(
            _payment_columns().where(
                Payment.id == payment_id, Payment.billing_account_id == account_id
            )
        )
    ).first()
    if row is None:
        return None
    allocations = await session.execute(
        select(
            PaymentAllocation.id,
            PaymentAllocation.charge_id,
            Charge.student_id,
            PaymentAllocation.amount,
            PaymentAllocation.created_at,
            PaymentAllocation.created_by_user_id,
            (~_released(PaymentAllocation.id)).label("is_valid"),
        )
        .join(Charge, Charge.id == PaymentAllocation.charge_id)
        .where(PaymentAllocation.payment_id == payment_id)
        .order_by(PaymentAllocation.created_at, PaymentAllocation.id)
    )
    return _payment_row(row), [
        PaymentAllocationRow(
            id=a.id,
            charge_id=a.charge_id,
            student_id=a.student_id,
            amount=a.amount,
            created_at=a.created_at,
            created_by_user_id=a.created_by_user_id,
            is_valid=bool(a.is_valid),
        )
        for a in allocations
    ]
