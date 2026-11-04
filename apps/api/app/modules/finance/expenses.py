"""Operating expenses (#115 B, C; spec §8; ADR-0017 E12, E16; data-model
§12.3, §13, §15).

Every function runs in the caller's Unit of Work and never commits. Inserts go
through the ORM, so the audit row comes with the Unit of Work's actor, with
``amount``, ``description``, ``receipt_reference`` and ``note`` masked (#115
E7).

**One rule set** (:func:`insert_expense`): the route and the recurring job
(#116) both insert through it. It reads the category and the vendor ``FOR
SHARE`` (``usable_category`` / ``usable_vendor``, #114 E7), so a deactivation
either waits for the insert or is seen by it, then applies the date rules (#115
E2): ``incurred_on`` not after the center's today, ``due_on`` not before
``incurred_on``. The order of the refusals is every ``…_unknown`` (``422``),
then every ``…_inactive`` (``409``), the category first, then the dates.

**Derived, never stored** (data-model §13.2): ``net`` = ``amount`` + Σ
adjustments; ``paid`` = Σ payments without a reversal; ``open`` = ``net`` −
``paid``; the status ``void`` (``net`` = 0), ``paid`` (``open`` = 0), ``overdue``
(``due_on`` before the center's today), ``partially_paid`` (something paid),
else ``unpaid``. :func:`expense_figures` computes them in SQL for a whole list,
filters included, in the list's one statement (#115 C2); #117 reuses it for
payables. :func:`expense_status` is the same rule in Python.

**The lock** (data-model §15): :func:`lock_expense`, the row ``FOR UPDATE``, is
the last business lock of every payment, reversal and adjustment, after the
idempotency key. Under it, :func:`settled` reads ``net`` and ``paid``, which no
one else can change until the commit.
"""

import uuid
from collections.abc import Collection
from dataclasses import dataclass
from datetime import date
from typing import Any, Final, cast

from sqlalchemy import (
    ColumnElement,
    Subquery,
    case,
    exists,
    func,
    literal,
    or_,
    select,
    type_coerce,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import RecordNotFoundError
from app.core.money import Money, MoneyType, money_sum
from app.core.pagination import OffsetPage, OffsetParams
from app.modules.administration import service as administration
from app.modules.administration.schemas import AccountName
from app.modules.finance.categories import usable_category
from app.modules.finance.errors import (
    DueOnBeforeIncurredOnError,
    ExpenseCategoryInactiveError,
    IncurredOnInFutureError,
    VendorInactiveError,
)
from app.modules.finance.models import (
    Expense,
    ExpenseAdjustment,
    ExpenseCategory,
    ExpensePayment,
    ExpensePaymentReversal,
    Vendor,
)
from app.modules.finance.schemas import (
    ExpenseAdjustmentItem,
    ExpenseCategoryRef,
    ExpenseCreate,
    ExpenseCreated,
    ExpenseDetail,
    ExpenseItem,
    ExpensePaymentItem,
    ExpensePaymentReversalItem,
    ExpenseSort,
    ExpenseStatus,
    SystemKey,
    VendorRef,
)
from app.modules.finance.vendors import usable_vendor
from app.modules.identity import service as identity

VOID: Final = "void"
PAID: Final = "paid"
OVERDUE: Final = "overdue"
PARTIALLY_PAID: Final = "partially_paid"
UNPAID: Final = "unpaid"


# --- Figures ---------------------------------------------------------------------


def expense_status(net: Money, paid: Money, due_on: date, today: date) -> ExpenseStatus:
    """The status of data-model §13.2; ``today`` is the center's."""
    if net.is_zero():
        return VOID
    if not (net - paid).is_positive():
        return PAID
    if due_on < today:
        return OVERDUE
    if paid.is_positive():
        return PARTIALLY_PAID
    return UNPAID


def unreversed() -> ColumnElement[bool]:
    """A payment without a reversal: the only kind that counts (#115 E2)."""
    return ~exists().where(
        ExpensePaymentReversal.expense_payment_id == ExpensePayment.id
    )


def _adjusted_sum(expense_id: Any) -> Any:
    return (
        select(money_sum(ExpenseAdjustment.amount))
        .where(ExpenseAdjustment.expense_id == expense_id)
        .scalar_subquery()
    )


def _paid_sum(expense_id: Any) -> Any:
    return (
        select(money_sum(ExpensePayment.amount))
        .where(ExpensePayment.expense_id == expense_id, unreversed())
        .scalar_subquery()
    )


def _status_expression(
    net: ColumnElement[Money],
    paid: ColumnElement[Money],
    due_on: ColumnElement[date],
    today: date,
) -> ColumnElement[str]:
    """:func:`expense_status` in SQL, the same rules in the same order."""
    zero = literal(Money.zero(), MoneyType())
    return case(
        (net == zero, VOID),
        (net - paid <= zero, PAID),
        (due_on < today, OVERDUE),
        (paid > zero, PARTIALLY_PAID),
        else_=UNPAID,
    )


def expense_figures(today: date) -> Subquery:
    """Every expense with ``net``, ``paid``, ``open`` and ``status`` (the
    center's ``today``), as one subquery: correlated sums, no lock. The
    columns are the ``expenses`` columns plus those four (``MoneyType`` and
    the status text)."""
    rows = select(
        Expense,
        type_coerce(Expense.amount + _adjusted_sum(Expense.id), MoneyType()).label(
            "net"
        ),
        _paid_sum(Expense.id).label("paid"),
    ).subquery("expense_sums")
    return select(
        rows,
        type_coerce(rows.c.net - rows.c.paid, MoneyType()).label("open"),
        _status_expression(rows.c.net, rows.c.paid, rows.c.due_on, today).label(
            "status"
        ),
    ).subquery("expense_figures")


# --- Insert (the seam for #116) ---------------------------------------------------


async def insert_expense(
    session: AsyncSession,
    *,
    category_id: uuid.UUID,
    vendor_id: uuid.UUID | None,
    description: str,
    amount: Money,
    incurred_on: date,
    due_on: date,
    receipt_reference: str | None,
    note: str | None,
    created_by_user_id: uuid.UUID | None,
    today: date | None = None,
) -> Expense:
    """A new expense, after every rule of the module docstring: ``422
    expense_category_unknown`` / ``vendor_unknown`` → ``409
    expense_category_inactive`` / ``vendor_inactive`` → ``422
    incurred_on_in_future`` → ``422 due_on_before_incurred_on``. The texts are
    already trimmed (the schema or the template). ``created_by_user_id`` is
    ``None`` only for the job. ``today`` is the center's (read when not
    given)."""
    inactive: list[Exception] = []
    try:
        await usable_category(session, category_id)
    except ExpenseCategoryInactiveError as exc:
        inactive.append(exc)
    if vendor_id is not None:
        try:
            await usable_vendor(session, vendor_id)
        except VendorInactiveError as exc:
            inactive.append(exc)
    if inactive:
        raise inactive[0]
    if today is None:
        today = await administration.center_today(session)
    if incurred_on > today:
        raise IncurredOnInFutureError()
    if due_on < incurred_on:
        raise DueOnBeforeIncurredOnError()
    expense = Expense(
        id=uuid.uuid7(),
        category_id=category_id,
        vendor_id=vendor_id,
        description=description,
        amount=amount,
        incurred_on=incurred_on,
        due_on=due_on,
        receipt_reference=receipt_reference,
        note=note,
        created_by_user_id=created_by_user_id,
    )
    session.add(expense)
    await session.flush()
    return expense


async def create_expense(
    session: AsyncSession, user_id: uuid.UUID, body: ExpenseCreate
) -> ExpenseCreated:
    """``POST /api/v1/finance/expenses``: :func:`insert_expense`. A new expense
    is ``unpaid``, or ``overdue`` when ``due_on`` is already past."""
    today = await administration.center_today(session)
    expense = await insert_expense(
        session,
        category_id=body.category_id,
        vendor_id=body.vendor_id,
        description=body.description,
        amount=body.amount,
        incurred_on=body.incurred_on,
        due_on=body.due_on,
        receipt_reference=body.receipt_reference,
        note=body.note,
        created_by_user_id=user_id,
        today=today,
    )
    return ExpenseCreated(
        id=expense.id,
        status=expense_status(body.amount, Money.zero(), body.due_on, today),
    )


# --- The lock ----------------------------------------------------------------------


async def lock_expense(session: AsyncSession, expense_id: uuid.UUID) -> Expense:
    """The expense ``FOR UPDATE`` (``404`` for none), re-read: the last business
    lock of a payment, a reversal or an adjustment (data-model §15)."""
    expense = await session.scalar(
        select(Expense)
        .where(Expense.id == expense_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if expense is None:
        raise RecordNotFoundError()
    return expense


@dataclass(frozen=True)
class Settled:
    """An expense's ``net`` and ``paid``, read under its lock."""

    net: Money
    paid: Money

    @property
    def open(self) -> Money:
        return self.net - self.paid


async def settled(session: AsyncSession, expense: Expense) -> Settled:
    """``net`` and ``paid`` of a locked expense. One statement."""
    row = (
        await session.execute(
            select(
                _adjusted_sum(expense.id).label("adjusted"),
                _paid_sum(expense.id).label("paid"),
            )
        )
    ).one()
    return Settled(net=expense.amount + row.adjusted, paid=row.paid)


# --- Reads -------------------------------------------------------------------------


def _like(q: str) -> str:
    """``q`` as an ``ILIKE`` pattern that matches ``%``, ``_`` and ``\\``
    literally (the ``vendors`` escape)."""
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


@dataclass(frozen=True)
class ExpenseFilters:
    """The list's filters (#115 C1); ``None`` or empty is "any"."""

    statuses: Collection[ExpenseStatus] | None = None
    category_id: uuid.UUID | None = None
    vendor_id: uuid.UUID | None = None
    incurred_from: date | None = None
    incurred_to: date | None = None
    q: str | None = None


def _rows(today: date) -> tuple[Subquery, Any]:
    figures = expense_figures(today)
    statement = (
        select(
            figures,
            ExpenseCategory.name.label("category_name"),
            ExpenseCategory.system_key.label("category_system_key"),
            ExpenseCategory.is_active.label("category_is_active"),
            Vendor.name.label("vendor_name"),
            Vendor.is_active.label("vendor_is_active"),
        )
        .join(ExpenseCategory, ExpenseCategory.id == figures.c.category_id)
        .outerjoin(Vendor, Vendor.id == figures.c.vendor_id)
    )
    return figures, statement


def _conditions(figures: Subquery, filters: ExpenseFilters) -> list[Any]:
    conditions: list[Any] = []
    if filters.statuses:
        conditions.append(figures.c.status.in_(sorted(set(filters.statuses))))
    if filters.category_id is not None:
        conditions.append(figures.c.category_id == filters.category_id)
    if filters.vendor_id is not None:
        conditions.append(figures.c.vendor_id == filters.vendor_id)
    if filters.incurred_from is not None:
        conditions.append(figures.c.incurred_on >= filters.incurred_from)
    if filters.incurred_to is not None:
        conditions.append(figures.c.incurred_on <= filters.incurred_to)
    if filters.q:
        pattern = _like(filters.q)
        conditions.append(
            or_(
                figures.c.description.ilike(pattern, escape="\\"),
                figures.c.receipt_reference.ilike(pattern, escape="\\"),
            )
        )
    return conditions


def _item_fields(row: Any) -> dict[str, Any]:
    vendor = None
    if row.vendor_id is not None:
        vendor = VendorRef(
            id=row.vendor_id, name=row.vendor_name, is_active=row.vendor_is_active
        )
    return {
        "id": row.id,
        "category": ExpenseCategoryRef(
            id=row.category_id,
            name=row.category_name,
            system_key=cast(SystemKey | None, row.category_system_key),
            is_active=row.category_is_active,
        ),
        "vendor": vendor,
        "description": row.description,
        "amount": row.amount,
        "adjusted_amount": row.net,
        "paid_amount": row.paid,
        "open_amount": row.open,
        "status": row.status,
        "incurred_on": row.incurred_on,
        "due_on": row.due_on,
        "receipt_reference": row.receipt_reference,
        "created_at": row.created_at,
    }


async def list_expenses(
    session: AsyncSession,
    *,
    filters: ExpenseFilters,
    sort: ExpenseSort,
    page: OffsetParams,
) -> OffsetPage[ExpenseItem]:
    """A page of expenses with their figures and status (#115 C1, C2):
    ``-incurred_on`` (then ``created_at`` newest first, then ``id``) or
    ``due_on`` (then ``incurred_on``, then ``id``). Inactive categories and
    vendors are listed and filterable. Two statements (the center's today and
    the page with its window total), a third only for the total of a page past
    the end. No lock."""
    today = await administration.center_today(session)
    figures, statement = _rows(today)
    conditions = _conditions(figures, filters)
    order: tuple[ColumnElement[Any], ...]
    if sort == "due_on":
        order = (figures.c.due_on, figures.c.incurred_on, figures.c.id)
    else:
        order = (
            figures.c.incurred_on.desc(),
            figures.c.created_at.desc(),
            figures.c.id,
        )
    rows = (
        await session.execute(
            statement.add_columns(func.count().over().label("total"))
            .where(*conditions)
            .order_by(*order)
            .limit(page.limit)
            .offset(page.offset)
        )
    ).all()
    if rows:
        total = int(rows[0].total)
    elif page.offset == 0:
        total = 0
    else:
        total = int(
            await session.scalar(
                select(func.count()).select_from(figures).where(*conditions)
            )
            or 0
        )
    return OffsetPage(
        items=[ExpenseItem(**_item_fields(row)) for row in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


async def get_expense(session: AsyncSession, expense_id: uuid.UUID) -> ExpenseDetail:
    """One expense (#115 C3): the item, the note, who entered it, its payments
    (reversed ones included, newest ``paid_on`` first, then ``created_at`` and
    ``id``) and its adjustments (oldest first). ``404`` for none. Five
    statements, no lock."""
    today = await administration.center_today(session)
    figures, statement = _rows(today)
    row = (await session.execute(statement.where(figures.c.id == expense_id))).first()
    if row is None:
        raise RecordNotFoundError()
    payments = (
        await session.execute(
            select(
                ExpensePayment,
                ExpensePaymentReversal.reason.label("reversal_reason"),
                ExpensePaymentReversal.reversed_by_user_id,
                ExpensePaymentReversal.created_at.label("reversed_at"),
            )
            .outerjoin(
                ExpensePaymentReversal,
                ExpensePaymentReversal.expense_payment_id == ExpensePayment.id,
            )
            .where(ExpensePayment.expense_id == expense_id)
            .order_by(
                ExpensePayment.paid_on.desc(),
                ExpensePayment.created_at.desc(),
                ExpensePayment.id.desc(),
            )
        )
    ).all()
    adjustments = (
        await session.scalars(
            select(ExpenseAdjustment)
            .where(ExpenseAdjustment.expense_id == expense_id)
            .order_by(ExpenseAdjustment.created_at, ExpenseAdjustment.id)
        )
    ).all()
    user_ids = {
        *(p.ExpensePayment.recorded_by_user_id for p in payments),
        *(p.reversed_by_user_id for p in payments if p.reversed_by_user_id),
        *(a.created_by_user_id for a in adjustments),
    }
    if row.created_by_user_id is not None:
        user_ids.add(row.created_by_user_id)
    accounts = await identity.get_account_references(session, user_ids)

    def name(user_id: uuid.UUID) -> AccountName:
        return AccountName(id=user_id, display_name=accounts[user_id].display_name)

    return ExpenseDetail(
        **_item_fields(row),
        note=row.note,
        created_by=(
            None if row.created_by_user_id is None else name(row.created_by_user_id)
        ),
        payments=[
            ExpensePaymentItem(
                id=p.ExpensePayment.id,
                paid_on=p.ExpensePayment.paid_on,
                amount=p.ExpensePayment.amount,
                method_code=p.ExpensePayment.method_code,
                reference=p.ExpensePayment.reference,
                note=p.ExpensePayment.note,
                recorded_by=name(p.ExpensePayment.recorded_by_user_id),
                created_at=p.ExpensePayment.created_at,
                reversal=(
                    None
                    if p.reversed_by_user_id is None
                    else ExpensePaymentReversalItem(
                        reason=p.reversal_reason,
                        reversed_by=name(p.reversed_by_user_id),
                        created_at=p.reversed_at,
                    )
                ),
            )
            for p in payments
        ],
        adjustments=[
            ExpenseAdjustmentItem(
                id=a.id,
                amount=a.amount,
                reason=a.reason,
                created_by=name(a.created_by_user_id),
                created_at=a.created_at,
            )
            for a in adjustments
        ],
    )
