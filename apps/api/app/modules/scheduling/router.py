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
