"""Expense adjustments (#115 F; ADR-0017 E12, E16; data-model §12.6, §13 X1,
§15).

The only correction of an expense's amount: a signed amount, never ``0.00``,
with a reason. An adjustment of exactly ``-net`` on an expense with nothing
paid voids it, for good (#115 E4): afterwards every payment and adjustment is
``409 expense_void``, and the expense is re-entered as a new one.

**Posting** (:func:`post_adjustment`), after the route's checks (permission,
``finance.correction`` reauthentication, ``422 text_invalid``, key, body): the
expense ``FOR UPDATE`` (``404``) → ``409 expense_void`` → ``409
expense_adjustment_exceeds_open`` (``amount < -open``: the net would go below
what was paid, X1; reverse a payment first) → ``422 money_out_of_range`` (the
new net beyond the money range) → the row.

Runs in the caller's Unit of Work and never commits; the ORM insert's audit row
masks ``amount`` and ``reason`` (#115 E7).
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import MoneyRangeError
from app.modules.administration import service as administration
from app.modules.finance.errors import (
    ExpenseAdjustmentExceedsOpenError,
    ExpenseAmountOutOfRangeError,
    ExpenseVoidError,
)
from app.modules.finance.expenses import expense_status, lock_expense, settled
from app.modules.finance.models import ExpenseAdjustment
from app.modules.finance.schemas import (
    ExpenseAdjustmentCreate,
    ExpenseAdjustmentPosted,
)


async def insert_adjustment(
    session: AsyncSession,
    expense_id: uuid.UUID,
    body: ExpenseAdjustmentCreate,
    created_by_user_id: uuid.UUID,
) -> uuid.UUID:
    """The adjustment row; the caller holds the expense ``FOR UPDATE`` and
    checked every rule."""
    adjustment = ExpenseAdjustment(
        id=uuid.uuid7(),
        expense_id=expense_id,
        amount=body.amount,
        reason=body.reason,
        created_by_user_id=created_by_user_id,
    )
    session.add(adjustment)
    await session.flush()
    return adjustment.id


async def post_adjustment(
    session: AsyncSession,
    user_id: uuid.UUID,
    expense_id: uuid.UUID,
    body: ExpenseAdjustmentCreate,
) -> ExpenseAdjustmentPosted:
    """``POST /api/v1/finance/expenses/{id}/adjustments``, in the order of the
    module docstring."""
    expense = await lock_expense(session, expense_id)
    figures = await settled(session, expense)
    if figures.net.is_zero():
        raise ExpenseVoidError()
    if body.amount < -figures.open:
        raise ExpenseAdjustmentExceedsOpenError(figures.open)
    try:
        net = figures.net + body.amount
    except MoneyRangeError:
        raise ExpenseAmountOutOfRangeError() from None
    adjustment_id = await insert_adjustment(session, expense_id, body, user_id)
    today = await administration.center_today(session)
    return ExpenseAdjustmentPosted(
        id=adjustment_id,
        expense_id=expense_id,
        status=expense_status(net, figures.paid, expense.due_on, today),
    )
