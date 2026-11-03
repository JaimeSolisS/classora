"""Manual payroll adjustments and carry-forward routes (M7-08c;
``app.workflows.payroll_manual_adjustments``; ADR-0017 E11, E12 and its M7-08c
notes).

- ``POST /api/v1/payroll/tutors/{tutor_user_id}/adjustments``:
  ``require("payroll.manage")``, ``require_recent_auth("finance.correction")``,
  ``Idempotency-Key`` (E4). Check order: ``401`` → ``403 csrf_failed`` → ``403
  permission_denied`` → ``403 reauth_required`` → ``422 text_invalid`` for a
  lone surrogate (the key's hash needs UTF-8, #109 QA D2) → ``400`` key →
  replay / ``422 idempotency_key_reused`` → ``422`` (the body) → the workflow
  (``403 payroll_own_compensation``, an Owner excepted → ``404`` → the rules).
  The framework puts the body after the key (M7-08c D1), so a refused
  reauthentication or body claims no key.
- ``POST /api/v1/payroll/periods/{period_id}/carry-forward``:
  ``require("payroll.prepare")``, ``Idempotency-Key``, no body, no
  reauthentication, own pay allowed (E4).

Everyone else gets ``403``: a delegated Administrator (``payroll.finalize``
only), a Coordinator, a tutor, families. The routes never commit; they end
with ``idem.respond``. Neither response holds an amount or a reason (E6).
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
    PayrollAdjustmentCreate,
    PayrollAdjustmentPosted,
    PayrollCarryForward,
)
from app.workflows import payroll_manual_adjustments

PAYROLL_MANAGE: Final = "payroll.manage"
PAYROLL_PREPARE: Final = "payroll.prepare"
CORRECTION: Final = "finance.correction"

router = APIRouter(prefix="/api/v1/payroll", tags=["payroll"])

_REUSED: Final = (
    "the Idempotency-Key was already used for a different request"
    " (idempotency_key_reused)"
)


@router.post(
    "/tutors/{tutor_user_id}/adjustments",
    status_code=201,
    response_model=PayrollAdjustmentPosted,
    dependencies=[require_recent_auth(CORRECTION)],
    responses={
        **PERMISSION_RESPONSES,
        **REAUTH_RESPONSES,
        **IDEMPOTENCY_RESPONSES,
        403: problem_response_doc(
            "Not allowed (permission_denied), the session wasn't reauthenticated"
            " (reauth_required), or the caller's own pay"
            " (payroll_own_compensation; only an Owner may write their own)."
        ),
        404: problem_response_doc(
            "No such tutor, or origin_line_id is no line of this tutor (not_found)."
        ),
        409: problem_response_doc(
            "The line isn't in a finalized period (payroll_line_not_finalized);"
            " the session isn't unresolved work of this tutor"
            " (payroll_work_not_unresolved); it is work before the tutor's first"
            " period, paid with a bonus (payroll_work_before_first_period); an"
            " adjustment already pays it (payroll_work_settled); or the tutor has"
            " no payroll period yet (payroll_no_period). A refusal changes"
            " nothing."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error: amount_zero, money_*,"
            " text_empty, text_too_long, text_invalid,"
            f" adjustment_references_both, …); or {_REUSED}."
        ),
    },
)
async def post_payroll_adjustment(
    tutor_user_id: uuid.UUID,
    principal: Annotated[Principal, require(PAYROLL_MANAGE)],
    _: Annotated[None, Depends(hashable_body)],
    idem: Annotated[IdempotentRequest, idempotent()],
    body: PayrollAdjustmentCreate,
    uow: UnitOfWorkDep,
) -> Response:
    """Add a manual adjustment, positive or negative, with a reason the tutor
    reads on the payslip. Name a line of a finalized period to correct it, or an
    unresolved session to pay it by hand (it is then settled: nothing pays it
    again), or neither. It lands in the tutor's earliest open period and shows
    on its lines at once. Needs a password reauthentication in the last 5
    minutes. Send a new `Idempotency-Key` per adjustment and the same one to
    retry it."""
    posted = await payroll_manual_adjustments.post_manual(
        uow.session, principal, tutor_user_id, body
    )
    return await idem.respond(posted, status_code=201)


@router.post(
    "/periods/{period_id}/carry-forward",
    status_code=201,
    response_model=PayrollCarryForward,
    responses={
        **PERMISSION_RESPONSES,
        **IDEMPOTENCY_RESPONSES,
        404: problem_response_doc("No such period (not_found)."),
        409: problem_response_doc(
            "The period isn't open (payroll_period_not_open, with"
            " details.status); it hasn't ended (payroll_period_not_ended, with"
            " details.ends_on); a line or the total is beyond the money range"
            " (payroll_amount_out_of_range); or its total after a recalculation"
            " isn't below zero (payroll_total_not_negative). A refusal changes"
            " nothing."
        ),
        422: problem_response_doc(f"{_REUSED[0].upper()}{_REUSED[1:]}."),
    },
)
async def carry_payroll_period_forward(
    period_id: uuid.UUID,
    principal: Annotated[Principal, require(PAYROLL_PREPARE)],
    idem: Annotated[IdempotentRequest, idempotent()],
    uow: UnitOfWorkDep,
) -> Response:
    """Move an ended open period's negative total to the tutor's next open
    period (opened if there is none): the period is recalculated, then gets the
    amount that brings it to 0.00, and the next one the same amount taken back.
    Both still need prepare and finalize. Send a new `Idempotency-Key` per
    carry and the same one to retry it."""
    carried = await payroll_manual_adjustments.carry_forward(
        uow.session, principal, period_id
    )
    return await idem.respond(carried, status_code=201)
