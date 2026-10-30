"""Corrections, reversals and refunds (#100 B; spec §6 "Refunds, reversals
and corrections", §9, W4.4, W4.5; ADR-0006 E5, E6, E8, E10, E13; ADR-0007
D10; data-model §3.10, §3.13, §3.15, §3.16, §3.20, §4.1, §6).

A mistake is corrected with **new rows**, never by editing a posted one:
the original charge, payment, allocation, receipt and package rows stay
exactly as they were (every ledger table is append-only, §5.1).

**Primitives** (exported through ``billing.service``). Each runs in the
caller's Unit of Work, flushes and never commits, needs the Unit of Work's
audit actor (a staff member; no job calls them) and audits every row it
writes (``create``):

- :func:`post_manual_charge` (R7): a ``manual`` charge for a current member
  of the family, with the staff-entered due date as is (no family offset,
  E11), no discount, no price, and no billing floor (the floor gates
  automatic charges only).
- :func:`post_manual_adjustment` (R3, R6): a signed ``manual`` adjustment
  through :func:`~app.modules.billing.ledger.post_adjustment` (I1 → ``409
  charge_net_negative``; a credit releases the newest allocations above the
  new net, E5). A ``package_purchase`` charge is refused (``409
  package_purchase_not_adjustable``): its money and units move together
  through :func:`cancel_package`.
- :func:`reverse_payment` (R5): one ``payment_reversals`` row and one
  ``allocation_releases`` row per **valid** allocation of the payment
  (re-inserted ones included). Refused when the payment is reversed
  already (``409 payment_already_reversed``) or has any refund (``409
  payment_has_refunds``: the ledger sum identity, #90 QA O3).
- :func:`refund_payment` (R4, R5): one ``refunds`` row drawn on one
  payment's unallocated remainder, the expression of #96's credit source
  (:func:`~app.modules.billing.balances.payment_remainder_column`).
  Refused for a reversed payment (``409 payment_reversed``) and above the
  remainder (``409 refund_exceeds_remainder``). Several partial refunds per
  payment are allowed; a refund is never undone in M6. #101's withdrawal
  refunds reuse it.
- :func:`cancel_package` (R6): one ``package_credit_transactions`` row
  ``kind='reversal'`` of ``−remaining`` against the grant (the only writer
  of that kind) and, unless the credit is ``0.00``, one
  ``package_cancellation`` adjustment of ``−credit`` on the purchase charge
  (``source_key = package_cancellation:<package_id>``). Default credit =
  ``net × remaining / units_granted`` (``Money.prorate``); an explicit one
  from ``0.00`` to the net overrides it.

**Lock order (data-model §6).** The route claims its idempotency key first
(#88); then :func:`~app.modules.billing.ledger.lock_billing_account` (the
account row ``FOR UPDATE``, the last business lock). Everything a rule
depends on (reversals, refunds, valid allocations, remainders, package
units, the purchase charge's net) is read **after** the lock, so a
concurrent correction, payment, credit allocation, adjustment or package
consumption on the same account is either fully seen or waits.

**Invariants** after every primitive: I1 (net ≥ 0), I2 (Σ valid
allocations ≤ net), I3 (Σ valid allocations + Σ refunds ≤ amount; a
reversed payment has no valid allocation and no refund is ever recorded
on it; a refunded payment is never reversed) and I8 (package remaining ≥
0, and exactly 0 after a cancellation).

No "un-allocate": a mis-allocated payment is reversed and recorded again.
"""

import uuid
from datetime import date
from typing import Final

from sqlalchemy import Exists, exists, func, select, type_coerce
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import current_actor_id
from app.core.authz import RecordNotFoundError
from app.core.money import Money, MoneyType
from app.modules.billing.balances import (
    _released,
    account_credit,
    charge_balances,
    payment_remainder_column,
)
from app.modules.billing.errors import (
    ChargeNetNegativeError,
    PackageNotCancellableError,
    PackagePurchaseNotAdjustableError,
    PaymentAlreadyReversedError,
    PaymentHasRefundsError,
    PaymentReversedError,
    RefundExceedsRemainderError,
    StudentNotInFamilyError,
)
from app.modules.billing.ledger import (
    ensure_billing_account,
    lock_billing_account,
    post_adjustment,
    post_charge,
)
from app.modules.billing.models import (
    MANUAL,
    PACKAGE_CANCELLATION,
    PACKAGE_REVERSAL_KEY,
    AllocationRelease,
    Charge,
    Package,
    PackageCreditTransaction,
    Payment,
    PaymentAllocation,
    PaymentReversal,
    Refund,
)
from app.modules.billing.schemas import (
    AdjustmentDraft,
    ChargeDraft,
    ManualAdjustmentResult,
    ManualChargeDraft,
    PackageCancellation,
    PaymentReversalResult,
    PostedCharge,
    RecordedRefund,
    RefundDraft,
    ReleasedAllocation,
)
from app.modules.families import service as families

PACKAGE_CANCELLATION_KEY: Final = "package_cancellation:{package_id}"
"""The ``source_key`` of a package cancellation's credit (one per
package)."""


def _actor(session: AsyncSession) -> uuid.UUID:
    actor = current_actor_id(session)
    if actor is None:
        raise ValueError("a correction needs the Unit of Work's actor")
    return actor


# --- Manual charges and adjustments (B1, B2) -----------------------------------------


async def post_manual_charge(
    session: AsyncSession, draft: ManualChargeDraft
) -> PostedCharge:
    """Post a manual charge (#100 B1, R7).

    The student's **current** family must be ``draft.family_id`` (else
    :class:`StudentNotInFamilyError`, nothing written; an archived student
    is allowed) → the family's account (created on first use) →
    :func:`~app.modules.billing.ledger.post_charge` (``kind='manual'``,
    ``list_amount = amount``, ``due_date`` as entered, no family offset, no
    discounts; it takes the account lock). Never commits."""
    _actor(session)
    family = await families.family_of_student(session, draft.student_id)
    if family is None or family.id != draft.family_id:
        raise StudentNotInFamilyError()
    account_id = await ensure_billing_account(session, draft.family_id)
    posted = await post_charge(
        session,
        ChargeDraft(
            student_id=draft.student_id,
            kind=MANUAL,
            service_date=draft.service_date,
            list_amount=draft.amount,
            due_date_base=draft.due_date,
            apply_family_offset=False,
            description=draft.description,
        ),
    )
    if posted.billing_account_id != account_id:
        # The student moved to another family in between: refuse rather
        # than post on another family's account (the caller rolls back).
        raise StudentNotInFamilyError()
    return posted


async def post_manual_adjustment(
    session: AsyncSession, charge_id: uuid.UUID, amount: Money, reason: str
) -> ManualAdjustmentResult:
    """Post a signed manual adjustment on a posted charge (#100 B2).

    The charge (unknown → :class:`ValueError`) → ``package_purchase`` →
    :class:`PackagePurchaseNotAdjustableError` → ``post_adjustment``
    (``kind='manual'``: the account lock, I1 →
    :class:`~app.modules.billing.errors.ChargeNetNegativeError`, the E5
    release) → the charge's net, open amount and status and the account
    credit afterwards. Never commits."""
    _actor(session)
    charge = (
        await session.execute(
            select(Charge.billing_account_id, Charge.kind).where(Charge.id == charge_id)
        )
    ).first()
    if charge is None:
        raise ValueError("unknown charge")
    if charge.kind == "package_purchase":
        raise PackagePurchaseNotAdjustableError()
    posted = await post_adjustment(
        session,
        AdjustmentDraft(charge_id=charge_id, kind=MANUAL, amount=amount, reason=reason),
    )
    [balance] = await charge_balances(
        session, charge.billing_account_id, charge_id=charge_id
    )
    return ManualAdjustmentResult(
        adjustment_id=posted.adjustment_id,
        released_allocation_ids=posted.released_allocation_ids,
        reinserted_allocation_id=posted.reinserted_allocation_id,
        net=balance.net,
        open=balance.open,
        status=balance.status,
        account_credit_after=await account_credit(session, charge.billing_account_id),
    )


# --- Reversals and refunds (B3, B4) --------------------------------------------------


async def _payment_account(session: AsyncSession, payment_id: uuid.UUID) -> uuid.UUID:
    account_id: uuid.UUID | None = await session.scalar(
        select(Payment.billing_account_id).where(Payment.id == payment_id)
    )
    if account_id is None:
        raise ValueError("unknown payment")
    return account_id


def _has_reversal(payment_id: uuid.UUID) -> Exists:
    return exists().where(PaymentReversal.payment_id == payment_id)


async def reverse_payment(
    session: AsyncSession, payment_id: uuid.UUID, reason: str
) -> PaymentReversalResult:
    """Reverse a payment (#100 B3, R5).

    The payment's account (unknown payment → :class:`ValueError`) ``FOR
    UPDATE`` → a reversal exists → :class:`PaymentAlreadyReversedError` →
    any refund → :class:`PaymentHasRefundsError` → the ``payment_reversals``
    row (``reversed_by_user_id`` = the actor) → one ``allocation_releases``
    row per valid allocation (``payment_reversal_id``, ``released_by_user_id``
    = the actor), oldest first, in one flush. A payment without a valid
    allocation is reversible (no release). Never commits."""
    actor = _actor(session)
    account_id = await _payment_account(session, payment_id)
    await lock_billing_account(session, account_id)
    state = (
        await session.execute(
            select(
                _has_reversal(payment_id).label("reversed"),
                exists().where(Refund.payment_id == payment_id).label("refunded"),
            ).where(Payment.id == payment_id)
        )
    ).one()
    if state.reversed:
        raise PaymentAlreadyReversedError()
    if state.refunded:
        raise PaymentHasRefundsError()
    reversal = PaymentReversal(
        payment_id=payment_id,
        billing_account_id=account_id,
        reason=reason,
        reversed_by_user_id=actor,
    )
    session.add(reversal)
    await session.flush()
    valid = (
        await session.execute(
            select(
                PaymentAllocation.id,
                PaymentAllocation.charge_id,
                PaymentAllocation.amount,
            )
            .where(
                PaymentAllocation.payment_id == payment_id,
                ~_released(PaymentAllocation.id),
            )
            .order_by(PaymentAllocation.created_at, PaymentAllocation.id)
        )
    ).all()
    session.add_all(
        AllocationRelease(
            allocation_id=row.id,
            payment_reversal_id=reversal.id,
            released_by_user_id=actor,
        )
        for row in valid
    )
    await session.flush()
    return PaymentReversalResult(
        reversal_id=reversal.id,
        released=tuple(
            ReleasedAllocation(
                allocation_id=row.id, charge_id=row.charge_id, amount=Money(row.amount)
            )
            for row in valid
        ),
        account_credit_after=await account_credit(session, account_id),
    )


async def refund_payment(session: AsyncSession, draft: RefundDraft) -> RecordedRefund:
    """Refund money from one payment's unallocated remainder (#100 B4, R4;
    the #101 entry point).

    The payment's account (unknown payment → :class:`ValueError`) ``FOR
    UPDATE`` → reversed → :class:`PaymentReversedError` → the remainder
    (``amount − Σ valid allocations − Σ refunds``, #96's expression) →
    ``amount > remainder`` → :class:`RefundExceedsRemainderError` → the
    ``refunds`` row (``refunded_by_user_id`` = the actor). The caller checks
    the dates and the method. Never commits."""
    actor = _actor(session)
    account_id = await _payment_account(session, draft.payment_id)
    await lock_billing_account(session, account_id)
    state = (
        await session.execute(
            select(
                _has_reversal(draft.payment_id).label("reversed"),
                type_coerce(payment_remainder_column(), MoneyType()).label("remainder"),
            ).where(Payment.id == draft.payment_id)
        )
    ).one()
    if state.reversed:
        raise PaymentReversedError()
    remainder = Money(state.remainder)
    if draft.amount > remainder:
        raise RefundExceedsRemainderError(remainder)
    refund = Refund(
        payment_id=draft.payment_id,
        billing_account_id=account_id,
        amount=draft.amount,
        refunded_on=draft.refunded_on,
        payment_method_code=draft.payment_method_code,
        reference=draft.reference,
        reason=draft.reason,
        refunded_by_user_id=actor,
    )
    session.add(refund)
    await session.flush()
    return RecordedRefund(
        refund_id=refund.id,
        remainder_after=remainder - draft.amount,
        account_credit_after=await account_credit(session, account_id),
    )


# --- Package cancellation (B5) -------------------------------------------------------


def package_cancellation_credit(net: Money, remaining: int, granted: int) -> Money:
    """The default credit of a cancellation (#100 R6, pure): ``net ×
    remaining / granted`` rounded once half away from zero
    (``Money.prorate``)."""
    return net.prorate(remaining, granted)


async def cancel_package(
    session: AsyncSession,
    package_id: uuid.UUID,
    *,
    reason: str,
    credit_amount: Money | None,
    today: date,
) -> PackageCancellation:
    """Cancel a package purchase (#100 B5, R6).

    The package (unknown → ``404``) → its account ``FOR UPDATE`` → under
    the lock: a ``reversal`` exists → ``cancelled``; an ``expire`` row or
    ``expires_on`` before ``today`` (the center's local date) → ``expired``;
    no units left → ``exhausted`` (each
    :class:`PackageNotCancellableError`) → the purchase charge's net → the
    credit (``credit_amount``, else :func:`package_cancellation_credit`);
    above the net → :class:`ChargeNetNegativeError` (nothing written) → the
    ``reversal`` row (``units = −remaining``, ``reverses_transaction_id`` =
    the grant) → for a credit above zero, the ``package_cancellation``
    adjustment through ``post_adjustment`` (E5 releases the newest
    allocations above the new net; the freed money is account credit).
    Sessions the package covered but hadn't consumed lose the coverage
    (remaining 0) and the next session sweep charges them. Never commits."""
    # The reversal row has no actor column; the adjustment's is the actor.
    _actor(session)
    package = (
        await session.execute(
            select(
                Package.billing_account_id, Package.units_granted, Package.expires_on
            ).where(Package.id == package_id)
        )
    ).first()
    if package is None:
        raise RecordNotFoundError()
    await lock_billing_account(session, package.billing_account_id)
    rows = (
        await session.execute(
            select(PackageCreditTransaction.id, PackageCreditTransaction.kind).where(
                PackageCreditTransaction.package_id == package_id,
                PackageCreditTransaction.kind.in_(("grant", "expire", "reversal")),
            )
        )
    ).all()
    kinds = {row.kind: row.id for row in rows}
    remaining = int(
        await session.scalar(
            select(func.coalesce(func.sum(PackageCreditTransaction.units), 0)).where(
                PackageCreditTransaction.package_id == package_id
            )
        )
        or 0
    )
    if "reversal" in kinds:
        raise PackageNotCancellableError("cancelled")
    if "expire" in kinds or package.expires_on < today:
        raise PackageNotCancellableError("expired")
    if remaining <= 0:
        raise PackageNotCancellableError("exhausted")
    charge_id: uuid.UUID | None = await session.scalar(
        select(Charge.id).where(
            Charge.kind == "package_purchase", Charge.package_id == package_id
        )
    )
    if charge_id is None or "grant" not in kinds:  # pragma: no cover - #94 L8
        raise ValueError("a sold package has its grant and purchase charge")
    [purchase] = await charge_balances(
        session, package.billing_account_id, charge_id=charge_id
    )
    credit = (
        credit_amount
        if credit_amount is not None
        else package_cancellation_credit(purchase.net, remaining, package.units_granted)
    )
    if credit > purchase.net:
        raise ChargeNetNegativeError(charge_id, purchase.net, -credit)
    reversal = PackageCreditTransaction(
        package_id=package_id,
        kind="reversal",
        units=-remaining,
        reverses_transaction_id=kinds["grant"],
    )
    try:
        async with session.begin_nested():
            session.add(reversal)
    except IntegrityError as exc:  # pragma: no cover - the lock serializes it
        if PACKAGE_REVERSAL_KEY not in str(exc.orig):
            raise
        raise PackageNotCancellableError("cancelled") from None
    adjustment_id: uuid.UUID | None = None
    released: tuple[uuid.UUID, ...] = ()
    if credit.is_positive():
        posted = await post_adjustment(
            session,
            AdjustmentDraft(
                charge_id=charge_id,
                kind=PACKAGE_CANCELLATION,
                amount=-credit,
                reason=reason,
                source_key=PACKAGE_CANCELLATION_KEY.format(package_id=package_id),
            ),
        )
        adjustment_id = posted.adjustment_id
        released = posted.released_allocation_ids
    return PackageCancellation(
        package_id=package_id,
        transaction_id=reversal.id,
        units_reversed=remaining,
        credit=credit,
        adjustment_id=adjustment_id,
        released_allocation_ids=released,
        account_credit_after=await account_credit(session, package.billing_account_id),
    )
