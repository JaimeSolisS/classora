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
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Final, cast

from sqlalchemy import Select, func, select, tuple_, type_coerce
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.audit import current_actor_id
from app.core.money import Money, MoneyType, money_sum
from app.modules.billing.discounts import DiscountKind, DiscountLine
from app.modules.billing.errors import (
    ChargeNetNegativeError,
    SessionSettledByPackageError,
    StudentHasNoFamilyError,
)
from app.modules.billing.models import (
    ACCOUNT_FAMILY_FK,
    ACCOUNT_FAMILY_UNIQUE,
    ADJUSTMENT_SOURCE_KEY,
    CHARGE_NATURAL_KEYS,
    ENROLLMENT_CHARGE_KINDS,
    AllocationRelease,
    BillingAccount,
    Charge,
    ChargeAdjustment,
    ChargeDiscount,
    PackageCreditTransaction,
    PaymentAllocation,
)
from app.modules.billing.schemas import (
    AdjustmentDraft,
    ChargeDraft,
    PostedAdjustment,
    PostedCharge,
)
from app.modules.families import service as families

ENROLLMENT_REMOVAL: Final = "enrollment_removal"


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


async def share_lock_billing_account(
    session: AsyncSession, account_id: uuid.UUID
) -> uuid.UUID | None:
    """The account row ``FOR SHARE`` (#97 F4, N4 (a)): it waits for (and
    then blocks) every writer that takes :func:`lock_billing_account`, so
    the open amounts and effective due dates read after it are the committed
    ones. Readers that take it don't block each other. Returns the account's
    family id, or ``None`` for an unknown account (one statement)."""
    family_id: uuid.UUID | None = await session.scalar(
        select(BillingAccount.family_id)
        .where(BillingAccount.id == account_id)
        .with_for_update(read=True)
    )
    return family_id


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
    if draft.kind == "tuition_period":
        return [
            Charge.kind == "tuition_period",
            Charge.tuition_plan_id == draft.tuition_plan_id,
            Charge.service_date == draft.service_date,
        ]
    if draft.kind == "package_purchase":
        return [
            Charge.kind == "package_purchase",
            Charge.package_id == draft.package_id,
        ]
    return None


async def _consumed(
    session: AsyncSession, class_session_id: uuid.UUID, student_id: uuid.UUID
) -> bool:
    """Whether a package ``consume`` row exists for the key (#94 D3, I9;
    any package, any account)."""
    found = await session.scalar(
        select(PackageCreditTransaction.id)
        .where(
            PackageCreditTransaction.kind == "consume",
            PackageCreditTransaction.class_session_id == class_session_id,
            PackageCreditTransaction.student_id == student_id,
        )
        .limit(1)
    )
    return found is not None


def session_charge_key_columns() -> Select[uuid.UUID | None, uuid.UUID]:
    """``SELECT class_session_id, student_id FROM charges WHERE kind =
    'session'`` (the session natural key, ``uq_charges_session_student``),
    **not executed**: the session sweeper anti-joins it (#91 C4)."""
    return select(Charge.class_session_id, Charge.student_id).where(
        Charge.kind == "session"
    )


def _net_column(charge: Any = Charge) -> Any:
    """``amount + Σ adjustments`` of a charge row, as a correlated scalar
    subquery (not executed)."""
    adjustments = (
        select(money_sum(ChargeAdjustment.amount))
        .where(ChargeAdjustment.charge_id == charge.id)
        .correlate(charge)
        .scalar_subquery()
    )
    return type_coerce(charge.amount + adjustments, MoneyType()).label("net")


def enrollment_fee_columns() -> Select[Any]:
    """``SELECT id, kind, enrollment_id, student_id, net, posted_at FROM
    charges WHERE kind IN ('group_enrollment', 'cohort_enrollment')``
    (``enrollment_id`` = the group or cohort enrollment id; ``net`` = amount
    + Σ adjustments), **not executed**: the #92 enrollment sweeper anti-joins
    it, and its classification reads the fees of a period and of the
    student's other periods of the same group or cohort (J5); #101 picks a
    withdrawal's fee line from it (the latest ``posted_at``)."""
    return select(
        Charge.id.label("charge_id"),
        Charge.kind,
        func.coalesce(Charge.group_enrollment_id, Charge.cohort_enrollment_id).label(
            "enrollment_id"
        ),
        Charge.student_id,
        _net_column(),
        Charge.posted_at,
    ).where(Charge.kind.in_(ENROLLMENT_CHARGE_KINDS))


@dataclass(frozen=True)
class SessionChargeState:
    """A session charge with its net and the sum of its ``enrollment_removal``
    adjustments (#92 J4)."""

    charge_id: uuid.UUID
    class_session_id: uuid.UUID
    student_id: uuid.UUID
    net: Money
    removals: Money


async def session_charge_states(
    session: AsyncSession, keys: Collection[tuple[uuid.UUID, uuid.UUID]]
) -> dict[tuple[uuid.UUID, uuid.UUID], SessionChargeState]:
    """The ``session`` charges of these ``(class_session_id, student_id)``
    keys with their net and Σ ``enrollment_removal`` (#92 J4: the removal
    credits and the re-enrollment restorations). Keys without a charge are
    left out. One statement (none for no keys); no lock: the caller posts
    under the account lock, which :func:`post_adjustment` takes."""
    if not keys:
        return {}
    removals = (
        select(money_sum(ChargeAdjustment.amount))
        .where(
            ChargeAdjustment.charge_id == Charge.id,
            ChargeAdjustment.kind == ENROLLMENT_REMOVAL,
        )
        .correlate(Charge)
        .scalar_subquery()
    )
    rows = await session.execute(
        select(
            Charge.id,
            Charge.class_session_id,
            Charge.student_id,
            _net_column(),
            removals.label("removals"),
        ).where(
            Charge.kind == "session",
            tuple_(Charge.class_session_id, Charge.student_id).in_(list(set(keys))),
        )
    )
    found: dict[tuple[uuid.UUID, uuid.UUID], SessionChargeState] = {}
    for row in rows:
        assert row.class_session_id is not None  # noqa: S101 - a session charge
        found[(row.class_session_id, row.student_id)] = SessionChargeState(
            charge_id=row.id,
            class_session_id=row.class_session_id,
            student_id=row.student_id,
            net=Money(row.net),
            removals=Money(row.removals),
        )
    return found


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
    (:func:`ensure_billing_account`) → the account row ``FOR UPDATE`` → for
    a ``session`` charge, a package ``consume`` row for the key →
    :class:`SessionSettledByPackageError`, nothing written (#94 D3: I9 is
    checked on both sides under the account lock) → the
    natural key: already posted → ``created = False``, nothing written →
    insert in a savepoint (E4) → the draft's discount lines as
    ``charge_discounts`` rows (#95 B5; only for a charge created now). The
    charge keeps this account even if the student later moves to another
    family.
    """
    family = await families.family_of_student(session, draft.student_id)
    if family is None:
        raise StudentHasNoFamilyError(draft.student_id)
    account_id = await ensure_billing_account(session, family.id)
    account = await lock_billing_account(session, account_id)
    if draft.kind == "session":
        assert draft.class_session_id is not None  # noqa: S101 - a session draft
        if await _consumed(session, draft.class_session_id, draft.student_id):
            raise SessionSettledByPackageError(draft.class_session_id, draft.student_id)
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
        tuition_plan_id=draft.tuition_plan_id,
        package_id=draft.package_id,
        service_date=draft.service_date,
        service_period_end=draft.service_period_end,
        price_version_id=draft.price_version_id,
        custom_price_id=draft.custom_price_id,
        education_level_id=draft.education_level_id,
        scheduled_minutes=draft.scheduled_minutes,
        list_amount=draft.list_amount,
        discount_amount=draft.discount_amount,
        amount=draft.amount,
        due_date=due_date,
        description=draft.description,
        proration_remaining=None if draft.proration is None else draft.proration[0],
        proration_total=None if draft.proration is None else draft.proration[1],
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
    if draft.discounts:
        # #95 B5: the snapshot of the applied discounts, only for a charge
        # created now (a repeat posts nothing), under the account lock.
        session.add_all(
            ChargeDiscount(
                charge_id=charge.id,
                discount_id=line.discount_id,
                position=line.position,
                kind=line.kind,
                name=line.name,
                percent=line.percent,
                fixed_amount=line.fixed_amount,
                amount=line.amount,
            )
            for line in draft.discounts
        )
        await session.flush()
    return PostedCharge(
        charge_id=charge.id, billing_account_id=account.id, created=True
    )


async def charge_discount_lines(
    session: AsyncSession, charge_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, list[DiscountLine]]:
    """The discount lines of each charge, in position order (#95 B6): the
    snapshot taken at posting (kind, name, percent or fixed amount as they
    were then). Charges without lines are left out. One statement (none for
    no ids)."""
    if not charge_ids:
        return {}
    rows = await session.scalars(
        select(ChargeDiscount)
        .where(ChargeDiscount.charge_id.in_(set(charge_ids)))
        .order_by(ChargeDiscount.charge_id, ChargeDiscount.position)
    )
    found: dict[uuid.UUID, list[DiscountLine]] = {}
    for row in rows:
        found.setdefault(row.charge_id, []).append(
            DiscountLine(
                discount_id=row.discount_id,
                position=row.position,
                kind=cast(DiscountKind, row.kind),
                name=row.name,
                percent=row.percent,
                fixed_amount=row.fixed_amount,
                amount=row.amount,
            )
        )
    return found


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
