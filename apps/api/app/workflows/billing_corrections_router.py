"""Correction, reversal and refund routes (#100 C1-C4, C6-C8;
``app.workflows.billing_corrections``; ADR-0006 E5, E6, E8; ADR-0007 D10).

Every ``POST`` posts ledger rows, so each declares an ``Idempotency-Key``
(#88): a retry with the same key replays the first response and a
concurrent duplicate runs once. Check order: ``401`` → ``403 csrf_failed``
→ ``403 privacy_notice_required`` → ``403 permission_denied`` → ``403
reauth_required`` (where declared, before the key, so nothing is stored) →
``400 idempotency_key_required|invalid`` → replay / ``422
idempotency_key_reused`` → ``422`` (the body) → the rules of
``app.workflows.billing_corrections``. The routes never commit; they end
with ``idem.respond``. No query parameter (#88).

- ``POST /api/v1/families/{family_id}/billing-account/charges``
  (``billing.manage``; no reauthentication, R3): a manual charge.
- ``POST …/charges/{charge_id}/adjustments`` (``billing.manage`` +
  ``finance.correction``, both signs, R3): a manual adjustment.
- ``POST …/payments/{payment_id}/reversal`` and ``POST
  …/payments/{payment_id}/refunds`` (``finance.refund.approve`` +
  ``finance.refund_or_reversal``; Finance Staff can't, E8): reverse a
  payment, refund from its unallocated remainder.
- ``GET …/charges/{charge_id}`` (``authenticated()`` + the #364 record
  policy): one charge with its adjustments and allocations; ``reason`` and
  the actor ids ``null`` for a guardian.

The package cancellation (``POST /api/v1/billing/packages/{package_id}/
cancellation``) lives with the package routes
(``app.workflows.packages_router``).
"""

import uuid
from typing import Annotated, Final

from fastapi import APIRouter, Response

from app.core.authz import (
    AUTH_RESPONSES,
    PERMISSION_RESPONSES,
    Principal,
    authenticated,
    require,
)
from app.core.errors import problem_response_doc
from app.core.idempotency import IDEMPOTENCY_RESPONSES, IdempotentRequest, idempotent
from app.core.reauth import REAUTH_RESPONSES, require_recent_auth
from app.core.uow import UnitOfWorkDep
from app.modules.billing.schemas import (
    ManualAdjustmentCreate,
    ManualChargeCreate,
    PaymentReversalCreate,
    RefundCreate,
)
from app.workflows import billing_corrections
from app.workflows.billing_correction_schemas import (
    ChargeDetailItem,
    ManualAdjustmentPosted,
    ManualChargePosted,
    PaymentReversed,
    RefundRecorded,
)

BILLING_MANAGE: Final = "billing.manage"
REFUND_APPROVE: Final = "finance.refund.approve"
CORRECTION: Final = "finance.correction"
REFUND_OR_REVERSAL: Final = "finance.refund_or_reversal"

router = APIRouter(tags=["billing"])

_FAMILIES: Final = "/api/v1/families/{family_id}/billing-account"
_NO_FAMILY = problem_response_doc("No such family (not_found).")
_NO_CHARGE = problem_response_doc(
    "No such family, or the charge isn't on its account (not_found)."
)
_NO_PAYMENT = problem_response_doc(
    "No such family, or the payment isn't on its account (not_found)."
)
_REUSED = "the Idempotency-Key was already used for a different request" + (
    " (idempotency_key_reused)"
)


@router.post(
    f"{_FAMILIES}/charges",
    status_code=201,
    response_model=ManualChargePosted,
    responses={
        **PERMISSION_RESPONSES,
        **IDEMPOTENCY_RESPONSES,
        404: _NO_FAMILY,
        422: problem_response_doc(
            "The body is not valid (validation_error: amount_not_positive,"
            " money_*, date_out_of_range, string_too_short, …); the student"
            f" isn't in this family (student_not_in_family); or {_REUSED}."
        ),
    },
)
async def post_manual_charge(
    principal: Annotated[Principal, require(BILLING_MANAGE)],
    idem: Annotated[IdempotentRequest, idempotent()],
    family_id: uuid.UUID,
    body: ManualChargeCreate,
    uow: UnitOfWorkDep,
) -> Response:
    """Post a manual charge for a student of the family: a description, an
    amount, a service date and a due date (used as entered: no family
    offset, no discounts). It is posted even while automatic billing is off.
    The family's billing account is created with its first charge. Send a
    new `Idempotency-Key` per charge and the same one to retry it."""
    posted = await billing_corrections.post_manual_charge(uow.session, family_id, body)
    return await idem.respond(posted, status_code=201)


@router.post(
    f"{_FAMILIES}/charges/{{charge_id}}/adjustments",
    status_code=201,
    response_model=ManualAdjustmentPosted,
    dependencies=[require_recent_auth(CORRECTION)],
    responses={
        **REAUTH_RESPONSES,
        **IDEMPOTENCY_RESPONSES,
        404: _NO_CHARGE,
        409: problem_response_doc(
            "The credit would take the charge below zero (charge_net_negative;"
            " details: charge_id, net, amount); a package purchase is"
            " corrected by cancelling the package"
            " (package_purchase_not_adjustable)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error: amount_zero, money_*,"
            f" string_too_short, …); or {_REUSED}."
        ),
    },
)
async def post_manual_adjustment(
    principal: Annotated[Principal, require(BILLING_MANAGE)],
    idem: Annotated[IdempotentRequest, idempotent()],
    family_id: uuid.UUID,
    charge_id: uuid.UUID,
    body: ManualAdjustmentCreate,
    uow: UnitOfWorkDep,
) -> Response:
    """Correct a posted charge with a credit (a negative amount) or a debit
    (a positive one) and a reason. A credit on a paid charge releases its
    newest allocations; that money becomes account credit. Needs a password
    reauthentication in the last 5 minutes. Send a new `Idempotency-Key`
    per adjustment and the same one to retry it."""
    posted = await billing_corrections.post_manual_adjustment(
        uow.session, family_id, charge_id, body
    )
    return await idem.respond(posted, status_code=201)


@router.post(
    f"{_FAMILIES}/payments/{{payment_id}}/reversal",
    status_code=201,
    response_model=PaymentReversed,
    dependencies=[require_recent_auth(REFUND_OR_REVERSAL)],
    responses={
        **REAUTH_RESPONSES,
        **IDEMPOTENCY_RESPONSES,
        404: _NO_PAYMENT,
        409: problem_response_doc(
            "The payment was already reversed (payment_already_reversed); it"
            " has refunds (payment_has_refunds)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error: string_too_short, …);"
            f" or {_REUSED}."
        ),
    },
)
async def reverse_payment(
    principal: Annotated[Principal, require(REFUND_APPROVE)],
    idem: Annotated[IdempotentRequest, idempotent()],
    family_id: uuid.UUID,
    payment_id: uuid.UUID,
    body: PaymentReversalCreate,
    uow: UnitOfWorkDep,
) -> Response:
    """Reverse a payment (a bounced transfer, a payment recorded by
    mistake) with a reason: every allocation it still has is released, so
    its charges reopen, and its unallocated remainder stops being account
    credit. The payment and its receipt stay, marked reversed. A payment
    with refunds can't be reversed. To fix a wrong allocation, reverse the
    payment and record it again. Needs a password reauthentication in the
    last 5 minutes."""
    reversed_ = await billing_corrections.reverse_payment(
        uow.session, family_id, payment_id, body
    )
    return await idem.respond(reversed_, status_code=201)


@router.post(
    f"{_FAMILIES}/payments/{{payment_id}}/refunds",
    status_code=201,
    response_model=RefundRecorded,
    dependencies=[require_recent_auth(REFUND_OR_REVERSAL)],
    responses={
        **REAUTH_RESPONSES,
        **IDEMPOTENCY_RESPONSES,
        404: _NO_PAYMENT,
        409: problem_response_doc(
            "The payment was reversed (payment_reversed); the refund is more"
            " than its unallocated remainder (refund_exceeds_remainder;"
            " details: remainder)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error: amount_not_positive,"
            " money_*, date_out_of_range, string_too_short, …); the method is"
            " unknown or deactivated (payment_method_unavailable); the date is"
            " before the payment (refunded_on_before_payment) or after today"
            f" (refunded_on_in_future); or {_REUSED}."
        ),
    },
)
async def refund_payment(
    principal: Annotated[Principal, require(REFUND_APPROVE)],
    idem: Annotated[IdempotentRequest, idempotent()],
    family_id: uuid.UUID,
    payment_id: uuid.UUID,
    body: RefundCreate,
    uow: UnitOfWorkDep,
) -> Response:
    """Record money paid back to the family from a payment's unallocated
    remainder, with its date, method, reference and reason. It lowers the
    account credit; open charges don't change. A refund can't be undone.
    Needs a password reauthentication in the last 5 minutes."""
    refunded = await billing_corrections.refund_payment(
        uow.session, family_id, payment_id, body
    )
    return await idem.respond(refunded, status_code=201)


@router.get(
    f"{_FAMILIES}/charges/{{charge_id}}",
    responses={
        **AUTH_RESPONSES,
        403: problem_response_doc(
            "The privacy notice must be accepted first (privacy_notice_required)."
        ),
        404: problem_response_doc(
            "No such family or charge, or one whose account you can't see (not_found)."
        ),
        422: problem_response_doc("An id is not a UUID (validation_error)."),
    },
)
async def get_family_charge(
    principal: Annotated[Principal, authenticated()],
    family_id: uuid.UUID,
    charge_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> ChargeDetailItem:
    """One charge of the family with its adjustments (credits negative,
    with their reasons) and every allocation it has had, oldest first.
    Staff with billing.view see every family; a guardian only their own
    family, without the reasons and the actor ids."""
    return await billing_corrections.get_charge(
        uow.session, principal, family_id, charge_id
    )
