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

Reads only: no ``Idempotency-Key``, no reauthentication.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.core.authz import AUTH_RESPONSES, Principal, authenticated
from app.core.errors import problem_response_doc
from app.core.pagination import (
    CursorPage,
    CursorPagination,
    OffsetPage,
    OffsetPagination,
)
from app.core.uow import UnitOfWorkDep
from app.modules.billing.schemas import ChargeStatus
from app.workflows import billing_accounts
from app.workflows.billing_account_schemas import (
    BillingAccountSummary,
    ChargeItem,
    LedgerEntryItem,
)

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
