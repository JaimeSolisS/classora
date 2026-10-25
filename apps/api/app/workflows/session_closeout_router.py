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
"""

import uuid
from typing import Annotated, Final

from fastapi import APIRouter

from app.core.authz import PERMISSION_RESPONSES, Principal, authenticated
from app.core.errors import problem_response_doc
from app.core.uow import UnitOfWorkDep
from app.workflows import session_closeout as workflow
from app.workflows.session_closeout_schemas import (
    SessionCloseoutSubmit,
    SessionCloseoutView,
)

router = APIRouter(prefix="/api/v1/class-sessions", tags=["closeout"])

_NOT_FOUND: Final = problem_response_doc(
    "No such class session, or not one whose closeout you may see (not_found)."
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
