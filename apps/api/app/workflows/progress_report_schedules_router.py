"""Progress report schedule routes (#316 S1-S3;
:mod:`app.workflows.progress_report_schedules` gives the orders).

The routes live here, next to the workflow they call, because a module's
router may not import ``app.workflows`` (ADR-0002). Every route is
``require("progress_report.review")`` (Owner, Administrator, Academic
Coordinator), checked before anything is read (E4): everyone else gets
``403 permission_denied``; an unknown student or schedule is ``404``.

- ``GET`` / ``POST /api/v1/students/{student_id}/progress-report-schedules``
  (S1, S2);
- ``PATCH /api/v1/progress-report-schedules/{schedule_id}`` (S3).
"""

import uuid
from typing import Annotated, Final

from fastapi import APIRouter

from app.core.authz import PERMISSION_RESPONSES, Principal, require
from app.core.errors import problem_response_doc
from app.core.uow import UnitOfWorkDep
from app.workflows import progress_report_schedules as workflow
from app.workflows.progress_report_schedule_schemas import (
    ProgressReportScheduleCreate,
    ProgressReportScheduleList,
    ProgressReportScheduleUpdate,
    ProgressReportScheduleView,
)

student_router = APIRouter(prefix="/api/v1/students", tags=["progress-reports"])
router = APIRouter(
    prefix="/api/v1/progress-report-schedules", tags=["progress-reports"]
)

PROGRESS_REPORT_REVIEW: Final = "progress_report.review"

_EXISTS: Final = problem_response_doc(
    "The tutor already has an active schedule of this cadence for this student"
    " and subject (progress_report_schedule_exists; details.schedule_id)."
)


@student_router.get(
    "/{student_id}/progress-report-schedules",
    responses={
        **PERMISSION_RESPONSES,
        404: problem_response_doc("No such student (not_found)."),
    },
)
async def list_progress_report_schedules(
    _: Annotated[Principal, require(PROGRESS_REPORT_REVIEW)],
    student_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> ProgressReportScheduleList:
    """The student's weekly and monthly report schedules (active first, at
    most 100), each with what exists for its latest ended period and why no
    draft would be created now, and the tutors who could own a new one."""
    return await workflow.list_schedules(uow.session, student_id)


@student_router.post(
    "/{student_id}/progress-report-schedules",
    status_code=201,
    responses={
        **PERMISSION_RESPONSES,
        404: problem_response_doc("No such student (not_found)."),
        409: _EXISTS,
        422: problem_response_doc(
            "The student is inactive (student_inactive), or the body is not"
            " valid (validation_error): schedule_author_invalid at"
            " body.author_user_id; subject_unknown or"
            " schedule_subject_not_assigned at body.subject_id;"
            " schedule_starts_on_invalid at body.starts_on."
        ),
    },
)
async def create_progress_report_schedule(
    principal: Annotated[Principal, require(PROGRESS_REPORT_REVIEW)],
    student_id: uuid.UUID,
    body: ProgressReportScheduleCreate,
    uow: UnitOfWorkDep,
) -> ProgressReportScheduleView:
    """Have a tutor who currently teaches the student get an empty draft
    report after each week or month ends. A reviewer still approves each
    report before the family sees it. Nobody is notified now."""
    return await workflow.create_schedule(
        uow.session, principal=principal, student_id=student_id, body=body
    )


@router.patch(
    "/{schedule_id}",
    responses={
        **PERMISSION_RESPONSES,
        404: problem_response_doc("No such schedule (not_found)."),
        409: problem_response_doc(
            "The schedule changed since it was read (version_conflict), or"
            " another active schedule has the same tutor, cadence and subject"
            " (progress_report_schedule_exists; details.schedule_id)."
        ),
        422: problem_response_doc(
            "Reactivating: the student is inactive (student_inactive), the"
            " tutor no longer teaches them (schedule_author_invalid), the"
            " subject is inactive (subject_unknown) or no longer taught to them"
            " (schedule_subject_not_assigned)."
        ),
    },
)
async def update_progress_report_schedule(
    _: Annotated[Principal, require(PROGRESS_REPORT_REVIEW)],
    schedule_id: uuid.UUID,
    body: ProgressReportScheduleUpdate,
    uow: UnitOfWorkDep,
) -> ProgressReportScheduleView:
    """Deactivate a schedule (no new drafts; reports already written stay)
    or reactivate it (the next draft is for the first period that ends from
    now on)."""
    return await workflow.update_schedule(
        uow.session, schedule_id=schedule_id, body=body
    )
