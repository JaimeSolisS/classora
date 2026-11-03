"""Payroll payment routes (#112; ``app.workflows.payroll_payments``; ADR-0017
E12, E13).

- ``POST /api/v1/payroll/periods/{period_id}/payments``:
  ``require("payroll.payment.record")``, ``Idempotency-Key``, no
  reauthentication (ADR-0017 E12, the ADR-0006 M6 clarification). Check order:
  ``401`` → ``403 csrf_failed`` → ``403 permission_denied`` → ``422
  text_invalid`` for a lone surrogate (the key's hash needs UTF-8) → ``400`` key
  → replay / ``422 idempotency_key_reused`` → ``422`` (the body) → the workflow.
- ``POST /api/v1/payroll/payments/{payment_id}/reversal``:
  ``require("finance.refund.approve")``,
  ``require_recent_auth("finance.refund_or_reversal")``, ``Idempotency-Key``.
  The same order, with ``403 reauth_required`` after ``permission_denied``.
- ``GET /api/v1/payroll/periods/{period_id}/payments``: ``authenticated()`` and
  the period reads' check (``payroll.view`` or ``payroll.finalize``, #110 F1),
  then the period's record policy (#112 E7). Not paginated: one period.

Owners and Finance Staff record; only an Owner, or an Administrator delegated
``finance.refund.approve``, reverses. Coordinators, tutors (their own period
too: #113 is their read) and families get ``403``. The ``POST`` routes never
commit; they end with ``idem.respond``. Neither response holds an amount, a
reference, a note or a reason (#112 E6).
"""

import uuid
from typing import Annotated, Final

from fastapi import APIRouter, Depends, Response

from app.core.authz import PERMISSION_RESPONSES, Principal, require
from app.core.errors import problem_response_doc
from app.core.idempotency import (
    IDEMPOTENCY_RESPONSES,
    IdempotentRequest,
    hashable_body,
    idempotent,
)
from app.core.reauth import REAUTH_RESPONSES, require_recent_auth
from app.core.uow import UnitOfWorkDep
from app.modules.payroll.schemas import (
    PayrollPaymentCreate,
    PayrollPaymentRecorded,
    PayrollPaymentReversalCreate,
    PayrollPaymentReversed,
)
from app.workflows import payroll_payments
from app.workflows.payroll_calculation_router import period_reader
from app.workflows.payroll_workflow_schemas import PayrollPaymentItem

PAYMENT_RECORD: Final = "payroll.payment.record"
REFUND_APPROVE: Final = "finance.refund.approve"
REFUND_OR_REVERSAL: Final = "finance.refund_or_reversal"

router = APIRouter(prefix="/api/v1/payroll", tags=["payroll"])

_REUSED: Final = (
    "the Idempotency-Key was already used for a different request"
    " (idempotency_key_reused)"
)
_OWN_PAY: Final = (
    "the period is the caller's own pay (payroll_own_compensation; only an Owner may)"
)


@router.post(
    "/periods/{period_id}/payments",
    status_code=201,
    response_model=PayrollPaymentRecorded,
    responses={
        **PERMISSION_RESPONSES,
        **IDEMPOTENCY_RESPONSES,
        403: problem_response_doc(f"Not allowed (permission_denied), or {_OWN_PAY}."),
        404: problem_response_doc("No such period (not_found)."),
        409: problem_response_doc(
            "The period isn't finalized (payroll_period_not_finalized, with"
            " details.status); a payment without a reversal has the same method"
            " and reference (payroll_payment_duplicate, with details.payment_id);"
            " or the payments would add up to more than the period's total"
            " (payroll_payment_exceeds_balance, with details.remaining). A refusal"
            " changes nothing."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error: amount_not_positive,"
            " money_*, text_empty, text_too_long, text_invalid, …); the method is"
            " unknown or deactivated (payment_method_unavailable); the date is"
            " after today (paid_on_in_future) or before the period"
            f" (paid_on_before_period); or {_REUSED}."
        ),
    },
)
async def record_payroll_payment(
    period_id: uuid.UUID,
    principal: Annotated[Principal, require(PAYMENT_RECORD)],
    _: Annotated[None, Depends(hashable_body)],
    idem: Annotated[IdempotentRequest, idempotent()],
    body: PayrollPaymentCreate,
    uow: UnitOfWorkDep,
) -> Response:
    """Log a payment made to the tutor outside the platform against a finalized
    period: the date, the amount, the method, the reference (the transfer number
    or receipt) and an optional note. Partial payments are allowed, up to the
    period's total. A mistake is reversed, never edited. Send a new
    `Idempotency-Key` per payment and the same one to retry it."""
    recorded = await payroll_payments.record_payment(
        uow.session, principal, period_id, body
    )
    return await idem.respond(recorded, status_code=201)


@router.post(
    "/payments/{payment_id}/reversal",
    status_code=201,
    response_model=PayrollPaymentReversed,
    dependencies=[require_recent_auth(REFUND_OR_REVERSAL)],
    responses={
        **PERMISSION_RESPONSES,
        **REAUTH_RESPONSES,
        **IDEMPOTENCY_RESPONSES,
        403: problem_response_doc(
            "Not allowed (permission_denied), the session wasn't reauthenticated"
            f" (reauth_required), or {_OWN_PAY}."
        ),
        404: problem_response_doc("No such payment (not_found)."),
        409: problem_response_doc(
            "The payment was already reversed (payroll_payment_already_reversed)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error: text_empty, text_too_long,"
            f" text_invalid on the reason); or {_REUSED}."
        ),
    },
)
async def reverse_payroll_payment(
    payment_id: uuid.UUID,
    principal: Annotated[Principal, require(REFUND_APPROVE)],
    _: Annotated[None, Depends(hashable_body)],
    idem: Annotated[IdempotentRequest, idempotent()],
    body: PayrollPaymentReversalCreate,
    uow: UnitOfWorkDep,
) -> Response:
    """Reverse a payment logged by mistake, with a reason. The payment stays
    listed, marked reversed, and no longer counts toward what was paid, so the
    right one can be logged again, with the same reference too. Needs a password
    reauthentication in the last 5 minutes."""
    reversed_ = await payroll_payments.reverse_payment(
        uow.session, principal, payment_id, body
    )
    return await idem.respond(reversed_, status_code=201)


@router.get(
    "/periods/{period_id}/payments",
    responses={
        **PERMISSION_RESPONSES,
        404: problem_response_doc("No such period (not_found)."),
    },
)
async def list_payroll_payments(
    period_id: uuid.UUID,
    principal: Annotated[Principal, Depends(period_reader)],
    uow: UnitOfWorkDep,
) -> list[PayrollPaymentItem]:
    """The period's payments, reversed ones included, newest payment date
    first. A reviewer with `payroll.finalize` alone reads those of prepared and
    finalized periods."""
    return await payroll_payments.list_payments(uow.session, principal, period_id)
