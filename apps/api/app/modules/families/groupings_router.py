"""Family grouping routes (#172; ``app.modules.families.groupings`` through
``service``).

- ``GET /api/v1/families/own`` (#102 A1, T2 (a)): ``authenticated()``, any
  role: the family of the caller's active guardian profile, or ``null``.
  Declared before ``/{family_id}`` so ``own`` is never parsed as an id.
  Never ``404``: it only describes the caller.
- ``GET /api/v1/families`` and ``GET /api/v1/families/{family_id}``:
  ``require("guardian.view")`` (there is no ``family.view``); the record
  policy limits a ``guardian`` to the family of their active profile. A
  hidden family is ``404``.
- ``POST /api/v1/families`` and ``PATCH /api/v1/families/{family_id}``:
  ``require("family.manage")`` (Owners, Administrators).
- ``PUT``/``DELETE /api/v1/families/{family_id}/students/{student_id}`` and
  ``…/guardians/{guardian_id}``: ``require("family.manage")``. ``PUT`` is
  idempotent.

No reauthentication (not in the ADR-0007 D10 list). No ``DELETE`` of a
family (deactivate it; deletion belongs to M10-07). The search text ``q`` is
a query parameter and is never logged (#9).
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.core.authz import (
    AUTH_RESPONSES,
    PERMISSION_RESPONSES,
    Principal,
    authenticated,
    require,
)
from app.core.errors import problem_response_doc
from app.core.pagination import OffsetPage, OffsetPagination
from app.core.uow import UnitOfWorkDep
from app.modules.families import service
from app.modules.families.permissions import FAMILY_MANAGE, GUARDIAN_VIEW
from app.modules.families.schemas import (
    FamilyCreate,
    FamilyDetail,
    FamilyListItem,
    FamilyStatus,
    FamilyUpdate,
    OwnFamily,
    SearchText,
)

router = APIRouter(prefix="/api/v1/families", tags=["families"])

NOT_FOUND = problem_response_doc("No such family, or not visible (not_found).")
MEMBER_NOT_FOUND = problem_response_doc(
    "No such family or member, or the member isn't in this family (not_found)."
)
ADD_NOT_FOUND = problem_response_doc(
    "No such family, or no such student or guardian (not_found)."
)
ADD_CONFLICT = problem_response_doc(
    "The family is inactive (family_inactive); or the member already belongs"
    " to another family (family_member_conflict)."
)
NAME_RULES = "a blank or too long name, an unknown field (validation_error)"


@router.get(
    "",
    responses={
        **PERMISSION_RESPONSES,
        422: problem_response_doc(
            "An unknown status, a search longer than 100 characters or with"
            " more than five words, or a limit or offset out of range"
            " (validation_error)."
        ),
    },
)
async def list_families(
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
        FamilyStatus, Query(description="active (the default), inactive or all.")
    ] = "active",
) -> OffsetPage[FamilyListItem]:
    """The families the caller may see (a guardian: only their own). With
    ``q``: by relevance, then by name."""
    return await service.list_families(
        uow.session, principal=principal, q=q, status=status, page=page
    )


@router.get(
    "/own",
    responses={
        **AUTH_RESPONSES,
        403: problem_response_doc(
            "The privacy notice must be accepted first (privacy_notice_required)."
        ),
    },
)
async def get_own_family(
    principal: Annotated[Principal, authenticated()], uow: UnitOfWorkDep
) -> OwnFamily:
    """The family of your active guardian profile (whatever your other
    roles), or `family: null` when you have no guardian profile, it is
    inactive, or it belongs to no family. An inactive family is returned
    with `is_active: false`. Use it to find your own family before reading
    its billing account."""
    return await service.own_family(uow.session, principal)


@router.post(
    "",
    status_code=201,
    responses={
        **PERMISSION_RESPONSES,
        422: problem_response_doc(f"The body is not valid: {NAME_RULES}."),
    },
)
async def create_family(
    principal: Annotated[Principal, require(FAMILY_MANAGE)],
    body: FamilyCreate,
    uow: UnitOfWorkDep,
) -> FamilyDetail:
    """Add an active family with no members; add them afterwards."""
    return await service.create_family(uow.session, principal=principal, body=body)


@router.get("/{family_id}", responses={**PERMISSION_RESPONSES, 404: NOT_FOUND})
async def get_family(
    principal: Annotated[Principal, require(GUARDIAN_VIEW)],
    family_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> FamilyDetail:
    """One family with its students and guardians (a guardian sees only
    their own profile among the guardians)."""
    return await service.get_family(
        uow.session, principal=principal, family_id=family_id
    )


@router.patch(
    "/{family_id}",
    responses={
        **PERMISSION_RESPONSES,
        404: NOT_FOUND,
        409: problem_response_doc(
            "The family changed since it was loaded (version_conflict)."
        ),
        422: problem_response_doc(
            f"The body is not valid: {NAME_RULES}; a null name or is_active, or"
            " a missing version (validation_error)."
        ),
    },
)
async def update_family(
    principal: Annotated[Principal, require(FAMILY_MANAGE)],
    family_id: uuid.UUID,
    body: FamilyUpdate,
    uow: UnitOfWorkDep,
) -> FamilyDetail:
    """Rename, deactivate or reactivate a family. Deactivating keeps its
    members; an inactive family takes no new ones."""
    return await service.update_family(
        uow.session, principal=principal, family_id=family_id, body=body
    )


@router.put(
    "/{family_id}/students/{student_id}",
    status_code=204,
    responses={**PERMISSION_RESPONSES, 404: ADD_NOT_FOUND, 409: ADD_CONFLICT},
)
async def add_family_student(
    principal: Annotated[Principal, require(FAMILY_MANAGE)],
    family_id: uuid.UUID,
    student_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> None:
    """Add the student to the family. Idempotent: a student already in this
    family is a no-op. To move a student, remove them from their family
    first."""
    await service.add_family_student(
        uow.session, principal=principal, family_id=family_id, student_id=student_id
    )


@router.delete(
    "/{family_id}/students/{student_id}",
    status_code=204,
    responses={**PERMISSION_RESPONSES, 404: MEMBER_NOT_FOUND},
)
async def remove_family_student(
    principal: Annotated[Principal, require(FAMILY_MANAGE)],
    family_id: uuid.UUID,
    student_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> None:
    """Remove the student from this family."""
    await service.remove_family_student(
        uow.session, principal=principal, family_id=family_id, student_id=student_id
    )


@router.put(
    "/{family_id}/guardians/{guardian_id}",
    status_code=204,
    responses={**PERMISSION_RESPONSES, 404: ADD_NOT_FOUND, 409: ADD_CONFLICT},
)
async def add_family_guardian(
    principal: Annotated[Principal, require(FAMILY_MANAGE)],
    family_id: uuid.UUID,
    guardian_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> None:
    """Add the guardian profile to the family (idempotent). Membership grants
    no access to the family's students: that follows the links."""
    await service.add_family_guardian(
        uow.session,
        principal=principal,
        family_id=family_id,
        guardian_id=guardian_id,
    )


@router.delete(
    "/{family_id}/guardians/{guardian_id}",
    status_code=204,
    responses={**PERMISSION_RESPONSES, 404: MEMBER_NOT_FOUND},
)
async def remove_family_guardian(
    principal: Annotated[Principal, require(FAMILY_MANAGE)],
    family_id: uuid.UUID,
    guardian_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> None:
    """Remove the guardian profile from this family. Its account stops seeing
    the family on its next request."""
    await service.remove_family_guardian(
        uow.session,
        principal=principal,
        family_id=family_id,
        guardian_id=guardian_id,
    )
