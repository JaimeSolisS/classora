"""The unbilled session items route (#91 D2-D5; ``app.workflows.
session_charges``).

``GET /api/v1/billing/unbilled-items``: ``require("billing.view")`` (Owners,
Administrators and Finance Staff). The confirmed or completed individual
sessions on or after the billing start date that the sweeper can't charge,
each with one reason: ``no_billing_account`` (the student has no family;
checked first) or ``no_price`` (no catalog price in effect on the session's
local date). Computed on read; billing off gives an empty page. Check
order: ``401`` → ``403 permission_denied`` → ``422`` (the query).
"""

import uuid
from typing import Annotated, Final

from fastapi import APIRouter, Query

from app.core.authz import PERMISSION_RESPONSES, Principal, require
from app.core.errors import problem_response_doc
from app.core.pagination import OffsetPage, OffsetPagination
from app.core.uow import UnitOfWorkDep
from app.workflows import session_charges
from app.workflows.session_charge_schemas import UnbilledReason, UnbilledSessionItem

BILLING_VIEW: Final = "billing.view"

router = APIRouter(prefix="/api/v1/billing", tags=["billing"])


@router.get(
    "/unbilled-items",
    responses={
        **PERMISSION_RESPONSES,
        422: problem_response_doc(
            "The query is not valid (validation_error): a reason other than"
            " no_billing_account or no_price, a malformed student id, or"
            " `limit`/`offset` out of range."
        ),
    },
)
async def list_unbilled_items(
    principal: Annotated[Principal, require(BILLING_VIEW)],
    page: OffsetPagination,
    uow: UnitOfWorkDep,
    reason: Annotated[list[UnbilledReason] | None, Query()] = None,
    student_id: uuid.UUID | None = None,
) -> OffsetPage[UnbilledSessionItem]:
    """The billable sessions that have no charge because the student has no
    family (`no_billing_account`) or no price is in effect on the session's
    local date (`no_price`), by service date, start time, student name and
    id. `reason` may be repeated. Empty while billing is off. Each item is
    charged by the next sweeper run once its cause is fixed."""
    return await session_charges.list_unbilled_items(
        uow.session, reasons=reason or (), student_id=student_id, page=page
    )
