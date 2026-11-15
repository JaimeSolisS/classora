"""Custom price routes (#95 E; ``app.workflows.custom_prices``; ADR-0006
E8, E9).

Every route needs ``billing.manage`` (Owners, Administrators and Finance
Staff; not delegable; H10), reads included. No reauthentication and no
``Idempotency-Key``: custom prices are configuration (#86 E6). The check
order of the writes is ``401`` → ``403 csrf_failed`` → ``403
privacy_notice_required`` → ``403 permission_denied`` → ``422`` (the body)
→ ``404`` → the row lock → ``409 version_conflict`` → the rules.

- ``GET /api/v1/billing/custom-prices``: filters ``student_id``,
  ``family_id``, ``service_price_id`` and ``status`` (repeatable).
- ``POST /api/v1/billing/custom-prices``.
- ``GET /api/v1/billing/custom-prices/{custom_price_id}``.
- ``PATCH /api/v1/billing/custom-prices/{custom_price_id}``: ``expires_on``
  only (E7).
- ``DELETE /api/v1/billing/custom-prices/{custom_price_id}``: an upcoming,
  unused one only.
"""

import uuid
from typing import Annotated, Final

from fastapi import APIRouter, Query, Response

from app.core.authz import PERMISSION_RESPONSES, Principal, require
from app.core.errors import problem_response_doc
from app.core.pagination import OffsetPage, OffsetPagination
from app.core.uow import UnitOfWorkDep
from app.modules.billing.schemas import CustomPriceCreate, CustomPriceUpdate
from app.workflows import custom_prices
from app.workflows.billing_discount_schemas import CustomPriceItem, CustomPriceStatus

BILLING_MANAGE: Final = "billing.manage"

router = APIRouter(prefix="/api/v1/billing/custom-prices", tags=["billing"])

_NOT_FOUND = problem_response_doc("No such custom price (not_found).")
_OVERLAP = (
    "Another custom price of the same student or family for the same price"
    " overlaps these dates (custom_price_overlap)."
)


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
async def list_custom_prices(
    principal: Annotated[Principal, require(BILLING_MANAGE)],
    page: OffsetPagination,
    uow: UnitOfWorkDep,
    student_id: uuid.UUID | None = None,
    family_id: uuid.UUID | None = None,
    service_price_id: uuid.UUID | None = None,
    status: Annotated[list[CustomPriceStatus] | None, Query()] = None,
) -> OffsetPage[CustomPriceItem]:
    """The custom prices by student or family name, then in the catalog's
    order, newest start first. `status` may be repeated."""
    return await custom_prices.list_custom_prices(
        uow.session,
        student_id=student_id,
        family_id=family_id,
        service_price_id=service_price_id,
        statuses=status or (),
        page=page,
    )


@router.post(
    "",
    status_code=201,
    responses={
        **PERMISSION_RESPONSES,
        409: problem_response_doc(_OVERLAP),
        422: problem_response_doc(
            "The body is not valid (validation_error): a missing or unknown"
            " field; exactly one of `student_id` and `family_id`"
            " (target_required, target_conflict); dates in 2000-2099"
            " (date_out_of_range) with `expires_on` >= `effective_from`"
            " (dates_order); the amount (money_invalid, money_precision,"
            " money_out_of_range, amount_negative). `effective_from` before the"
            " center's local today (custom_price_effective_from_invalid). Then"
            " the references, all in one response: service_price_unknown,"
            " pricing_model_not_allowed (only per_session and full_course"
            " prices), student_unknown, family_unknown."
        ),
    },
)
async def create_custom_price(
    principal: Annotated[Principal, require(BILLING_MANAGE)],
    body: CustomPriceCreate,
    uow: UnitOfWorkDep,
) -> CustomPriceItem:
    """Give a student or a family its own price for a catalog price, from
    today or a later date. A student's price beats the family's, and both
    beat the catalog; discounts still apply on top."""
    return await custom_prices.create_custom_price(uow.session, body)


@router.get("/{custom_price_id}", responses={**PERMISSION_RESPONSES, 404: _NOT_FOUND})
async def get_custom_price(
    principal: Annotated[Principal, require(BILLING_MANAGE)],
    custom_price_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> CustomPriceItem:
    """A custom price."""
    return await custom_prices.get_custom_price(uow.session, custom_price_id)


@router.patch(
    "/{custom_price_id}",
    responses={
        **PERMISSION_RESPONSES,
        404: _NOT_FOUND,
        409: problem_response_doc(
            "The custom price changed since it was loaded (version_conflict); "
            + _OVERLAP
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): `version` and"
            " `expires_on` are required; any other field is field_immutable;"
            " date_out_of_range. The end date must be today or later, on or"
            " after the start and on or after the last service date charged at"
            " this price (custom_price_expiry_invalid)."
        ),
    },
)
async def update_custom_price(
    principal: Annotated[Principal, require(BILLING_MANAGE)],
    custom_price_id: uuid.UUID,
    body: CustomPriceUpdate,
    uow: UnitOfWorkDep,
) -> CustomPriceItem:
    """End a custom price (`expires_on`, inclusive) or re-open it (`null`).
    To change the amount, end it and create a new one from a later date."""
    return await custom_prices.update_custom_price(uow.session, custom_price_id, body)


@router.delete(
    "/{custom_price_id}",
    status_code=204,
    response_class=Response,
    responses={
        **PERMISSION_RESPONSES,
        404: _NOT_FOUND,
        409: problem_response_doc(
            "Only an upcoming custom price can be deleted"
            " (custom_price_in_effect); one used by a charge can't be"
            " (custom_price_in_use)."
        ),
    },
)
async def delete_custom_price(
    principal: Annotated[Principal, require(BILLING_MANAGE)],
    custom_price_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> None:
    """Delete an upcoming custom price that no charge uses."""
    await custom_prices.delete_custom_price(uow.session, custom_price_id)
