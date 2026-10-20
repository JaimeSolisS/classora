"""Academic period and closure routes (#38; ``app.modules.catalog.service``).

- Reads, for every signed-in user (``authenticated()``): ``GET
  /api/v1/academic-periods``, ``GET /api/v1/academic-periods/{period_id}``,
  ``GET /api/v1/closures``, ``GET /api/v1/closures/check``, ``GET
  /api/v1/closures/{closure_id}``.
- Writes, ``require("catalog.manage")`` (Owners and Administrators): ``POST``
  and ``PATCH`` of both. No reauthentication (not in ADR-0007 D10).

Dates are local calendar dates (``YYYY-MM-DD``), inclusive at both ends.
Check order of a ``PATCH``: ``401`` → ``csrf_failed`` → ``permission_denied``
→ ``422`` → ``404`` → ``409 version_conflict`` → ``422`` (one sent date makes
the stored range invalid) → ``409 name_taken`` → ``409 period_overlap``
(periods only).
"""

import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Query
from fastapi.exceptions import RequestValidationError

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
    AcademicPeriodCreate,
    AcademicPeriodDetail,
    AcademicPeriodUpdate,
    CatalogStatus,
    ClosureCheck,
    ClosureCreate,
    ClosureDetail,
    ClosureType,
    ClosureUpdate,
    LocalDate,
    PeriodType,
)

router = APIRouter(prefix="/api/v1", tags=["catalog"])

CATALOG_MANAGE = "catalog.manage"

PERIOD_NOT_FOUND = problem_response_doc("No such academic period (not_found).")
CLOSURE_NOT_FOUND = problem_response_doc("No such closure (not_found).")
QUERY_INVALID = problem_response_doc(
    "An unknown status or type, a date not in the YYYY-MM-DD form, or a limit"
    " or offset out of range (validation_error)."
)
BODY_INVALID = (
    "The body is not valid: a blank or too long name, an unknown type, a date"
    " not in the YYYY-MM-DD form, an end before the start or a range too long"
    " (on ends_on), an unknown field"
)
VERSION_CONFLICT = (
    "The record changed since it was loaded (version_conflict); reload and retry."
)
NAME_TAKEN = (
    "Another period already has this name, ignoring case; inactive periods"
    " count (name_taken)."
)
PERIOD_OVERLAP = (
    "Another active period of the same type shares at least one day with"
    " these dates (period_overlap)."
)

StatusQuery = Annotated[
    CatalogStatus,
    Query(description="active (the default), inactive or all."),
]


# --- Academic periods ----------------------------------------------------------------


@router.get(
    "/academic-periods",
    responses={**AUTH_RESPONSES, 422: QUERY_INVALID},
)
async def list_academic_periods(
    principal: Annotated[Principal, authenticated()],
    page: OffsetPagination,
    uow: UnitOfWorkDep,
    status: StatusQuery = "active",
    period_type: Annotated[
        PeriodType | None, Query(description="Only periods of this type.")
    ] = None,
    on: Annotated[
        LocalDate | None,
        Query(description="Only periods that contain this date (YYYY-MM-DD)."),
    ] = None,
) -> OffsetPage[AcademicPeriodDetail]:
    """The academic periods with ``status`` (active ones by default), newest
    start first, then by name."""
    return await service.list_academic_periods(
        uow.session, status=status, period_type=period_type, on=on, page=page
    )


@router.post(
    "/academic-periods",
    status_code=201,
    responses={
        **PERMISSION_RESPONSES,
        409: problem_response_doc(f"{NAME_TAKEN} Or {PERIOD_OVERLAP}"),
        422: problem_response_doc(f"{BODY_INVALID} (validation_error)."),
    },
)
async def create_academic_period(
    principal: Annotated[Principal, require(CATALOG_MANAGE)],
    body: AcademicPeriodCreate,
    uow: UnitOfWorkDep,
) -> AcademicPeriodDetail:
    """Add an active academic period (inclusive dates, at most 730 days
    apart)."""
    return await service.create_academic_period(uow.session, body)


@router.get(
    "/academic-periods/{period_id}",
    responses={**AUTH_RESPONSES, 404: PERIOD_NOT_FOUND},
)
async def get_academic_period(
    principal: Annotated[Principal, authenticated()],
    period_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> AcademicPeriodDetail:
    """One academic period, active or not."""
    return await service.get_academic_period(uow.session, period_id)


@router.patch(
    "/academic-periods/{period_id}",
    responses={
        **PERMISSION_RESPONSES,
        404: PERIOD_NOT_FOUND,
        409: problem_response_doc(
            f"{VERSION_CONFLICT} Or {NAME_TAKEN} Or {PERIOD_OVERLAP}"
        ),
        422: problem_response_doc(
            f"{BODY_INVALID}, a null field, a missing version, or one sent date"
            " that makes the stored range invalid (validation_error)."
        ),
    },
)
async def update_academic_period(
    principal: Annotated[Principal, require(CATALOG_MANAGE)],
    period_id: uuid.UUID,
    body: AcademicPeriodUpdate,
    uow: UnitOfWorkDep,
) -> AcademicPeriodDetail:
    """Rename, change the dates, deactivate (``is_active: false``) or
    reactivate a period. Fields not sent stay unchanged; the type can't
    change."""
    return await service.update_academic_period(uow.session, period_id, body)


# --- Closures ------------------------------------------------------------------------


def _ensure_from_not_after_to(from_date: date | None, to_date: date | None) -> None:
    if from_date is not None and to_date is not None and from_date > to_date:
        errors: list[dict[str, Any]] = [
            {
                "loc": ("query", "to"),
                "type": "value_error",
                "msg": "to must not be before from",
            }
        ]
        raise RequestValidationError(errors)


@router.get(
    "/closures",
    responses={
        **AUTH_RESPONSES,
        422: problem_response_doc(
            "An unknown status or type, a date not in the YYYY-MM-DD form,"
            " from after to, or a limit or offset out of range"
            " (validation_error)."
        ),
    },
)
async def list_closures(
    principal: Annotated[Principal, authenticated()],
    page: OffsetPagination,
    uow: UnitOfWorkDep,
    status: StatusQuery = "active",
    closure_type: Annotated[
        ClosureType | None, Query(description="Only closures of this type.")
    ] = None,
    from_date: Annotated[
        LocalDate | None,
        Query(
            alias="from",
            description="Only closures that end on or after this date.",
        ),
    ] = None,
    to_date: Annotated[
        LocalDate | None,
        Query(
            alias="to",
            description="Only closures that start on or before this date.",
        ),
    ] = None,
) -> OffsetPage[ClosureDetail]:
    """The closures with ``status`` (active ones by default) that overlap
    ``[from, to]`` (inclusive; either bound optional), by start, then end."""
    _ensure_from_not_after_to(from_date, to_date)
    return await service.list_closures(
        uow.session,
        status=status,
        closure_type=closure_type,
        from_date=from_date,
        to_date=to_date,
        page=page,
    )


@router.post(
    "/closures",
    status_code=201,
    responses={
        **PERMISSION_RESPONSES,
        422: problem_response_doc(f"{BODY_INVALID} (validation_error)."),
    },
)
async def create_closure(
    principal: Annotated[Principal, require(CATALOG_MANAGE)],
    body: ClosureCreate,
    uow: UnitOfWorkDep,
) -> ClosureDetail:
    """Add an active closure (whole local days, inclusive, at most 366 days
    apart). Closures may overlap each other."""
    return await service.create_closure(uow.session, body)


# Declared before /closures/{closure_id}, so "check" is never read as an id.
@router.get(
    "/closures/check",
    responses={
        **AUTH_RESPONSES,
        422: problem_response_doc(
            "date is missing or not in the YYYY-MM-DD form (validation_error)."
        ),
    },
)
async def check_closures(
    principal: Annotated[Principal, authenticated()],
    uow: UnitOfWorkDep,
    on_date: Annotated[
        LocalDate,
        Query(alias="date", description="The local date to check (YYYY-MM-DD)."),
    ],
) -> ClosureCheck:
    """Whether the center is closed on a local date, with the active closures
    that contain it."""
    return await service.check_closures(uow.session, on_date)


@router.get(
    "/closures/{closure_id}",
    responses={**AUTH_RESPONSES, 404: CLOSURE_NOT_FOUND},
)
async def get_closure(
    principal: Annotated[Principal, authenticated()],
    closure_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> ClosureDetail:
    """One closure, active or not."""
    return await service.get_closure(uow.session, closure_id)


@router.patch(
    "/closures/{closure_id}",
    responses={
        **PERMISSION_RESPONSES,
        404: CLOSURE_NOT_FOUND,
        409: problem_response_doc(VERSION_CONFLICT),
        422: problem_response_doc(
            f"{BODY_INVALID}, a null field, a missing version, or one sent date"
            " that makes the stored range invalid (validation_error)."
        ),
    },
)
async def update_closure(
    principal: Annotated[Principal, require(CATALOG_MANAGE)],
    closure_id: uuid.UUID,
    body: ClosureUpdate,
    uow: UnitOfWorkDep,
) -> ClosureDetail:
    """Rename, retype, change the dates, deactivate or reactivate a closure.
    Fields not sent stay unchanged."""
    return await service.update_closure(uow.session, closure_id, body)
