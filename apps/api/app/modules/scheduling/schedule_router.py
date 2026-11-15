"""Role-scoped schedule routes (#67; :mod:`app.modules.scheduling.role_schedules`
gives the rules, :mod:`app.modules.scheduling.permissions` the record
policies).

- ``GET /api/v1/tutors/{user_id}/schedule?from&to``:
  ``require("tutor.view")`` + record policy (the tutor themselves, or staff
  with ``schedule.view``; anyone else ``404``).
- ``GET /api/v1/students/{student_id}/schedule?from&to``:
  ``require("student.view")`` + record policy (the student themselves, a
  linked guardian under the adult rule, or staff with ``schedule.view``;
  ``403 student_consent_required`` for a linked guardian of an adult
  without consent; anyone else ``404``).
- ``GET /api/v1/schedule/family-students``: ``require("student.view")``;
  the caller's own family students only.

Order: ``401``/``403`` → ``422`` query → ``404`` / ``403
student_consent_required`` → ``200``. Read-only. ``GET
/api/v1/class-sessions`` (#57) stays staff-only.
"""

import uuid
from typing import Annotated, Final

from fastapi import APIRouter, Query

from app.core.authz import PERMISSION_RESPONSES, Principal, require
from app.core.errors import problem_response_doc
from app.core.uow import UnitOfWorkDep
from app.modules.scheduling import service
from app.modules.scheduling.schedule_schemas import (
    FamilySchedule,
    FamilyScheduleStudents,
    ScheduleRangeQuery,
    TutorSchedule,
)

TUTOR_VIEW: Final = "tutor.view"
STUDENT_VIEW: Final = "student.view"

router = APIRouter(tags=["schedules"])

_RANGE_422: Final = problem_response_doc(
    "The query is not valid (validation_error): a missing, malformed or naive"
    " `from`/`to` (naive_datetime), `to` not after `from` (range_invalid),"
    " more than 45 days (range_too_long) or an unknown parameter."
)


@router.get(
    "/api/v1/tutors/{user_id}/schedule",
    responses={
        **PERMISSION_RESPONSES,
        404: problem_response_doc(
            "No such tutor, or not one whose schedule you may see (not_found)."
        ),
        422: _RANGE_422,
    },
)
async def get_tutor_schedule(
    principal: Annotated[Principal, require(TUTOR_VIEW)],
    user_id: uuid.UUID,
    query: Annotated[ScheduleRangeQuery, Query()],
    uow: UnitOfWorkDep,
) -> TutorSchedule:
    """The sessions the tutor currently teaches that overlap `[from, to)`
    (at most 45 days), by start then id, at most 1000 (`truncated` when more
    match): `offered` (awaiting the tutor's answer), `confirmed` and
    `completed`, and `cancelled` or `skipped_closure` ones that were once
    confirmed. Never `unassigned` or `conflict`, never markers. Only the
    tutor themselves, or staff with schedule.view."""
    return await service.tutor_schedule(uow.session, principal, user_id, query)


@router.get(
    "/api/v1/students/{student_id}/schedule",
    responses={
        **PERMISSION_RESPONSES,
        403: problem_response_doc(
            "The student is an adult who hasn't given you consent"
            " (student_consent_required), or privacy_notice_required."
        ),
        404: problem_response_doc(
            "No such student, or not one whose schedule you may see (not_found)."
        ),
        422: _RANGE_422,
    },
)
async def get_student_schedule(
    principal: Annotated[Principal, require(STUDENT_VIEW)],
    student_id: uuid.UUID,
    query: Annotated[ScheduleRangeQuery, Query()],
    uow: UnitOfWorkDep,
) -> FamilySchedule:
    """The student's sessions overlapping `[from, to)` (at most 45 days), by
    start then id, at most 1000 (`truncated` when more match): `confirmed`
    and `completed`, and `cancelled` or `skipped_closure` ones that were
    once confirmed. Each shows the tutor's display name and the room's
    name only; never other students. For the student themselves, a linked
    guardian (an adult student only with their academic-access consent),
    or staff with schedule.view."""
    return await service.family_schedule(uow.session, principal, student_id, query)


@router.get("/api/v1/schedule/family-students", responses=PERMISSION_RESPONSES)
async def list_family_students(
    principal: Annotated[Principal, require(STUDENT_VIEW)],
    uow: UnitOfWorkDep,
) -> FamilyScheduleStudents:
    """The students whose schedule you may choose: your own profile (as a
    student) and the students linked to your guardian profile, by name.
    `schedule_access` is `consent_required` for an adult who hasn't given
    you consent. Empty for a caller who is neither a student nor a
    guardian."""
    return await service.family_students(uow.session, principal)
