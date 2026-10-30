"""Payments and allocations (#96 C; M1, M3-M5, M7, M8; spec §6, W4.3,
W4.4; ADR-0006 E5, E6, E8, E12): the cross-module work behind
``app.workflows.billing_payments_router``.

The billing module owns the payment rows, the allocation rules and the
account lock (``billing.service``: ``record_payment``,
``allocate_account_credit``); the families module owns the family
(``families.service``) and administration the center's local today. This
workflow joins them (ADR-0002). Nothing here commits: the route stores its
response on the idempotency key in the same transaction (#88).

**Recording** (C2 order, after the access rule, the idempotency key and
the body): the family (unknown → ``404 not_found``; an inactive family may
pay) → the method (unknown or inactive → ``422
payment_method_unavailable``) → ``received_on`` after the center's today →
``422 received_on_in_future`` → the family's account
(``ensure_billing_account``: created on the first payment, with its own
``create`` audit event) → ``billing.record_payment`` (account lock → ``422
allocation_charge_not_found`` / ``409 allocation_exceeds_open``).

**Allocating credit** (C4): the family (``404``) → its account (none →
``409 nothing_to_allocate``; a read never creates one) →
``billing.allocate_account_credit``.

**Reads** (C6, M7) reuse the #364 record policy of
:mod:`app.workflows.billing_accounts` (``_visible_family``: ``billing.view``
→ every family; a guardian → their active profile's family; anyone else
the same ``404``). A caller without ``billing.view`` gets ``note``,
``recorded_by_user_id`` and the allocations' ``created_by_user_id`` as
``null`` (``sees_every_account``). The list costs at most four statements
per page (the guardian's own family, the family, the account, the page),
the detail at most five.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal, RecordNotFoundError
from app.core.pagination import OffsetPage, OffsetParams
from app.modules.administration import service as administration
from app.modules.billing import service as billing
from app.modules.billing.schemas import CreditAllocationCreate, PaymentCreate
from app.modules.families import service as families
from app.workflows.billing_accounts import _visible_family, sees_every_account
from app.workflows.billing_payment_schemas import (
    CreditAllocated,
    CreditAllocationItem,
    PaymentAllocationItem,
    PaymentDetail,
    PaymentItem,
    PaymentMethodItem,
    PaymentRecorded,
    ReceiptReference,
    RecordedAllocationItem,
)


def _item(row: billing.PaymentRow, *, staff: bool) -> PaymentItem:
    return PaymentItem(
        id=row.id,
        amount=row.amount,
        received_on=row.received_on,
        payment_method_code=row.payment_method_code,
        reference=row.reference,
        payer_name=row.payer_name,
        note=row.note if staff else None,
        recorded_by_user_id=row.recorded_by_user_id if staff else None,
        recorded_at=row.recorded_at,
        allocated_amount=row.allocated_amount,
        refunded_amount=row.refunded_amount,
        unallocated_amount=row.unallocated_amount,
        is_reversed=row.is_reversed,
        receipt=None
        if row.receipt_id is None or row.receipt_number is None
        else ReceiptReference(id=row.receipt_id, number=row.receipt_number),
    )


def _detail(
    row: billing.PaymentRow,
    allocations: list[billing.PaymentAllocationRow],
    *,
    staff: bool,
) -> PaymentDetail:
    return PaymentDetail(
        **_item(row, staff=staff).model_dump(),
        allocations=[
            PaymentAllocationItem(
                id=a.id,
                charge_id=a.charge_id,
                student_id=a.student_id,
                amount=a.amount,
                created_at=a.created_at,
                created_by_user_id=a.created_by_user_id if staff else None,
                is_valid=a.is_valid,
            )
            for a in allocations
        ],
    )


async def _existing_family(session: AsyncSession, family_id: uuid.UUID) -> None:
    if await families.family_record_reference(session, family_id) is None:
        raise RecordNotFoundError()


async def record_payment(
    session: AsyncSession, family_id: uuid.UUID, body: PaymentCreate
) -> PaymentRecorded:
    """``POST …/billing-account/payments`` (C1-C3; the module docstring
    order)."""
    await _existing_family(session, family_id)
    if not await billing.active_payment_method(session, body.payment_method_code):
        raise billing.PaymentMethodUnavailableError()
    if body.received_on > await administration.center_today(session):
        raise billing.ReceivedOnInFutureError()
    account_id = await billing.ensure_billing_account(session, family_id)
    recorded = await billing.record_payment(
        session,
        billing.PaymentDraft(
            billing_account_id=account_id,
            amount=body.amount,
            received_on=body.received_on,
            payment_method_code=body.payment_method_code,
            reference=body.reference,
            payer_name=body.payer_name,
            note=body.note,
            allocation=billing.AllocationRequest.of(body.allocation),
        ),
    )
    found = await billing.get_payment(session, account_id, recorded.payment_id)
    assert found is not None  # noqa: S101 - written above, same transaction
    return PaymentRecorded(
        payment=_item(found[0], staff=True),
        allocations=[
            RecordedAllocationItem(
                id=a.allocation_id,
                charge_id=a.charge_id,
                student_id=a.student_id,
                amount=a.amount,
            )
            for a in recorded.allocations
        ],
        unallocated_amount=recorded.unallocated,
        account_credit_after=recorded.account_credit_after,
        receipt=ReceiptReference(
            id=recorded.receipt_id, number=recorded.receipt_number
        ),
    )


async def allocate_credit(
    session: AsyncSession, family_id: uuid.UUID, body: CreditAllocationCreate
) -> CreditAllocated:
    """``POST …/billing-account/allocations`` (C4)."""
    await _existing_family(session, family_id)
    account = await billing.account_of_family(session, family_id)
    if account is None:
        raise billing.NothingToAllocateError()
    result = await billing.allocate_account_credit(
        session,
        billing.CreditAllocationRequest(
            billing_account_id=account.id,
            allocation=billing.AllocationRequest.of(body.allocation),
            amount=body.amount,
        ),
    )
    return CreditAllocated(
        allocations=[
            CreditAllocationItem(
                id=a.allocation_id,
                payment_id=a.payment_id,
                charge_id=a.charge_id,
                student_id=a.student_id,
                amount=a.amount,
            )
            for a in result.allocations
        ],
        allocated_total=result.allocated_total,
        account_credit_after=result.account_credit_after,
    )


async def list_payments(
    session: AsyncSession,
    principal: Principal,
    family_id: uuid.UUID,
    *,
    page: OffsetParams,
) -> OffsetPage[PaymentItem]:
    """``GET …/billing-account/payments`` (C6): newest ``received_on``
    first. A family without an account gives an empty page."""
    await _visible_family(session, principal, family_id)
    account = await billing.account_of_family(session, family_id)
    if account is None:
        return OffsetPage[PaymentItem](
            items=[], total=0, limit=page.limit, offset=page.offset
        )
    found = await billing.list_payments(session, account.id, page=page)
    staff = sees_every_account(principal)
    return OffsetPage[PaymentItem](
        items=[_item(row, staff=staff) for row in found.items],
        total=found.total,
        limit=page.limit,
        offset=page.offset,
    )


async def get_payment(
    session: AsyncSession,
    principal: Principal,
    family_id: uuid.UUID,
    payment_id: uuid.UUID,
) -> PaymentDetail:
    """``GET …/billing-account/payments/{payment_id}`` (C6). A payment of
    another family's account is ``404``, like an unknown one."""
    await _visible_family(session, principal, family_id)
    account = await billing.account_of_family(session, family_id)
    if account is None:
        raise RecordNotFoundError()
    found = await billing.get_payment(session, account.id, payment_id)
    if found is None:
        raise RecordNotFoundError()
    row, allocations = found
    return _detail(row, allocations, staff=sees_every_account(principal))


async def list_payment_methods(session: AsyncSession) -> list[PaymentMethodItem]:
    """``GET /api/v1/billing/payment-methods`` (C5): every method by
    code."""
    return [
        PaymentMethodItem(code=row.code, is_active=row.is_active)
        for row in await billing.payment_methods_list(session)
    ]
