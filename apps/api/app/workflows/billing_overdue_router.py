"""The overdue accounts route (#97 E1; N7 (a);
``app.workflows.billing_overdue``).

``GET /api/v1/billing/overdue-accounts``: ``require("billing.view")``
(Owners, Administrators and Finance Staff). One row per family account
with overdue charges: the overdue total and count, the oldest effective due
date and the days since, the aging buckets (1–30, 31–60, 61–90, over 90
days), the outstanding amount, the account credit (shown, not netted), the
net balance and the last reminder; the totals over every matching account.
Filters ``min_days_overdue`` (1–3650, default 1) and ``family_status``
(``active | inactive | all``, default ``all``); offset pagination. Check
order: ``401`` → ``403 permission_denied`` → ``422`` (the query).
"""

from typing import Annotated, Final

from fastapi import APIRouter, Query

from app.core.authz import PERMISSION_RESPONSES, Principal, require
from app.core.errors import problem_response_doc
from app.core.pagination import OffsetPagination
from app.core.uow import UnitOfWorkDep
from app.modules.families.schemas import FamilyStatus
from app.workflows import billing_overdue
from app.workflows.billing_account_schemas import OverdueAccountsPage

BILLING_VIEW: Final = "billing.view"

router = APIRouter(prefix="/api/v1/billing", tags=["billing"])


@router.get(
    "/overdue-accounts",
    responses={
        **PERMISSION_RESPONSES,
        422: problem_response_doc(
            "The query is not valid (validation_error): `min_days_overdue`"
            " not from 1 to 3650, an unknown `family_status`, or"
            " `limit`/`offset` out of range."
        ),
    },
)
async def list_overdue_accounts(
    principal: Annotated[Principal, require(BILLING_VIEW)],
    page: OffsetPagination,
    uow: UnitOfWorkDep,
    min_days_overdue: Annotated[
        int,
        Query(
            ge=billing_overdue.MIN_DAYS_OVERDUE,
            le=billing_overdue.MAX_DAYS_OVERDUE,
            description="Only charges at least this many days past their due date.",
        ),
    ] = billing_overdue.MIN_DAYS_OVERDUE,
    family_status: Annotated[
        FamilyStatus, Query(description="Active, inactive or all families.")
    ] = "all",
) -> OverdueAccountsPage:
    """The families whose accounts have charges past their due date (the
    center's local date), most days overdue first, then the largest overdue
    total, then the family name. Account credit is shown but not subtracted
    from the overdue amounts until staff allocate it."""
    return await billing_overdue.list_overdue_accounts(
        uow.session,
        min_days_overdue=min_days_overdue,
        family_status=family_status,
        page=page,
    )
