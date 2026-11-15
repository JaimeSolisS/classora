"""Progress report routes (#83; :mod:`app.workflows.progress_reports` gives
the record policies and the orders).

The routes live here, next to the workflow they call, because a module's
router may not import ``app.workflows`` (ADR-0002). Every route is
``authenticated()`` plus a record policy (#21), and the ``404`` of a hidden
record is the body of an unknown id. The decision routes check that the
caller reads the report (``404``) before ``progress_report.review``
(``403 permission_denied``).

- ``POST`` / ``GET /api/v1/progress-reports``: create a draft (R1); the
  working list (R2).
- ``GET`` / ``PATCH`` / ``DELETE /api/v1/progress-reports/{id}`` (R3-R5),
  ``POST …/submit | approve | request-changes | retract`` (R6-R9).
- ``GET /api/v1/students/{id}/progress-reports[/{report_id}]`` (R10, R11):
  approved reports in the closed family-safe shape, for every audience.
"""

import uuid
from typing import Annotated, Final

from fastapi import APIRouter, Query, Response

from app.core.authz import PERMISSION_RESPONSES, Principal, authenticated
from app.core.errors import problem_response_doc
from app.core.uow import UnitOfWorkDep
from app.modules.academics.service import progress_report_rules as rules
from app.workflows import progress_reports as workflow
from app.workflows.progress_report_schemas import (
    ProgressReportCreate,
    ProgressReportFamilyPage,
    ProgressReportFamilyView,
    ProgressReportPage,
    ProgressReportReason,
    ProgressReportUpdate,
    ProgressReportVersion,
    ProgressReportView,
    ReportKind,
    ReportStatus,
)

router = APIRouter(prefix="/api/v1/progress-reports", tags=["progress-reports"])
student_router = APIRouter(prefix="/api/v1/students", tags=["progress-reports"])

_REPORT_NOT_FOUND: Final = problem_response_doc(
    "No such progress report, it was deleted, or it isn't one you may see (not_found)."
)
_AUTHOR_403: Final = problem_response_doc(
    "Only the author changes a report (progress_report_author_only); the"
    " student is no longer one of your current assignments"
    " (progress_report_read_only); csrf_failed; privacy_notice_required."
)
_REVIEW_403: Final = problem_response_doc(
    "You can't review progress reports (permission_denied), or it is your own"
    " report (progress_report_self_review; the Owner may); csrf_failed;"
    " privacy_notice_required."
)
_EDIT_409: Final = (
    "The report changed since it was read (version_conflict), is waiting for"
    " review (progress_report_under_review) or is approved or retracted"
    " (progress_report_closed)."
)
type Limit = Annotated[
    int, Query(ge=1, le=rules.LIST_LIMIT_MAX, description="1-50, default 20.")
]


# --- Working routes (R1-R9) -----------------------------------------------------------


@router.post(
    "",
    status_code=201,
    responses={
        **PERMISSION_RESPONSES,
        403: problem_response_doc(
            "Staff don't write progress reports (progress_report_tutor_only);"
            " you don't currently teach this student this subject"
            " (report_subject_not_assigned); csrf_failed; privacy_notice_required."
        ),
        404: problem_response_doc(
            "No such student, or not one of your current students (not_found)."
        ),
        409: problem_response_doc(
            "You already have a weekly or monthly report for this student,"
            " period and subject (progress_report_exists; details.report_id)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): too_long, subject_unknown"
            " (unknown or inactive subject), period_invalid, period_in_future."
        ),
    },
)
async def create_progress_report(
    principal: Annotated[Principal, authenticated()],
    body: ProgressReportCreate,
    uow: UnitOfWorkDep,
) -> ProgressReportView:
    """Start a draft progress report about one of your current students.
    Nobody sees it until you submit it, and the student and their guardians
    only once a reviewer approves it."""
    return await workflow.create_report(uow.session, principal=principal, body=body)


@router.get(
    "",
    responses={
        **PERMISSION_RESPONSES,
        404: problem_response_doc(
            "Only tutors and progress report reviewers have a working list (not_found)."
        ),
    },
)
async def list_progress_reports(
    principal: Annotated[Principal, authenticated()],
    uow: UnitOfWorkDep,
    status: ReportStatus | None = None,
    student_id: uuid.UUID | None = None,
    kind: ReportKind | None = None,
    mine: bool = False,
    cursor: str | None = None,
    limit: Limit = rules.LIST_LIMIT_DEFAULT,
) -> ProgressReportPage:
    """Progress reports without their text: a tutor's own; for reviewers
    every report that isn't a draft (plus their own drafts). With
    `status=submitted` the oldest submission comes first (the review
    queue); otherwise the newest report first. `mine=true` lists only your
    own."""
    return await workflow.list_reports(
        uow.session,
        principal,
        status=status,
        student_id=student_id,
        kind=kind,
        mine=mine,
        cursor=cursor,
        limit=limit,
    )


@router.get(
    "/{report_id}",
    responses={**PERMISSION_RESPONSES, 404: _REPORT_NOT_FOUND},
)
async def get_progress_report(
    principal: Annotated[Principal, authenticated()],
    report_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> ProgressReportView:
    """One progress report with its narrative, its facts and its review
    history (the author and reviewers)."""
    return await workflow.get_report(uow.session, principal, report_id)


@router.patch(
    "/{report_id}",
    responses={
        **PERMISSION_RESPONSES,
        403: _AUTHOR_403,
        404: _REPORT_NOT_FOUND,
        409: problem_response_doc(_EDIT_409),
        422: problem_response_doc(
            "The body is not valid (validation_error): no version, too_long, or"
            " an immutable field such as student_id, kind, period or subject_id"
            " (extra_forbidden)."
        ),
    },
)
async def update_progress_report(
    principal: Annotated[Principal, authenticated()],
    report_id: uuid.UUID,
    body: ProgressReportUpdate,
    uow: UnitOfWorkDep,
) -> ProgressReportView:
    """Edit the narrative of a draft or of a report sent back for changes."""
    return await workflow.update_report(
        uow.session, principal=principal, report_id=report_id, body=body
    )


@router.delete(
    "/{report_id}",
    status_code=204,
    response_class=Response,
    responses={
        **PERMISSION_RESPONSES,
        403: _AUTHOR_403,
        404: _REPORT_NOT_FOUND,
        409: problem_response_doc(_EDIT_409),
        422: problem_response_doc(
            "The version query parameter is missing or not an integer"
            " (validation_error)."
        ),
    },
)
async def delete_progress_report(
    principal: Annotated[Principal, authenticated()],
    report_id: uuid.UUID,
    version: Annotated[int, Query(description="The report's version, as last read.")],
    uow: UnitOfWorkDep,
) -> None:
    """Delete a draft or a report sent back for changes."""
    await workflow.delete_report(
        uow.session, principal=principal, report_id=report_id, version=version
    )


@router.post(
    "/{report_id}/submit",
    responses={
        **PERMISSION_RESPONSES,
        403: _AUTHOR_403,
        404: _REPORT_NOT_FOUND,
        409: problem_response_doc(_EDIT_409),
        422: problem_response_doc(
            "No version (validation_error), or the report has no summary"
            " (summary_required)."
        ),
    },
)
async def submit_progress_report(
    principal: Annotated[Principal, authenticated()],
    report_id: uuid.UUID,
    body: ProgressReportVersion,
    uow: UnitOfWorkDep,
) -> ProgressReportView:
    """Send the report for review. Its facts are compiled now and frozen;
    a reviewer approves exactly this version."""
    return await workflow.submit_report(
        uow.session, principal=principal, report_id=report_id, version=body.version
    )


@router.post(
    "/{report_id}/approve",
    responses={
        **PERMISSION_RESPONSES,
        403: _REVIEW_403,
        404: _REPORT_NOT_FOUND,
        409: problem_response_doc(
            "The report changed since it was read (version_conflict) or isn't"
            " waiting for review (progress_report_not_submitted)."
        ),
        422: problem_response_doc("No version (validation_error)."),
    },
)
async def approve_progress_report(
    principal: Annotated[Principal, authenticated()],
    report_id: uuid.UUID,
    body: ProgressReportVersion,
    uow: UnitOfWorkDep,
) -> ProgressReportView:
    """Approve the submitted report: the student and their guardians with
    access to their academic records see it from now on and are notified,
    as is the author. An approved report never changes."""
    return await workflow.approve_report(
        uow.session, principal=principal, report_id=report_id, version=body.version
    )


@router.post(
    "/{report_id}/request-changes",
    responses={
        **PERMISSION_RESPONSES,
        403: _REVIEW_403,
        404: _REPORT_NOT_FOUND,
        409: problem_response_doc(
            "The report changed since it was read (version_conflict), isn't"
            " waiting for review (progress_report_not_submitted), or its author"
            " no longer teaches the student (progress_report_author_not_current)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): no version,"
            " reason_required, too_long."
        ),
    },
)
async def request_progress_report_changes(
    principal: Annotated[Principal, authenticated()],
    report_id: uuid.UUID,
    body: ProgressReportReason,
    uow: UnitOfWorkDep,
) -> ProgressReportView:
    """Send the submitted report back to its author with a reason; the
    author is notified (the reason stays internal)."""
    return await workflow.request_changes(
        uow.session, principal=principal, report_id=report_id, body=body
    )


@router.post(
    "/{report_id}/retract",
    responses={
        **PERMISSION_RESPONSES,
        403: _REVIEW_403,
        404: _REPORT_NOT_FOUND,
        409: problem_response_doc(
            "The report changed since it was read (version_conflict), is"
            " already retracted (progress_report_closed) or was never submitted"
            " (progress_report_not_submitted)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): no version,"
            " reason_required, too_long."
        ),
    },
)
async def retract_progress_report(
    principal: Annotated[Principal, authenticated()],
    report_id: uuid.UUID,
    body: ProgressReportReason,
    uow: UnitOfWorkDep,
) -> ProgressReportView:
    """Retract a submitted or approved report with a reason (wrong student,
    wrong content). Families never see it again; the author is notified
    and may write a new one."""
    return await workflow.retract_report(
        uow.session, principal=principal, report_id=report_id, body=body
    )


# --- Approved reports (R10, R11) ------------------------------------------------------

_CONSENT_403: Final = problem_response_doc(
    "The adult student hasn't given you access to their academic records"
    " (student_consent_required); privacy_notice_required."
)


@student_router.get(
    "/{student_id}/progress-reports",
    responses={
        **PERMISSION_RESPONSES,
        403: _CONSENT_403,
        404: problem_response_doc(
            "No such student, or not one whose progress reports you may see"
            " (not_found)."
        ),
    },
)
async def list_student_progress_reports(
    principal: Annotated[Principal, authenticated()],
    student_id: uuid.UUID,
    uow: UnitOfWorkDep,
    kind: ReportKind | None = None,
    subject_id: uuid.UUID | None = None,
    cursor: str | None = None,
    limit: Limit = rules.LIST_LIMIT_DEFAULT,
) -> ProgressReportFamilyPage:
    """A student's approved progress reports, latest period first: staff
    reviewers and the student's tutors, the student and their guardians
    with access to their academic records (a tutor who taught the student
    before: the reports they wrote). Reports that aren't approved never
    appear."""
    return await workflow.list_approved(
        uow.session,
        principal,
        student_id,
        kind=kind,
        subject_id=subject_id,
        cursor=cursor,
        limit=limit,
    )


@student_router.get(
    "/{student_id}/progress-reports/{report_id}",
    responses={
        **PERMISSION_RESPONSES,
        403: _CONSENT_403,
        404: problem_response_doc(
            "No such approved report of this student, or not one you may see"
            " (not_found)."
        ),
    },
)
async def get_student_progress_report(
    principal: Annotated[Principal, authenticated()],
    student_id: uuid.UUID,
    report_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> ProgressReportFamilyView:
    """One approved progress report: the narrative and the facts exactly as
    approved."""
    return await workflow.get_approved(uow.session, principal, student_id, report_id)
