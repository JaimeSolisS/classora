"""The ledger's posting primitives (#90 C1-C6; ADR-0006; data-model §3.1,
§3.9, §3.10, §4.1, §6).

Every function runs in the caller's Unit of Work: it flushes, never
commits, and lets every unrelated error through, so a caller's rollback
removes every row and audit event it wrote. No function here creates a
payment.

**Lock order (data-model §6).** The caller takes its own scheduling or
courses locks first; :func:`lock_billing_account` (the account row ``FOR
UPDATE``) is always the **last** business lock, and the App invariants
(I1, I2) are checked after it.

**Natural keys on audited tables (#90 E4).** The audit guard refuses ORM
``INSERT … ON CONFLICT`` on audited tables, so a natural-key insert is a
check under the account lock, then an ORM insert inside a savepoint
(``begin_nested``). A violation of a **named** natural-key index (only
possible from a posting to another account, which the account lock doesn't
serialize) rolls the savepoint back, with its audit event, and the existing
row is returned with ``created = False``. Any other ``IntegrityError``
propagates.

**Actor and time.** ``posted_by_user_id`` and ``released_by_user_id`` are
the Unit of Work's audit actor (``None`` in a job); instants come from
:mod:`app.core.clock`.
"""

import uuid
from collections.abc import Collection
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.audit import current_actor_id
from app.core.money import Money, money_sum
from app.modules.billing.errors import ChargeNetNegativeError, StudentHasNoFamilyError
from app.modules.billing.models import (
    ACCOUNT_FAMILY_FK,
    ACCOUNT_FAMILY_UNIQUE,
    ADJUSTMENT_SOURCE_KEY,
    CHARGE_NATURAL_KEYS,
    AllocationRelease,
    BillingAccount,
    Charge,
    ChargeAdjustment,
    PaymentAllocation,
)
from app.modules.billing.schemas import (
    AdjustmentDraft,
    ChargeDraft,
    PostedAdjustment,
    PostedCharge,
)
from app.modules.families import service as families


def _violates(error: IntegrityError, names: Collection[str]) -> bool:
    message = str(error.orig)
    return any(name in message for name in names)


# --- Accounts ------------------------------------------------------------------------


async def _account_of_family(
    session: AsyncSession, family_id: uuid.UUID
) -> uuid.UUID | None:
    found: uuid.UUID | None = await session.scalar(
        select(BillingAccount.id).where(BillingAccount.family_id == family_id)
    )
    return found


async def ensure_billing_account(
    session: AsyncSession, family_id: uuid.UUID
) -> uuid.UUID:
    """The family's billing account id, created on first use with
    ``due_offset_days = 0`` (#90 C1, E4). A deactivated family still gets
    one. An unknown ``family_id`` is a programming error
    (:class:`ValueError`)."""
    existing = await _account_of_family(session, family_id)
    if existing is not None:
        return existing
    account = BillingAccount(family_id=family_id, due_offset_days=0)
    try:
        async with session.begin_nested():
            session.add(account)
    except IntegrityError as exc:
        if _violates(exc, (ACCOUNT_FAMILY_FK,)):
            raise ValueError("unknown family") from None
        if not _violates(exc, (ACCOUNT_FAMILY_UNIQUE,)):
            raise
        # Another transaction created it first (and has committed).
        existing = await _account_of_family(session, family_id)
        if existing is None:  # pragma: no cover - the violation means it exists
            raise
        return existing
    return account.id


async def lock_billing_account(
    session: AsyncSession, account_id: uuid.UUID
) -> BillingAccount:
    """The account row ``FOR UPDATE`` (#90 C2): always the last lock of a
    financial mutation (data-model §6). An unknown id is a
    :class:`ValueError`."""
    account = await session.scalar(
        select(BillingAccount)
        .where(BillingAccount.id == account_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if account is None:
        raise ValueError("unknown billing account")
    return account


# --- Charges -------------------------------------------------------------------------


def _natural_key_filter(draft: ChargeDraft) -> list[Any] | None:
    """The ``WHERE`` of the draft's natural key (J4), or ``None`` (a manual
    charge has none)."""
    if draft.kind == "session":
        return [
            Charge.kind == "session",
            Charge.class_session_id == draft.class_session_id,
            Charge.student_id == draft.student_id,
        ]
    if draft.kind == "group_enrollment":
        return [
            Charge.kind == "group_enrollment",
            Charge.group_enrollment_id == draft.group_enrollment_id,
        ]
    if draft.kind == "cohort_enrollment":
        return [
            Charge.kind == "cohort_enrollment",
            Charge.cohort_enrollment_id == draft.cohort_enrollment_id,
        ]
    return None


async def _posted_charge(session: AsyncSession, key: list[Any]) -> PostedCharge | None:
    row = (
        await session.execute(select(Charge.id, Charge.billing_account_id).where(*key))
    ).first()
    if row is None:
        return None
    return PostedCharge(
        charge_id=row.id, billing_account_id=row.billing_account_id, created=False
    )


async def post_charge(session: AsyncSession, draft: ChargeDraft) -> PostedCharge:
    """Post one charge (#90 C3).

    The student's family (``families.service.family_of_student``; none →
    :class:`StudentHasNoFamilyError`, nothing written) → its account
    (:func:`ensure_billing_account`) → the account row ``FOR UPDATE`` → the
    natural key: already posted → ``created = False``, nothing written →
    insert in a savepoint (E4). The charge keeps this account even if the
    student later moves to another family.
    """
    family = await families.family_of_student(session, draft.student_id)
    if family is None:
        raise StudentHasNoFamilyError(draft.student_id)
    account_id = await ensure_billing_account(session, family.id)
    account = await lock_billing_account(session, account_id)
    key = _natural_key_filter(draft)
    if key is not None:
        existing = await _posted_charge(session, key)
        if existing is not None:
            return existing
    due_date = draft.due_date_base
    if draft.apply_family_offset:
        due_date += timedelta(days=account.due_offset_days)
    charge = Charge(
        billing_account_id=account.id,
        student_id=draft.student_id,
        kind=draft.kind,
        class_session_id=draft.class_session_id,
        group_enrollment_id=draft.group_enrollment_id,
        cohort_enrollment_id=draft.cohort_enrollment_id,
        service_date=draft.service_date,
        price_version_id=draft.price_version_id,
        education_level_id=draft.education_level_id,
        scheduled_minutes=draft.scheduled_minutes,
        list_amount=draft.list_amount,
        discount_amount=draft.discount_amount,
        amount=draft.amount,
        due_date=due_date,
        description=draft.description,
        posted_at=clock.now(),
        posted_by_user_id=current_actor_id(session),
    )
    try:
        async with session.begin_nested():
            session.add(charge)
    except IntegrityError as exc:
        if key is None or not _violates(exc, CHARGE_NATURAL_KEYS):
            raise
        existing = await _posted_charge(session, key)
        if existing is None:  # pragma: no cover - the violation means it exists
            raise
        return existing
    return PostedCharge(
        charge_id=charge.id, billing_account_id=account.id, created=True
    )


# --- Adjustments ---------------------------------------------------------------------


async def _adjustment_with_key(
    session: AsyncSession, source_key: str
) -> uuid.UUID | None:
    found: uuid.UUID | None = await session.scalar(
        select(ChargeAdjustment.id).where(ChargeAdjustment.source_key == source_key)
    )
    return found


async def _net(session: AsyncSession, charge: Any) -> Money:
    adjustments: Money | None = await session.scalar(
        select(money_sum(ChargeAdjustment.amount)).where(
            ChargeAdjustment.charge_id == charge.id
        )
    )
    return Money(charge.amount) + (Money.zero() if adjustments is None else adjustments)


async def _valid_allocations(
    session: AsyncSession, charge_id: uuid.UUID
) -> list[PaymentAllocation]:
    """The charge's valid allocations (no release), newest first."""
    released = select(AllocationRelease.id).where(
        AllocationRelease.allocation_id == PaymentAllocation.id
    )
    return list(
        await session.scalars(
            select(PaymentAllocation)
            .where(PaymentAllocation.charge_id == charge_id, ~released.exists())
            .order_by(PaymentAllocation.created_at.desc(), PaymentAllocation.id.desc())
        )
    )


async def _release_excess(
    session: AsyncSession,
    *,
    adjustment: ChargeAdjustment,
    new_net: Money,
) -> tuple[tuple[uuid.UUID, ...], uuid.UUID | None]:
    """E5: when the new net is below the valid allocations, release the
    newest ones until the excess is covered, and re-insert the part of the
    last one that wasn't needed. Afterwards Σ valid allocations =
    min(before, new net) (I2); the freed money is its payment's account
    credit."""
    allocations = await _valid_allocations(session, adjustment.charge_id)
    excess = Money.total(a.amount for a in allocations) - new_net
    if not excess.is_positive():
        return (), None
    actor = current_actor_id(session)
    released: list[uuid.UUID] = []
    freed = Money.zero()
    last: PaymentAllocation | None = None
    for allocation in allocations:
        session.add(
            AllocationRelease(
                allocation_id=allocation.id,
                charge_adjustment_id=adjustment.id,
                released_by_user_id=actor,
            )
        )
        released.append(allocation.id)
        freed += allocation.amount
        last = allocation
        if freed >= excess:
            break
    reinserted: PaymentAllocation | None = None
    if last is not None and freed > excess:
        reinserted = PaymentAllocation(
            payment_id=last.payment_id,
            charge_id=last.charge_id,
            billing_account_id=last.billing_account_id,
            amount=freed - excess,
            created_by_user_id=None,
        )
        session.add(reinserted)
    await session.flush()
    return tuple(released), None if reinserted is None else reinserted.id


async def post_adjustment(
    session: AsyncSession, draft: AdjustmentDraft
) -> PostedAdjustment:
    """Post one signed adjustment against a charge (#90 C4).

    The charge's account (no lock; an unknown charge is a
    :class:`ValueError`) → the account row ``FOR UPDATE`` → the
    ``source_key``: already posted → ``created = False``, nothing written →
    the new net under the lock: below ``0.00`` →
    :class:`ChargeNetNegativeError` (I1, nothing written) → insert in a
    savepoint → release allocations above the new net (E5). The adjustment
    copies the charge's account and student (the composite foreign key is
    the backstop, I5).
    """
    charge = (
        await session.execute(
            select(
                Charge.id, Charge.billing_account_id, Charge.student_id, Charge.amount
            ).where(Charge.id == draft.charge_id)
        )
    ).first()
    if charge is None:
        raise ValueError("unknown charge")
    await lock_billing_account(session, charge.billing_account_id)
    if draft.source_key is not None:
        existing = await _adjustment_with_key(session, draft.source_key)
        if existing is not None:
            return PostedAdjustment(adjustment_id=existing, created=False)
    net = await _net(session, charge)
    new_net = net + draft.amount
    if new_net.is_negative():
        raise ChargeNetNegativeError(charge.id, net, draft.amount)
    adjustment = ChargeAdjustment(
        charge_id=charge.id,
        billing_account_id=charge.billing_account_id,
        student_id=charge.student_id,
        kind=draft.kind,
        amount=draft.amount,
        reason=draft.reason,
        source_key=draft.source_key,
        posted_at=clock.now(),
        posted_by_user_id=current_actor_id(session),
    )
    try:
        async with session.begin_nested():
            session.add(adjustment)
    except IntegrityError as exc:
        if draft.source_key is None or not _violates(exc, (ADJUSTMENT_SOURCE_KEY,)):
            raise
        existing = await _adjustment_with_key(session, draft.source_key)
        if existing is None:  # pragma: no cover - the violation means it exists
            raise
        return PostedAdjustment(adjustment_id=existing, created=False)
    if not draft.amount.is_negative():
        return PostedAdjustment(adjustment_id=adjustment.id, created=True)
    released, reinserted = await _release_excess(
        session, adjustment=adjustment, new_net=new_net
    )
    return PostedAdjustment(
        adjustment_id=adjustment.id,
        created=True,
        released_allocation_ids=released,
        reinserted_allocation_id=reinserted,
    )
