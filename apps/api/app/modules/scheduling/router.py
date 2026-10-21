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
- ``POST /api/v1/class-sessions/suggestions`` (#50 B): read-only,
  ``require("schedule.manage")`` (the holders of the create it serves). The
  free times nearest the requested start for the same student, subject,
  tutor, classroom and duration; order ``401``/``403`` → ``422`` body →
  zone (plain read) → ``422`` bounds → ``422`` window → ``422`` references
  → ``200``. Nothing is written.
- ``GET /api/v1/class-sessions/{class_session_id}``:
  ``require("schedule.view")`` (Owners, Administrators, Academic
  Coordinators and Finance staff); every holder sees every session.
"""

import uuid
from typing import Annotated, Final

from fastapi import APIRouter

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
    ClassSessionCreate,
    ClassSessionCreated,
    ClassSessionDetail,
    SlotSuggestionRequest,
    SlotSuggestions,
)

SCHEDULE_VIEW: Final = "schedule.view"
SCHEDULE_MANAGE: Final = "schedule.manage"
SCHEDULE_OVERRIDE: Final = "schedule.override"

router = APIRouter(prefix="/api/v1/class-sessions", tags=["class-sessions"])


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


@router.post(
    "/suggestions",
    responses={
        **PERMISSION_RESPONSES,
        422: problem_response_doc(
            "The body is not valid (validation_error): a missing or unknown"
            " field (an `override` included), a kind other than individual,"
            " `limit` outside 1-10; the window (search_range_invalid,"
            " search_range_too_long, on search_to); the bounds"
            " (naive_datetime, not_whole_minutes, ends_before_start,"
            " duration_out_of_range, crosses_local_date); or the references"
            " (student_unknown, student_inactive, subject_unknown,"
            " subject_inactive, tutor_unknown, tutor_inactive,"
            " classroom_unknown, classroom_inactive), all in one response."
        ),
    },
)
async def suggest_class_session_slots(
    principal: Annotated[Principal, require(SCHEDULE_MANAGE)],
    body: SlotSuggestionRequest,
    uow: UnitOfWorkDep,
) -> SlotSuggestions:
    """Free times for the same student, subject, tutor, classroom and
    duration: starts on a 15-minute local grid in the window (default the
    requested date and the 6 days after it, at most 14 dates), never the
    requested start, each passing every hard and soft rule; nearest the
    requested start first (a tie goes to the earlier one), at most `limit`
    (default 5). Each slot lists the workload warnings a create would get.
    Read-only: nothing is created, locked or audited."""
    return await service.suggest_slots(uow.session, body)


@router.get(
    "/{class_session_id:uuid}",
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
