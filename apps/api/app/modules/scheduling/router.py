"""Class session routes (#48; ``app.modules.scheduling.service``; ADR-0005).

- ``POST /api/v1/class-sessions``: ``require("schedule.manage")`` (Owners and
  Administrators). A one-off individual session; order ``401``/``403`` →
  ``422`` body → student lock → zone ``FOR SHARE`` → ``422`` bounds →
  ``422`` references → ``409 scheduling_conflict`` (every conflict) →
  ``201`` (``offered`` with a tutor, ``unassigned`` without one).
- ``GET /api/v1/class-sessions?from=…&to=…`` (#57 A): the calendar's range
  read, ``require("schedule.view")``; the sessions overlapping ``[from,
  to)`` (at most 45 days) with their markers, at most 1000.
- ``GET /api/v1/schedule/tutor-options`` (#57 B): the tutor filter's
  options, ``require("schedule.view")`` (Finance holds no ``tutor.view``).
- ``GET /api/v1/class-sessions/{class_session_id}``:
  ``require("schedule.view")`` (Owners, Administrators, Academic
  Coordinators and Finance staff); every holder sees every session.
"""

import uuid
from typing import Annotated, Final

from fastapi import APIRouter, Query

from app.core.authz import PERMISSION_RESPONSES, Principal, require
from app.core.errors import problem_response_doc
from app.core.uow import UnitOfWorkDep
from app.modules.scheduling import service
from app.modules.scheduling.schemas import (
    ClassSessionCalendar,
    ClassSessionCreate,
    ClassSessionCreated,
    ClassSessionDetail,
    ClassSessionRangeQuery,
    TutorOptions,
)

SCHEDULE_VIEW: Final = "schedule.view"
SCHEDULE_MANAGE: Final = "schedule.manage"

router = APIRouter(prefix="/api/v1/class-sessions", tags=["class-sessions"])
options_router = APIRouter(prefix="/api/v1/schedule", tags=["class-sessions"])

TUTOR_OPTIONS_DEFAULT_LIMIT: Final = 20
TUTOR_OPTIONS_MAX_LIMIT: Final = 50


@router.post(
    "",
    status_code=201,
    responses={
        **PERMISSION_RESPONSES,
        409: problem_response_doc(
            "The session breaks scheduling rules (scheduling_conflict);"
            " `conflicts` lists every one, hard and soft. Nothing is created."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): a missing or unknown"
            " field (an `override` included), a kind other than individual;"
            " the bounds (naive_datetime, not_whole_minutes,"
            " ends_before_start, duration_out_of_range, crosses_local_date);"
            " or the references (student_unknown, student_inactive,"
            " subject_unknown, subject_inactive, tutor_unknown, tutor_inactive,"
            " classroom_unknown, classroom_inactive), all in one response."
        ),
    },
)
async def create_class_session(
    principal: Annotated[Principal, require(SCHEDULE_MANAGE)],
    body: ClassSessionCreate,
    uow: UnitOfWorkDep,
) -> ClassSessionCreated:
    """Schedule a one-off individual session. With a tutor it is created
    `offered` to them, without one `unassigned`. Every rule is checked
    first; `warnings` is always empty for now."""
    return await service.create_class_session(uow.session, body)


@router.get(
    "",
    responses={
        **PERMISSION_RESPONSES,
        422: problem_response_doc(
            "The query is not valid (validation_error): a missing, malformed"
            " or naive `from`/`to` (naive_datetime), `to` not after `from`"
            " (range_invalid), more than 45 days (range_too_long), an unknown"
            " parameter, a malformed id or an unknown kind."
        ),
    },
)
async def list_class_sessions(
    principal: Annotated[Principal, require(SCHEDULE_VIEW)],
    query: Annotated[ClassSessionRangeQuery, Query()],
    uow: UnitOfWorkDep,
) -> ClassSessionCalendar:
    """The sessions overlapping `[from, to)` (`starts_at < to` and `ends_at >
    from`), by start then id, at most 1000 (`truncated` when more match).
    Filters combine with AND; an unknown id matches nothing. `cancelled`
    sessions only with `include_cancelled`. `markers` are computed on read:
    `center_closed` (an unassigned, offered or confirmed session on a
    closure's local date) and `tutor_not_qualified` (a future offered or
    confirmed session whose tutor holds no approved qualification)."""
    return await service.list_calendar_sessions(uow.session, query)


@router.get(
    "/{class_session_id}",
    responses={
        **PERMISSION_RESPONSES,
        404: problem_response_doc("No such class session (not_found)."),
    },
)
async def get_class_session(
    principal: Annotated[Principal, require(SCHEDULE_VIEW)],
    class_session_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> ClassSessionDetail:
    """One class session with its subject, tutor, classroom and students."""
    return await service.get_class_session(uow.session, class_session_id)


@options_router.get(
    "/tutor-options",
    responses={
        **PERMISSION_RESPONSES,
        422: problem_response_doc(
            "q empty or longer than 100 characters, or limit outside 1-50"
            " (validation_error)."
        ),
    },
)
async def list_tutor_options(
    principal: Annotated[Principal, require(SCHEDULE_VIEW)],
    uow: UnitOfWorkDep,
    q: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    limit: Annotated[
        int, Query(ge=1, le=TUTOR_OPTIONS_MAX_LIMIT)
    ] = TUTOR_OPTIONS_DEFAULT_LIMIT,
) -> TutorOptions:
    """The users holding the tutor role whose username or display name
    contains `q` (not case-sensitive), by display name; inactive tutors are
    included with their `state`. No contact data."""
    return await service.list_tutor_options(
        uow.session, q=(q or "").strip() or None, limit=limit
    )
