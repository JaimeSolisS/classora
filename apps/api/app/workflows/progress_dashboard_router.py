"""Progress dashboard routes (#84; :mod:`app.workflows.progress_dashboard`
gives the record policy and the order).

The routes live here, next to the workflow they call, because a module's
router may not import ``app.workflows`` (ADR-0002). Both are
``authenticated()`` plus a record policy (#21); the ``404`` of a hidden
student is the body of an unknown id. Both are read-only.

- ``GET /api/v1/students/{id}/progress[?from&to&subject_id]``: live
  aggregates and monthly trends (staff with ``evaluation.manage``, the
  student's current tutors, the student and guardians with academic
  access).
- ``GET /api/v1/students/{id}/session-log[?from&to&subject_id&cursor&limit]``:
  the student's started sessions with their attendance and the approved
  summary (the same, plus a former tutor for the sessions they taught).
"""

import uuid
from datetime import date
from typing import Annotated, Final

from fastapi import APIRouter, Query

from app.core.authz import PERMISSION_RESPONSES, Principal, authenticated
from app.core.errors import problem_response_doc
from app.core.uow import UnitOfWorkDep
from app.modules.academics.service import progress_dashboard_rules as rules
from app.workflows import progress_dashboard as workflow
from app.workflows.progress_dashboard_schemas import SessionLog, StudentProgress

student_router = APIRouter(prefix="/api/v1/students", tags=["progress-dashboard"])

_CONSENT_403: Final = problem_response_doc(
    "The adult student hasn't given you access to their academic records"
    " (student_consent_required); privacy_notice_required."
)
type From = Annotated[
    date | None,
    Query(
        alias="from",
        description="First center-local date (inclusive).",
    ),
]
type To = Annotated[
    date | None, Query(description="Last center-local date (inclusive).")
]


@student_router.get(
    "/{student_id}/progress",
    responses={
        **PERMISSION_RESPONSES,
        403: _CONSENT_403,
        404: problem_response_doc(
            "No such student, or not one whose progress you may see (not_found)."
        ),
        422: problem_response_doc(
            "A query parameter is not valid (validation_error): 'from' after"
            " 'to' or before 2000-01-01 (range_invalid, on from), 'to' after"
            " today (range_in_future, on to), more than 24 calendar months"
            " (range_too_long, on from), an unknown subject (subject_unknown,"
            " on subject_id)."
        ),
    },
)
async def get_student_progress(
    principal: Annotated[Principal, authenticated()],
    student_id: uuid.UUID,
    uow: UnitOfWorkDep,
    from_: From = None,
    to: To = None,
    subject_id: uuid.UUID | None = None,
) -> StudentProgress:
    """A student's progress for a period (by default the last 12 calendar
    months up to today): attendance, evaluation and homework averages by
    subject and month, homework completion and learning-plan progress. For
    staff with evaluation.manage, the student's current tutors, the student
    and their guardians with access to their academic records."""
    return await workflow.student_progress(
        uow.session,
        principal,
        student_id,
        from_=from_,
        to=to,
        subject_id=subject_id,
    )


@student_router.get(
    "/{student_id}/session-log",
    responses={
        **PERMISSION_RESPONSES,
        400: problem_response_doc("The cursor is not valid (invalid_cursor)."),
        403: _CONSENT_403,
        404: problem_response_doc(
            "No such student, or not one whose classes you may see (not_found)."
        ),
        422: problem_response_doc(
            "A query parameter is not valid (validation_error): 'from' after"
            " 'to' (range_invalid, on from)."
        ),
    },
)
async def get_student_session_log(
    principal: Annotated[Principal, authenticated()],
    student_id: uuid.UUID,
    uow: UnitOfWorkDep,
    from_: From = None,
    to: To = None,
    subject_id: uuid.UUID | None = None,
    cursor: str | None = None,
    limit: Annotated[
        int,
        Query(ge=1, le=rules.LOG_LIMIT_MAX, description="1-50, default 20."),
    ] = rules.LOG_LIMIT_DEFAULT,
) -> SessionLog:
    """A student's started classes, newest first: the subject, the tutor,
    the student's own attendance and the class summary once a reviewer
    approved it. A former tutor sees only the classes they taught."""
    return await workflow.session_log(
        uow.session,
        principal,
        student_id,
        from_=from_,
        to=to,
        subject_id=subject_id,
        cursor=cursor,
        limit=limit,
    )
