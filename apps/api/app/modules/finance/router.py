"""Expense category and vendor routes (#114 B, C, D; ADR-0017 E12, E15).

Every route needs ``expense.configure`` (Owners, Administrators and Finance
Staff; not delegable), the reads too (#114 E6). No reauthentication, no
``Idempotency-Key`` (the name is a natural key) and no rate limit. There is no
``DELETE`` (#114 E5): ``is_active`` retires a row.

Check order: ``401`` → ``403 csrf_failed`` (writes) → ``403
permission_denied`` → ``422`` (the body or the query) → ``404`` → ``409``.

- ``GET``, ``POST /api/v1/finance/expense-categories`` and ``PATCH …/{id}``.
- ``GET``, ``POST /api/v1/finance/vendors`` and ``PATCH …/{id}``.
"""

import uuid
from typing import Annotated, Final

from fastapi import APIRouter, Query

from app.core.authz import PERMISSION_RESPONSES, Principal, require
from app.core.errors import problem_response_doc
from app.core.pagination import OffsetPage, OffsetPagination
from app.core.uow import UnitOfWorkDep
from app.modules.finance import service as finance
from app.modules.finance.schemas import (
    ExpenseCategoryCreate,
    ExpenseCategoryItem,
    ExpenseCategoryUpdate,
    VendorCreate,
    VendorItem,
    VendorUpdate,
)

EXPENSE_CONFIGURE: Final = "expense.configure"
SEARCH_MAX: Final = 100

router = APIRouter(prefix="/api/v1/finance", tags=["finance"])

_CATEGORIES: Final = "/expense-categories"
_VENDORS: Final = "/vendors"
_TEXT_RULES: Final = "text_empty, text_too_long, text_invalid"


@router.get(_CATEGORIES, responses=PERMISSION_RESPONSES)
async def list_expense_categories(
    principal: Annotated[Principal, require(EXPENSE_CONFIGURE)],
    uow: UnitOfWorkDep,
) -> list[ExpenseCategoryItem]:
    """Every expense category, active first, then by name ignoring case. Not
    paginated. While `system_key` is set, show the label of the key in the
    viewer's language instead of `name`."""
    return await finance.list_categories(uow.session)


@router.post(
    _CATEGORIES,
    status_code=201,
    responses={
        **PERMISSION_RESPONSES,
        409: problem_response_doc(
            "Another category has this name, or a seeded category still shows"
            " it as its Spanish or English label, ignoring case"
            " (expense_category_exists)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): a missing or unknown"
            f" field, system_key included, or the name ({_TEXT_RULES})."
        ),
    },
)
async def create_expense_category(
    principal: Annotated[Principal, require(EXPENSE_CONFIGURE)],
    body: ExpenseCategoryCreate,
    uow: UnitOfWorkDep,
) -> ExpenseCategoryItem:
    """Add an expense category."""
    return await finance.add_category(uow.session, body)


@router.patch(
    f"{_CATEGORIES}/{{category_id}}",
    responses={
        **PERMISSION_RESPONSES,
        404: problem_response_doc("No such category (not_found)."),
        409: problem_response_doc(
            "The version is stale (version_conflict), or the name is taken"
            " (expense_category_exists; the category's own name and labels"
            " don't count)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): an unknown field, neither"
            f" name nor is_active (no_changes), or the name ({_TEXT_RULES})."
        ),
    },
)
async def update_expense_category(
    principal: Annotated[Principal, require(EXPENSE_CONFIGURE)],
    category_id: uuid.UUID,
    body: ExpenseCategoryUpdate,
    uow: UnitOfWorkDep,
) -> ExpenseCategoryItem:
    """Rename, deactivate or reactivate a category. Its id never changes, so
    past expenses keep their grouping and show the new name. A new name
    clears `system_key` for good. An inactive category stays listed but can't
    be used for a new expense."""
    return await finance.change_category(uow.session, category_id, body)


@router.get(
    _VENDORS,
    responses={
        **PERMISSION_RESPONSES,
        422: problem_response_doc(
            "A limit, offset, active or q out of range (validation_error)."
        ),
    },
)
async def list_vendors(
    principal: Annotated[Principal, require(EXPENSE_CONFIGURE)],
    page: OffsetPagination,
    uow: UnitOfWorkDep,
    q: Annotated[
        str | None,
        Query(
            max_length=SEARCH_MAX,
            description="Part of the name, ignoring case; trimmed. `%`, `_` and"
            " `\\` match themselves.",
        ),
    ] = None,
    active: Annotated[
        bool | None,
        Query(description="Only active (`true`) or inactive (`false`) vendors."),
    ] = None,
) -> OffsetPage[VendorItem]:
    """A page of vendors, active first, then by name ignoring case."""
    return await finance.list_vendors(
        uow.session, q=(q or "").strip() or None, active=active, page=page
    )


@router.post(
    _VENDORS,
    status_code=201,
    responses={
        **PERMISSION_RESPONSES,
        409: problem_response_doc(
            "Another vendor has this name, ignoring case (vendor_exists)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): a missing or unknown"
            f" field, or the name or contact ({_TEXT_RULES})."
        ),
    },
)
async def create_vendor(
    principal: Annotated[Principal, require(EXPENSE_CONFIGURE)],
    body: VendorCreate,
    uow: UnitOfWorkDep,
) -> VendorItem:
    """Add a vendor."""
    return await finance.add_vendor(uow.session, body)


@router.patch(
    f"{_VENDORS}/{{vendor_id}}",
    responses={
        **PERMISSION_RESPONSES,
        404: problem_response_doc("No such vendor (not_found)."),
        409: problem_response_doc(
            "The version is stale (version_conflict), or another vendor has"
            " this name (vendor_exists)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): an unknown field, none of"
            " name, contact and is_active (no_changes), or the name or contact"
            f" ({_TEXT_RULES})."
        ),
    },
)
async def update_vendor(
    principal: Annotated[Principal, require(EXPENSE_CONFIGURE)],
    vendor_id: uuid.UUID,
    body: VendorUpdate,
    uow: UnitOfWorkDep,
) -> VendorItem:
    """Rename a vendor, change or clear (`null`) its contact, or deactivate or
    reactivate it. An inactive vendor stays listed but can't be used for a
    new expense."""
    return await finance.change_vendor(uow.session, vendor_id, body)
