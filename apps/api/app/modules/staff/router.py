"""Tutor profile routes (#43; ``app.modules.staff.service``).

Every route is ``require("tutor.view")`` (Owners, Administrators, Academic
Coordinators and Tutors; owner decision E1 (a)); the record policy then
limits a tutor to their own profile, and ``tutor.manage`` (Owners and
Administrators) is checked inside the service for edits:

- ``GET /api/v1/tutors``: the tutors the caller may see.
- ``GET /api/v1/tutors/{user_id}``: one profile; ``404`` when the user is
  unknown, doesn't hold ``tutor`` or isn't visible.
- ``PATCH /api/v1/tutors/{user_id}``: no reauthentication (profile edits are
  not in ADR-0007 D10).

Check order of the ``PATCH``: ``401`` → ``csrf_failed`` →
``permission_denied`` → ``422`` → ``404`` → ``403 permission_denied`` (a
field the caller may not edit) → ``409 version_conflict``.

The search text ``q`` is a query parameter: the access log line has no query
string (#9), so it is never logged.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.core.authz import PERMISSION_RESPONSES, Principal, require
from app.core.errors import problem_response_doc
from app.core.pagination import OffsetPage, OffsetPagination
from app.core.uow import UnitOfWorkDep
from app.modules.identity.schemas import UserState
from app.modules.staff import service
from app.modules.staff.permissions import TUTOR_VIEW
from app.modules.staff.schemas import TutorDetail, TutorListItem, TutorUpdate

router = APIRouter(prefix="/api/v1/tutors", tags=["tutors"])

NOT_FOUND = problem_response_doc(
    "No such user, the user doesn't hold the tutor role, or the caller may not"
    " see this tutor (not_found)."
)


@router.get(
    "",
    responses={
        **PERMISSION_RESPONSES,
        422: problem_response_doc(
            "An unknown state, q longer than 100 characters, or a limit or"
            " offset out of range (validation_error)."
        ),
    },
)
async def list_tutors(
    principal: Annotated[Principal, require(TUTOR_VIEW)],
    page: OffsetPagination,
    uow: UnitOfWorkDep,
    q: Annotated[
        str | None,
        Query(
            max_length=100,
            description=(
                "Part of the username or display name (not case-sensitive;"
                " accents are not folded)."
            ),
        ),
    ] = None,
    state: Annotated[UserState | None, Query(description="One user state.")] = None,
) -> OffsetPage[TutorListItem]:
    """The users holding the tutor role, by display name, then username, with
    their contact fields. A tutor sees only themselves."""
    return await service.list_tutors(
        uow.session,
        principal=principal,
        q=(q or "").strip() or None,
        state=state,
        page=page,
    )


@router.get("/{user_id}", responses={**PERMISSION_RESPONSES, 404: NOT_FOUND})
async def get_tutor(
    principal: Annotated[Principal, require(TUTOR_VIEW)],
    user_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> TutorDetail:
    """One tutor profile, with the fields the caller may edit."""
    return await service.get_tutor(uow.session, principal=principal, user_id=user_id)


@router.patch(
    "/{user_id}",
    responses={
        **PERMISSION_RESPONSES,
        403: problem_response_doc(
            f"{PERMISSION_RESPONSES[403]['description']} Also: a sent field is"
            " not in the caller's editable_fields; nothing changes"
            " (permission_denied)."
        ),
        404: NOT_FOUND,
        409: problem_response_doc(
            "The profile changed since it was loaded, or another first edit"
            " created it (version_conflict); reload and retry."
        ),
        422: problem_response_doc(
            "The body is not valid: a missing or negative version, a blank"
            " display name, an email or phone that doesn't match its pattern,"
            " an experience longer than 4000 characters, an unknown field"
            " (validation_error)."
        ),
    },
)
async def update_tutor(
    principal: Annotated[Principal, require(TUTOR_VIEW)],
    user_id: uuid.UUID,
    body: TutorUpdate,
    uow: UnitOfWorkDep,
) -> TutorDetail:
    """Change the sent fields. Administrators edit every field; a tutor edits
    their own email, phone and professional experience. ``version`` 0 is a
    tutor without a stored profile: the first change creates it."""
    return await service.update_tutor(
        uow.session, principal=principal, user_id=user_id, body=body
    )
