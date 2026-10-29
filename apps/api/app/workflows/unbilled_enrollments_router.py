"""The unbilled enrollments route (#92 F2, J9; ``app.workflows.
enrollment_charges``).

``GET /api/v1/billing/unbilled-enrollments``: ``require("billing.view")``
(Owners, Administrators and Finance Staff). The group and cohort enrollment
periods on or after the billing start date whose fee the enrollment sweeper
can't post, each with one reason: ``no_price`` (a cohort with no course fee
in effect on the enrollment's local date; checked first) or
``no_billing_account`` (the student has no family). A session-priced group
period isn't listed: its sessions are in ``/unbilled-items``. Computed on
read; billing off gives an empty page. Check order: ``401`` → ``403
permission_denied`` → ``422`` (the query).
"""

import uuid
from typing import Annotated, Final

from fastapi import APIRouter, Query

from app.core.authz import PERMISSION_RESPONSES, Principal, require
from app.core.errors import problem_response_doc
from app.core.pagination import OffsetPage, OffsetPagination
from app.core.uow import UnitOfWorkDep
from app.workflows import enrollment_charges
from app.workflows.enrollment_charge_schemas import (
    EnrollmentChargeKind,
    UnbilledEnrollmentItem,
)
from app.workflows.session_charge_schemas import UnbilledReason

BILLING_VIEW: Final = "billing.view"

router = APIRouter(prefix="/api/v1/billing", tags=["billing"])


@router.get(
    "/unbilled-enrollments",
    responses={
        **PERMISSION_RESPONSES,
        422: problem_response_doc(
            "The query is not valid (validation_error): a reason other than"
            " no_billing_account or no_price, a kind other than group_enrollment"
            " or cohort_enrollment, a malformed student id, or `limit`/`offset`"
            " out of range."
        ),
    },
)
async def list_unbilled_enrollments(
    principal: Annotated[Principal, require(BILLING_VIEW)],
    page: OffsetPagination,
    uow: UnitOfWorkDep,
    reason: Annotated[list[UnbilledReason] | None, Query()] = None,
    kind: Annotated[list[EnrollmentChargeKind] | None, Query()] = None,
    student_id: uuid.UUID | None = None,
) -> OffsetPage[UnbilledEnrollmentItem]:
    """The group and cohort enrollment periods that have no fee charge because
    no course fee is in effect on the enrollment's local date (`no_price`,
    cohorts) or the student has no family (`no_billing_account`), by service
    date, student name and enrollment id. `reason` and `kind` may be
    repeated. Empty while billing is off. Each period is charged by the next
    sweeper run once its cause is fixed."""
    return await enrollment_charges.list_unbilled_enrollments(
        uow.session,
        reasons=reason or (),
        kinds=kind or (),
        student_id=student_id,
        page=page,
    )
