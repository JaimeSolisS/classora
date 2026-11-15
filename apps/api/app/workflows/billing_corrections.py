"""Corrections, reversals and refunds (#100 C1-C4, C6; R2-R8; spec §6,
W4.4, W4.5; ADR-0006 E5, E8, E13): the cross-module work behind
``app.workflows.billing_corrections_router``.

The billing module owns the rows, the rules and the account lock
(``billing.service``: ``post_manual_charge``, ``post_manual_adjustment``,
``reverse_payment``, ``refund_payment``); the families module owns the
family and its members; administration the center's local today. This
workflow joins them (ADR-0002). Nothing here commits: each route stores its
response on its idempotency key in the same transaction (#88).

**Orders** (after the access rule, the reauthentication, the idempotency
key and the body):

- **Manual charge** (C1): the family (unknown → ``404``) → a date more
  than 366 days after today → ``422 date_out_of_range`` (field) →
  ``billing.post_manual_charge`` (``422 student_not_in_family``; creates
  the account on first use).
- **Manual adjustment** (C2): the charge on the family's account (else
  ``404``, an unknown family alike) → ``billing.post_manual_adjustment``
  (``409 package_purchase_not_adjustable``, ``409 charge_net_negative``).
- **Reversal** (C3): the payment on the family's account (else ``404``) →
  ``billing.reverse_payment`` (``409 payment_already_reversed``, ``409
  payment_has_refunds``).
- **Refund** (C4): the payment on the family's account (else ``404``) →
  the method (``422 payment_method_unavailable``) → ``refunded_on`` before
  the payment's ``received_on`` (``422 refunded_on_before_payment``) or
  after today (``422 refunded_on_in_future``) → ``billing.refund_payment``
  (``409 payment_reversed``, ``409 refund_exceeds_remainder``).

**Charge detail** (C6, R8): the #364 record policy of
:mod:`app.workflows.billing_accounts` (``_visible_family``) → the charge on
the family's account (else the same ``404``) → the item, its adjustments
and its allocations. ``reason`` and the actor ids are ``null`` for a caller
without ``billing.view``. Statements: the guardian's own family, the
family, the charge's account, the center's today, the charge, the
student's name, the adjustments, the allocations, the discount lines
(#102 C1, the posting-time snapshot): at most nine, whatever the number of
rows (the billing reads are five).
"""

import uuid
from datetime import timedelta
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal, RecordNotFoundError
from app.core.errors import FieldError
from app.core.pagination import OffsetParams
from app.modules.administration import service as administration
from app.modules.billing import service as billing
from app.modules.families import service as families
from app.workflows.billing_account_schemas import ChargeItem
from app.workflows.billing_accounts import (
    _visible_family,
    charge_item,
    sees_every_account,
    students_by_id,
)
from app.workflows.billing_correction_schemas import (
    AdjustmentItem,
    ChargeAllocationItem,
    ChargeDetailItem,
    ChargeDiscountLine,
    ManualAdjustmentPosted,
    ManualChargePosted,
    PaymentReversed,
    RefundRecorded,
    ReleasedAllocationItem,
)
from app.workflows.billing_payments import (
    payment_item,
    refund_item,
    reversal_item,
)

MANUAL_CHARGE_HORIZON: Final = timedelta(days=366)
"""How far after the center's today a manual charge's dates may be (R7)."""


async def _existing_family(session: AsyncSession, family_id: uuid.UUID) -> None:
    if await families.family_record_reference(session, family_id) is None:
        raise RecordNotFoundError()


async def _charge(
    session: AsyncSession,
    account_id: uuid.UUID,
    charge_id: uuid.UUID,
    *,
    staff: bool,
) -> ChargeItem:
    [found] = (
        await billing.charge_details(
            session,
            account_id,
            page=OffsetParams(limit=1, offset=0),
            charge_id=charge_id,
        )
    ).items
    students = await students_by_id(session, {found.student_id})
    return charge_item(found, students, staff=staff)


async def _charge_account(
    session: AsyncSession, family_id: uuid.UUID, charge_id: uuid.UUID
) -> uuid.UUID:
    account_id = await billing.charge_account_of_family(session, charge_id, family_id)
    if account_id is None:
        raise RecordNotFoundError()
    return account_id


async def _payment(
    session: AsyncSession, family_id: uuid.UUID, payment_id: uuid.UUID
) -> tuple[uuid.UUID, billing.PaymentRow]:
    """The family's account and the payment on it, else ``404``."""
    account = await billing.account_of_family(session, family_id)
    if account is None:
        raise RecordNotFoundError()
    found = await billing.get_payment(session, account.id, payment_id)
    if found is None:
        raise RecordNotFoundError()
    return account.id, found[0]


async def _payment_row(
    session: AsyncSession, account_id: uuid.UUID, payment_id: uuid.UUID
) -> billing.PaymentRow:
    found = await billing.get_payment(session, account_id, payment_id)
    assert found is not None  # noqa: S101 - written above, same transaction
    return found[0]


# --- Writes (C1-C4) ------------------------------------------------------------------


async def post_manual_charge(
    session: AsyncSession, family_id: uuid.UUID, body: billing.ManualChargeCreate
) -> ManualChargePosted:
    """``POST …/billing-account/charges`` (C1, R7)."""
    await _existing_family(session, family_id)
    latest = await administration.center_today(session) + MANUAL_CHARGE_HORIZON
    late = [
        name
        for name, value in (
            ("service_date", body.service_date),
            ("due_date", body.due_date),
        )
        if value > latest
    ]
    if late:
        raise billing.BillingValidationError(
            [
                FieldError(
                    path=["body", name],
                    code="date_out_of_range",
                    message="Choose a date at most 366 days from today.",
                )
                for name in late
            ]
        )
    posted = await billing.post_manual_charge(
        session,
        billing.ManualChargeDraft(
            family_id=family_id,
            student_id=body.student_id,
            description=body.description,
            amount=body.amount,
            service_date=body.service_date,
            due_date=body.due_date,
        ),
    )
    return ManualChargePosted(
        charge=await _charge(
            session, posted.billing_account_id, posted.charge_id, staff=True
        )
    )


async def post_manual_adjustment(
    session: AsyncSession,
    family_id: uuid.UUID,
    charge_id: uuid.UUID,
    body: billing.ManualAdjustmentCreate,
) -> ManualAdjustmentPosted:
    """``POST …/charges/{charge_id}/adjustments`` (C2, R3)."""
    account_id = await _charge_account(session, family_id, charge_id)
    result = await billing.post_manual_adjustment(
        session, charge_id, body.amount, body.reason
    )
    [adjustment] = [
        row
        for row in await billing.charge_adjustments(session, charge_id)
        if row.id == result.adjustment_id
    ]
    return ManualAdjustmentPosted(
        adjustment=_adjustment_item(adjustment, staff=True),
        charge=await _charge(session, account_id, charge_id, staff=True),
        released_allocation_ids=list(result.released_allocation_ids),
        account_credit_after=result.account_credit_after,
    )


async def reverse_payment(
    session: AsyncSession,
    family_id: uuid.UUID,
    payment_id: uuid.UUID,
    body: billing.PaymentReversalCreate,
) -> PaymentReversed:
    """``POST …/payments/{payment_id}/reversal`` (C3, R5)."""
    account_id, _ = await _payment(session, family_id, payment_id)
    result = await billing.reverse_payment(session, payment_id, body.reason)
    row = await _payment_row(session, account_id, payment_id)
    reversal = reversal_item(row, staff=True)
    assert reversal is not None  # noqa: S101 - written above, same transaction
    return PaymentReversed(
        reversal=reversal,
        payment=payment_item(row, staff=True),
        released=[
            ReleasedAllocationItem(
                allocation_id=r.allocation_id, charge_id=r.charge_id, amount=r.amount
            )
            for r in result.released
        ],
        account_credit_after=result.account_credit_after,
    )


async def refund_payment(
    session: AsyncSession,
    family_id: uuid.UUID,
    payment_id: uuid.UUID,
    body: billing.RefundCreate,
) -> RefundRecorded:
    """``POST …/payments/{payment_id}/refunds`` (C4, R4)."""
    account_id, payment = await _payment(session, family_id, payment_id)
    if not await billing.active_payment_method(session, body.payment_method_code):
        raise billing.PaymentMethodUnavailableError()
    if body.refunded_on < payment.received_on:
        raise billing.RefundedOnBeforePaymentError()
    if body.refunded_on > await administration.center_today(session):
        raise billing.RefundedOnInFutureError()
    result = await billing.refund_payment(
        session,
        billing.RefundDraft(
            payment_id=payment_id,
            amount=body.amount,
            refunded_on=body.refunded_on,
            payment_method_code=body.payment_method_code,
            reference=body.reference,
            reason=body.reason,
        ),
    )
    [refund] = [
        r
        for r in await billing.payment_refunds(session, payment_id)
        if r.id == result.refund_id
    ]
    return RefundRecorded(
        refund=refund_item(refund, staff=True),
        payment=payment_item(
            await _payment_row(session, account_id, payment_id), staff=True
        ),
        account_credit_after=result.account_credit_after,
    )


# --- Reads (C6) ----------------------------------------------------------------------


def _adjustment_item(row: billing.AdjustmentRow, *, staff: bool) -> AdjustmentItem:
    return AdjustmentItem(
        id=row.id,
        kind=row.kind,
        amount=row.amount,
        reason=row.reason if staff else None,
        posted_at=row.posted_at,
        posted_by_user_id=row.posted_by_user_id if staff else None,
    )


async def get_charge(
    session: AsyncSession,
    principal: Principal,
    family_id: uuid.UUID,
    charge_id: uuid.UUID,
) -> ChargeDetailItem:
    """``GET …/billing-account/charges/{charge_id}`` (C6, R8). A charge of
    another family's account is ``404``, like an unknown one."""
    await _visible_family(session, principal, family_id)
    account_id = await _charge_account(session, family_id, charge_id)
    staff = sees_every_account(principal)
    item = await _charge(session, account_id, charge_id, staff=staff)
    adjustments = await billing.charge_adjustments(session, charge_id)
    allocations = await billing.charge_allocations(session, charge_id)
    lines = (await billing.charge_discount_lines(session, [charge_id])).get(
        charge_id, []
    )
    return ChargeDetailItem(
        **item.model_dump(),
        adjustments=[_adjustment_item(row, staff=staff) for row in adjustments],
        allocations=[
            ChargeAllocationItem(
                id=a.id,
                payment_id=a.payment_id,
                amount=a.amount,
                created_at=a.created_at,
                is_valid=a.is_valid,
            )
            for a in allocations
        ],
        discounts=[
            ChargeDiscountLine(
                position=line.position,
                kind=line.kind,
                name=line.name,
                percent=None if line.percent is None else f"{line.percent:.4f}",
                fixed_amount=line.fixed_amount,
                amount=line.amount,
            )
            for line in lines
        ],
    )
