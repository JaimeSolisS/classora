"""Payroll workflow routes (#110; ``app.workflows.payroll_workflow``; ADR-0017
E10, E12).

- ``GET /api/v1/payroll/periods``: ``authenticated()`` and a check for
  ``payroll.view`` or ``payroll.finalize``, offset pagination. A delegated
  Administrator lists ``prepared`` and ``finalized`` periods only (#110 E8).
- ``POST …/{period_id}/prepare``: ``require("payroll.prepare")``, no body. No key
  and no reauthentication: the status check refuses a second one.
- ``POST …/{period_id}/return``: ``authenticated()`` and a check for
  ``payroll.prepare`` or ``payroll.finalize``, no key and no reauthentication
  (#110 E7).
- ``POST …/{period_id}/finalize``: ``require("payroll.finalize")``,
  ``require_recent_auth``, ``Idempotency-Key``. Check order: ``401`` → ``403
  csrf_failed`` → ``403 permission_denied`` → ``403 reauth_required`` → ``400`` key
  → replay / ``422 idempotency_key_reused`` → ``422`` (the body) → the workflow.
  The route never commits; it ends with ``idem.respond``.

Everyone else gets ``403``, a tutor asking for their own period too: #113 is
their read. The two checks are dependencies, so they come before the body, as
``require`` does.
"""

import uuid
from typing import Annotated, Final

from fastapi import APIRouter, Depends, Response

from app.core.authz import (
    PERMISSION_RESPONSES,
    Principal,
    authenticated,
    require,
)
from app.core.errors import problem_response_doc
from app.core.idempotency import (
    IDEMPOTENCY_RESPONSES,
    IdempotentRequest,
    hashable_body,
    idempotent,
)
from app.core.pagination import OffsetPage, OffsetPagination
from app.core.reauth import REAUTH_RESPONSES, require_recent_auth
from app.core.uow import UnitOfWorkDep
from app.modules.payroll.schemas import (
    PayrollPeriodFinalize,
    PayrollPeriodReturn,
    PayrollPeriodTransition,
    PeriodStatus,
)
from app.workflows import payroll_workflow
from app.workflows.payroll_calculation_router import period_reader
from app.workflows.payroll_workflow_schemas import PayrollPeriodItem

PAYROLL_PREPARE: Final = "payroll.prepare"
PAYROLL_FINALIZE: Final = "payroll.finalize"

router = APIRouter(prefix="/api/v1/payroll/periods", tags=["payroll"])

_NOT_FOUND = problem_response_doc("No such period (not_found).")
_NOT_PREPARED: Final = (
    "The period isn't prepared (payroll_period_not_prepared, with details.status)"
)
_UNRESOLVED: Final = (
    "work with no basis stops it (payroll_unresolved_work, with details.count and"
    " at most 20 details.items)"
)


async def returner(
    principal: Annotated[Principal, authenticated()],
) -> Principal:
    """``403`` unless the caller holds either permission, before the body."""
    payroll_workflow.ensure_can_return(principal)
    return principal


@router.get(
    "",
    responses={
        **PERMISSION_RESPONSES,
        422: problem_response_doc("A filter, limit or offset isn't valid."),
    },
)
async def list_payroll_periods(
    principal: Annotated[Principal, Depends(period_reader)],
    uow: UnitOfWorkDep,
    page: OffsetPagination,
    status: PeriodStatus | None = None,
    tutor_user_id: uuid.UUID | None = None,
) -> OffsetPage[PayrollPeriodItem]:
    """Payroll periods, newest end date first. A reviewer with `payroll.finalize`
    alone sees prepared and finalized periods. `total` is null on a period whose
    lines sum beyond the money range."""
    return await payroll_workflow.list_periods(
        uow.session, principal, page=page, status=status, tutor_user_id=tutor_user_id
    )


@router.post(
    "/{period_id}/prepare",
    responses={
        **PERMISSION_RESPONSES,
        404: _NOT_FOUND,
        409: problem_response_doc(
            "The period isn't open (payroll_period_not_open, with details.status);"
            " it hasn't ended (payroll_period_not_ended, with details.ends_on); a"
            " line or the total is beyond the money range"
            f" (payroll_amount_out_of_range); {_UNRESOLVED}; or the total is below"
            " zero (payroll_negative_total). A refusal changes nothing."
        ),
    },
)
async def prepare_payroll_period(
    period_id: uuid.UUID,
    principal: Annotated[Principal, require(PAYROLL_PREPARE)],
    uow: UnitOfWorkDep,
) -> PayrollPeriodTransition:
    """Recalculate an open period that has ended, and send it for review. It
    opens an item in the approval inbox. Periods may be prepared in any order."""
    return await payroll_workflow.prepare(uow.session, principal, period_id)


@router.post(
    "/{period_id}/return",
    responses={
        **PERMISSION_RESPONSES,
        404: _NOT_FOUND,
        409: problem_response_doc(f"{_NOT_PREPARED}."),
        422: problem_response_doc(
            "The body is not valid (validation_error): text_empty, text_too_long"
            " or text_invalid on the reason."
        ),
    },
)
async def return_payroll_period(
    period_id: uuid.UUID,
    principal: Annotated[Principal, Depends(returner)],
    body: PayrollPeriodReturn,
    uow: UnitOfWorkDep,
) -> PayrollPeriodTransition:
    """Send a prepared period back to open, with a reason. Its lines stay until
    the next recalculation, and its review item is cancelled."""
    return await payroll_workflow.return_period(
        uow.session, principal, period_id, body.reason
    )


@router.post(
    "/{period_id}/finalize",
    dependencies=[require_recent_auth(PAYROLL_FINALIZE)],
    response_model=PayrollPeriodTransition,
    responses={
        **PERMISSION_RESPONSES,
        **REAUTH_RESPONSES,
        **IDEMPOTENCY_RESPONSES,
        403: problem_response_doc(
            "Not allowed (permission_denied), the session wasn't reauthenticated"
            " (reauth_required), or a second person has to do it"
            " (payroll_self_finalize, with details.reason preparer or own_pay; an"
            " Owner is excepted)."
        ),
        404: _NOT_FOUND,
        409: problem_response_doc(
            f"{_NOT_PREPARED}; it was prepared again after this review"
            " (payroll_review_outdated); an earlier period isn't finalized"
            " (payroll_period_out_of_order, with details.earliest_period_id and"
            " details.status); the facts changed (payroll_period_stale, with"
            f" details.added, removed and changed); {_UNRESOLVED}; or the total is"
            " beyond the money range (payroll_amount_out_of_range)."
        ),
    },
)
async def finalize_payroll_period(
    period_id: uuid.UUID,
    principal: Annotated[Principal, require(PAYROLL_FINALIZE)],
    _: Annotated[None, Depends(hashable_body)],
    idem: Annotated[IdempotentRequest, idempotent()],
    body: PayrollPeriodFinalize,
    uow: UnitOfWorkDep,
) -> Response:
    """Lock a prepared period: from here on it never changes. Send the
    `prepared_at` of the review you read, a new `Idempotency-Key` per decision and
    the same one to retry it. A tutor's periods finalize oldest first, and the
    tutor is notified."""
    transition = await payroll_workflow.finalize(
        uow.session, principal, period_id, body.prepared_at
    )
    return await idem.respond(transition, status_code=200)
