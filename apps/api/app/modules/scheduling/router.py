"""Class session routes (#48; ``app.modules.scheduling.service``; ADR-0005).

- ``POST /api/v1/class-sessions``: ``require("schedule.manage")`` (Owners and
  Administrators). A one-off individual session; order ``401``/``403`` →
  ``422`` body → student lock → zone ``FOR SHARE`` → ``422`` bounds →
  ``422`` references → ``409 scheduling_conflict`` (every conflict, unless
  an ``override`` covers them all, #49) → ``201`` (``offered`` with a
  tutor, ``unassigned`` without one). An ``override`` needs
  ``schedule.override`` (the Owner, or an Administrator while an Owner's
  delegation is active): ``403`` right after the body's ``422``, before
  anything is read or locked.
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

from app.core.authz import (
    PERMISSION_RESPONSES,
    Principal,
    ensure_permission,
    require,
)
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
SCHEDULE_OVERRIDE: Final = "schedule.override"

router = APIRouter(prefix="/api/v1/class-sessions", tags=["class-sessions"])
options_router = APIRouter(prefix="/api/v1/schedule", tags=["class-sessions"])

TUTOR_OPTIONS_DEFAULT_LIMIT: Final = 20
TUTOR_OPTIONS_MAX_LIMIT: Final = 50


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
            "The session breaks scheduling rules (scheduling_conflict);"
            " `conflicts` lists every one, hard and soft. A hard conflict, or"
            " a soft one the `override` doesn't list, always refuses."
            " Nothing is created."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): a missing or unknown"
            " field, a kind other than individual; the `override`"
            " (reason_required, reason_too_long, codes_required,"
            " duplicate_code, code_not_overridable, code_unknown);"
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
    first; with an `override` the listed soft conflicts are allowed, stored
    with the reason and audited. `warnings` is always empty for now."""
    if body.override is not None:
        ensure_permission(principal, SCHEDULE_OVERRIDE)
    return await service.create_class_session(
        uow.session, body, actor_id=principal.user_id
    )


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
