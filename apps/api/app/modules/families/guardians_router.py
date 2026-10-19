"""Guardian profile and link routes (#34; ``app.modules.families.guardians``
through ``service``).

- ``GET /api/v1/guardians`` and ``GET /api/v1/guardians/{guardian_id}``:
  ``require("guardian.view")``; the record policy limits a ``guardian`` to
  their own active profile. A hidden profile is ``404``.
- ``POST /api/v1/guardians`` and ``PATCH /api/v1/guardians/{guardian_id}``:
  ``require("family.manage")`` (Owners, Administrators).
- ``POST /api/v1/students/{student_id}/guardians`` and ``DELETE
  /api/v1/students/{student_id}/guardians/{guardian_id}``:
  ``require("family.manage")``.

No reauthentication: linking records isn't in the ADR-0007 D10 list. No
``DELETE`` of a profile (deactivate it; deletion belongs to M10-07).
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.authz import PERMISSION_RESPONSES, Principal, require
from app.core.errors import problem_response_doc
from app.core.pagination import OffsetPage, OffsetPagination
from app.core.uow import UnitOfWorkDep
from app.modules.families import service
from app.modules.families.assignments import (
    TutorAssignmentSource,
    get_tutor_assignment_source,
)
from app.modules.families.permissions import FAMILY_MANAGE, GUARDIAN_VIEW
from app.modules.families.schemas import (
    GuardianCreate,
    GuardianDetail,
    GuardianListItem,
    GuardianStatus,
    GuardianUpdate,
    SearchText,
    StudentGuardianCreate,
    StudentGuardianItem,
)

router = APIRouter(tags=["guardians"])

GUARDIANS = "/api/v1/guardians"
STUDENT_GUARDIANS = "/api/v1/students/{student_id}/guardians"

AssignmentSource = Annotated[
    TutorAssignmentSource, Depends(get_tutor_assignment_source)
]

NOT_FOUND = problem_response_doc("No such guardian, or not visible (not_found).")
BODY_RULES = (
    "a blank or too long name, a malformed email or phone, an unknown field"
    " (validation_error); an account that is unknown, archived or doesn't hold"
    " the guardian role (guardian_account_unavailable)"
)
ACCOUNT_IN_USE = (
    "The account is already linked to another guardian (guardian_account_in_use)."
)


@router.get(
    GUARDIANS,
    responses={
        **PERMISSION_RESPONSES,
        422: problem_response_doc(
            "An unknown status, a search longer than 100 characters or with"
            " more than five words, or a limit or offset out of range"
            " (validation_error)."
        ),
    },
)
async def list_guardians(
    principal: Annotated[Principal, require(GUARDIAN_VIEW)],
    page: OffsetPagination,
    uow: UnitOfWorkDep,
    q: Annotated[
        SearchText | None,
        Query(
            description=(
                "Name search, ignoring case and accents, in any word order and"
                " tolerant of typos (1-100 characters, at most 5 words)."
            )
        ),
    ] = None,
    status: Annotated[
        GuardianStatus, Query(description="active (the default), inactive or all.")
    ] = "active",
) -> OffsetPage[GuardianListItem]:
    """The guardian profiles the caller may see (a guardian: only their own).
    With ``q``: by relevance, then by family names and given names."""
    return await service.list_guardians(
        uow.session, principal=principal, q=q, status=status, page=page
    )


@router.post(
    GUARDIANS,
    status_code=201,
    responses={
        **PERMISSION_RESPONSES,
        409: problem_response_doc(ACCOUNT_IN_USE),
        422: problem_response_doc(f"The body is not valid: {BODY_RULES}."),
    },
)
async def create_guardian(
    principal: Annotated[Principal, require(FAMILY_MANAGE)],
    body: GuardianCreate,
    uow: UnitOfWorkDep,
) -> GuardianDetail:
    """Add an active guardian profile, optionally linked to a login account."""
    return await service.create_guardian(uow.session, principal=principal, body=body)


@router.get(
    GUARDIANS + "/{guardian_id}",
    responses={**PERMISSION_RESPONSES, 404: NOT_FOUND},
)
async def get_guardian(
    principal: Annotated[Principal, require(GUARDIAN_VIEW)],
    guardian_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> GuardianDetail:
    """One guardian profile with its linked students. ``account`` is ``null``
    unless the caller holds ``family.manage``."""
    return await service.get_guardian(
        uow.session, principal=principal, guardian_id=guardian_id
    )


@router.patch(
    GUARDIANS + "/{guardian_id}",
    responses={
        **PERMISSION_RESPONSES,
        404: NOT_FOUND,
        409: problem_response_doc(
            "The guardian changed since it was loaded (version_conflict); or "
            + ACCOUNT_IN_USE
        ),
        422: problem_response_doc(
            f"The body is not valid: {BODY_RULES}; a null name or is_active, or"
            " a missing version (validation_error)."
        ),
    },
)
async def update_guardian(
    principal: Annotated[Principal, require(FAMILY_MANAGE)],
    guardian_id: uuid.UUID,
    body: GuardianUpdate,
    uow: UnitOfWorkDep,
) -> GuardianDetail:
    """Change a profile: the fields sent, ``user_id`` to link, move or (with
    ``null``) clear the account, ``is_active`` to deactivate or reactivate.
    An inactive profile grants its account nothing."""
    return await service.update_guardian(
        uow.session, principal=principal, guardian_id=guardian_id, body=body
    )


@router.post(
    STUDENT_GUARDIANS,
    status_code=201,
    responses={
        **PERMISSION_RESPONSES,
        404: problem_response_doc("No such student (not_found)."),
        409: problem_response_doc(
            "The guardian is already linked to this student (guardian_already_linked)."
        ),
        422: problem_response_doc(
            "An unknown relationship or field, a missing guardian_id"
            " (validation_error); an unknown or inactive guardian"
            " (guardian_unavailable)."
        ),
    },
)
async def link_guardian(
    principal: Annotated[Principal, require(FAMILY_MANAGE)],
    student_id: uuid.UUID,
    body: StudentGuardianCreate,
    uow: UnitOfWorkDep,
    source: AssignmentSource,
) -> StudentGuardianItem:
    """Link an active guardian to the student with a relationship. The
    guardian's account then sees the student on its next request."""
    return await service.link_guardian(
        uow.session,
        principal=principal,
        source=source,
        student_id=student_id,
        body=body,
    )


@router.delete(
    STUDENT_GUARDIANS + "/{guardian_id}",
    status_code=204,
    responses={
        **PERMISSION_RESPONSES,
        404: problem_response_doc(
            "No such student, guardian or link between them (not_found)."
        ),
    },
)
async def unlink_guardian(
    principal: Annotated[Principal, require(FAMILY_MANAGE)],
    student_id: uuid.UUID,
    guardian_id: uuid.UUID,
    uow: UnitOfWorkDep,
    source: AssignmentSource,
) -> None:
    """Remove the link. The guardian's account loses access to the student on
    its next request."""
    await service.unlink_guardian(
        uow.session,
        principal=principal,
        source=source,
        student_id=student_id,
        guardian_id=guardian_id,
    )
