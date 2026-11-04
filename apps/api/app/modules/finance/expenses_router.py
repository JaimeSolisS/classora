"""Operating expense routes (#115; ADR-0017 E12, E16; data-model §12.3-§12.6).

Owners and Finance Staff (``expense.view``, ``expense.manage``; not delegable)
read and record expenses, payments and adjustments. Administrators, academic
coordinators, tutors, students and guardians get ``403``: ADR-0017 E12 keeps
expense amounts from Administrators.

- ``GET /api/v1/finance/expenses`` and ``GET …/expenses/{expense_id}``:
  ``require("expense.view")``.
- ``POST /api/v1/finance/expenses``: ``require("expense.manage")``,
  ``Idempotency-Key``, no reauthentication.
- ``POST …/expenses/{expense_id}/payments``: the same.
- ``POST …/expenses/{expense_id}/adjustments``: ``require("expense.manage")``,
  ``require_recent_auth("finance.correction")``, ``Idempotency-Key``.
- ``POST …/expense-payments/{payment_id}/reversal``: ``require("expense.view")``
  **and** ``finance.refund.approve`` (#115 E6, checked together before the
  reauthentication; the access rule is the non-delegable ``expense.view``, so a
  delegation of ``finance.refund.approve`` alone never reaches it),
  ``require_recent_auth("finance.refund_or_reversal")``, ``Idempotency-Key``.
  Today that is the Owner only: an Administrator delegated
  ``finance.refund.approve`` holds no ``expense.view``.

Check order of the ``POST`` routes: ``401`` → ``403 csrf_failed`` → ``403
permission_denied`` → ``403 reauth_required`` (where declared) → ``422
text_invalid`` for a lone surrogate (the key's hash needs UTF-8) → ``400`` key
→ replay / ``422 idempotency_key_reused`` → ``422`` (the body) → the rules of
:mod:`~app.modules.finance.expenses`,
:mod:`~app.modules.finance.expense_payments` and
:mod:`~app.modules.finance.expense_adjustments`. They never commit; they end
with ``idem.respond``. No response holds an amount, a description, a
reference, a note or a reason (#115 E7): the stored idempotent response is in
clear. There is no ``PATCH`` or ``DELETE`` (#115 E3).
"""

import uuid
from datetime import date
from typing import Annotated, Final

from fastapi import APIRouter, Depends, Query, Response

from app.core.authz import (
    PERMISSION_RESPONSES,
    CurrentPrincipal,
    Principal,
    as_principal,
    ensure_permission,
    ensure_privacy_notice_accepted,
    require,
)
from app.core.errors import problem_response_doc
from app.core.idempotency import (
    IDEMPOTENCY_RESPONSES,
    IdempotentRequest,
    hashable_body,
    idempotent,
)
from app.core.pagination import OffsetPage, OffsetPagination
from app.core.reauth import REAUTH_RESPONSES, require_recent_auth
from app.core.uow import UnitOfWorkDep
from app.modules.finance import service as finance
from app.modules.finance.errors import IncurredRangeInvalidError
from app.modules.finance.schemas import (
    ExpenseAdjustmentCreate,
    ExpenseAdjustmentPosted,
    ExpenseCreate,
    ExpenseCreated,
    ExpenseDetail,
    ExpenseItem,
    ExpensePaymentCreate,
    ExpensePaymentRecorded,
    ExpensePaymentReversalCreate,
    ExpensePaymentReversed,
    ExpenseSort,
    ExpenseStatus,
)

EXPENSE_VIEW: Final = "expense.view"
EXPENSE_MANAGE: Final = "expense.manage"
REFUND_APPROVE: Final = "finance.refund.approve"
CORRECTION: Final = "finance.correction"
REFUND_OR_REVERSAL: Final = "finance.refund_or_reversal"
SEARCH_MAX: Final = 100

router = APIRouter(prefix="/api/v1/finance", tags=["finance"])

_EXPENSES: Final = "/expenses"
_EXPENSE: Final = "/expenses/{expense_id}"
_REUSED: Final = (
    "the Idempotency-Key was already used for a different request"
    " (idempotency_key_reused)"
)
_TEXT_RULES: Final = "text_empty, text_too_long, text_invalid"
_NOT_FOUND: Final = problem_response_doc("No such expense (not_found).")
_VOID: Final = (
    "the expense is void and takes no more payments or adjustments (expense_void)"
)


async def _expense_viewer(principal: CurrentPrincipal) -> None:
    """#115 E6: a reverser needs ``finance.refund.approve`` and
    ``expense.view`` (the route's access rule). Both are checked here, so it is
    ``403 permission_denied`` before ``403 reauth_required``."""
    checked = as_principal(principal)
    ensure_privacy_notice_accepted(checked)
    ensure_permission(checked, REFUND_APPROVE)
    ensure_permission(checked, EXPENSE_VIEW)


@router.get(
    _EXPENSES,
    responses={
        **PERMISSION_RESPONSES,
        422: problem_response_doc(
            "The query is not valid (validation_error): an unknown status or"
            " sort, a malformed id or date, incurred_to before incurred_from"
            " (range_invalid), q over 100 characters, or limit/offset out of"
            " range."
        ),
    },
)
async def list_expenses(
    principal: Annotated[Principal, require(EXPENSE_VIEW)],
    page: OffsetPagination,
    uow: UnitOfWorkDep,
    status: Annotated[
        list[ExpenseStatus] | None,
        Query(description="Only expenses in these statuses (repeatable)."),
    ] = None,
    category_id: uuid.UUID | None = None,
    vendor_id: uuid.UUID | None = None,
    incurred_from: date | None = None,
    incurred_to: date | None = None,
    q: Annotated[
        str | None,
        Query(
            max_length=SEARCH_MAX,
            description="Part of the description or the receipt reference,"
            " ignoring case; trimmed. `%`, `_` and `\\` match themselves.",
        ),
    ] = None,
    sort: ExpenseSort = "-incurred_on",
) -> OffsetPage[ExpenseItem]:
    """A page of expenses with their net (`adjusted_amount`), paid and open
    amounts and their status, computed with the center's today. Newest
    expense date first (`-incurred_on`, the default), or soonest due date
    first (`due_on`). Inactive categories and vendors are listed and can be
    filtered on."""
    if (
        incurred_from is not None
        and incurred_to is not None
        and incurred_to < incurred_from
    ):
        raise IncurredRangeInvalidError()
    return await finance.list_expenses(
        uow.session,
        filters=finance.ExpenseFilters(
            statuses=status,
            category_id=category_id,
            vendor_id=vendor_id,
            incurred_from=incurred_from,
            incurred_to=incurred_to,
            q=(q or "").strip() or None,
        ),
        sort=sort,
        page=page,
    )


@router.get(_EXPENSE, responses={**PERMISSION_RESPONSES, 404: _NOT_FOUND})
async def get_expense(
    expense_id: uuid.UUID,
    principal: Annotated[Principal, require(EXPENSE_VIEW)],
    uow: UnitOfWorkDep,
) -> ExpenseDetail:
    """One expense with its note, who entered it (`null` when generated), its
    payments (reversed ones included, newest payment date first) and its
    adjustments (oldest first)."""
    return await finance.get_expense(uow.session, expense_id)


@router.post(
    _EXPENSES,
    status_code=201,
    response_model=ExpenseCreated,
    responses={
        **PERMISSION_RESPONSES,
        **IDEMPOTENCY_RESPONSES,
        409: problem_response_doc(
            "The category or the vendor is inactive (expense_category_inactive,"
            " vendor_inactive)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error: amount_not_positive,"
            f" money_*, {_TEXT_RULES}, …); the category or the vendor doesn't"
            " exist (expense_category_unknown, vendor_unknown); the expense date"
            " is after today (incurred_on_in_future) or the due date before it"
            f" (due_on_before_incurred_on); or {_REUSED}."
        ),
    },
)
async def create_expense(
    principal: Annotated[Principal, require(EXPENSE_MANAGE)],
    _: Annotated[None, Depends(hashable_body)],
    idem: Annotated[IdempotentRequest, idempotent()],
    body: ExpenseCreate,
    uow: UnitOfWorkDep,
) -> Response:
    """Record a one-time operating expense. The answer holds the id and the
    status only (`unpaid`, or `overdue` for a past due date). Send a new
    `Idempotency-Key` per expense and the same one to retry it. An expense is
    never edited: a wrong amount takes an adjustment, anything else a void
    and a new expense."""
    created = await finance.create_expense(uow.session, principal.user_id, body)
    return await idem.respond(created, status_code=201)


@router.post(
    f"{_EXPENSE}/payments",
    status_code=201,
    response_model=ExpensePaymentRecorded,
    responses={
        **PERMISSION_RESPONSES,
        **IDEMPOTENCY_RESPONSES,
        404: _NOT_FOUND,
        409: problem_response_doc(
            f"{_VOID.capitalize()}; a payment without a reversal has the same"
            " method and reference (expense_payment_duplicate, with"
            " details.payment_id); or the payments would add up to more than"
            " the expense (expense_payment_exceeds_open, with details.open)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error: amount_not_positive,"
            f" money_*, {_TEXT_RULES}, …); the method is unknown or deactivated"
            " (payment_method_unavailable); the date is after today"
            f" (paid_on_in_future); or {_REUSED}."
        ),
    },
)
async def record_expense_payment(
    expense_id: uuid.UUID,
    principal: Annotated[Principal, require(EXPENSE_MANAGE)],
    _: Annotated[None, Depends(hashable_body)],
    idem: Annotated[IdempotentRequest, idempotent()],
    body: ExpensePaymentCreate,
    uow: UnitOfWorkDep,
) -> Response:
    """Record a payment of an expense made outside the platform. Partial
    payments are allowed, up to the open amount. A mistake is reversed, never
    edited. Send a new `Idempotency-Key` per payment and the same one to
    retry it."""
    recorded = await finance.record_payment(
        uow.session, principal.user_id, expense_id, body
    )
    return await idem.respond(recorded, status_code=201)


@router.post(
    "/expense-payments/{payment_id}/reversal",
    status_code=201,
    response_model=ExpensePaymentReversed,
    dependencies=[
        Depends(_expense_viewer),
        require_recent_auth(REFUND_OR_REVERSAL),
    ],
    responses={
        **PERMISSION_RESPONSES,
        **REAUTH_RESPONSES,
        **IDEMPOTENCY_RESPONSES,
        403: problem_response_doc(
            "Not allowed (permission_denied: finance.refund.approve and"
            " expense.view are both needed), or the session wasn't"
            " reauthenticated (reauth_required)."
        ),
        404: problem_response_doc("No such payment (not_found)."),
        409: problem_response_doc(
            "The payment was already reversed (expense_payment_already_reversed)."
        ),
        422: problem_response_doc(
            f"The body is not valid (validation_error: {_TEXT_RULES} on the"
            f" reason); or {_REUSED}."
        ),
    },
)
async def reverse_expense_payment(
    payment_id: uuid.UUID,
    principal: Annotated[Principal, require(EXPENSE_VIEW)],
    _: Annotated[None, Depends(hashable_body)],
    idem: Annotated[IdempotentRequest, idempotent()],
    body: ExpensePaymentReversalCreate,
    uow: UnitOfWorkDep,
) -> Response:
    """Reverse a payment recorded by mistake, with a reason. The payment stays
    listed, marked reversed, and no longer counts toward what was paid, so the
    right one can be recorded again, with the same reference too. Needs a
    password reauthentication in the last 5 minutes."""
    reversed_ = await finance.reverse_payment(
        uow.session, principal.user_id, payment_id, body
    )
    return await idem.respond(reversed_, status_code=201)


@router.post(
    f"{_EXPENSE}/adjustments",
    status_code=201,
    response_model=ExpenseAdjustmentPosted,
    dependencies=[require_recent_auth(CORRECTION)],
    responses={
        **PERMISSION_RESPONSES,
        **REAUTH_RESPONSES,
        **IDEMPOTENCY_RESPONSES,
        403: problem_response_doc(
            "Not allowed (permission_denied), or the session wasn't"
            " reauthenticated (reauth_required)."
        ),
        404: _NOT_FOUND,
        409: problem_response_doc(
            f"{_VOID.capitalize()}; or a negative amount goes beyond the open"
            " amount (expense_adjustment_exceeds_open, with details.open):"
            " reverse a payment first."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error: amount_zero, money_*,"
            f" {_TEXT_RULES} on the reason, …); the new amount would be out of"
            f" range (money_out_of_range on amount); or {_REUSED}."
        ),
    },
)
async def post_expense_adjustment(
    expense_id: uuid.UUID,
    principal: Annotated[Principal, require(EXPENSE_MANAGE)],
    _: Annotated[None, Depends(hashable_body)],
    idem: Annotated[IdempotentRequest, idempotent()],
    body: ExpenseAdjustmentCreate,
    uow: UnitOfWorkDep,
) -> Response:
    """Correct an expense's amount with a signed adjustment and a reason.
    Exactly minus the net voids an expense with nothing paid, for good: re-enter
    it as a new expense. Needs a password reauthentication in the last 5
    minutes."""
    posted = await finance.post_adjustment(
        uow.session, principal.user_id, expense_id, body
    )
    return await idem.respond(posted, status_code=201)
