"""Derived balances, charge statuses and the ledger history (#90 D1-D5;
data-model §3.26, §4.2).

Nothing here is stored: every value is computed from the ledger rows. The
reads take no lock and write nothing.

- ``net(charge) = amount + Σ adjustments``; ``open = net − Σ valid
  allocations`` (an allocation is valid while it has no release).
- **Outstanding** = Σ ``open``; **account credit** = Σ over the payments
  without a reversal of ``amount − Σ valid allocations − Σ refunds``;
  **net balance** = outstanding − account credit.
- **Status** (:func:`charge_status`): ``void`` → ``paid`` → ``overdue`` →
  ``partially_paid`` → ``pending``, with "today" the center's local date
  and the **effective** due date (the latest ``charge_due_date_changes``
  row, else ``charges.due_date``; #97 B1).
- **Charge list** (:func:`charge_details`, #364 B2): a page of charges
  with their stored fields, derived values and status, filtered by student
  and status in SQL (the same status rule as :func:`charge_status`).
- **History** (:func:`ledger_entries`): the ``billing_ledger_entries``
  view, newest first, read through :data:`LEDGER_VIEW` (a lightweight
  ``table()`` construct that is not on ``Base.metadata``; the view is
  defined only in migration 0061).
"""

import uuid
from collections import defaultdict
from collections.abc import Collection
from datetime import date, datetime
from typing import Any, Final

from sqlalchemy import (
    ColumnElement,
    DateTime,
    String,
    and_,
    case,
    column,
    func,
    literal,
    select,
    table,
    tuple_,
    type_coerce,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import Money, MoneyType, money_sum
from app.core.pagination import (
    CursorPage,
    CursorParams,
    InvalidCursorError,
    OffsetParams,
    decode_cursor,
    encode_cursor,
)
from app.modules.administration import service as administration
from app.modules.billing.models import (
    AllocationRelease,
    BillingAccount,
    Charge,
    ChargeAdjustment,
    ChargeDueDateChange,
    Payment,
    PaymentAllocation,
    PaymentReversal,
    Refund,
)
from app.modules.billing.schemas import (
    AccountBalance,
    BillingAccountReference,
    ChargeBalance,
    ChargeDetail,
    ChargeDetailPage,
    ChargeStatus,
    LedgerEntry,
    StudentBalance,
)

LEDGER_VIEW: Final = table(
    "billing_ledger_entries",
    column("entry_type", String()),
    column("entry_id", UUID(as_uuid=True)),
    column("billing_account_id", UUID(as_uuid=True)),
    column("kind", String()),
    column("student_id", UUID(as_uuid=True)),
    column("charge_id", UUID(as_uuid=True)),
    column("payment_id", UUID(as_uuid=True)),
    column("amount", MoneyType()),
    column("balance_effect", MoneyType()),
    column("occurred_at", DateTime(timezone=True)),
)
"""The ``billing_ledger_entries`` view (migration 0061), outside the
metadata so Alembic never sees it."""


def charge_status(
    net: Money, allocated: Money, effective_due_date: date, today: date
) -> ChargeStatus:
    """A charge's status (data-model §4.2): ``void`` when the net is zero
    and nothing is allocated; ``paid`` when nothing is open; ``overdue``
    when something is open and the due date is before ``today`` (the
    center's local date); ``partially_paid`` when something is open and
    something is allocated; otherwise ``pending``."""
    if net.is_zero() and allocated.is_zero():
        return "void"
    open_amount = net - allocated
    if not open_amount.is_positive():
        return "paid"
    if effective_due_date < today:
        return "overdue"
    if allocated.is_positive():
        return "partially_paid"
    return "pending"


def _effective_due_date(charge: Any = Charge) -> ColumnElement[date]:
    """The charge's effective due date (#97 B1; data-model §3.11): the
    ``due_date`` of its latest ``charge_due_date_changes`` row (by
    ``created_at``, ``id``), else ``charges.due_date``, as a correlated
    expression (not executed). Every status, order and reminder reads it;
    nothing reads ``charges.due_date`` directly for those."""
    latest = (
        select(ChargeDueDateChange.due_date)
        .where(ChargeDueDateChange.charge_id == charge.id)
        .order_by(ChargeDueDateChange.created_at.desc(), ChargeDueDateChange.id.desc())
        .limit(1)
        .correlate(charge)
        .scalar_subquery()
    )
    return func.coalesce(latest, charge.due_date)


def effective_due_date_column(charge: Any = Charge) -> ColumnElement[date]:
    """:func:`_effective_due_date`, public for the other billing files (the
    overdue list and the reminder job)."""
    return _effective_due_date(charge)


def _released(allocation_id: Any) -> Any:
    return (
        select(AllocationRelease.id)
        .where(AllocationRelease.allocation_id == allocation_id)
        .exists()
    )


def _valid_allocated(*where: Any) -> Any:
    """Σ valid allocations matching ``where``, as a scalar subquery."""
    return (
        select(money_sum(PaymentAllocation.amount))
        .where(*where, ~_released(PaymentAllocation.id))
        .scalar_subquery()
    )


async def _today(session: AsyncSession, today: date | None) -> date:
    return today if today is not None else await administration.center_today(session)


async def charge_balances(
    session: AsyncSession,
    account_id: uuid.UUID,
    *,
    student_id: uuid.UUID | None = None,
    charge_id: uuid.UUID | None = None,
    today: date | None = None,
) -> list[ChargeBalance]:
    """Every charge of the account (or of one of its students, or the one
    charge ``charge_id``, #97 D1) with its derived values and status (#90
    D1), ordered by ``service_date``, ``posted_at``, ``id``. One statement
    (two when ``today`` is read from the center's settings)."""
    today = await _today(session, today)
    adjustments = (
        select(money_sum(ChargeAdjustment.amount))
        .where(ChargeAdjustment.charge_id == Charge.id)
        .scalar_subquery()
    )
    allocated = _valid_allocated(PaymentAllocation.charge_id == Charge.id)
    query = select(
        Charge.id,
        Charge.student_id,
        Charge.kind,
        Charge.service_date,
        _effective_due_date().label("effective_due_date"),
        Charge.amount,
        adjustments.label("adjustments_total"),
        allocated.label("allocated"),
        Charge.posted_at,
    ).where(Charge.billing_account_id == account_id)
    if student_id is not None:
        query = query.where(Charge.student_id == student_id)
    if charge_id is not None:
        query = query.where(Charge.id == charge_id)
    rows = await session.execute(
        query.order_by(Charge.service_date, Charge.posted_at, Charge.id)
    )
    balances = []
    for row in rows:
        net = row.amount + row.adjustments_total
        balances.append(
            ChargeBalance(
                charge_id=row.id,
                student_id=row.student_id,
                kind=row.kind,
                service_date=row.service_date,
                effective_due_date=row.effective_due_date,
                amount=row.amount,
                adjustments_total=row.adjustments_total,
                net=net,
                allocated=row.allocated,
                open=net - row.allocated,
                status=charge_status(net, row.allocated, row.effective_due_date, today),
                posted_at=row.posted_at,
            )
        )
    return balances


def _refunded(payment: Any = Payment) -> Any:
    """Σ refunds of a payment row, as a correlated scalar subquery."""
    return (
        select(money_sum(Refund.amount))
        .where(Refund.payment_id == payment.id)
        .scalar_subquery()
    )


def _reversed(payment: Any = Payment) -> Any:
    """Whether a payment row has a reversal (``EXISTS``)."""
    return (
        select(PaymentReversal.id)
        .where(PaymentReversal.payment_id == payment.id)
        .exists()
    )


def payment_remainder_column() -> Any:
    """``amount − Σ valid allocations − Σ refunds`` of a ``payments`` row,
    **not executed**: the one remainder expression behind
    :func:`account_credit`, :func:`payment_remainders` (#96 M4) and the
    payment reads. #100's refunds draw on it too (I3)."""
    return (
        Payment.amount
        - _valid_allocated(PaymentAllocation.payment_id == Payment.id)
        - _refunded()
    )


async def account_credit(session: AsyncSession, account_id: uuid.UUID) -> Money:
    """Σ over the account's payments without a reversal of ``amount − Σ
    valid allocations − Σ refunds`` (data-model §4.2). One statement."""
    credit: Money | None = await session.scalar(
        select(money_sum(payment_remainder_column())).where(
            Payment.billing_account_id == account_id, ~_reversed()
        )
    )
    return Money.zero() if credit is None else credit


async def payment_remainders(
    session: AsyncSession, account_id: uuid.UUID
) -> list[tuple[uuid.UUID, Money]]:
    """``(payment_id, remainder)`` of the account's payments without a
    reversal whose remainder is above zero, oldest first by
    ``received_on``, ``created_at``, ``id`` (#96 M4: the sources of a
    credit allocation; their sum is :func:`account_credit`). One statement;
    no lock (the caller holds the account lock)."""
    remainder = type_coerce(payment_remainder_column(), MoneyType()).label("remainder")
    inner = (
        select(Payment.id, Payment.received_on, Payment.created_at, remainder)
        .where(Payment.billing_account_id == account_id, ~_reversed())
        .subquery("payment_remainders")
    )
    rows = await session.execute(
        select(inner.c.id, inner.c.remainder)
        .where(inner.c.remainder > literal(Money.zero(), MoneyType()))
        .order_by(inner.c.received_on, inner.c.created_at, inner.c.id)
    )
    return [(row.id, Money(row.remainder)) for row in rows]


def _by_status(charges: list[ChargeBalance], status: ChargeStatus) -> Money:
    return Money.total(c.open for c in charges if c.status == status)


def _student_balance(
    student_id: uuid.UUID, charges: list[ChargeBalance]
) -> StudentBalance:
    return StudentBalance(
        student_id=student_id,
        outstanding=Money.total(c.open for c in charges),
        pending=_by_status(charges, "pending"),
        partially_paid=_by_status(charges, "partially_paid"),
        overdue=_by_status(charges, "overdue"),
    )


async def account_balance(
    session: AsyncSession, account_id: uuid.UUID, *, today: date | None = None
) -> AccountBalance:
    """The account's outstanding amount, account credit and net balance, the
    open amounts by status, and each student's (#90 D2; ordered by
    ``student_id``). At most three statements: the center's today, the
    charges, the account credit."""
    charges = await charge_balances(session, account_id, today=today)
    credit = await account_credit(session, account_id)
    per_student: defaultdict[uuid.UUID, list[ChargeBalance]] = defaultdict(list)
    for charge in charges:
        per_student[charge.student_id].append(charge)
    outstanding = Money.total(c.open for c in charges)
    return AccountBalance(
        billing_account_id=account_id,
        outstanding=outstanding,
        account_credit=credit,
        net_balance=outstanding - credit,
        pending=_by_status(charges, "pending"),
        partially_paid=_by_status(charges, "partially_paid"),
        overdue=_by_status(charges, "overdue"),
        by_student=tuple(
            _student_balance(student, per_student[student])
            for student in sorted(per_student)
        ),
    )


async def account_of_family(
    session: AsyncSession, family_id: uuid.UUID
) -> BillingAccountReference | None:
    """The family's billing account, or ``None`` when it has none yet (one
    statement, no lock; a read never creates one, #364 B4)."""
    row = (
        await session.execute(
            select(
                BillingAccount.id,
                BillingAccount.family_id,
                BillingAccount.due_offset_days,
                BillingAccount.version,
            ).where(BillingAccount.family_id == family_id)
        )
    ).first()
    if row is None:
        return None
    return BillingAccountReference(
        id=row.id,
        family_id=row.family_id,
        due_offset_days=row.due_offset_days,
        version=row.version,
    )


async def family_balance(
    session: AsyncSession, family_id: uuid.UUID, *, today: date | None = None
) -> AccountBalance | None:
    """The family's :func:`account_balance`, or ``None`` when it has no
    account yet (a read never creates one; #90 D3)."""
    account = await account_of_family(session, family_id)
    if account is None:
        return None
    return await account_balance(session, account.id, today=today)


def _status_expression(
    net: ColumnElement[Money],
    allocated: ColumnElement[Money],
    due: ColumnElement[date],
    today: date,
) -> ColumnElement[str]:
    """:func:`charge_status` in SQL, the same rules in the same order."""
    zero = literal(Money.zero(), MoneyType())
    return case(
        (and_(net == zero, allocated == zero), "void"),
        (net - allocated <= zero, "paid"),
        (due < today, "overdue"),
        (allocated > zero, "partially_paid"),
        else_="pending",
    )


async def charge_details(
    session: AsyncSession,
    account_id: uuid.UUID,
    *,
    page: OffsetParams,
    student_id: uuid.UUID | None = None,
    statuses: Collection[ChargeStatus] | None = None,
    today: date | None = None,
) -> ChargeDetailPage:
    """A page of the account's charges (#364 B2), newest first by
    ``service_date``, ``posted_at``, ``id``, optionally of one student and
    in some statuses (``today`` is the center's local date). One statement
    for the page with its total (a window count), one more for the total
    when the page is past the end, plus the center's today when not given.
    No lock."""
    today = await _today(session, today)
    adjustments = (
        select(money_sum(ChargeAdjustment.amount))
        .where(ChargeAdjustment.charge_id == Charge.id)
        .scalar_subquery()
    )
    allocated = _valid_allocated(PaymentAllocation.charge_id == Charge.id)
    inner = select(
        Charge.id,
        Charge.student_id,
        Charge.kind,
        Charge.service_date,
        _effective_due_date().label("due_date"),
        Charge.due_date.label("original_due_date"),
        Charge.description,
        Charge.list_amount,
        Charge.discount_amount,
        Charge.amount,
        adjustments.label("adjustments_total"),
        allocated.label("allocated"),
        Charge.posted_at,
        Charge.posted_by_user_id,
        Charge.class_session_id,
        Charge.group_enrollment_id,
        Charge.cohort_enrollment_id,
    ).where(Charge.billing_account_id == account_id)
    if student_id is not None:
        inner = inner.where(Charge.student_id == student_id)
    rows_of = inner.subquery("charge_rows")
    status = _status_expression(
        rows_of.c.amount + rows_of.c.adjustments_total,
        rows_of.c.allocated,
        rows_of.c.due_date,
        today,
    ).label("status")
    with_status = select(rows_of, status).subquery("charge_statuses")
    conditions = []
    if statuses:
        conditions.append(with_status.c.status.in_(sorted(set(statuses))))
    rows = (
        await session.execute(
            select(with_status, func.count().over().label("total"))
            .where(*conditions)
            .order_by(
                with_status.c.service_date.desc(),
                with_status.c.posted_at.desc(),
                with_status.c.id.desc(),
            )
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
                select(func.count()).select_from(with_status).where(*conditions)
            )
            or 0
        )
    items = []
    for row in rows:
        net = row.amount + row.adjustments_total
        items.append(
            ChargeDetail(
                charge_id=row.id,
                student_id=row.student_id,
                kind=row.kind,
                service_date=row.service_date,
                due_date=row.due_date,
                original_due_date=row.original_due_date,
                description=row.description,
                list_amount=row.list_amount,
                discount_amount=row.discount_amount,
                amount=row.amount,
                adjustments_total=row.adjustments_total,
                net=net,
                allocated=row.allocated,
                open=net - row.allocated,
                status=row.status,
                posted_at=row.posted_at,
                posted_by_user_id=row.posted_by_user_id,
                class_session_id=row.class_session_id,
                group_enrollment_id=row.group_enrollment_id,
                cohort_enrollment_id=row.cohort_enrollment_id,
            )
        )
    return ChargeDetailPage(items=tuple(items), total=total)


def _position(cursor: str) -> tuple[datetime, uuid.UUID]:
    occurred_at, entry_id = decode_cursor(cursor, types=(str, str))
    try:
        when = datetime.fromisoformat(str(occurred_at))
        identifier = uuid.UUID(str(entry_id))
    except ValueError:
        raise InvalidCursorError() from None
    if when.tzinfo is None:
        raise InvalidCursorError()
    return when, identifier


async def ledger_entries(
    session: AsyncSession,
    account_id: uuid.UUID,
    *,
    params: CursorParams,
    student_id: uuid.UUID | None = None,
) -> CursorPage[LedgerEntry]:
    """The account's ledger rows, newest first by ``(occurred_at,
    entry_id)`` (#90 D4). With ``student_id``, only the rows of that
    student (charges, adjustments, allocations, releases; payment-level rows
    have none). A bad cursor is ``400 invalid_cursor``. One statement per
    page."""
    view = LEDGER_VIEW.c
    query = select(LEDGER_VIEW).where(view.billing_account_id == account_id)
    if student_id is not None:
        query = query.where(view.student_id == student_id)
    if params.cursor is not None:
        when, identifier = _position(params.cursor)
        query = query.where(
            tuple_(view.occurred_at, view.entry_id) < tuple_(when, identifier)
        )
    rows = (
        await session.execute(
            query.order_by(view.occurred_at.desc(), view.entry_id.desc()).limit(
                params.limit + 1
            )
        )
    ).all()
    items = [
        LedgerEntry.model_validate(dict(row._mapping)) for row in rows[: params.limit]
    ]
    next_cursor = (
        encode_cursor([items[-1].occurred_at.isoformat(), str(items[-1].entry_id)])
        if len(rows) > params.limit and items
        else None
    )
    return CursorPage[LedgerEntry](items=items, next_cursor=next_cursor)
