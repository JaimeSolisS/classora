"""Package product routes (#94 B; ``app.workflows.packages``; ADR-0006
E8, E10).

Every route needs ``billing.manage`` (Owners, Administrators and Finance
Staff; not delegable), reads included. No reauthentication and no
``Idempotency-Key``: a product is configuration, not a ledger row (the #89
precedent). The check order of the writes is ``401`` → ``403 csrf_failed``
→ ``403 privacy_notice_required`` → ``403 permission_denied`` → ``422``
(the body) → ``404`` → the rules.

- ``GET /api/v1/billing/package-products``: offset pagination; filters
  ``is_active`` and ``subject_id``.
- ``POST /api/v1/billing/package-products``: a product and its first price
  version.
- ``GET`` / ``PATCH /api/v1/billing/package-products/{product_id}``
  (``name`` and ``is_active`` only).
- ``POST /api/v1/billing/package-products/{product_id}/versions`` and
  ``DELETE …/versions/{price_version_id}`` (the #89 version rules).

There is no product ``DELETE``: a product is deactivated.
"""

import uuid
from typing import Annotated, Final

from fastapi import APIRouter, Response

from app.core.authz import PERMISSION_RESPONSES, Principal, require
from app.core.errors import problem_response_doc
from app.core.pagination import OffsetPage, OffsetPagination
from app.core.uow import UnitOfWorkDep
from app.modules.billing.schemas import PriceVersionCreate
from app.workflows import packages
from app.workflows.package_schemas import (
    PackageProductCreate,
    PackageProductDetail,
    PackageProductItem,
    PackageProductUpdate,
)

BILLING_MANAGE: Final = "billing.manage"

router = APIRouter(prefix="/api/v1/billing/package-products", tags=["billing"])

_NOT_FOUND = problem_response_doc("No such package product (not_found).")


@router.get(
    "",
    responses={
        **PERMISSION_RESPONSES,
        422: problem_response_doc(
            "The query is not valid (validation_error): a malformed subject id,"
            " a non-boolean `is_active`, or `limit`/`offset` out of range."
        ),
    },
)
async def list_package_products(
    principal: Annotated[Principal, require(BILLING_MANAGE)],
    page: OffsetPagination,
    uow: UnitOfWorkDep,
    is_active: bool | None = None,
    subject_id: uuid.UUID | None = None,
) -> OffsetPage[PackageProductItem]:
    """The package products by name, with the price in effect today."""
    return await packages.list_package_products(
        uow.session, is_active=is_active, subject_id=subject_id, page=page
    )


@router.post(
    "",
    status_code=201,
    responses={
        **PERMISSION_RESPONSES,
        422: problem_response_doc(
            "The body is not valid (validation_error): a missing or unknown"
            " field; `name` empty or over 120 characters; `unit` other than"
            " sessions or minutes; `quantity` outside 1-500 sessions or"
            " 15-30000 minutes (quantity_out_of_range) or minutes not in steps"
            " of 15 (quantity_step); `validity_days` outside 1-730"
            " (validity_days_out_of_range); `effective_from` outside 2000-2099"
            " (date_out_of_range); the amount (money_invalid, money_precision,"
            " money_out_of_range, amount_negative; `null` is refused). Then an"
            " unknown or inactive subject (subject_unknown, subject_inactive)."
        ),
    },
)
async def create_package_product(
    principal: Annotated[Principal, require(BILLING_MANAGE)],
    body: PackageProductCreate,
    uow: UnitOfWorkDep,
) -> PackageProductDetail:
    """Create a package of sessions or minutes with its validity, an
    optional subject (none: any subject) and its first price version, which
    may start on any date. A package covers individual sessions only. The
    unit, quantity, validity and subject can't change later: create another
    product instead."""
    return await packages.create_package_product(uow.session, body)


@router.get(
    "/{product_id}",
    responses={**PERMISSION_RESPONSES, 404: _NOT_FOUND},
)
async def get_package_product(
    principal: Annotated[Principal, require(BILLING_MANAGE)],
    product_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> PackageProductDetail:
    """A package product with all its price versions, newest first."""
    return await packages.get_package_product(uow.session, product_id)


@router.patch(
    "/{product_id}",
    responses={
        **PERMISSION_RESPONSES,
        404: _NOT_FOUND,
        409: problem_response_doc(
            "The product changed since it was loaded (version_conflict)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): `version` is required;"
            " only `name` and `is_active` may change (the unit, quantity,"
            " validity and subject are fixed: extra_forbidden)."
        ),
    },
)
async def update_package_product(
    principal: Annotated[Principal, require(BILLING_MANAGE)],
    product_id: uuid.UUID,
    body: PackageProductUpdate,
    uow: UnitOfWorkDep,
) -> PackageProductDetail:
    """Rename a package product, or deactivate (reactivate) it. A
    deactivated product can't be sold; packages already sold keep working."""
    return await packages.update_package_product(uow.session, product_id, body)


@router.post(
    "/{product_id}/versions",
    status_code=201,
    responses={
        **PERMISSION_RESPONSES,
        404: _NOT_FOUND,
        422: problem_response_doc(
            "The body is not valid (validation_error): `effective_from` and"
            " `amount` are required (`amount: null` means not offered from"
            " that date); date_out_of_range; money_invalid, money_precision,"
            " money_out_of_range, amount_negative. A new version must start on"
            " or after the center's local today and after the latest version"
            " (price_effective_from_invalid)."
        ),
    },
)
async def add_package_product_version(
    principal: Annotated[Principal, require(BILLING_MANAGE)],
    product_id: uuid.UUID,
    body: PriceVersionCreate,
    uow: UnitOfWorkDep,
) -> PackageProductDetail:
    """Add a price version. Versions are never edited: to change an upcoming
    one, delete it and add another."""
    return await packages.add_package_product_version(uow.session, product_id, body)


@router.delete(
    "/{product_id}/versions/{price_version_id}",
    status_code=204,
    response_class=Response,
    responses={
        **PERMISSION_RESPONSES,
        404: problem_response_doc(
            "No such product, or a version of another price (not_found)."
        ),
        409: problem_response_doc(
            "Only an upcoming version can be deleted (price_version_in_effect);"
            " a version used by a charge or a package can't be"
            " (price_version_in_use)."
        ),
    },
)
async def delete_package_product_version(
    principal: Annotated[Principal, require(BILLING_MANAGE)],
    product_id: uuid.UUID,
    price_version_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> None:
    """Delete an upcoming price version."""
    await packages.delete_package_product_version(
        uow.session, product_id, price_version_id
    )
