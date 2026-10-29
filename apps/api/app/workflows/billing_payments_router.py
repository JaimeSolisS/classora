"""Payment routes (#96 C; ``app.workflows.billing_payments``; ADR-0006 E5,
E6, E8, E12).

- ``POST /api/v1/families/{family_id}/billing-account/payments`` and
  ``POST …/allocations`` (``payment.record`` and an ``Idempotency-Key``,
  #88): record a payment (and allocate it), or allocate account credit.
  Each posts ledger rows, so a retry with the same key replays the first
  response and a concurrent duplicate runs once. Check order: ``401`` →
  ``403 csrf_failed`` → ``403 privacy_notice_required`` → ``403
  permission_denied`` → ``400 idempotency_key_required|invalid`` → replay
  / ``422 idempotency_key_reused`` → ``422`` (the body) → the rules of
  ``app.workflows.billing_payments``. The routes never commit; they end
  with ``idem.respond``. No reauthentication (#96 M6): a mistake is undone
  by a #100 reversal, which needs it. No query parameter (#88).
- ``GET /api/v1/families/{family_id}/billing-account/payments`` and
  ``GET …/payments/{payment_id}`` (``authenticated()`` + the #364 record
  policy; ``note`` and the actor ids ``null`` for a guardian).
- ``GET /api/v1/billing/payment-methods`` (``billing.view``).
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
from app.core.pagination import OffsetPage, OffsetPagination
from app.core.uow import UnitOfWorkDep
from app.modules.billing.schemas import CreditAllocationCreate, PaymentCreate
from app.workflows import billing_payments
from app.workflows.billing_payment_schemas import (
    CreditAllocated,
    PaymentDetail,
    PaymentItem,
    PaymentMethodItem,
    PaymentRecorded,
)

BILLING_VIEW: Final = "billing.view"
PAYMENT_RECORD: Final = "payment.record"

router = APIRouter(tags=["billing"])

_FAMILIES: Final = "/api/v1/families/{family_id}/billing-account"
_NO_FAMILY = problem_response_doc("No such family (not_found).")
_FORBIDDEN = problem_response_doc(
    "The privacy notice must be accepted first (privacy_notice_required)."
)
_NOT_VISIBLE = problem_response_doc(
    "No such family or payment, or one whose account you can't see (not_found)."
)


@router.post(
    f"{_FAMILIES}/payments",
    status_code=201,
    response_model=PaymentRecorded,
    responses={
        **PERMISSION_RESPONSES,
        **IDEMPOTENCY_RESPONSES,
        404: _NO_FAMILY,
        409: problem_response_doc(
            "An allocation is more than what is still open on its charge"
            " (allocation_exceeds_open; details: charge_id, open)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error: amount_not_positive,"
            " money_*, date_out_of_range, allocation_exceeds_payment,"
            " allocation_duplicate_charge, …); the method is unknown or"
            " deactivated (payment_method_unavailable); the date is after"
            " today (received_on_in_future); a chosen charge is not on this"
            " family's account (allocation_charge_not_found); or the"
            " Idempotency-Key was already used for a different request"
            " (idempotency_key_reused)."
        ),
    },
)
async def record_family_payment(
    principal: Annotated[Principal, require(PAYMENT_RECORD)],
    idem: Annotated[IdempotentRequest, idempotent()],
    family_id: uuid.UUID,
    body: PaymentCreate,
    uow: UnitOfWorkDep,
) -> Response:
    """Record an external payment (cash, transfer or other) for a family
    and allocate it: `oldest_first` (the earliest due date first, across
    all the family's students), `explicit` (chosen charges and amounts,
    each at most what is open) or `none`. Whatever isn't allocated stays as
    account credit. The family's billing account is created with its first
    payment. Send a new `Idempotency-Key` per payment and the same one to
    retry it."""
    recorded = await billing_payments.record_payment(uow.session, family_id, body)
    return await idem.respond(recorded, status_code=201)


@router.post(
    f"{_FAMILIES}/allocations",
    status_code=201,
    response_model=CreditAllocated,
    responses={
        **PERMISSION_RESPONSES,
        **IDEMPOTENCY_RESPONSES,
        404: _NO_FAMILY,
        409: problem_response_doc(
            "There is no account credit or no open charge"
            " (nothing_to_allocate); the amount or the lines are more than the"
            " account credit (insufficient_account_credit; details:"
            " account_credit); a line is more than what is open on its charge"
            " (allocation_exceeds_open)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error: amount_not_allowed,"
            " amount_not_positive, allocation_duplicate_charge, …); a chosen"
            " charge is not on this family's account"
            " (allocation_charge_not_found); or the Idempotency-Key was"
            " already used for a different request (idempotency_key_reused)."
        ),
    },
)
async def allocate_family_credit(
    principal: Annotated[Principal, require(PAYMENT_RECORD)],
    idem: Annotated[IdempotentRequest, idempotent()],
    family_id: uuid.UUID,
    body: CreditAllocationCreate,
    uow: UnitOfWorkDep,
) -> Response:
    """Allocate the family's account credit to open charges, `oldest_first`
    (up to `amount`, or all the credit) or `explicit`. The money comes from
    the oldest payments with an unallocated remainder first. Send a new
    `Idempotency-Key` per allocation and the same one to retry it."""
    allocated = await billing_payments.allocate_credit(uow.session, family_id, body)
    return await idem.respond(allocated, status_code=201)


@router.get(
    f"{_FAMILIES}/payments",
    responses={
        **AUTH_RESPONSES,
        403: _FORBIDDEN,
        404: _NOT_VISIBLE,
        422: problem_response_doc(
            "The query is not valid (validation_error): a malformed family id,"
            " or `limit`/`offset` out of range."
        ),
    },
)
async def list_family_payments(
    principal: Annotated[Principal, authenticated()],
    family_id: uuid.UUID,
    page: OffsetPagination,
    uow: UnitOfWorkDep,
) -> OffsetPage[PaymentItem]:
    """The family's payments, the latest received first, with their
    allocated, refunded and unallocated amounts. Staff with billing.view
    see every family; a guardian only their own family, without the note
    and the actor ids."""
    return await billing_payments.list_payments(
        uow.session, principal, family_id, page=page
    )


@router.get(
    f"{_FAMILIES}/payments/{{payment_id}}",
    responses={
        **AUTH_RESPONSES,
        403: _FORBIDDEN,
        404: _NOT_VISIBLE,
        422: problem_response_doc("An id is not a UUID (validation_error)."),
    },
)
async def get_family_payment(
    principal: Annotated[Principal, authenticated()],
    family_id: uuid.UUID,
    payment_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> PaymentDetail:
    """One payment of the family with every allocation it has had, oldest
    first (released ones have `is_valid: false`)."""
    return await billing_payments.get_payment(
        uow.session, principal, family_id, payment_id
    )


@router.get(
    "/api/v1/billing/payment-methods",
    responses={**PERMISSION_RESPONSES},
)
async def list_payment_methods(
    principal: Annotated[Principal, require(BILLING_VIEW)],
    uow: UnitOfWorkDep,
) -> list[PaymentMethodItem]:
    """Every payment method by code, with whether it is active. Offer only
    the active ones; labels come from the client catalogs."""
    return await billing_payments.list_payment_methods(uow.session)
