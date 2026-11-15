"""The unbilled tuition periods route (#93 G1, K9; ``app.workflows.
tuition_charges``).

``GET /api/v1/billing/unbilled-tuition-periods``: ``require("billing.view")``
(Owners, Administrators and Finance Staff). The due tuition periods (from
the billing start date to the center's today, not paused, before the plan's
end) that the daily job can't post, each with one reason:
``family_changed`` (the student is no longer in the family whose account
the plan bills; checked first) or ``no_price`` (the plan's price has no
version in effect on the period's first day, or it says "not offered").
Computed on read; billing off gives an empty page. Check order: ``401`` →
``403 permission_denied`` → ``422`` (the query).
"""

import uuid
from typing import Annotated, Final

from fastapi import APIRouter, Query

from app.core.authz import PERMISSION_RESPONSES, Principal, require
from app.core.errors import problem_response_doc
from app.core.pagination import OffsetPage, OffsetPagination
from app.core.uow import UnitOfWorkDep
from app.workflows import tuition_charges
from app.workflows.tuition_plan_schemas import (
    TuitionUnbilledReason,
    UnbilledTuitionItem,
)

BILLING_VIEW: Final = "billing.view"

router = APIRouter(prefix="/api/v1/billing", tags=["billing"])


@router.get(
    "/unbilled-tuition-periods",
    responses={
        **PERMISSION_RESPONSES,
        422: problem_response_doc(
            "The query is not valid (validation_error): a reason other than"
            " no_price or family_changed, a malformed student id, or"
            " `limit`/`offset` out of range."
        ),
    },
)
async def list_unbilled_tuition_periods(
    principal: Annotated[Principal, require(BILLING_VIEW)],
    page: OffsetPagination,
    uow: UnitOfWorkDep,
    reason: Annotated[list[TuitionUnbilledReason] | None, Query()] = None,
    student_id: uuid.UUID | None = None,
) -> OffsetPage[UnbilledTuitionItem]:
    """The due tuition periods that have no charge because the plan's price
    has no amount on the period's first day (`no_price`) or the student
    moved to another family (`family_changed`), by period start, student
    name and plan id. `reason` may be repeated. Empty while billing is off.
    Each period is charged by the next daily run once its price is fixed; a
    family change needs a new plan."""
    return await tuition_charges.list_unbilled_tuition_periods(
        uow.session, reasons=reason or (), student_id=student_id, page=page
    )
