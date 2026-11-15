"""Withdrawal routes (#101 D1-D3, S5; ``app.workflows.withdrawals``;
ADR-0006 E6, E8; ADR-0007 D10).

- ``GET /api/v1/billing/withdrawal-preview?group_enrollment_id=…|
  cohort_enrollment_id=…|package_id=…`` (``billing.view``): read-only;
  exactly one parameter (``422 withdrawal_subject_invalid``); unknown →
  ``404 withdrawal_subject_not_found``.
- ``POST /api/v1/billing/withdrawals/account-credit`` (``billing.manage`` +
  reauthentication ``finance.correction`` + ``Idempotency-Key``) and ``POST
  /api/v1/billing/withdrawals/refund`` (``finance.refund.approve`` +
  reauthentication ``finance.refund_or_reversal`` + ``Idempotency-Key``).
  An enrollment subject also needs ``schedule.manage``, checked in the
  workflow (``403 permission_denied``), because the confirm withdraws the
  enrollment. Check order: ``401`` → ``403 csrf_failed`` → ``403
  privacy_notice_required`` → ``403 permission_denied`` → ``403
  reauth_required`` (before the key, so nothing is stored) → ``400
  idempotency_key_required|invalid`` → replay / ``422
  idempotency_key_reused`` → ``422`` (the body) → the workflow. The routes
  never commit; they end with ``idem.respond``. No query parameter.
- ``GET /api/v1/billing/withdrawals`` and ``GET …/{withdrawal_id}``
  (``billing.view``): staff only (#102 decides any guardian view).
"""

import uuid
from typing import Annotated, Final

from fastapi import APIRouter, Response

from app.core import clock
from app.core.authz import PERMISSION_RESPONSES, Principal, require
from app.core.errors import problem_response_doc
from app.core.idempotency import IDEMPOTENCY_RESPONSES, IdempotentRequest, idempotent
from app.core.pagination import OffsetPage, OffsetPagination
from app.core.reauth import REAUTH_RESPONSES, require_recent_auth
from app.core.uow import UnitOfWorkDep
from app.modules.billing import service as billing
from app.workflows import withdrawals
from app.workflows.withdrawal_schemas import (
    WithdrawalConfirmed,
    WithdrawalCreditConfirm,
    WithdrawalDetail,
    WithdrawalItem,
    WithdrawalPreview,
    WithdrawalRefundConfirm,
)

BILLING_VIEW: Final = "billing.view"
BILLING_MANAGE: Final = "billing.manage"
REFUND_APPROVE: Final = "finance.refund.approve"
CORRECTION: Final = "finance.correction"
REFUND_OR_REVERSAL: Final = "finance.refund_or_reversal"

router = APIRouter(tags=["billing"])

_REUSED = "the Idempotency-Key was already used for a different request" + (
    " (idempotency_key_reused)"
)
_CONFIRM_409 = (
    "Settled already (withdrawal_exists; details: withdrawal_id); the"
    " situation changed since the preview (withdrawal_preview_stale; details:"
    " preview, the fresh one); the enrollment changed (version_conflict) or"
    " isn't active (enrollment_not_active); the package is cancelled,"
    " withdrawn, expired or used up (package_not_withdrawable; details:"
    " reason); a covered session awaits its closeout approval"
    " (withdrawal_closeout_pending; details: class_session_ids); the student"
    " isn't in a family (student_has_no_family); the charges are on two"
    " accounts (withdrawal_spans_accounts); an override above the net"
    " (charge_net_negative)"
)
_CONFIRM_422 = (
    "The body is not valid (validation_error: withdrawal_subject_invalid,"
    " string_too_short, amount_negative, credit_override_duplicate, money_*,"
    " …); an override names a charge that isn't a line"
    " (credit_override_unknown_charge)"
)


@router.get(
    "/api/v1/billing/withdrawal-preview",
    responses={
        **PERMISSION_RESPONSES,
        404: problem_response_doc(
            "No such enrollment or package (withdrawal_subject_not_found)."
        ),
        422: problem_response_doc(
            "Not exactly one of group_enrollment_id, cohort_enrollment_id,"
            " package_id (withdrawal_subject_invalid), or a malformed id"
            " (validation_error)."
        ),
    },
)
async def preview_withdrawal(
    principal: Annotated[Principal, require(BILLING_VIEW)],
    uow: UnitOfWorkDep,
    group_enrollment_id: uuid.UUID | None = None,
    cohort_enrollment_id: uuid.UUID | None = None,
    package_id: uuid.UUID | None = None,
) -> WithdrawalPreview:
    """What withdrawing a student from a group, a course cohort or a
    package would credit: the consumed and unused sessions (or units), each
    fee, tuition period or package charge credited and why, the automatic
    session credits of a group, how much lowers the debt and how much paid
    money could be refunded. An enrollment already withdrawn is settled as
    of its withdrawal. Nothing is written; a blocked withdrawal is still
    shown with its reasons. Send the `fingerprint` with the confirm."""
    given = [
        (kind, value)
        for kind, value in (
            ("group_enrollment", group_enrollment_id),
            ("cohort_enrollment", cohort_enrollment_id),
            ("package", package_id),
        )
        if value is not None
    ]
    if len(given) != 1:
        raise billing.WithdrawalSubjectInvalidError()
    [(kind, subject_id)] = given
    return await withdrawals.preview_withdrawal(
        uow.session,
        kind,  # type: ignore[arg-type]
        subject_id,
        now=clock.now(),
    )


@router.post(
    "/api/v1/billing/withdrawals/account-credit",
    status_code=201,
    response_model=WithdrawalConfirmed,
    dependencies=[require_recent_auth(CORRECTION)],
    responses={
        **REAUTH_RESPONSES,
        **IDEMPOTENCY_RESPONSES,
        404: problem_response_doc(
            "No such enrollment or package (withdrawal_subject_not_found)."
        ),
        409: problem_response_doc(f"{_CONFIRM_409}."),
        422: problem_response_doc(f"{_CONFIRM_422}; or {_REUSED}."),
    },
)
async def confirm_account_credit(
    principal: Annotated[Principal, require(BILLING_MANAGE)],
    idem: Annotated[IdempotentRequest, idempotent()],
    body: WithdrawalCreditConfirm,
    uow: UnitOfWorkDep,
) -> Response:
    """Confirm a withdrawal as account credit: an active enrollment is
    withdrawn now, the due fee or tuition is posted, and each line is
    credited (as previewed, or as overridden, with the reason). Paid money
    the credits free stays on the account as credit. Withdrawing an
    enrollment also needs schedule.manage. Needs a password
    reauthentication in the last 5 minutes. Send a new `Idempotency-Key`
    per withdrawal and the same one to retry it."""
    confirmed = await withdrawals.confirm_withdrawal(
        uow, principal, body, outcome="account_credit"
    )
    return await idem.respond(confirmed, status_code=201)


@router.post(
    "/api/v1/billing/withdrawals/refund",
    status_code=201,
    response_model=WithdrawalConfirmed,
    dependencies=[require_recent_auth(REFUND_OR_REVERSAL)],
    responses={
        **REAUTH_RESPONSES,
        **IDEMPOTENCY_RESPONSES,
        404: problem_response_doc(
            "No such enrollment or package (withdrawal_subject_not_found)."
        ),
        409: problem_response_doc(
            f"{_CONFIRM_409}; nothing paid is freed (withdrawal_nothing_to_refund)."
        ),
        422: problem_response_doc(
            f"{_CONFIRM_422}; the method is unknown or deactivated"
            " (payment_method_unavailable); the date is after today"
            " (refunded_on_in_future) or before a source payment"
            f" (refunded_on_before_payment); or {_REUSED}."
        ),
    },
)
async def confirm_refund(
    principal: Annotated[Principal, require(REFUND_APPROVE)],
    idem: Annotated[IdempotentRequest, idempotent()],
    body: WithdrawalRefundConfirm,
    uow: UnitOfWorkDep,
) -> Response:
    """Confirm a withdrawal as a refund: the same as account credit, then
    the paid money the credits free is paid back (one refund per source
    payment, with the date, method and reference). Older, unrelated credit
    isn't refunded. Withdrawing an enrollment also needs schedule.manage.
    Needs a password reauthentication in the last 5 minutes."""
    confirmed = await withdrawals.confirm_withdrawal(
        uow, principal, body, outcome="refund"
    )
    return await idem.respond(confirmed, status_code=201)


@router.get(
    "/api/v1/billing/withdrawals",
    responses={
        **PERMISSION_RESPONSES,
        422: problem_response_doc(
            "The query is not valid (validation_error): a malformed id, or"
            " `limit`/`offset` out of range."
        ),
    },
)
async def list_withdrawals(
    principal: Annotated[Principal, require(BILLING_VIEW)],
    page: OffsetPagination,
    uow: UnitOfWorkDep,
    family_id: uuid.UUID | None = None,
    student_id: uuid.UUID | None = None,
    group_enrollment_id: uuid.UUID | None = None,
    cohort_enrollment_id: uuid.UUID | None = None,
    package_id: uuid.UUID | None = None,
) -> OffsetPage[WithdrawalItem]:
    """The confirmed withdrawals, newest first, optionally of one family,
    student, enrollment or package."""
    return await withdrawals.list_withdrawals(
        uow.session,
        family_id=family_id,
        student_id=student_id,
        group_enrollment_id=group_enrollment_id,
        cohort_enrollment_id=cohort_enrollment_id,
        package_id=package_id,
        limit=page.limit,
        offset=page.offset,
    )


@router.get(
    "/api/v1/billing/withdrawals/{withdrawal_id}",
    responses={
        **PERMISSION_RESPONSES,
        404: problem_response_doc("No such withdrawal (not_found)."),
    },
)
async def get_withdrawal(
    principal: Annotated[Principal, require(BILLING_VIEW)],
    withdrawal_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> WithdrawalDetail:
    """One withdrawal with the preview it was confirmed on, its outcome and
    amounts, the ids of its credits, refunds and automatic session credits,
    who confirmed it and why."""
    return await withdrawals.get_withdrawal(uow.session, withdrawal_id)
