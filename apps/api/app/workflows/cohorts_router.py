"""Course cohort routes (#65; ``app.workflows.cohorts``; ADR-0002, ADR-0005
D9).

The routes live here, next to the workflow they call (the #56 pattern): a
cohort is a ``courses`` row whose sessions are a ``scheduling`` series, and
a module's router may not import ``app.workflows``.

- ``POST /api/v1/cohorts``: ``require("schedule.manage")`` (Owners and
  Administrators). An ``override`` needs ``schedule.override`` (``403``
  right after the body's ``422``). The order is in
  ``app.workflows.cohorts``.
- ``PATCH /api/v1/cohorts/{cohort_id}``: ``require("schedule.manage")``;
  name and maximum.
- ``POST /api/v1/cohorts/{cohort_id}/enrollments`` and
  ``…/enrollments/{enrollment_id}/withdraw`` (#66):
  ``require("schedule.manage")`` (ADR-0005 D9 "enroll in and withdraw from
  groups and cohorts"); an enrollment ``override`` needs
  ``schedule.override``. They call ``app.workflows.cohort_enrollment``.
- ``GET /api/v1/cohorts``, ``GET /api/v1/cohorts/{cohort_id}`` and ``GET
  /api/v1/cohorts/{cohort_id}/sessions``: ``authenticated()`` with the #66
  record policy (``app.workflows.cohorts.cohort_viewer``): ``course.view``
  holders (Owners, Administrators, Academic Coordinators, Finance Staff)
  see everything; an enrolled student and the guardians linked to them (an
  adult's only with the academic consent, #246 E1) see only their cohorts
  and their students' family-visible sessions; a tutor the cohorts they
  teach and their own sessions (#246); anyone else an empty list or ``404
  cohort_not_found``.

- ``GET /api/v1/cohorts/{cohort_id}/completion`` (#85):
  ``authenticated()`` with its own record policy
  (``app.workflows.course_completion``): ``evaluation.manage`` holders and
  the tutor currently teaching the cohort read every active enrollment,
  students and guardians (the same family rule) only their own; Finance
  (``course.view`` without ``evaluation.manage``) ``403 permission_denied``;
  anyone else ``404 cohort_not_found``.

The tutor of a cohort is offered through its series:
``POST /api/v1/class-series/{class_series_id}/offers`` (#233,
``schedule.assign``) also takes a cohort's series.
"""

import uuid
from typing import Annotated, Final

from fastapi import APIRouter, Depends, Query

from app.core.authz import (
    PERMISSION_RESPONSES,
    Principal,
    authenticated,
    ensure_permission,
    require,
)
from app.core.config import Settings, get_settings
from app.core.errors import problem_response_doc
from app.core.pagination import CursorPagination
from app.core.uow import UnitOfWorkDep
from app.modules.courses.schemas import CohortStatusFilter, CohortUpdate
from app.modules.scheduling.schemas import CohortSessionsQuery
from app.workflows import cohort_enrollment, cohorts, course_completion
from app.workflows.cohort_schemas import (
    CohortCompletion,
    CohortCreate,
    CohortCreated,
    CohortDetail,
    CohortEnrollmentCreate,
    CohortEnrollmentCreated,
    CohortEnrollmentItem,
    CohortPage,
    CohortSessions,
    CohortWithdrawal,
)

SCHEDULE_MANAGE: Final = "schedule.manage"
SCHEDULE_OVERRIDE: Final = "schedule.override"

router = APIRouter(prefix="/api/v1/cohorts", tags=["cohorts"])

_NOT_FOUND = problem_response_doc(
    "No such cohort, or one you can't see (cohort_not_found)."
)


@router.post(
    "",
    status_code=201,
    responses={
        **PERMISSION_RESPONSES,
        403: problem_response_doc(
            "Permission denied (permission_denied): no schedule.manage, or an"
            " `override` without schedule.override. The privacy notice must be"
            " accepted first (privacy_notice_required); a failed Origin or"
            " X-CSRF-Token check (csrf_failed)."
        ),
        409: problem_response_doc(
            "Another cohort has the name, in any case (cohort_name_taken); or"
            " an occurrence in the horizon breaks scheduling rules"
            " (scheduling_conflict): `conflicts` lists every conflict of every"
            " occurrence (with `details.occurrence_start` and `details.date`),"
            " e.g. `room_overlap`, `tutor_not_qualified`; a hard conflict, or a"
            " soft one the `override` doesn't list, refuses. With a tutor, a"
            " series with no occurrence still to come can't be offered"
            " (series_not_offerable). Nothing is created."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): a missing or unknown"
            " field (`duration_minutes`, `recurrence.until`,"
            " `recurrence.count`, `rrule` and `student_ids` included); the name"
            " (name_required, name_too_long); `max_students` outside 1-500;"
            " `start_time` not HH:MM (time_format); the `override` (as for a"
            " series); the template (template_unknown, template_inactive);"
            " the subject (subject_not_in_template, subject_inactive); the"
            " recurrence (weekdays_empty, weekday_out_of_range,"
            " weekdays_duplicate, interval_out_of_range, too_many_occurrences);"
            " starts_on_in_past; crosses_local_date on start_time;"
            " respond_by_requires_tutor, respond_by_invalid; the references"
            " (tutor_unknown, tutor_inactive, classroom_unknown,"
            " classroom_inactive), all in one response."
        ),
    },
)
async def create_cohort(
    principal: Annotated[Principal, require(SCHEDULE_MANAGE)],
    body: CohortCreate,
    uow: UnitOfWorkDep,
    settings: Annotated[Settings, Depends(get_settings)],
) -> CohortCreated:
    """Create a cohort of a course template. The template is frozen as its
    current version's snapshot. The sessions are the occurrences of a
    weekly `course` series with the template's session length, as many as
    the template's sessions outside the closures known now; they are
    created up to the rolling horizon and checked like a series. With a
    tutor the occurrences are `offered` to them, without one
    `unassigned`."""
    if body.override is not None:
        ensure_permission(principal, SCHEDULE_OVERRIDE)
    cohort_id = await cohorts.create_cohort(
        uow.session,
        body,
        horizon_weeks=settings.scheduling_horizon_weeks,
        actor_id=principal.user_id,
    )
    detail = await cohorts.get_cohort_detail(uow.session, cohort_id)
    return CohortCreated(**detail.model_dump(), warnings=[])


@router.get(
    "",
    responses={
        **PERMISSION_RESPONSES,
        400: problem_response_doc("The cursor is not valid (invalid_cursor)."),
        422: problem_response_doc(
            "The query is not valid (validation_error): an unknown status, a"
            " malformed id, `q` over 120 characters or `limit` outside 1-100."
        ),
    },
)
async def list_cohorts(
    principal: Annotated[Principal, authenticated()],
    page: CursorPagination,
    uow: UnitOfWorkDep,
    q: Annotated[
        str | None, Query(max_length=120, description="The name contains it.")
    ] = None,
    template_id: uuid.UUID | None = None,
    subject_id: uuid.UUID | None = None,
    status: Annotated[
        CohortStatusFilter,
        Query(
            description=(
                "upcoming, in_progress, ended, current (upcoming and in"
                " progress; the default) or all."
            )
        ),
    ] = "current",
) -> CohortPage:
    """The cohorts you can see by name, with their template, subject, tutor
    and dates (cursor pagination). Staff with course.view see every cohort
    and its enrolled count; an enrolled student or their guardian only the
    cohorts the student is enrolled in (an adult's guardian only with the
    student's academic consent), and a tutor the cohorts whose series they
    teach or were offered, without the count or the tutor assignment;
    anyone else an empty list."""
    return await cohorts.list_cohorts(
        uow.session,
        viewer=await cohorts.cohort_viewer(uow.session, principal),
        q=q,
        template_id=template_id,
        subject_id=subject_id,
        status=status,
        limit=page.limit,
        cursor=page.cursor,
    )


@router.get("/{cohort_id}", responses={**PERMISSION_RESPONSES, 404: _NOT_FOUND})
async def get_cohort(
    principal: Annotated[Principal, authenticated()],
    cohort_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> CohortDetail:
    """One cohort: its template (as frozen), subject, maximum and effective
    capacity, status and dates, current series and next sessions, and its
    syllabus from the snapshot; for staff with course.view also the roster
    and the enrolled count. An enrolled student or their guardian sees only
    a cohort the student is enrolled in, without the roster, the count or
    the tutor assignment (the tutor's name only), and only the student's
    sessions; the cohort's tutor sees `tutor_roster` (the students' names)
    and the sessions they teach."""
    viewer = await cohorts.cohort_viewer(uow.session, principal)
    return await cohorts.get_cohort_detail(uow.session, cohort_id, viewer)


@router.get(
    "/{cohort_id}/sessions",
    responses={
        **PERMISSION_RESPONSES,
        404: _NOT_FOUND,
        422: problem_response_doc(
            "The query is not valid (validation_error): a missing, malformed"
            " or naive `from`/`to` (naive_datetime), `to` not after `from`"
            " (range_invalid), more than 45 days (range_too_long), or an"
            " unknown parameter."
        ),
    },
)
async def list_cohort_sessions(
    principal: Annotated[Principal, authenticated()],
    cohort_id: uuid.UUID,
    query: Annotated[CohortSessionsQuery, Query()],
    uow: UnitOfWorkDep,
) -> CohortSessions:
    """The cohort's sessions overlapping `[from, to)`, by start. Staff with
    course.view see every state; an enrolled student or their guardian the
    sessions their students take part in that are confirmed or completed
    (or were confirmed before being cancelled or skipped), each with
    `students` (only theirs); the cohort's tutor the sessions they teach,
    with `participants`."""
    viewer = await cohorts.cohort_viewer(uow.session, principal)
    return await cohorts.list_cohort_sessions(uow.session, cohort_id, query, viewer)


@router.get(
    "/{cohort_id}/completion",
    responses={
        **PERMISSION_RESPONSES,
        403: problem_response_doc(
            "Permission denied (permission_denied): course.view without"
            " evaluation.manage (Finance), for a cohort where you neither teach"
            " nor have an enrolled student of your own."
        ),
        404: _NOT_FOUND,
    },
)
async def get_cohort_completion(
    principal: Annotated[Principal, authenticated()],
    cohort_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> CohortCompletion:
    """Each actively enrolled student's progress against the course's
    completion criteria, computed live from the cohort's frozen syllabus:
    attendance (completed sessions attended, present or late, against the
    minimum), required modules and required assessments (the latest final
    evaluation counts), and an overall status: `in_progress` until the
    course has ended and every started class is closed out, then
    `completed` or `not_completed`. Staff with evaluation.manage and the
    tutor currently teaching the cohort see every enrolled student; a
    student or their guardian (an adult's only with the academic consent)
    only their own. Nothing is stored and there is no override."""
    return await course_completion.get_cohort_completion(
        uow.session, principal, cohort_id
    )


@router.patch(
    "/{cohort_id}",
    responses={
        **PERMISSION_RESPONSES,
        404: _NOT_FOUND,
        409: problem_response_doc(
            "The cohort changed since it was read (version_conflict), or another"
            " cohort has the name (cohort_name_taken)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): nothing to change"
            " (no_changes), the name (name_required, name_too_long),"
            " `max_students` outside 1-500, or below the active enrollments"
            " (max_students_below_enrolled)."
        ),
    },
)
async def update_cohort(
    principal: Annotated[Principal, require(SCHEDULE_MANAGE)],
    cohort_id: uuid.UUID,
    body: CohortUpdate,
    uow: UnitOfWorkDep,
) -> CohortDetail:
    """Rename a cohort or change its maximum. The maximum can't go below the
    enrolled students; above the room's capacity the room still limits the
    sessions. The template, the subject and the
    snapshot never change; the dates, room and tutor change through the
    series."""
    await cohorts.update_cohort(uow.session, cohort_id, body)
    return await cohorts.get_cohort_detail(uow.session, cohort_id)


@router.post(
    "/{cohort_id}/enrollments",
    status_code=201,
    responses={
        **PERMISSION_RESPONSES,
        403: problem_response_doc(
            "Permission denied (permission_denied): no schedule.manage, or an"
            " `override` without schedule.override."
        ),
        404: problem_response_doc("No such cohort (cohort_not_found)."),
        409: problem_response_doc(
            "The cohort has no series still producing sessions (cohort_ended);"
            " the student is already enrolled (already_enrolled,"
            " details.enrollment_id); or the enrollment breaks scheduling rules"
            " (scheduling_conflict): `group_capacity_exceeded` (soft, can be"
            " overridden; `details.cohort_id`) first, then each future"
            " session's `student_overlap` and `room_capacity_exceeded` (hard),"
            " with `details.occurrence_start` and `details.date`."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): a missing or unknown"
            " field; the `override` (as for a session); or the student"
            " (student_unknown, student_inactive)."
        ),
    },
)
async def enroll_in_cohort(
    principal: Annotated[Principal, require(SCHEDULE_MANAGE)],
    cohort_id: uuid.UUID,
    body: CohortEnrollmentCreate,
    uow: UnitOfWorkDep,
) -> CohortEnrollmentCreated:
    """Enroll a student now, also after the course has started: they join
    every future session of the cohort that hasn't begun (not completed or
    cancelled ones) and the later ones the schedule creates. Above
    `max_students` it needs an `override`; above a room's capacity, or
    overlapping another session of the student, it is refused."""
    if body.override is not None:
        ensure_permission(principal, SCHEDULE_OVERRIDE)
    change = await cohort_enrollment.enroll_in_cohort(
        uow, cohort_id, body, actor_id=principal.user_id
    )
    item = await cohorts.get_cohort_enrollment(
        uow.session, change.cohort_id, change.enrollment_id
    )
    return CohortEnrollmentCreated(**item.model_dump(), warnings=[])


@router.post(
    "/{cohort_id}/enrollments/{enrollment_id}/withdraw",
    responses={
        **PERMISSION_RESPONSES,
        404: problem_response_doc(
            "No such cohort (cohort_not_found), or no such enrollment in it"
            " (enrollment_not_found)."
        ),
        409: problem_response_doc(
            "The enrollment changed since it was read (version_conflict), or"
            " it was already withdrawn (enrollment_not_active)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): a missing or unknown"
            " field, or the reason (reason_required for a blank one,"
            " reason_too_long)."
        ),
    },
)
async def withdraw_from_cohort(
    principal: Annotated[Principal, require(SCHEDULE_MANAGE)],
    cohort_id: uuid.UUID,
    enrollment_id: uuid.UUID,
    body: CohortWithdrawal,
    uow: UnitOfWorkDep,
) -> CohortEnrollmentItem:
    """Withdraw a student now, with an optional reason, also after the
    course has ended: they leave every future session of the cohort that
    hasn't begun and the later ones; past, begun, completed and cancelled
    sessions keep them. The enrollment period is kept (for billing)."""
    change = await cohort_enrollment.withdraw_from_cohort(
        uow, cohort_id, enrollment_id, body, actor_id=principal.user_id
    )
    return await cohorts.get_cohort_enrollment(
        uow.session, change.cohort_id, change.enrollment_id
    )
