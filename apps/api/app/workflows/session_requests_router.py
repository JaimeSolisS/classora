"""Staff review routes of session requests (#60;
:mod:`app.workflows.confirm_session_request`; ADR-0002, ADR-0005 D9).

The routes live here, next to the workflow they call, because a module's
router may not import ``app.workflows`` (ADR-0002). All three need
``schedule.manage`` (Owners and Administrators: "review session …
requests"), so they are outside the family record policy of
``scheduling.requests_router``.

- ``GET /api/v1/session-requests/{request_id}/review``: the staff view.
- ``POST /api/v1/session-requests/{request_id}/confirm``: create the
  session (offered to the chosen tutor) and confirm the request; an
  ``override`` also needs ``schedule.override`` (``403`` after the body's
  ``422``, before any lock, #49).
- ``POST /api/v1/session-requests/{request_id}/reject``: reject with a
  reason the family sees.
"""

import uuid
from typing import Annotated, Final

from fastapi import APIRouter

from app.core.authz import (
    PERMISSION_RESPONSES,
    Principal,
    ensure_permission,
    require,
)
from app.core.errors import problem_response_doc
from app.core.uow import UnitOfWorkDep
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.request_schemas import (
    SessionRequestConfirm,
    SessionRequestConfirmed,
    SessionRequestReject,
    SessionRequestReview,
)
from app.workflows import confirm_session_request as workflow

SCHEDULE_MANAGE: Final = "schedule.manage"
SCHEDULE_OVERRIDE: Final = "schedule.override"

router = APIRouter(prefix="/api/v1/session-requests", tags=["session-requests"])

_NOT_FOUND = problem_response_doc("No such session request (not_found).")
_NOT_PENDING = (
    " The request changed since you loaded it (version_conflict) or is no"
    " longer pending (session_request_not_pending, details.status)."
)


@router.get("/{request_id}/review", responses={**PERMISSION_RESPONSES, 404: _NOT_FOUND})
async def get_session_request_review(
    principal: Annotated[Principal, require(SCHEDULE_MANAGE)],
    request_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> SessionRequestReview:
    """A session request as staff review it: the student, the subject, the
    requested time, the requester and the note, its inbox item and, once
    decided, the created session or the rejection reason. `can_decide` is
    true while it is pending and its time hasn't started."""
    return await scheduling.get_session_request_review(uow.session, request_id)


@router.post(
    "/{request_id}/confirm",
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
            "Nothing is created and the request stays pending."
            + _NOT_PENDING
            + " The requested time has started (session_request_started);"
            " the session breaks scheduling rules now (scheduling_conflict,"
            " `conflicts` lists every one: a hard conflict, or a soft one the"
            " `override` doesn't list, refuses)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): a missing"
            " `tutor_user_id` or an unknown field; the `override`"
            " (reason_required, reason_too_long, codes_required,"
            " duplicate_code, code_not_overridable, code_unknown); the"
            " references (student_inactive, subject_inactive, tutor_unknown,"
            " tutor_inactive, classroom_unknown, classroom_inactive), all in"
            " one response; or `respond_by` (respond_by_invalid)."
        ),
    },
)
async def confirm_session_request(
    principal: Annotated[Principal, require(SCHEDULE_MANAGE)],
    request_id: uuid.UUID,
    body: SessionRequestConfirm,
    uow: UnitOfWorkDep,
) -> SessionRequestConfirmed:
    """Confirm a pending request by choosing a qualified tutor (and
    optionally a room). The session is created through the same checks as
    scheduling one by hand, at the requested time, and **offered** to the
    tutor (who accepts or declines); the booking notice counts from when the
    family made the request. The request becomes `confirmed` (it stays so
    if the tutor later declines), its inbox item is approved and the
    requester is notified."""
    if body.override is not None:
        ensure_permission(principal, SCHEDULE_OVERRIDE)
    return await workflow.confirm_session_request(
        uow.session, principal, request_id, body
    )


@router.post(
    "/{request_id}/reject",
    responses={
        **PERMISSION_RESPONSES,
        404: _NOT_FOUND,
        409: problem_response_doc(_NOT_PENDING.strip()),
        422: problem_response_doc(
            "The body is not valid (validation_error): `reason` missing or"
            " empty (missing) or longer than 1000 characters"
            " (string_too_long), or an unknown field."
        ),
    },
)
async def reject_session_request(
    principal: Annotated[Principal, require(SCHEDULE_MANAGE)],
    request_id: uuid.UUID,
    body: SessionRequestReject,
    uow: UnitOfWorkDep,
) -> SessionRequestReview:
    """Reject a pending request with a reason. The family sees the reason
    in their requests (not in the notification); the inbox item is rejected
    with it and the student's pending limit is freed. Allowed after the
    requested time has started."""
    return await workflow.reject_session_request(
        uow.session, principal, request_id, body
    )
