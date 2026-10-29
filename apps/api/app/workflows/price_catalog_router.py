"""Service price catalog routes (#89 B; ``app.workflows.price_catalog``;
ADR-0006 "Price catalog").

Every route needs ``billing.manage`` (Owners, Administrators and Finance
Staff; not delegable; E6), reads included. No reauthentication and no
``Idempotency-Key``: catalog rows are configuration, not ledger rows (#86
E6). The check order of the writes is ``401`` → ``403 csrf_failed`` →
``403 privacy_notice_required`` → ``403 permission_denied`` → ``422`` (the
body) → ``404`` → the rules of ``app.modules.billing.prices``.

- ``GET /api/v1/billing/service-prices``: offset pagination; filters
  ``class_type``, ``pricing_model`` (``per_session``/``full_course``/
  ``per_period``, #93) and ``subject_id``.
- ``POST /api/v1/billing/service-prices``: an item and its first version.
- ``GET /api/v1/billing/service-prices/{service_price_id}``.
- ``POST /api/v1/billing/service-prices/{service_price_id}/versions``.
- ``DELETE /api/v1/billing/service-prices/{service_price_id}/versions/``
  ``{price_version_id}``.

There is no ``PATCH`` and no item ``DELETE`` (B7): the key never changes and
a price is retired with a ``null`` version.
"""

import uuid
from typing import Annotated, Final

from fastapi import APIRouter, Response

from app.core.authz import PERMISSION_RESPONSES, Principal, require
from app.core.errors import problem_response_doc
from app.core.pagination import OffsetPage, OffsetPagination
from app.core.uow import UnitOfWorkDep
from app.modules.billing.schemas import (
    CatalogPricingModel,
    ClassType,
    PriceVersionCreate,
    ServicePriceCreate,
)
from app.workflows import price_catalog
from app.workflows.price_catalog_schemas import ServicePriceDetail, ServicePriceItem

BILLING_MANAGE: Final = "billing.manage"

router = APIRouter(prefix="/api/v1/billing/service-prices", tags=["billing"])

_NOT_FOUND = problem_response_doc(
    "No such price (not_found); `package` prices are not served here."
)


@router.get(
    "",
    responses={
        **PERMISSION_RESPONSES,
        422: problem_response_doc(
            "The query is not valid (validation_error): an unknown class type,"
            " a pricing model other than per_session, full_course or"
            " per_period, a malformed subject id, or `limit`/`offset` out of"
            " range."
        ),
    },
)
async def list_service_prices(
    principal: Annotated[Principal, require(BILLING_MANAGE)],
    page: OffsetPagination,
    uow: UnitOfWorkDep,
    class_type: ClassType | None = None,
    pricing_model: CatalogPricingModel | None = None,
    subject_id: uuid.UUID | None = None,
) -> OffsetPage[ServicePriceItem]:
    """The catalog prices with their current and next versions, by class
    type (individual, group, course), the subject's level and name, the
    pricing model (per_period last, then weekly, every two weeks, monthly),
    unscoped before scoped (then the cohort or group name), any level first
    (then the level order), any duration first (then ascending) and id.
    `package` prices are not listed."""
    return await price_catalog.list_service_prices(
        uow.session,
        class_type=class_type,
        pricing_model=pricing_model,
        subject_id=subject_id,
        page=page,
    )


@router.post(
    "",
    status_code=201,
    responses={
        **PERMISSION_RESPONSES,
        409: problem_response_doc(
            "A price with the same class type, pricing model, subject, level,"
            " duration and scope exists (service_price_exists);"
            " `details.service_price_id` is its id."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): a missing or unknown"
            " field; a pricing model other than per_session, full_course or"
            " per_period, or one the class type doesn't allow"
            " (pricing_model_not_allowed); `period_frequency` (weekly,"
            " biweekly, monthly) missing on a per_period price"
            " (period_frequency_required) or set on another"
            " (period_frequency_not_allowed); a"
            " cohort for a non-course or a group for a non-group price"
            " (scope_not_allowed), or both (scope_conflict); a level on a"
            " scoped price (level_not_allowed); a duration on a full_course,"
            " per_period or scoped price (duration_not_allowed) or outside 15-480"
            " (duration_out_of_range); `effective_from` outside 2000-2099"
            " (date_out_of_range); the amount (money_invalid, money_precision,"
            " money_out_of_range, amount_negative; `null` is refused). Then the"
            " references, all in one response: subject_unknown,"
            " subject_inactive, education_level_unknown,"
            " education_level_inactive, cohort_unknown, group_unknown and"
            " scope_subject_mismatch."
        ),
    },
)
async def create_service_price(
    principal: Annotated[Principal, require(BILLING_MANAGE)],
    body: ServicePriceCreate,
    uow: UnitOfWorkDep,
) -> ServicePriceDetail:
    """Create a catalog price and its first version, which may start on any
    date (a price entered late can bill the sessions that waited). A
    per_period price (a tuition plan's amount) has a period length, part of
    its key: a weekly and a monthly price of the same subject are two
    prices."""
    return await price_catalog.create_service_price(uow.session, body)


@router.get(
    "/{service_price_id}",
    responses={**PERMISSION_RESPONSES, 404: _NOT_FOUND},
)
async def get_service_price(
    principal: Annotated[Principal, require(BILLING_MANAGE)],
    service_price_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> ServicePriceDetail:
    """A catalog price with all its versions, newest first."""
    return await price_catalog.get_service_price(uow.session, service_price_id)


@router.post(
    "/{service_price_id}/versions",
    status_code=201,
    responses={
        **PERMISSION_RESPONSES,
        404: _NOT_FOUND,
        422: problem_response_doc(
            "The body is not valid (validation_error): `effective_from` and"
            " `amount` are required (`amount: null` means not offered from"
            " that date); date_out_of_range; money_invalid, money_precision,"
            " money_out_of_range, amount_negative. When the price has"
            " versions, a new one must start on or after the center's local"
            " today and after the latest version"
            " (price_effective_from_invalid)."
        ),
    },
)
async def add_price_version(
    principal: Annotated[Principal, require(BILLING_MANAGE)],
    service_price_id: uuid.UUID,
    body: PriceVersionCreate,
    uow: UnitOfWorkDep,
) -> ServicePriceDetail:
    """Add a version. Versions are never edited: to change an upcoming one,
    delete it and add another."""
    return await price_catalog.add_price_version(uow.session, service_price_id, body)


@router.delete(
    "/{service_price_id}/versions/{price_version_id}",
    status_code=204,
    response_class=Response,
    responses={
        **PERMISSION_RESPONSES,
        404: problem_response_doc(
            "No such price, or a version of another price (not_found)."
        ),
        409: problem_response_doc(
            "Only an upcoming version can be deleted (price_version_in_effect);"
            " a version used by a charge or a package can't be"
            " (price_version_in_use)."
        ),
    },
)
async def delete_price_version(
    principal: Annotated[Principal, require(BILLING_MANAGE)],
    service_price_id: uuid.UUID,
    price_version_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> None:
    """Delete an upcoming version."""
    await price_catalog.delete_price_version(
        uow.session, service_price_id, price_version_id
    )
