"""Classroom routes (#39; ``app.modules.catalog.service``).

- Reads, for every signed-in user (``authenticated()``; tutors pick
  preferred rooms and schedules show rooms): ``GET /api/v1/classrooms``,
  ``GET /api/v1/classrooms/{classroom_id}``.
- Writes, ``require("catalog.manage")`` (Owners and Administrators): ``POST``
  and ``PATCH``. No reauthentication (not in ADR-0007 D10).

Window times are local wall-clock times (``HH:MM``). Check order of a
``PATCH``: ``401`` → ``csrf_failed`` → ``permission_denied`` → ``422`` →
``404`` → ``409 version_conflict`` → ``409 name_taken``.
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
    ClassroomCreate,
    ClassroomDetail,
    ClassroomUpdate,
)

router = APIRouter(prefix="/api/v1", tags=["catalog"])

CATALOG_MANAGE = "catalog.manage"

NOT_FOUND = problem_response_doc("No such classroom (not_found).")
BODY_INVALID = (
    "The body is not valid: a blank or too long name, a capacity outside 1-500,"
    " more than 28 windows, a weekday outside 1-7, a time not in the HH:MM form,"
    " an end not after its start (on ends_at), two windows of one weekday that"
    " overlap (window_overlap on the later item), an unknown field"
)
NAME_TAKEN = (
    "Another classroom already has this name, ignoring case; inactive"
    " classrooms count (name_taken)."
)
VERSION_CONFLICT = (
    "The record changed since it was loaded (version_conflict); reload and retry."
)


@router.get(
    "/classrooms",
    responses={
        **AUTH_RESPONSES,
        422: problem_response_doc(
            "An unknown status, or a limit or offset out of range (validation_error)."
        ),
    },
)
async def list_classrooms(
    principal: Annotated[Principal, authenticated()],
    page: OffsetPagination,
    uow: UnitOfWorkDep,
    status: Annotated[
        CatalogStatus, Query(description="active (the default), inactive or all.")
    ] = "active",
) -> OffsetPage[ClassroomDetail]:
    """The classrooms with ``status`` (active ones by default), by name, with
    their weekly windows (empty: no time restriction)."""
    return await service.list_classrooms(uow.session, status=status, page=page)


@router.post(
    "/classrooms",
    status_code=201,
    responses={
        **PERMISSION_RESPONSES,
        409: problem_response_doc(NAME_TAKEN),
        422: problem_response_doc(f"{BODY_INVALID} (validation_error)."),
    },
)
async def create_classroom(
    principal: Annotated[Principal, require(CATALOG_MANAGE)],
    body: ClassroomCreate,
    uow: UnitOfWorkDep,
) -> ClassroomDetail:
    """Add an active classroom, optionally with weekly windows."""
    return await service.create_classroom(uow.session, body)


@router.get(
    "/classrooms/{classroom_id}",
    responses={**AUTH_RESPONSES, 404: NOT_FOUND},
)
async def get_classroom(
    principal: Annotated[Principal, authenticated()],
    classroom_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> ClassroomDetail:
    """One classroom, active or not."""
    return await service.get_classroom(uow.session, classroom_id)


@router.patch(
    "/classrooms/{classroom_id}",
    responses={
        **PERMISSION_RESPONSES,
        404: NOT_FOUND,
        409: problem_response_doc(f"{VERSION_CONFLICT} Or {NAME_TAKEN}"),
        422: problem_response_doc(
            f"{BODY_INVALID}, a null field or a missing version (validation_error)."
        ),
    },
)
async def update_classroom(
    principal: Annotated[Principal, require(CATALOG_MANAGE)],
    classroom_id: uuid.UUID,
    body: ClassroomUpdate,
    uow: UnitOfWorkDep,
) -> ClassroomDetail:
    """Rename, change the capacity, deactivate (``is_active: false``) or
    reactivate a classroom, or replace its windows (``availability``: the
    full list; ``[]`` clears them). Fields not sent stay unchanged."""
    return await service.update_classroom(uow.session, classroom_id, body)
