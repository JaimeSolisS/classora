"""Discount routes (#95 D; ``app.workflows.billing_discounts``; ADR-0006
E8, E9).

Every route needs ``billing.manage`` (Owners, Administrators and Finance
Staff; not delegable; H10), reads included. No reauthentication and no
``Idempotency-Key``: discounts are configuration, not ledger rows (#86 E6).
The check order of the writes is ``401`` → ``403 csrf_failed`` → ``403
privacy_notice_required`` → ``403 permission_denied`` → ``422`` (the body)
→ ``404`` → the row lock → ``409 version_conflict`` → the rules.

- ``GET /api/v1/billing/discounts``: offset pagination; filters ``kind`` and
  ``status`` (repeatable) and ``q`` (name).
- ``POST /api/v1/billing/discounts``.
- ``GET /api/v1/billing/discounts/{discount_id}``.
- ``PATCH /api/v1/billing/discounts/{discount_id}``.
- ``POST /api/v1/billing/discounts/{discount_id}/assignments``.
- ``DELETE /api/v1/billing/discounts/{discount_id}/assignments/``
  ``{assignment_id}``.

There is no discount ``DELETE`` (D8, H8): a discount is retired with
``is_active = false``. A change never touches posted charges, which keep
their ``charge_discounts`` snapshot.
"""

import uuid
from typing import Annotated, Final

from fastapi import APIRouter, Query, Response

from app.core.authz import PERMISSION_RESPONSES, Principal, require
from app.core.errors import problem_response_doc
from app.core.pagination import OffsetPage, OffsetPagination
from app.core.uow import UnitOfWorkDep
from app.modules.billing.discounts import DiscountKind
from app.modules.billing.schemas import (
    DiscountAssignmentCreate,
    DiscountCreate,
    DiscountUpdate,
)
from app.workflows import billing_discounts
from app.workflows.billing_discount_schemas import (
    DiscountDetail,
    DiscountItem,
    DiscountStatus,
)

BILLING_MANAGE: Final = "billing.manage"

router = APIRouter(prefix="/api/v1/billing/discounts", tags=["billing"])

_NOT_FOUND = problem_response_doc("No such discount (not_found).")
_RULES = (
    " The rules, all in one response: exactly one of `percent` and `amount`"
    " (value_required, value_conflict); `percent` a decimal string more than 0"
    " and at most 100 with at most four places (percent_invalid,"
    " percent_out_of_range, percent_precision); `amount` more than 0"
    " (amount_not_positive, money_invalid, money_precision,"
    " money_out_of_range); a scholarship is `assigned` and a sibling discount"
    " `all` (audience_not_allowed); a promotion has both dates"
    " (dates_required); `starts_on` <= `expires_on` (dates_order); dates in"
    " 2000-2099 (date_out_of_range); criteria lists not empty"
    " (criteria_empty_list) and at most 50 ids (criteria_too_many);"
    " `min_family_students` 2-20 (min_family_students_out_of_range), required"
    " for a sibling discount (min_family_students_required) and refused for"
    " the others (min_family_students_not_allowed). Then the references of the"
    " criteria, with the list index in the path: subject_unknown,"
    " subject_inactive, education_level_unknown, education_level_inactive."
)
_NAME_EXISTS = "Another discount has this name, ignoring case (discount_name_exists)."


@router.get(
    "",
    responses={
        **PERMISSION_RESPONSES,
        422: problem_response_doc(
            "The query is not valid (validation_error): an unknown kind or"
            " status, or `limit`/`offset` out of range."
        ),
    },
)
async def list_discounts(
    principal: Annotated[Principal, require(BILLING_MANAGE)],
    page: OffsetPagination,
    uow: UnitOfWorkDep,
    kind: Annotated[list[DiscountKind] | None, Query()] = None,
    status: Annotated[list[DiscountStatus] | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=120)] = None,
) -> OffsetPage[DiscountItem]:
    """The discounts, active first, then by kind (scholarship, sibling,
    discount, promotion), name and id. `kind` and `status` may be repeated;
    `q` matches part of the name, ignoring case."""
    return await billing_discounts.list_discounts(
        uow.session, kinds=kind or (), statuses=status or (), q=q, page=page
    )


@router.post(
    "",
    status_code=201,
    responses={
        **PERMISSION_RESPONSES,
        409: problem_response_doc(_NAME_EXISTS),
        422: problem_response_doc(
            "The body is not valid (validation_error): a missing or unknown"
            " field." + _RULES
        ),
    },
)
async def create_discount(
    principal: Annotated[Principal, require(BILLING_MANAGE)],
    body: DiscountCreate,
    uow: UnitOfWorkDep,
) -> DiscountDetail:
    """Create a discount, scholarship, sibling discount or promotion. It
    applies to charges posted from now on whose service date is inside its
    dates and that meet its criteria (and, for `assigned`, its
    assignments)."""
    return await billing_discounts.create_discount(uow.session, body)


@router.get("/{discount_id}", responses={**PERMISSION_RESPONSES, 404: _NOT_FOUND})
async def get_discount(
    principal: Annotated[Principal, require(BILLING_MANAGE)],
    discount_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> DiscountDetail:
    """A discount with its assignments."""
    return await billing_discounts.get_discount(uow.session, discount_id)


@router.patch(
    "/{discount_id}",
    responses={
        **PERMISSION_RESPONSES,
        404: _NOT_FOUND,
        409: problem_response_doc(
            "The discount changed since it was loaded (version_conflict); "
            + _NAME_EXISTS
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): a missing `version`, an"
            " unknown field, `kind` or `audience` (field_immutable), `null` for"
            " `name`, `eligibility` or `is_active` (value_not_null). The"
            " resulting discount must meet the creation rules." + _RULES
        ),
    },
)
async def update_discount(
    principal: Annotated[Principal, require(BILLING_MANAGE)],
    discount_id: uuid.UUID,
    body: DiscountUpdate,
    uow: UnitOfWorkDep,
) -> DiscountDetail:
    """Change a discount (switching between a percentage and an amount sends
    the other as `null`; `eligibility` replaces the whole object). Posted
    charges keep the values they were computed with. Sending the current
    values changes nothing (the version is still checked)."""
    return await billing_discounts.update_discount(uow.session, discount_id, body)


@router.post(
    "/{discount_id}/assignments",
    status_code=201,
    responses={
        **PERMISSION_RESPONSES,
        404: _NOT_FOUND,
        409: problem_response_doc(
            "The student or family is already assigned (discount_assignment_exists)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): exactly one of"
            " `student_id` and `family_id` (target_required, target_conflict);"
            " an unknown student or family (student_unknown, family_unknown)."
            " The discount is for everyone (audience_not_assigned)."
        ),
    },
)
async def add_assignment(
    principal: Annotated[Principal, require(BILLING_MANAGE)],
    discount_id: uuid.UUID,
    body: DiscountAssignmentCreate,
    uow: UnitOfWorkDep,
) -> DiscountDetail:
    """Assign an `assigned` discount to a student or a family (a family
    covers each of its students when a charge is posted)."""
    return await billing_discounts.add_assignment(uow.session, discount_id, body)


@router.delete(
    "/{discount_id}/assignments/{assignment_id}",
    status_code=204,
    response_class=Response,
    responses={
        **PERMISSION_RESPONSES,
        404: problem_response_doc(
            "No such discount, or an assignment of another one (not_found)."
        ),
    },
)
async def delete_assignment(
    principal: Annotated[Principal, require(BILLING_MANAGE)],
    discount_id: uuid.UUID,
    assignment_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> None:
    """Remove an assignment. Posted charges keep their discounts."""
    await billing_discounts.delete_assignment(uow.session, discount_id, assignment_id)
