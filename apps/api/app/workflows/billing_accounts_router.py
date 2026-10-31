"""Billing account read routes (#364 B; #90b F1 (a);
``app.workflows.billing_accounts``).

All three are family-scoped, so a client goes from a family to its account
without knowing the account id, and a family without an account yet still
answers (``account_id: null``, zeros, empty pages). They use
``authenticated()`` plus the record policy of
:mod:`app.workflows.billing_accounts`: ``billing.view`` holders see every
family, a ``guardian`` only the family of their active guardian profile,
anyone else ``404 not_found`` (the same body as an unknown family). The
check order is ``401`` → ``403 privacy_notice_required`` → ``422`` (the
query) → ``404`` → ``200`` (``400 invalid_cursor`` for a bad ledger
cursor).

- ``GET /api/v1/families/{family_id}/billing-account``: the balance.
- ``GET /api/v1/families/{family_id}/billing-account/charges``: offset
  pagination; filters ``student_id`` and ``status`` (repeatable).
- ``GET /api/v1/families/{family_id}/billing-account/ledger``: cursor
  pagination; filter ``student_id``.
- ``GET /api/v1/families/{family_id}/billing-account/packages`` and
  ``…/packages/{package_id}`` (#102 B, T3 (a)): the packages on the
  family's account with #94's shapes; offset pagination, filters
  ``student_id`` and ``status`` (repeatable). A package of another family
  is the same ``404`` as an unknown family. #94's staff routes under
  ``/api/v1/billing/packages`` stay ``billing.view`` only.

Reads only: no ``Idempotency-Key``, no reauthentication.

**Due dates** (#97 C, D; N5, N6): staff routes, ``require`` with no record
policy beyond the family's existence. Neither moves money, so neither
takes an ``Idempotency-Key`` (a repeated due-date change is ``409
due_date_unchanged``; a repeated offset is a no-op) nor reauthentication.

- ``PATCH /api/v1/families/{family_id}/billing-account``
  (``billing.manage``): the family's due-date offset, ``{version | null,
  due_offset_days}``; it applies to charges posted afterwards. Check order
  ``401`` → ``403 csrf_failed`` → ``403 permission_denied`` → ``422`` →
  ``404`` → ``409 version_conflict``.
- ``POST …/charges/{charge_id}/due-date-changes`` (``billing.manage``):
  ``{due_date, reason}`` → ``201``; ``404`` (no family, or the charge isn't
  on its account) → ``409 charge_settled`` / ``409 due_date_unchanged``.
- ``GET …/charges/{charge_id}/due-date-changes`` (``billing.view``; staff
  only: the reasons are staff text), oldest first.
"""

import uuid
from typing import Annotated, Final

from fastapi import APIRouter, Query

from app.core.authz import (
    AUTH_RESPONSES,
    PERMISSION_RESPONSES,
    Principal,
    authenticated,
    require,
)
from app.core.errors import problem_response_doc
from app.core.pagination import (
    CursorPage,
    CursorPagination,
    OffsetPage,
    OffsetPagination,
)
from app.core.uow import UnitOfWorkDep
from app.modules.billing.schemas import (
    BillingAccountUpdate,
    ChargeStatus,
    DueDateChangeCreate,
)
from app.modules.billing.service import PackageStatus
from app.workflows import billing_accounts, packages
from app.workflows.billing_account_schemas import (
    BillingAccountSettings,
    BillingAccountSummary,
    ChargeItem,
    DueDateChangeItem,
    DueDateChangeRecorded,
    LedgerEntryItem,
)
from app.workflows.package_schemas import PackageDetail, PackageItem

BILLING_VIEW: Final = "billing.view"
BILLING_MANAGE: Final = "billing.manage"

router = APIRouter(prefix="/api/v1/families", tags=["billing"])

_FORBIDDEN = problem_response_doc(
    "The privacy notice must be accepted first (privacy_notice_required)."
)
_NOT_FOUND = problem_response_doc(
    "No such family, or one whose account you can't see (not_found)."
)


@router.get(
    "/{family_id}/billing-account",
    responses={
        **AUTH_RESPONSES,
        403: _FORBIDDEN,
        404: _NOT_FOUND,
        422: problem_response_doc("The family id is not a UUID (validation_error)."),
    },
)
async def get_family_billing_account(
    principal: Annotated[Principal, authenticated()],
    family_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> BillingAccountSummary:
    """The family's billing account: the outstanding amount, the account
    credit, the net balance and the open amounts by status (pending,
    partially paid, overdue), overall and per student, computed for the
    center's today. Staff with billing.view see every family; a guardian
    only their own family. A family with no account yet has `account_id:
    null` and zeros."""
    return await billing_accounts.get_summary(uow.session, principal, family_id)


@router.get(
    "/{family_id}/billing-account/charges",
    responses={
        **AUTH_RESPONSES,
        403: _FORBIDDEN,
        404: _NOT_FOUND,
        422: problem_response_doc(
            "The query is not valid (validation_error): a malformed family or"
            " student id, an unknown status, or `limit`/`offset` out of range."
        ),
    },
)
async def list_family_billing_charges(
    principal: Annotated[Principal, authenticated()],
    family_id: uuid.UUID,
    page: OffsetPagination,
    uow: UnitOfWorkDep,
    student_id: uuid.UUID | None = None,
    status: Annotated[
        list[ChargeStatus] | None,
        Query(description="Only charges in these statuses (repeatable)."),
    ] = None,
) -> OffsetPage[ChargeItem]:
    """The family's charges with their net, allocated and open amounts and
    their status, newest service date first (then posted time, then id).
    `posted_by_user_id` is null for a guardian. A student with no charge on
    the account gives an empty page."""
    return await billing_accounts.list_charges(
        uow.session,
        principal,
        family_id,
        page=page,
        student_id=student_id,
        statuses=status,
    )


@router.get(
    "/{family_id}/billing-account/ledger",
    responses={
        **AUTH_RESPONSES,
        400: problem_response_doc("The cursor is not valid (invalid_cursor)."),
        403: _FORBIDDEN,
        404: _NOT_FOUND,
        422: problem_response_doc(
            "The query is not valid (validation_error): a malformed family or"
            " student id, or `limit` out of range."
        ),
    },
)
async def list_family_billing_ledger(
    principal: Annotated[Principal, authenticated()],
    family_id: uuid.UUID,
    page: CursorPagination,
    uow: UnitOfWorkDep,
    student_id: uuid.UUID | None = None,
) -> CursorPage[LedgerEntryItem]:
    """The family's ledger history, newest first: charges, adjustments,
    payments, reversals, allocations, releases and refunds, each with its
    effect on the net balance. With `student_id`, only that student's rows
    (payment-level rows have no student)."""
    return await billing_accounts.list_ledger(
        uow.session, principal, family_id, params=page, student_id=student_id
    )


@router.get(
    "/{family_id}/billing-account/packages",
    responses={
        **AUTH_RESPONSES,
        403: _FORBIDDEN,
        404: _NOT_FOUND,
        422: problem_response_doc(
            "The query is not valid (validation_error): a malformed family or"
            " student id, an unknown status, or `limit`/`offset` out of range."
        ),
    },
)
async def list_family_billing_packages(
    principal: Annotated[Principal, authenticated()],
    family_id: uuid.UUID,
    page: OffsetPagination,
    uow: UnitOfWorkDep,
    student_id: uuid.UUID | None = None,
    status: Annotated[
        list[PackageStatus] | None,
        Query(description="Only packages in these statuses (repeatable)."),
    ] = None,
) -> OffsetPage[PackageItem]:
    """The prepaid packages bought on the family's account with their
    remaining units and status (active, exhausted, expired, cancelled,
    withdrawn; for the center's today), the earliest expiry first (then
    purchase date and id). A package stays on the account it was bought on,
    even after its student moves to another family. A student with no
    package on the account gives an empty page."""
    return await packages.list_family_packages(
        uow.session,
        principal,
        family_id,
        student_id=student_id,
        statuses=status or [],
        page=page,
    )


@router.get(
    "/{family_id}/billing-account/packages/{package_id}",
    responses={
        **AUTH_RESPONSES,
        403: _FORBIDDEN,
        404: problem_response_doc(
            "No such family, or one whose account you can't see, or the"
            " package is not on its account (not_found)."
        ),
        422: problem_response_doc("An id is not a UUID (validation_error)."),
    },
)
async def get_family_billing_package(
    principal: Annotated[Principal, authenticated()],
    family_id: uuid.UUID,
    package_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> PackageDetail:
    """One package on the family's account with its unit movements, oldest
    first (a consumed unit names its session and the session's start)."""
    return await packages.get_family_package(
        uow.session, principal, family_id, package_id
    )


@router.patch(
    "/{family_id}/billing-account",
    responses={
        **PERMISSION_RESPONSES,
        404: problem_response_doc("No such family (not_found)."),
        409: problem_response_doc(
            "The account changed since it was loaded, or `version` doesn't"
            " match whether the family has an account (version_conflict);"
            " reload and retry."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): a missing `version`, or"
            " `due_offset_days` not from 0 to 60, or an unknown field."
        ),
    },
)
async def update_family_billing_account(
    principal: Annotated[Principal, require(BILLING_MANAGE)],
    family_id: uuid.UUID,
    body: BillingAccountUpdate,
    uow: UnitOfWorkDep,
) -> BillingAccountSettings:
    """Set the family's due-date offset (0–60 days added to the due date of
    every charge posted from now on; posted charges keep their due dates).
    Send the account's `version` from the billing account read, or `null`
    when the family has no account yet (it is created). Sending the current
    value changes nothing. An inactive family may be changed."""
    return await billing_accounts.update_offset(uow.session, family_id, body)


_NO_CHARGE = problem_response_doc(
    "No such family, or the charge is not on its account (not_found)."
)


@router.post(
    "/{family_id}/billing-account/charges/{charge_id}/due-date-changes",
    status_code=201,
    responses={
        **PERMISSION_RESPONSES,
        404: _NO_CHARGE,
        409: problem_response_doc(
            "The charge is paid or void (charge_settled), or already due on"
            " that date (due_date_unchanged; details: due_date)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): `due_date` missing or"
            " outside 2000-2099 (date_out_of_range), `reason` blank or longer"
            " than 500 characters, or an unknown field."
        ),
    },
)
async def change_charge_due_date(
    principal: Annotated[Principal, require(BILLING_MANAGE)],
    family_id: uuid.UUID,
    charge_id: uuid.UUID,
    body: DueDateChangeCreate,
    uow: UnitOfWorkDep,
) -> DueDateChangeRecorded:
    """Move an open charge's due date (earlier or later) with a reason. The
    posted charge never changes: the change is recorded, and the charge's
    status, the overdue amounts, reminders and the oldest-first payment
    order follow the new date. Returns the change with the charge's status
    after it."""
    return await billing_accounts.record_due_date_change(
        uow.session, family_id, charge_id, body
    )


@router.get(
    "/{family_id}/billing-account/charges/{charge_id}/due-date-changes",
    responses={
        **PERMISSION_RESPONSES,
        404: _NO_CHARGE,
        422: problem_response_doc("An id is not a UUID (validation_error)."),
    },
)
async def list_charge_due_date_changes(
    principal: Annotated[Principal, require(BILLING_VIEW)],
    family_id: uuid.UUID,
    charge_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> list[DueDateChangeItem]:
    """Every due-date change of the charge with its reason and who made it,
    oldest first. Staff only."""
    return await billing_accounts.list_due_date_changes(
        uow.session, family_id, charge_id
    )
