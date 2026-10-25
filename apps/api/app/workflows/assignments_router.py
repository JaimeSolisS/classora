"""Homework assignment routes (#82; :mod:`app.workflows.assignments` gives
the record policies and the orders).

The routes live here, next to the workflow they call, because a module's
router may not import ``app.workflows`` (ADR-0002). Every route is
``authenticated()`` plus a record policy (#21), and the ``404`` of a hidden
record is the body of an unknown id.

- ``POST /api/v1/assignments`` (R1), ``GET`` (R2: the working list),
  ``GET`` / ``PATCH`` / ``DELETE /api/v1/assignments/{id}`` (R3-R5);
- ``PUT /api/v1/assignments/{id}/students/{student_id}/submission`` (R6) and
  ``…/grade`` (R7);
- ``GET /api/v1/students/{student_id}/assignments[/{assignment_id}]`` (R8,
  R9: one closed per-student shape for every audience).
"""

import uuid
from datetime import date
from typing import Annotated, Final

from fastapi import APIRouter, Query, Response

from app.core.authz import PERMISSION_RESPONSES, Principal, authenticated
from app.core.errors import problem_response_doc
from app.core.uow import UnitOfWorkDep
from app.modules.academics.service import assignment_rules as rules
from app.workflows import assignments as workflow
from app.workflows.assignment_schemas import (
    AssignmentCreate,
    AssignmentList,
    AssignmentPatch,
    AssignmentView,
    Due,
    GradePut,
    RosterEntry,
    StudentAssignment,
    StudentAssignmentList,
    SubmissionPut,
    SubmissionState,
)

router = APIRouter(prefix="/api/v1/assignments", tags=["assignments"])
student_router = APIRouter(prefix="/api/v1/students", tags=["assignments"])

type Limit = Annotated[
    int, Query(ge=1, le=rules.LIST_LIMIT_MAX, description="1-50, default 20.")
]

_NOT_FOUND: Final = problem_response_doc(
    "No such homework, it was deleted, or it isn't one you may see (not_found)."
)
_ROW_NOT_FOUND: Final = problem_response_doc(
    "No such homework, the student isn't one of its students, or it isn't one"
    " you may see (not_found)."
)
_CONSENT_403: Final = problem_response_doc(
    "The adult student hasn't given you access to their academic records"
    " (student_consent_required); privacy_notice_required."
)
_WRITE_403: Final = problem_response_doc(
    "Only the tutor who set it (assignment_creator_only); the student is no"
    " longer one of your current students (assignment_read_only); a failed"
    " Origin or X-CSRF-Token check (csrf_failed); privacy_notice_required."
)


# --- R1-R5 ----------------------------------------------------------------------------


@router.post(
    "",
    status_code=201,
    responses={
        **PERMISSION_RESPONSES,
        403: problem_response_doc(
            "Only tutors set homework (assignment_tutor_only); csrf_failed;"
            " privacy_notice_required."
        ),
        409: problem_response_doc(
            "The class isn't confirmed or completed (session_not_assignable)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): title_required,"
            " instructions_required, too_long, max_score_out_of_range,"
            " score_precision, duplicate_recipient, recipients_too_many,"
            " subject_unknown, class_session_invalid, subject_mismatch,"
            " recipients_required, recipient_invalid (details.student_ids),"
            " due_in_past, due_too_far."
        ),
    },
)
async def create_assignment(
    principal: Annotated[Principal, authenticated()],
    body: AssignmentCreate,
    uow: UnitOfWorkDep,
) -> AssignmentView:
    """Set homework for one or more of your current students (optionally
    linked to one of your classes). Each student and the guardians with
    access to their academic records see it at once and are notified."""
    return await workflow.create_assignment(uow.session, principal=principal, body=body)


@router.get("", responses={**PERMISSION_RESPONSES})
async def list_assignments(
    principal: Annotated[Principal, authenticated()],
    uow: UnitOfWorkDep,
    student_id: uuid.UUID | None = None,
    subject_id: uuid.UUID | None = None,
    class_session_id: uuid.UUID | None = None,
    due: Annotated[
        Due | None,
        Query(description="upcoming: due today or later; past: before today."),
    ] = None,
    cursor: str | None = None,
    limit: Limit = rules.LIST_LIMIT_DEFAULT,
) -> AssignmentList:
    """The homework you set (tutors), or every homework assignment (staff
    with assignment.review), newest deadline first, with counts per state.
    Anyone else gets 403 permission_denied."""
    return await workflow.list_assignments(
        uow.session,
        principal,
        student_id=student_id,
        subject_id=subject_id,
        class_session_id=class_session_id,
        due=due,
        cursor=cursor,
        limit=limit,
    )


@router.get("/{assignment_id}", responses={**PERMISSION_RESPONSES, 404: _NOT_FOUND})
async def get_assignment(
    principal: Annotated[Principal, authenticated()],
    assignment_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> AssignmentView:
    """One homework assignment with its roster (every student's submission
    and grade): the tutor who set it and staff with assignment.review."""
    return await workflow.get_assignment(uow.session, principal, assignment_id)


@router.patch(
    "/{assignment_id}",
    responses={
        **PERMISSION_RESPONSES,
        403: _WRITE_403,
        404: _NOT_FOUND,
        409: problem_response_doc(
            "The homework changed since it was read (version_conflict); the"
            " maximum score is locked after a grade (assignment_has_grades); a"
            " removed student already submitted (recipient_has_submission)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): no version, null for a"
            " key other than due_time or max_score, an immutable key such as"
            " subject_id (extra_forbidden), title_required,"
            " instructions_required, too_long, max_score_out_of_range,"
            " score_precision, duplicate_recipient, recipient_invalid,"
            " recipients_required, recipients_too_many, due_in_past,"
            " due_too_far."
        ),
    },
)
async def edit_assignment(
    principal: Annotated[Principal, authenticated()],
    assignment_id: uuid.UUID,
    body: AssignmentPatch,
    uow: UnitOfWorkDep,
) -> AssignmentView:
    """Change homework you set: a key you leave out keeps its value;
    due_time null means the end of the day; max_score null makes it
    feedback-only. Added students are notified."""
    return await workflow.edit_assignment(
        uow.session, principal=principal, assignment_id=assignment_id, body=body
    )


@router.delete(
    "/{assignment_id}",
    status_code=204,
    response_class=Response,
    responses={
        **PERMISSION_RESPONSES,
        403: _WRITE_403,
        404: _NOT_FOUND,
        409: problem_response_doc(
            "The homework changed since it was read (version_conflict) or has"
            " a submission (assignment_has_submissions)."
        ),
        422: problem_response_doc(
            "The version query parameter is missing or not an integer"
            " (validation_error)."
        ),
    },
)
async def delete_assignment(
    principal: Annotated[Principal, authenticated()],
    assignment_id: uuid.UUID,
    version: Annotated[
        int, Query(description="The assignment's version, as last read.")
    ],
    uow: UnitOfWorkDep,
) -> None:
    """Delete homework you set while nobody has submitted. It disappears
    everywhere."""
    await workflow.delete_assignment(
        uow.session, principal=principal, assignment_id=assignment_id, version=version
    )


# --- R6, R7 ---------------------------------------------------------------------------


@router.put(
    "/{assignment_id}/students/{student_id}/submission",
    responses={
        **PERMISSION_RESPONSES,
        403: problem_response_doc(
            "Guardians don't submit (submission_student_only); the adult student"
            " hasn't given you access (student_consent_required); only the tutor"
            " who set it records handed-in work (assignment_creator_only); the"
            " student is no longer one of your current students"
            " (assignment_read_only); csrf_failed; privacy_notice_required."
        ),
        404: _ROW_NOT_FOUND,
        409: problem_response_doc(
            "The submission changed since it was read (version_conflict); it was"
            " graded (submission_graded); the deadline passed and it can't be"
            " replaced (submission_locked)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): version_required,"
            " too_long, url_invalid, submission_empty."
        ),
    },
)
async def submit_assignment(
    principal: Annotated[Principal, authenticated()],
    assignment_id: uuid.UUID,
    student_id: uuid.UUID,
    body: SubmissionPut,
    uow: UnitOfWorkDep,
) -> StudentAssignment:
    """The student submits text and/or a link (replaceable until the
    deadline; a missing one can still be submitted late), or the tutor who
    set the homework records work handed in on paper."""
    return await workflow.submit(
        uow.session,
        principal=principal,
        assignment_id=assignment_id,
        student_id=student_id,
        body=body,
    )


@router.put(
    "/{assignment_id}/students/{student_id}/grade",
    responses={
        **PERMISSION_RESPONSES,
        403: _WRITE_403,
        404: _ROW_NOT_FOUND,
        409: problem_response_doc(
            "The submission changed since it was read (version_conflict) or"
            " nothing was submitted (submission_required)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): no version,"
            " score_precision, score_required, score_out_of_range,"
            " score_not_allowed, feedback_required, too_long,"
            " change_reason_required."
        ),
    },
)
async def grade_assignment(
    principal: Annotated[Principal, authenticated()],
    assignment_id: uuid.UUID,
    student_id: uuid.UUID,
    body: GradePut,
    uow: UnitOfWorkDep,
) -> RosterEntry:
    """Grade a student's submitted homework (a score and feedback, or
    feedback only). The student and their guardians see it at once and are
    notified; changing a grade needs a reason they never see."""
    return await workflow.grade(
        uow.session,
        principal=principal,
        assignment_id=assignment_id,
        student_id=student_id,
        body=body,
    )


# --- R8, R9 ---------------------------------------------------------------------------


@student_router.get(
    "/{student_id}/assignments",
    responses={
        **PERMISSION_RESPONSES,
        403: _CONSENT_403,
        404: problem_response_doc(
            "No such student, or not one whose homework you may see (not_found)."
        ),
        422: problem_response_doc(
            "A query parameter is not valid (validation_error): range_invalid"
            " ('from' after 'to')."
        ),
    },
)
async def list_student_assignments(
    principal: Annotated[Principal, authenticated()],
    student_id: uuid.UUID,
    uow: UnitOfWorkDep,
    state: SubmissionState | None = None,
    subject_id: uuid.UUID | None = None,
    from_: Annotated[
        date | None, Query(alias="from", description="due_on on or after.")
    ] = None,
    to: Annotated[date | None, Query(description="due_on on or before.")] = None,
    cursor: str | None = None,
    limit: Limit = rules.LIST_LIMIT_DEFAULT,
) -> StudentAssignmentList:
    """A student's homework with their own submission and grade (never
    another student's), newest deadline first, and a summary per state.
    Staff with assignment.review, tutors currently teaching the student,
    the student and their guardians with access to their academic
    records."""
    return await workflow.list_student_assignments(
        uow.session,
        principal,
        student_id,
        state=state,
        subject_id=subject_id,
        from_=from_,
        to=to,
        cursor=cursor,
        limit=limit,
    )


@student_router.get(
    "/{student_id}/assignments/{assignment_id}",
    responses={**PERMISSION_RESPONSES, 403: _CONSENT_403, 404: _ROW_NOT_FOUND},
)
async def get_student_assignment(
    principal: Annotated[Principal, authenticated()],
    student_id: uuid.UUID,
    assignment_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> StudentAssignment:
    """One homework assignment with the student's own submission and
    grade."""
    return await workflow.get_student_assignment(
        uow.session, principal, student_id, assignment_id
    )
