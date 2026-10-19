"""Academic catalog routes (#32; ``app.modules.catalog.service``).

- Reads, for every signed-in user (``authenticated()``): ``GET
  /api/v1/education-levels``, ``GET /api/v1/education-levels/{level_id}``,
  ``GET /api/v1/subjects``, ``GET /api/v1/subjects/{subject_id}``.
- Writes, ``require("catalog.manage")`` (Owners and Administrators): ``POST
  /api/v1/education-levels``, ``PATCH /api/v1/education-levels/{level_id}``,
  ``POST /api/v1/education-levels/{level_id}/subjects``, ``PATCH
  /api/v1/subjects/{subject_id}``. No reauthentication (catalog edits are not
  in ADR-0007 D10).

Check order of a ``PATCH``: ``401`` → ``csrf_failed`` → ``permission_denied``
→ ``422`` → ``404`` → ``409 version_conflict`` → ``409 name_taken`` → ``409
education_level_has_active_subjects`` / ``education_level_inactive``.
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
from app.modules.catalog import service
from app.modules.catalog.schemas import (
    CatalogStatus,
    EducationLevelCreate,
    EducationLevelDetail,
    EducationLevelUpdate,
    SubjectCreate,
    SubjectDetail,
    SubjectUpdate,
)

router = APIRouter(prefix="/api/v1", tags=["catalog"])

CATALOG_MANAGE = "catalog.manage"

LEVEL_NOT_FOUND = problem_response_doc("No such education level (not_found).")
SUBJECT_NOT_FOUND = problem_response_doc("No such subject (not_found).")
STATUS_INVALID = problem_response_doc(
    "An unknown status, or a limit or offset out of range (validation_error)."
)
NAME_INVALID = (
    "The body is not valid: a blank name, a name longer than 100 characters,"
    " an unknown field"
)
VERSION_CONFLICT = (
    "The record changed since it was loaded (version_conflict); reload and retry."
)
NAME_TAKEN = (
    "Another record already has this name, ignoring case; inactive records"
    " count (name_taken)."
)

StatusQuery = Annotated[
    CatalogStatus,
    Query(description="active (the default), inactive or all."),
]


@router.get(
    "/education-levels",
    responses={**AUTH_RESPONSES, 422: STATUS_INVALID},
)
async def list_education_levels(
    principal: Annotated[Principal, authenticated()],
    page: OffsetPagination,
    uow: UnitOfWorkDep,
    status: StatusQuery = "active",
) -> OffsetPage[EducationLevelDetail]:
    """The education levels with ``status`` (active ones by default), by
    position, then name."""
    return await service.list_education_levels(uow.session, status=status, page=page)


@router.post(
    "/education-levels",
    status_code=201,
    responses={
        **PERMISSION_RESPONSES,
        409: problem_response_doc(NAME_TAKEN),
        422: problem_response_doc(
            f"{NAME_INVALID}, or a position outside 0-9999 (validation_error)."
        ),
    },
)
async def create_education_level(
    principal: Annotated[Principal, require(CATALOG_MANAGE)],
    body: EducationLevelCreate,
    uow: UnitOfWorkDep,
) -> EducationLevelDetail:
    """Add an active education level. Without ``sort_order`` it goes last."""
    return await service.create_education_level(uow.session, body)


@router.get(
    "/education-levels/{level_id}",
    responses={**AUTH_RESPONSES, 404: LEVEL_NOT_FOUND},
)
async def get_education_level(
    principal: Annotated[Principal, authenticated()],
    level_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> EducationLevelDetail:
    """One education level, active or not."""
    return await service.get_education_level(uow.session, level_id)


@router.patch(
    "/education-levels/{level_id}",
    responses={
        **PERMISSION_RESPONSES,
        404: LEVEL_NOT_FOUND,
        409: problem_response_doc(
            f"{VERSION_CONFLICT} Or {NAME_TAKEN} Or the level still has an"
            " active subject and can't be deactivated"
            " (education_level_has_active_subjects)."
        ),
        422: problem_response_doc(
            f"{NAME_INVALID}, a null field, a position outside 0-9999 or a"
            " missing version (validation_error)."
        ),
    },
)
async def update_education_level(
    principal: Annotated[Principal, require(CATALOG_MANAGE)],
    level_id: uuid.UUID,
    body: EducationLevelUpdate,
    uow: UnitOfWorkDep,
) -> EducationLevelDetail:
    """Rename, reorder, deactivate (``is_active: false``) or reactivate a
    level. Fields not sent stay unchanged. A level with active subjects
    can't be deactivated; reactivating changes no subject."""
    return await service.update_education_level(uow.session, level_id, body)


@router.post(
    "/education-levels/{level_id}/subjects",
    status_code=201,
    responses={
        **PERMISSION_RESPONSES,
        404: LEVEL_NOT_FOUND,
        409: problem_response_doc(
            f"{NAME_TAKEN} (within the level) Or the level is inactive"
            " (education_level_inactive)."
        ),
        422: problem_response_doc(f"{NAME_INVALID} (validation_error)."),
    },
)
async def create_subject(
    principal: Annotated[Principal, require(CATALOG_MANAGE)],
    level_id: uuid.UUID,
    body: SubjectCreate,
    uow: UnitOfWorkDep,
) -> SubjectDetail:
    """Add an active subject to an active level."""
    return await service.create_subject(uow.session, level_id, body)


@router.get(
    "/subjects",
    responses={**AUTH_RESPONSES, 422: STATUS_INVALID},
)
async def list_subjects(
    principal: Annotated[Principal, authenticated()],
    page: OffsetPagination,
    uow: UnitOfWorkDep,
    education_level_id: Annotated[
        uuid.UUID | None,
        Query(description="Only this level's subjects (unknown: an empty page)."),
    ] = None,
    status: StatusQuery = "active",
) -> OffsetPage[SubjectDetail]:
    """The subjects with ``status`` (active ones by default), by level
    position and name, then subject name."""
    return await service.list_subjects(
        uow.session, education_level_id=education_level_id, status=status, page=page
    )


@router.get(
    "/subjects/{subject_id}",
    responses={**AUTH_RESPONSES, 404: SUBJECT_NOT_FOUND},
)
async def get_subject(
    principal: Annotated[Principal, authenticated()],
    subject_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> SubjectDetail:
    """One subject, active or not, with its level's name."""
    return await service.get_subject(uow.session, subject_id)


@router.patch(
    "/subjects/{subject_id}",
    responses={
        **PERMISSION_RESPONSES,
        404: SUBJECT_NOT_FOUND,
        409: problem_response_doc(
            f"{VERSION_CONFLICT} Or {NAME_TAKEN} (within the level) Or the"
            " subject is reactivated while its level is inactive"
            " (education_level_inactive)."
        ),
        422: problem_response_doc(
            f"{NAME_INVALID} (education_level_id can't be changed), a null"
            " field or a missing version (validation_error)."
        ),
    },
)
async def update_subject(
    principal: Annotated[Principal, require(CATALOG_MANAGE)],
    subject_id: uuid.UUID,
    body: SubjectUpdate,
    uow: UnitOfWorkDep,
) -> SubjectDetail:
    """Rename, deactivate or reactivate a subject. Its level never changes."""
    return await service.update_subject(uow.session, subject_id, body)
