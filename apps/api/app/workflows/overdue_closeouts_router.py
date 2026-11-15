"""Overdue closeouts route (#301 B; :mod:`app.workflows.closeout_on_behalf`
gives the rules).

- ``GET /api/v1/closeouts/overdue``: ``require("teaching_hours.approve")``
  (Owner, Administrator, Academic Coordinator), checked before anything is
  read. Its own ``/api/v1/closeouts`` prefix keeps it clear of
  ``/api/v1/class-sessions/{class_session_id}``.
"""

import uuid
from datetime import date
from typing import Annotated, Final

from fastapi import APIRouter, Query

from app.core.authz import PERMISSION_RESPONSES, Principal, require
from app.core.errors import problem_response_doc
from app.core.pagination import OffsetPage, OffsetPagination
from app.core.uow import UnitOfWorkDep
from app.workflows import closeout_on_behalf as workflow
from app.workflows.session_closeout_schemas import OverdueCloseoutItem

router = APIRouter(prefix="/api/v1/closeouts", tags=["closeout"])

TEACHING_HOURS_APPROVE: Final = "teaching_hours.approve"


@router.get(
    "/overdue",
    responses={
        **PERMISSION_RESPONSES,
        422: problem_response_doc(
            "The query is not valid (validation_error): a tutor_user_id that"
            " isn't a UUID, a date not in the YYYY-MM-DD form, to before from"
            " (range_invalid at query.to), or a limit or offset out of range."
        ),
    },
)
async def list_overdue_closeouts(
    principal: Annotated[Principal, require(TEACHING_HOURS_APPROVE)],
    page: OffsetPagination,
    uow: UnitOfWorkDep,
    tutor_user_id: Annotated[
        uuid.UUID | None,
        Query(description="Only this tutor's classes (an unknown id: none)."),
    ] = None,
    from_date: Annotated[
        date | None,
        Query(
            alias="from",
            description="Only classes starting on or after this center-local date.",
        ),
    ] = None,
    to_date: Annotated[
        date | None,
        Query(
            alias="to",
            description="Only classes starting on or before this center-local date.",
        ),
    ] = None,
) -> OffsetPage[OverdueCloseoutItem]:
    """Classes that have ended without a closeout (confirmed, past their
    end), oldest first, for academic reviewers to close out on the tutor's
    behalf. No student names."""
    return await workflow.list_overdue_closeouts(
        uow.session,
        page=page,
        tutor_user_id=tutor_user_id,
        from_date=from_date,
        to_date=to_date,
    )
