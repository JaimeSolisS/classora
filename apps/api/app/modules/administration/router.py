"""``GET /api/v1/audit-events``: the audit log for holders of ``audit.view``."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi.exceptions import RequestValidationError
from pydantic import AwareDatetime

from app.core.authz import PERMISSION_RESPONSES, require
from app.core.errors import problem_response_doc
from app.core.pagination import CursorPage, CursorPagination
from app.core.uow import UnitOfWorkDep
from app.modules.administration.schemas import AuditEventListItem
from app.modules.administration.service import AuditEventFilters, list_audit_events

router = APIRouter(tags=["audit"])

RECORD_TYPE_PATTERN = "^[a-z_]{1,63}$"


def audit_event_filters(
    record_type: Annotated[
        str | None,
        Query(pattern=RECORD_TYPE_PATTERN, description="Table name, with record_id."),
    ] = None,
    record_id: Annotated[
        uuid.UUID | None, Query(description="Record id, with record_type.")
    ] = None,
    actor_id: Annotated[
        uuid.UUID | None, Query(description="Events written by this user.")
    ] = None,
    occurred_from: Annotated[
        AwareDatetime | None, Query(description="Inclusive lower bound.")
    ] = None,
    occurred_to: Annotated[
        AwareDatetime | None, Query(description="Exclusive upper bound.")
    ] = None,
) -> AuditEventFilters:
    """The filters, with the checks that involve two parameters (422)."""
    errors: list[dict[str, Any]] = []
    if (record_type is None) != (record_id is None):
        missing = "record_id" if record_id is None else "record_type"
        errors.append(
            {
                "loc": ("query", missing),
                "type": "missing",
                "msg": "record_type and record_id must be given together",
            }
        )
    if (
        occurred_from is not None
        and occurred_to is not None
        and occurred_from >= occurred_to
    ):
        errors.append(
            {
                "loc": ("query", "occurred_to"),
                "type": "value_error",
                "msg": "occurred_to must be later than occurred_from",
            }
        )
    if errors:
        raise RequestValidationError(errors)
    return AuditEventFilters(
        record_type=record_type,
        record_id=record_id,
        actor_id=actor_id,
        occurred_from=occurred_from,
        occurred_to=occurred_to,
    )


@router.get(
    "/api/v1/audit-events",
    dependencies=[require("audit.view")],
    responses={
        **PERMISSION_RESPONSES,
        400: problem_response_doc("The pagination cursor is not valid."),
    },
)
async def list_events(
    filters: Annotated[AuditEventFilters, Depends(audit_event_filters)],
    page: CursorPagination,
    uow: UnitOfWorkDep,
) -> CursorPage[AuditEventListItem]:
    """The audit log, newest first, filtered by record, actor and time range."""
    return await list_audit_events(uow.session, filters, page)
