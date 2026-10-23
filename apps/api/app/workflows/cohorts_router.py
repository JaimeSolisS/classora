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
- ``GET /api/v1/cohorts``, ``GET /api/v1/cohorts/{cohort_id}`` and ``GET
  /api/v1/cohorts/{cohort_id}/sessions``: ``require("course.view")`` (Owners,
  Administrators, Academic Coordinators, Finance Staff). #66 opens them to
  enrolled students and their guardians, #67 to tutors.

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
    ensure_permission,
    require,
)
from app.core.config import Settings, get_settings
from app.core.errors import problem_response_doc
from app.core.pagination import OffsetPage, OffsetPagination
from app.core.uow import UnitOfWorkDep
from app.modules.courses.schemas import CohortStatusFilter, CohortUpdate
from app.modules.scheduling.schemas import CohortSessionsQuery
from app.workflows import cohorts
from app.workflows.cohort_schemas import (
    CohortCreate,
    CohortCreated,
    CohortDetail,
    CohortListItem,
    CohortSessions,
)

SCHEDULE_MANAGE: Final = "schedule.manage"
SCHEDULE_OVERRIDE: Final = "schedule.override"
COURSE_VIEW: Final = "course.view"

router = APIRouter(prefix="/api/v1/cohorts", tags=["cohorts"])

_NOT_FOUND = problem_response_doc("No such cohort (cohort_not_found).")


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
        422: problem_response_doc(
            "The query is not valid (validation_error): an unknown status, a"
            " malformed id, `q` over 120 characters, `limit` outside 1-100 or a"
            " negative `offset`."
        ),
    },
)
async def list_cohorts(
    principal: Annotated[Principal, require(COURSE_VIEW)],
    page: OffsetPagination,
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
) -> OffsetPage[CohortListItem]:
    """The cohorts by name, with their template, subject, tutor and dates."""
    return await cohorts.list_cohorts(
        uow.session,
        q=q,
        template_id=template_id,
        subject_id=subject_id,
        status=status,
        page=page,
    )


@router.get("/{cohort_id}", responses={**PERMISSION_RESPONSES, 404: _NOT_FOUND})
async def get_cohort(
    principal: Annotated[Principal, require(COURSE_VIEW)],
    cohort_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> CohortDetail:
    """One cohort: its template (as frozen), subject, maximum and effective
    capacity, status and dates, current series and next sessions, and its
    syllabus from the snapshot."""
    return await cohorts.get_cohort_detail(uow.session, cohort_id)


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
    principal: Annotated[Principal, require(COURSE_VIEW)],
    cohort_id: uuid.UUID,
    query: Annotated[CohortSessionsQuery, Query()],
    uow: UnitOfWorkDep,
) -> CohortSessions:
    """The cohort's sessions overlapping `[from, to)`, by start, in every
    state."""
    return await cohorts.list_cohort_sessions(uow.session, cohort_id, query)


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
            " (no_changes), the name (name_required, name_too_long) or"
            " `max_students` outside 1-500."
        ),
    },
)
async def update_cohort(
    principal: Annotated[Principal, require(SCHEDULE_MANAGE)],
    cohort_id: uuid.UUID,
    body: CohortUpdate,
    uow: UnitOfWorkDep,
) -> CohortDetail:
    """Rename a cohort or change its maximum; above the room's capacity the
    room still limits the sessions. The template, the subject and the
    snapshot never change; the dates, room and tutor change through the
    series."""
    await cohorts.update_cohort(uow.session, cohort_id, body)
    return await cohorts.get_cohort_detail(uow.session, cohort_id)
