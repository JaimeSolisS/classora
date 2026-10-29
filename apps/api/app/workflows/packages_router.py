"""Package sale and read routes (#94 C, D5; ``app.workflows.packages``;
ADR-0006 E1, E6, E8, E10).

- ``POST /api/v1/billing/packages`` (``billing.manage`` and an
  ``Idempotency-Key``, #88): sell a package to a student. It posts a ledger
  row (the ``package_purchase`` charge), so a retry with the same key
  replays the first response and a concurrent duplicate runs once. The
  check order is ``401`` → ``403 csrf_failed`` → ``403
  privacy_notice_required`` → ``403 permission_denied`` → ``400
  idempotency_key_required|invalid`` → replay / ``422
  idempotency_key_reused`` → ``422`` (the body) → the rules of
  ``app.workflows.packages``. The route never commits; it ends with
  ``idem.respond``. No reauthentication (M6).
- ``GET /api/v1/billing/packages`` and ``GET …/{package_id}``
  (``billing.view``): staff only (#94 L9; the family portal reads arrive
  with #102).
"""

import uuid
from typing import Annotated, Final

from fastapi import APIRouter, Query, Response

from app.core.authz import PERMISSION_RESPONSES, Principal, require
from app.core.errors import problem_response_doc
from app.core.idempotency import IDEMPOTENCY_RESPONSES, IdempotentRequest, idempotent
from app.core.pagination import OffsetPage, OffsetPagination
from app.core.uow import UnitOfWorkDep
from app.modules.billing.service import PackageStatus
from app.workflows import packages
from app.workflows.package_schemas import PackageDetail, PackageItem, PackagePurchase

BILLING_VIEW: Final = "billing.view"
BILLING_MANAGE: Final = "billing.manage"

router = APIRouter(prefix="/api/v1/billing/packages", tags=["billing"])


@router.post(
    "",
    status_code=201,
    response_model=PackageDetail,
    responses={
        **PERMISSION_RESPONSES,
        **IDEMPOTENCY_RESPONSES,
        409: problem_response_doc(
            "The package is deactivated (package_product_inactive); it has no"
            " price today (package_not_offered); the student isn't in a family"
            " (student_has_no_family)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): a missing, malformed or"
            " unknown field; an unknown package (package_product_unknown) or"
            " student (student_unknown); or the Idempotency-Key was already"
            " used for a different request (idempotency_key_reused)."
        ),
    },
)
async def purchase_package(
    principal: Annotated[Principal, require(BILLING_MANAGE)],
    idem: Annotated[IdempotentRequest, idempotent()],
    body: PackagePurchase,
    uow: UnitOfWorkDep,
) -> Response:
    """Sell a package to a student. It is bought today, at today's price
    (discounts included), on the account of the student's family: one
    package purchase charge (due today plus the family's due-date offset)
    and the package's units. The units pay for the student's individual
    sessions, oldest-expiring package first, when each closeout is approved;
    they are never shared with siblings. Send a new `Idempotency-Key` per
    sale and the same one to retry it."""
    detail = await packages.purchase_package(uow.session, body)
    return await idem.respond(detail, status_code=201)


@router.get(
    "",
    responses={
        **PERMISSION_RESPONSES,
        422: problem_response_doc(
            "The query is not valid (validation_error): a malformed id, an"
            " unknown status, or `limit`/`offset` out of range."
        ),
    },
)
async def list_packages(
    principal: Annotated[Principal, require(BILLING_VIEW)],
    page: OffsetPagination,
    uow: UnitOfWorkDep,
    student_id: uuid.UUID | None = None,
    family_id: uuid.UUID | None = None,
    status: Annotated[list[PackageStatus] | None, Query()] = None,
) -> OffsetPage[PackageItem]:
    """The sold packages, the earliest expiry first (then purchase date and
    id). `status` (active, exhausted, expired; for the center's today) may
    be repeated."""
    return await packages.list_packages(
        uow.session,
        student_id=student_id,
        family_id=family_id,
        statuses=status or (),
        page=page,
    )


@router.get(
    "/{package_id}",
    responses={
        **PERMISSION_RESPONSES,
        404: problem_response_doc("No such package (not_found)."),
    },
)
async def get_package(
    principal: Annotated[Principal, require(BILLING_VIEW)],
    package_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> PackageDetail:
    """A sold package with its unit movements (grant, consumptions with
    their sessions, expiry), oldest first."""
    return await packages.get_package(uow.session, package_id)
