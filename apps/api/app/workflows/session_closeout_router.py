"""Session closeout routes (#77; :mod:`app.workflows.session_closeout`
gives the orders and the handler).

The routes live here, next to the workflow they call, because a module's
router may not import ``app.workflows`` (ADR-0002).

- ``GET /api/v1/class-sessions/{id}/closeout``: ``authenticated()`` + the
  record policy (the session's current tutor, or a ``schedule.view``
  holder; anyone else ``404``).
- ``POST /api/v1/class-sessions/{id}/closeout``: ``authenticated()`` + the
  record policy: the current tutor only (``403 closeout_tutor_only`` for
  other visible callers, ``404`` for the rest).
- ``PUT /api/v1/class-sessions/{id}/closeout`` (#78 D): ``authenticated()``
  + the record policy: the current tutor who submitted it.
- ``POST /api/v1/class-sessions/{id}/closeout/approve``,
  ``…/request-changes`` and ``…/corrections`` (#78 C):
  ``require("teaching_hours.approve")`` (Owner, Administrator, Academic
  Coordinator), checked before anything is read.
"""

import uuid
from typing import Annotated, Final

from fastapi import APIRouter

from app.core.authz import PERMISSION_RESPONSES, Principal, authenticated, require
from app.core.errors import problem_response_doc
from app.core.uow import UnitOfWorkDep
from app.modules.academics.schemas import (
    CloseoutApprove,
    CloseoutCorrection,
    CloseoutRequestChanges,
    CloseoutResubmit,
)
from app.workflows import session_closeout as workflow
from app.workflows import session_closeout_review as review
from app.workflows.session_closeout_schemas import (
    SessionCloseoutSubmit,
    SessionCloseoutView,
)

router = APIRouter(prefix="/api/v1/class-sessions", tags=["closeout"])

TEACHING_HOURS_APPROVE: Final = "teaching_hours.approve"

_NOT_FOUND: Final = problem_response_doc(
    "No such class session, or not one whose closeout you may see (not_found)."
)
_REVIEW_NOT_FOUND: Final = problem_response_doc(
    "No such class session, or it has no closeout (not_found)."
)
_REVIEW_403: Final = problem_response_doc(
    "You lack teaching_hours.approve (permission_denied, checked first); the"
    " closeout is your own and you aren't the Owner (closeout_self_review);"
    " a failed Origin or X-CSRF-Token check (csrf_failed);"
    " privacy_notice_required."
)


@router.get(
    "/{class_session_id}/closeout",
    responses={**PERMISSION_RESPONSES, 404: _NOT_FOUND},
)
async def get_class_session_closeout(
    principal: Annotated[Principal, authenticated()],
    class_session_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> SessionCloseoutView:
    """A session's closeout: whether it can be submitted now, how complete
    the attendance is and, once submitted, the actual minutes, the summary
    (for the tutor and academic reviewers only), who submitted it and its
    inbox item. For the session's current tutor, or staff with
    schedule.view."""
    return await workflow.get_session_closeout(uow.session, principal, class_session_id)


@router.post(
    "/{class_session_id}/closeout",
    status_code=201,
    responses={
        **PERMISSION_RESPONSES,
        403: problem_response_doc(
            "Only the session's current tutor submits its closeout"
            " (closeout_tutor_only); a failed Origin or X-CSRF-Token check"
            " (csrf_failed); privacy_notice_required."
        ),
        404: _NOT_FOUND,
        409: problem_response_doc(
            "The session changed since it was read (version_conflict); its"
            " closeout was already submitted (closeout_already_submitted);"
            " it isn't confirmed (session_not_closable, details.state); it"
            " hasn't started (session_not_started); or a participant has no"
            " attendance mark (closeout_attendance_incomplete,"
            " details.missing_student_ids). Nothing is changed."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): a missing or unknown"
            " field, actual_minutes outside 0-720, a summary field over 2000"
            " characters (too_long), or a required summary field left empty"
            " (summary_required at summary.topics, summary.performance or"
            " summary.next_steps; optional when every participant is"
            " absent). Nothing is changed."
        ),
    },
)
async def submit_class_session_closeout(
    principal: Annotated[Principal, authenticated()],
    class_session_id: uuid.UUID,
    body: SessionCloseoutSubmit,
    uow: UnitOfWorkDep,
) -> SessionCloseoutView:
    """Close out a class once it has started: with every participant's
    attendance marked, store the actual teaching minutes and the summary.
    The session becomes completed and a pending item goes to the approval
    inbox (Owners, Administrators, Academic Coordinators). Submitted hours
    are not approved."""
    return await workflow.submit_session_closeout(
        uow.session,
        principal=principal,
        class_session_id=class_session_id,
        body=body,
    )


@router.put(
    "/{class_session_id}/closeout",
    responses={
        **PERMISSION_RESPONSES,
        403: problem_response_doc(
            "Only the session's current tutor who submitted the closeout"
            " changes it (closeout_tutor_only); a failed Origin or"
            " X-CSRF-Token check (csrf_failed); privacy_notice_required."
        ),
        404: problem_response_doc(
            "No such class session, not one whose closeout you may see, or it"
            " has no closeout (not_found)."
        ),
        409: problem_response_doc(
            "The closeout changed since it was read (version_conflict); it is"
            " already under review (closeout_under_review); or a participant"
            " has no attendance mark (closeout_attendance_incomplete,"
            " details.missing_student_ids). Nothing is changed."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): actual_minutes outside"
            " 0-720, a summary field over 2000 characters (too_long) or a"
            " required one left empty (summary_required), a reason over 1000"
            " characters (reason_too_long) or none for a correction of an"
            " approved closeout (reason_required); or nothing changed in an"
            " approved closeout (closeout_unchanged). Nothing is changed."
        ),
    },
)
async def resubmit_class_session_closeout(
    principal: Annotated[Principal, authenticated()],
    class_session_id: uuid.UUID,
    body: CloseoutResubmit,
    uow: UnitOfWorkDep,
) -> SessionCloseoutView:
    """Resubmit your closeout after changes were requested, or ask for a
    correction of an approved one (with a reason). It goes back to the
    approval inbox; an approval stays in force until a reviewer approves
    the correction."""
    return await review.resubmit_closeout(
        uow.session,
        principal=principal,
        class_session_id=class_session_id,
        body=body,
    )


@router.post(
    "/{class_session_id}/closeout/approve",
    responses={
        **PERMISSION_RESPONSES,
        403: _REVIEW_403,
        404: _REVIEW_NOT_FOUND,
        409: problem_response_doc(
            "The closeout changed since it was read (version_conflict) or"
            " isn't waiting for review (closeout_not_submitted)."
        ),
    },
)
async def approve_class_session_closeout(
    principal: Annotated[Principal, require(TEACHING_HOURS_APPROVE)],
    class_session_id: uuid.UUID,
    body: CloseoutApprove,
    uow: UnitOfWorkDep,
) -> SessionCloseoutView:
    """Approve a submitted closeout: an immutable approval revision with the
    actual minutes and the attendance as they are now. A later approval
    supersedes it; it is never edited."""
    return await review.approve_closeout(
        uow.session,
        principal=principal,
        class_session_id=class_session_id,
        body=body,
    )


@router.post(
    "/{class_session_id}/closeout/request-changes",
    responses={
        **PERMISSION_RESPONSES,
        403: _REVIEW_403,
        404: _REVIEW_NOT_FOUND,
        409: problem_response_doc(
            "The closeout changed since it was read (version_conflict) or"
            " isn't waiting for review (closeout_not_submitted)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): no reason"
            " (reason_required) or one over 1000 characters (reason_too_long)."
        ),
    },
)
async def request_class_session_closeout_changes(
    principal: Annotated[Principal, require(TEACHING_HOURS_APPROVE)],
    class_session_id: uuid.UUID,
    body: CloseoutRequestChanges,
    uow: UnitOfWorkDep,
) -> SessionCloseoutView:
    """Send a submitted closeout back to its tutor with a reason. The tutor
    can then fix the attendance, minutes and summary and resubmit."""
    return await review.request_closeout_changes(
        uow.session,
        principal=principal,
        class_session_id=class_session_id,
        body=body,
    )


@router.post(
    "/{class_session_id}/closeout/corrections",
    responses={
        **PERMISSION_RESPONSES,
        403: _REVIEW_403,
        404: _REVIEW_NOT_FOUND,
        409: problem_response_doc(
            "The closeout changed since it was read (version_conflict), or"
            " changes were requested and the tutor is editing it"
            " (closeout_with_tutor)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): actual_minutes outside"
            " 0-720, a summary field over 2000 characters (too_long), a"
            " required one left empty (summary_required), no reason"
            " (reason_required) or one over 1000 characters (reason_too_long);"
            " or the correction changes nothing (closeout_unchanged)."
        ),
    },
)
async def correct_class_session_closeout(
    principal: Annotated[Principal, require(TEACHING_HOURS_APPROVE)],
    class_session_id: uuid.UUID,
    body: CloseoutCorrection,
    uow: UnitOfWorkDep,
) -> SessionCloseoutView:
    """Correct the minutes or the summary of a closeout with a reason. On an
    approved closeout the correction is a new approval that supersedes the
    current one (also with no field changed, to re-approve changed
    attendance)."""
    return await review.correct_closeout(
        uow.session,
        principal=principal,
        class_session_id=class_session_id,
        body=body,
    )
