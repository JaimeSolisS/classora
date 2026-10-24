"""Tutoring group routes (#56; ``app.workflows.group_enrollment``;
ADR-0002, ADR-0005 D9).

The routes live here, next to the workflow they call, because a module's
router may not import ``app.workflows`` (ADR-0002): the writes go through
the workflow (the M6-07 billing hook), the reads through
``scheduling.service``.

- ``POST /api/v1/groups``: ``require("schedule.manage")`` (Owners and
  Administrators). A group and its weekly ``group`` series; the order is in
  ``scheduling.groups.create_group``. An ``override`` needs
  ``schedule.override`` (``403`` right after the body's ``422``).
- ``PATCH /api/v1/groups/{group_id}``: ``require("schedule.manage")``;
  name and maximum.
- ``POST /api/v1/groups/{group_id}/enrollments`` and
  ``…/enrollments/{enrollment_id}/withdraw``: ``require("schedule.manage")``
  (D9 "enroll in and withdraw from groups"); an enrollment ``override``
  needs ``schedule.override``.
- ``GET /api/v1/groups``, ``GET /api/v1/groups/{group_id}`` and ``GET
  /api/v1/groups/{group_id}/sessions``: ``authenticated()`` with the record
  policy of ``scheduling.group_reads`` (staff with ``schedule.view`` see
  everything; a student or a linked guardian (an adult's only with the
  academic consent, #246 E1) their groups and the family-visible sessions;
  a tutor the groups they teach and their own sessions; anyone else an
  empty list or ``404 group_not_found``).
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
from app.core.uow import UnitOfWorkDep
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.schemas import (
    GROUPS_DEFAULT_LIMIT,
    GROUPS_MAX_LIMIT,
    GroupCreate,
    GroupCreated,
    GroupDetail,
    GroupEnrollmentCreate,
    GroupEnrollmentCreated,
    GroupEnrollmentItem,
    GroupPage,
    GroupSessions,
    GroupSessionsQuery,
    GroupStatus,
    GroupUpdate,
    GroupWithdrawal,
)
from app.workflows import group_enrollment

SCHEDULE_MANAGE: Final = "schedule.manage"
SCHEDULE_OVERRIDE: Final = "schedule.override"

router = APIRouter(prefix="/api/v1/groups", tags=["groups"])

_NOT_FOUND = problem_response_doc(
    "No such group, or one you can't see (group_not_found)."
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
            "Another group has the name, in any case (group_name_taken); or the"
            " group breaks scheduling rules (scheduling_conflict): `conflicts`"
            " lists the group-level `group_capacity_exceeded` first (more"
            " initial students than `max_students`), then every conflict of"
            " every occurrence in the horizon (with `details.occurrence_start`"
            " and `details.date`), e.g. `room_capacity_exceeded` and"
            " `student_overlap`; a hard conflict, or a soft one the `override`"
            " doesn't list, refuses. With a tutor, a series with no occurrence"
            " still to come can't be offered (series_not_offerable). Nothing is"
            " created."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): a missing or unknown"
            " field (an `rrule` included); the name (name_required,"
            " name_too_long); `max_students` outside 1-500; duplicate"
            " `student_ids` (student_ids_duplicate) or more than 500; the"
            " `override` (reason_required, reason_too_long, codes_required,"
            " duplicate_code, code_not_overridable, code_unknown,"
            " pairs_too_many, duplicate_pair, occurrence_start_invalid);"
            " `start_time` not HH:MM (time_format), a duration outside 15-480;"
            " the recurrence (weekdays_empty, weekday_out_of_range,"
            " weekdays_duplicate, interval_out_of_range, until_and_count,"
            " count_out_of_range, until_before_start, no_occurrences);"
            " starts_on_in_past; crosses_local_date on start_time; the"
            " references (student_unknown, student_inactive with the index,"
            " subject_unknown, subject_inactive, tutor_unknown, tutor_inactive,"
            " classroom_unknown, classroom_inactive), all in one response."
        ),
    },
)
async def create_group(
    principal: Annotated[Principal, require(SCHEDULE_MANAGE)],
    body: GroupCreate,
    uow: UnitOfWorkDep,
    settings: Annotated[Settings, Depends(get_settings)],
) -> GroupCreated:
    """Create an ongoing tutoring group with a weekly schedule. Its sessions
    are the occurrences of a `group` series, created up to the rolling
    horizon and checked like a series (a closure date is skipped); every
    initial student is enrolled and takes part in each one. Above
    `max_students` the create needs an `override` (`codes:
    ["group_capacity_exceeded"]`); above a room's capacity it is refused.
    With a tutor the occurrences are `offered` to them, without one
    `unassigned`."""
    if body.override is not None:
        ensure_permission(principal, SCHEDULE_OVERRIDE)
    group_id = await group_enrollment.create_group(
        uow.session,
        body,
        horizon_weeks=settings.scheduling_horizon_weeks,
        actor_id=principal.user_id,
    )
    detail = await scheduling.get_group(uow.session, principal, group_id)
    return GroupCreated(**detail.model_dump(), warnings=[])


@router.get(
    "",
    responses={
        **PERMISSION_RESPONSES,
        400: problem_response_doc("The cursor is not valid (invalid_cursor)."),
        422: problem_response_doc(
            "The query is not valid (validation_error): an unknown status,"
            " a malformed subject id, or `limit` outside 1-100."
        ),
    },
)
async def list_groups(
    principal: Annotated[Principal, authenticated()],
    uow: UnitOfWorkDep,
    q: Annotated[str | None, Query(max_length=120)] = None,
    subject_id: uuid.UUID | None = None,
    status: GroupStatus = "active",
    limit: Annotated[int, Query(ge=1, le=GROUPS_MAX_LIMIT)] = GROUPS_DEFAULT_LIMIT,
    cursor: str | None = None,
) -> GroupPage:
    """The groups you can see, by name: `active` (still producing
    sessions; the default), `ended` or `all`. Staff with schedule.view see
    every group; a student or their guardian only the groups the student is
    enrolled in (an adult's guardian only with the student's academic
    consent), and a tutor the groups whose series they teach or were offered,
    without the enrolled count; anyone else an empty list."""
    return await scheduling.list_groups(
        uow.session,
        principal,
        q=q,
        subject_id=subject_id,
        status=status,
        limit=limit,
        cursor=cursor,
    )


@router.get("/{group_id}", responses={**PERMISSION_RESPONSES, 404: _NOT_FOUND})
async def get_group(
    principal: Annotated[Principal, authenticated()],
    group_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> GroupDetail:
    """One group: its subject, maximum, effective capacity, current series
    and next sessions; for staff with schedule.view also the roster and the
    enrolled count. A student or their guardian sees only a group the
    student is enrolled in, with the tutor's name only and the student's
    own sessions; the group's tutor sees `tutor_roster` (the students'
    names) and the sessions they teach."""
    return await scheduling.get_group(uow.session, principal, group_id)


@router.get(
    "/{group_id}/sessions",
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
async def list_group_sessions(
    principal: Annotated[Principal, authenticated()],
    group_id: uuid.UUID,
    query: Annotated[GroupSessionsQuery, Query()],
    uow: UnitOfWorkDep,
) -> GroupSessions:
    """The group's sessions overlapping `[from, to)`, by start. Staff with
    schedule.view see every state; a student or their guardian the
    sessions their students take part in that are confirmed or completed
    (or were confirmed before being cancelled or skipped), with `students`
    (only theirs); the group's tutor the sessions they teach, with
    `participants`."""
    return await scheduling.list_group_sessions(uow.session, principal, group_id, query)


@router.patch(
    "/{group_id}",
    responses={
        **PERMISSION_RESPONSES,
        404: _NOT_FOUND,
        409: problem_response_doc(
            "The group changed since it was read (version_conflict), or another"
            " group has the name (group_name_taken)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): nothing to change"
            " (no_changes), the name (name_required, name_too_long),"
            " `max_students` outside 1-500, or below the active enrollments"
            " (max_students_below_enrolled)."
        ),
    },
)
async def update_group(
    principal: Annotated[Principal, require(SCHEDULE_MANAGE)],
    group_id: uuid.UUID,
    body: GroupUpdate,
    uow: UnitOfWorkDep,
) -> GroupDetail:
    """Rename a group or change its maximum. The maximum can't go below the
    enrolled students; above the room's capacity the room still limits the
    sessions. The schedule, tutor and room change through the series."""
    await scheduling.update_group(uow.session, group_id, body)
    return await scheduling.get_group(uow.session, principal, group_id)


@router.post(
    "/{group_id}/enrollments",
    status_code=201,
    responses={
        **PERMISSION_RESPONSES,
        403: problem_response_doc(
            "Permission denied (permission_denied): no schedule.manage, or an"
            " `override` without schedule.override."
        ),
        404: _NOT_FOUND,
        409: problem_response_doc(
            "The group has no series still producing sessions (group_ended);"
            " the student is already enrolled (already_enrolled,"
            " details.enrollment_id); or the enrollment breaks scheduling rules"
            " (scheduling_conflict): `group_capacity_exceeded` (soft, can be"
            " overridden) first, then each future session's"
            " `student_overlap` and `room_capacity_exceeded` (hard), with"
            " `details.occurrence_start` and `details.date`."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): a missing or unknown"
            " field; the `override` (as for a session); or the student"
            " (student_unknown, student_inactive)."
        ),
    },
)
async def enroll_student(
    principal: Annotated[Principal, require(SCHEDULE_MANAGE)],
    group_id: uuid.UUID,
    body: GroupEnrollmentCreate,
    uow: UnitOfWorkDep,
) -> GroupEnrollmentCreated:
    """Enroll a student now, also after the group has started: they join
    every future session of the group that hasn't begun (not completed or
    cancelled ones) and the later ones the schedule creates. Above
    `max_students` it needs an `override`; above a room's capacity, or
    overlapping another session of the student, it is refused."""
    if body.override is not None:
        ensure_permission(principal, SCHEDULE_OVERRIDE)
    change = await group_enrollment.enroll_student(
        uow.session, group_id, body, actor_id=principal.user_id
    )
    item = await scheduling.get_group_enrollment(
        uow.session, change.group_id, change.enrollment_id
    )
    return GroupEnrollmentCreated(**item.model_dump(), warnings=[])


@router.post(
    "/{group_id}/enrollments/{enrollment_id}/withdraw",
    responses={
        **PERMISSION_RESPONSES,
        404: problem_response_doc(
            "No such group (group_not_found), or no such enrollment in it"
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
async def withdraw_student(
    principal: Annotated[Principal, require(SCHEDULE_MANAGE)],
    group_id: uuid.UUID,
    enrollment_id: uuid.UUID,
    body: GroupWithdrawal,
    uow: UnitOfWorkDep,
) -> GroupEnrollmentItem:
    """Withdraw a student now, with an optional reason: they leave every
    future session of the group that hasn't begun and the later ones; past,
    begun, completed and cancelled sessions keep them. The enrollment period
    is kept (for billing)."""
    change = await group_enrollment.withdraw_student(
        uow.session, group_id, enrollment_id, body, actor_id=principal.user_id
    )
    return await scheduling.get_group_enrollment(
        uow.session, change.group_id, change.enrollment_id
    )
