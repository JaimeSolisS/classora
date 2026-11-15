"""Staff review routes of change requests (#62 C;
:mod:`app.workflows.change_request_decision`; ADR-0002, ADR-0005 D9).

The routes live here, next to the workflow they call, because a module's
router may not import ``app.workflows`` (ADR-0002). All three need
``schedule.manage`` (Owners and Administrators: "review … change and
cancellation requests"), so they are outside the record policy of
``scheduling.change_requests_router``.

- ``GET /api/v1/change-requests/{change_request_id}/review``: the staff view.
- ``POST /api/v1/change-requests/{change_request_id}/approve``: apply the
  change (cancel, or move to the proposed time) and approve, optionally as
  an exception (``excused``, with a reason); an ``override`` (a reschedule's
  soft conflicts) also needs ``schedule.override`` (``403`` after the body's
  ``422``, before any lock, #49).
- ``POST /api/v1/change-requests/{change_request_id}/reject``: reject with
  a reason the requester sees.
"""

import uuid
from typing import Annotated, Final

from fastapi import APIRouter, BackgroundTasks, Depends
from procrastinate import App

from app.core.authz import (
    PERMISSION_RESPONSES,
    Principal,
    ensure_permission,
    require,
)
from app.core.errors import FieldError, problem_response_doc
from app.core.jobs import defer_schedule_reconciliation, get_job_app
from app.core.uow import UnitOfWorkDep
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.change_request_schemas import (
    ChangeRequestApprove,
    ChangeRequestApproved,
    ChangeRequestReject,
    ChangeRequestReview,
)
from app.modules.scheduling.errors import SessionValidationError
from app.workflows import change_request_decision as workflow

SCHEDULE_MANAGE: Final = "schedule.manage"
SCHEDULE_OVERRIDE: Final = "schedule.override"

router = APIRouter(prefix="/api/v1/change-requests", tags=["change-requests"])

_NOT_FOUND = problem_response_doc("No such change request (not_found).")
_STALE = (
    " The request changed since you loaded it (version_conflict) or is no"
    " longer pending (change_request_not_pending, details.status)."
)


@router.get(
    "/{change_request_id}/review",
    responses={**PERMISSION_RESPONSES, 404: _NOT_FOUND},
)
async def get_change_request_review(
    principal: Annotated[Principal, require(SCHEDULE_MANAGE)],
    change_request_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> ChangeRequestReview:
    """A change request as staff review it: who asked, the session as it is
    now (tutor and room included), the time when it was asked, the proposed
    time, the note, the policy version and its outcome (and, once approved,
    the effective outcome), its inbox item and the decision. `can_decide`
    is true while it is pending, the session is still confirmed and it
    hasn't started."""
    return await scheduling.get_change_request_review(uow.session, change_request_id)


@router.post(
    "/{change_request_id}/approve",
    responses={
        **PERMISSION_RESPONSES,
        403: problem_response_doc(
            "Permission denied (permission_denied): no schedule.manage, or an"
            " `override` without schedule.override. The privacy notice must be"
            " accepted first (privacy_notice_required); a failed Origin or"
            " X-CSRF-Token check (csrf_failed)."
        ),
        404: _NOT_FOUND,
        409: problem_response_doc(
            "Nothing changes and the request stays pending."
            + _STALE
            + " The session has started (change_request_session_started) or is"
            " no longer confirmed (change_request_session_changed,"
            " details.state); the proposed time breaks scheduling rules now"
            " (scheduling_conflict, `conflicts` lists every one)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): an exception without a"
            " reason (reason_required); a reason over 1000 characters"
            " (string_too_long); `classroom_id` or `override` on a cancellation"
            " (classroom_not_applicable, override_not_applicable); an exception"
            " on an already excused request (exception_not_applicable); the"
            " move's references or override codes."
        ),
    },
)
async def approve_change_request(
    principal: Annotated[Principal, require(SCHEDULE_MANAGE)],
    change_request_id: uuid.UUID,
    body: ChangeRequestApprove,
    uow: UnitOfWorkDep,
    background: BackgroundTasks,
    job_app: Annotated[App, Depends(get_job_app)],
) -> ChangeRequestApproved:
    """Approve a pending request: a cancellation cancels the session, a
    reschedule moves it to the proposed time (optionally to another room)
    through the same checks as a manual move, the booking notice counted
    from when the request was made. With `exception` (and a reason) the
    outcome becomes excused. The requester is notified; the others affected
    get the usual cancellation or reschedule notification."""
    if body.exception and body.reason is None:
        error: FieldError = {
            "path": ["body", "reason"],
            "code": "reason_required",
            "message": "Give a reason for the exception.",
        }
        raise SessionValidationError([error])
    if body.override is not None:
        ensure_permission(principal, SCHEDULE_OVERRIDE)
    approved = await workflow.approve_change_request(
        uow.session, principal, change_request_id, body
    )
    # #99 D9: after the commit (a background task runs after the response),
    # the session's charge is reconciled by a queued job.
    background.add_task(defer_schedule_reconciliation, job_app)
    return approved


@router.post(
    "/{change_request_id}/reject",
    responses={
        **PERMISSION_RESPONSES,
        404: _NOT_FOUND,
        409: problem_response_doc(_STALE.strip()),
        422: problem_response_doc(
            "The body is not valid (validation_error): `reason` missing or"
            " empty (missing) or longer than 1000 characters"
            " (string_too_long), or an unknown field."
        ),
    },
)
async def reject_change_request(
    principal: Annotated[Principal, require(SCHEDULE_MANAGE)],
    change_request_id: uuid.UUID,
    body: ChangeRequestReject,
    uow: UnitOfWorkDep,
) -> ChangeRequestReview:
    """Reject a pending request with a reason the requester sees. The
    session is untouched. Allowed after the session has started."""
    return await workflow.reject_change_request(
        uow.session, principal, change_request_id, body
    )
