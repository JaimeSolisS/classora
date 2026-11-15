"""Recurring expense templates (#116 C, D; spec §8; ADR-0017 E17 and its #116
notes; data-model §12.7, §13.1 X2, §15).

Every function runs in the caller's Unit of Work and never commits. Writes go
through the ORM, so the audit row comes with the Unit of Work's actor, with
``name`` and ``amount`` masked (#116 E9).

**Names** (#116 E3): unique ignoring case, paused and ended templates
included. The name is the natural key, so the create takes no
``Idempotency-Key``: a lost create retried is ``409 expense_template_exists``.
A query refuses a taken name in the documented order; the unique index, in a
savepoint, is the backstop for a race.

**The grid** (:mod:`~app.modules.finance.template_periods`): ``frequency`` and
``starts_on`` never change (#116 E4). ``next_period_start`` is the next period
the job looks at; it never moves backwards.

**Live** (#116 E2; :mod:`~app.modules.finance.template_usage`): active and not
ended. Every write that leaves a template live reads its resulting category and
vendor through ``usable_*`` (``FOR SHARE``), changed or not (C5), so a live
template's category and vendor are always active and the job never meets an
inactive one. A write that leaves it paused or ended checks only that a new
category or vendor exists.

**Future periods only** (#116 E7): a change applies to every period not yet
generated when it commits. The template row ``FOR UPDATE`` serializes a change
and a job step, so each period takes all the old values or all the new ones.

**The job's unit** (:func:`generate_template_periods`, #116 D3), in this
order: the template row ``FOR UPDATE`` (:func:`lock_template`), re-read → for
each period from ``next_period_start`` while it starts on or before the
center's today and on or before ``ends_on``, oldest first: a savepoint with
``insert_expense`` (``created_by_user_id`` ``NULL``, the description the
template's name, ``incurred_on = period_start``, ``due_on = period_start +
due_offset_days``); a violation of ``uq_expenses_template_period`` rolls the
savepoint back and counts ``already_generated`` (never ``ON CONFLICT``:
``expenses`` is ``Audited``) → ``next_period_start`` moves to the period after
the last one looked at.
"""

import uuid
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Final, cast

from sqlalchemy import ColumnElement, and_, case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import RecordNotFoundError
from app.core.models import ensure_version
from app.core.money import Money
from app.modules.administration import service as administration
from app.modules.finance.errors import (
    ExpenseCategoryUnknownError,
    ExpenseTemplateEndsOnInvalidError,
    ExpenseTemplateExistsError,
    ExpenseTemplateStartsOnInvalidError,
    VendorUnknownError,
)
from app.modules.finance.expenses import ensure_usable, insert_expense
from app.modules.finance.models import (
    TEMPLATE_NAME_KEY,
    TEMPLATE_PERIOD_KEY,
    Expense,
    ExpenseCategory,
    ExpenseTemplate,
    Vendor,
)
from app.modules.finance.schemas import (
    ExpenseCategoryRef,
    ExpenseTemplateCreate,
    ExpenseTemplateItem,
    ExpenseTemplateUpdate,
    SystemKey,
    TemplateStatus,
    VendorRef,
)
from app.modules.finance.template_periods import (
    Frequency,
    first_on_or_after,
    next_after,
)
from app.modules.finance.template_usage import live

STARTS_ON_BACK_DAYS: Final = 31
STARTS_ON_AHEAD_DAYS: Final = 366
"""The ``starts_on`` window around the center's today (#116 E5, the #93
tuition bound)."""

CANDIDATE_PAGE: Final = 100
"""Templates per keyset page of the job's candidates."""

ACTIVE: Final = "active"
PAUSED: Final = "paused"
ENDED: Final = "ended"


# --- Status and the item --------------------------------------------------------------


def template_status(
    *, is_active: bool, ends_on: date | None, next_period_start: date
) -> TemplateStatus:
    """#116 C7: ``ended``, else ``paused``, else ``active``."""
    if ends_on is not None and next_period_start > ends_on:
        return ENDED
    if not is_active:
        return PAUSED
    return ACTIVE


def _status_expression() -> ColumnElement[str]:
    """:func:`template_status` in SQL."""
    return case(
        (
            and_(
                ExpenseTemplate.ends_on.is_not(None),
                ExpenseTemplate.next_period_start > ExpenseTemplate.ends_on,
            ),
            ENDED,
        ),
        (ExpenseTemplate.is_active.is_(False), PAUSED),
        else_=ACTIVE,
    )


def _last_period_start() -> Any:
    return (
        select(func.max(Expense.period_start))
        .where(Expense.expense_template_id == ExpenseTemplate.id)
        .correlate(ExpenseTemplate)
        .scalar_subquery()
    )


def _items_statement() -> Any:
    status = _status_expression()
    return (
        select(
            ExpenseTemplate,
            ExpenseCategory.name.label("category_name"),
            ExpenseCategory.system_key.label("category_system_key"),
            ExpenseCategory.is_active.label("category_is_active"),
            Vendor.name.label("vendor_name"),
            Vendor.is_active.label("vendor_is_active"),
            _last_period_start().label("last_period_start"),
            status.label("status"),
        )
        .join(ExpenseCategory, ExpenseCategory.id == ExpenseTemplate.category_id)
        .outerjoin(Vendor, Vendor.id == ExpenseTemplate.vendor_id)
    )


def _item(row: Any) -> ExpenseTemplateItem:
    template: ExpenseTemplate = row.ExpenseTemplate
    vendor = None
    if template.vendor_id is not None:
        vendor = VendorRef(
            id=template.vendor_id,
            name=row.vendor_name,
            is_active=row.vendor_is_active,
        )
    return ExpenseTemplateItem(
        id=template.id,
        name=template.name,
        category=ExpenseCategoryRef(
            id=template.category_id,
            name=row.category_name,
            system_key=cast(SystemKey | None, row.category_system_key),
            is_active=row.category_is_active,
        ),
        vendor=vendor,
        amount=template.amount,
        frequency=cast(Frequency, template.frequency),
        starts_on=template.starts_on,
        ends_on=template.ends_on,
        due_offset_days=template.due_offset_days,
        next_period_start=template.next_period_start,
        last_period_start=row.last_period_start,
        status=row.status,
        is_active=template.is_active,
        version=template.version,
        created_at=template.created_at,
        updated_at=template.updated_at,
    )


# --- Reads ----------------------------------------------------------------------------


@dataclass(frozen=True)
class TemplateFilters:
    """The list's filters (#116 C8); ``None`` or empty is "any"."""

    statuses: Collection[TemplateStatus] | None = None
    category_id: uuid.UUID | None = None
    vendor_id: uuid.UUID | None = None


async def list_templates(
    session: AsyncSession, filters: TemplateFilters
) -> list[ExpenseTemplateItem]:
    """Every template (no pagination), ``active``, then ``paused``, then
    ``ended``, then by name ignoring case, then id (#116 C8). Inactive
    categories and vendors are listed and filterable. One statement, no lock."""
    status = _status_expression()
    conditions: list[ColumnElement[bool]] = []
    if filters.statuses:
        conditions.append(status.in_(sorted(set(filters.statuses))))
    if filters.category_id is not None:
        conditions.append(ExpenseTemplate.category_id == filters.category_id)
    if filters.vendor_id is not None:
        conditions.append(ExpenseTemplate.vendor_id == filters.vendor_id)
    rank = case((status == ACTIVE, 0), (status == PAUSED, 1), else_=2)
    rows = (
        await session.execute(
            _items_statement()
            .where(*conditions)
            .order_by(rank, func.lower(ExpenseTemplate.name), ExpenseTemplate.id)
        )
    ).all()
    return [_item(row) for row in rows]


async def get_template(
    session: AsyncSession, template_id: uuid.UUID
) -> ExpenseTemplateItem:
    """One template (#116 C8); ``404`` for none. One statement, no lock."""
    row = (
        await session.execute(
            _items_statement()
            .where(ExpenseTemplate.id == template_id)
            .execution_options(populate_existing=True)
        )
    ).first()
    if row is None:
        raise RecordNotFoundError()
    return _item(row)


# --- Writes ---------------------------------------------------------------------------


async def _refuse_a_taken_name(
    session: AsyncSession, name: str, *, other_than: uuid.UUID | None = None
) -> None:
    query = select(ExpenseTemplate.id).where(
        func.lower(ExpenseTemplate.name) == name.lower()
    )
    if other_than is not None:
        query = query.where(ExpenseTemplate.id != other_than)
    if await session.scalar(query.limit(1)) is not None:
        raise ExpenseTemplateExistsError()


async def _flush(session: AsyncSession, row: ExpenseTemplate) -> None:
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError as exc:
        if TEMPLATE_NAME_KEY in str(exc.orig):
            raise ExpenseTemplateExistsError() from None
        raise


async def _ensure_exists(
    session: AsyncSession,
    category_id: uuid.UUID | None,
    vendor_id: uuid.UUID | None,
) -> None:
    """``422 expense_category_unknown`` / ``vendor_unknown`` for an id that
    doesn't exist, active or not (a write that leaves the template paused or
    ended)."""
    if category_id is not None and (
        await session.scalar(
            select(ExpenseCategory.id).where(ExpenseCategory.id == category_id)
        )
        is None
    ):
        raise ExpenseCategoryUnknownError()
    if vendor_id is not None and (
        await session.scalar(select(Vendor.id).where(Vendor.id == vendor_id)) is None
    ):
        raise VendorUnknownError()


def _starts_on_allowed(starts_on: date, today: date) -> bool:
    return (
        today - timedelta(days=STARTS_ON_BACK_DAYS)
        <= starts_on
        <= today + timedelta(days=STARTS_ON_AHEAD_DAYS)
    )


async def last_generated(session: AsyncSession, template_id: uuid.UUID) -> date | None:
    """The latest ``period_start`` generated for the template. One
    statement."""
    return await session.scalar(
        select(func.max(Expense.period_start)).where(
            Expense.expense_template_id == template_id
        )
    )


async def create_template(
    session: AsyncSession, body: ExpenseTemplateCreate
) -> ExpenseTemplateItem:
    """``POST /api/v1/finance/expense-templates`` (#116 C1-C3), after the body:
    ``409 expense_template_exists`` → ``422 expense_category_unknown`` /
    ``vendor_unknown`` → ``409 expense_category_inactive`` /
    ``vendor_inactive`` (``FOR SHARE``) → ``422
    expense_template_starts_on_invalid`` → ``422
    expense_template_ends_on_invalid``. ``next_period_start = starts_on``."""
    await _refuse_a_taken_name(session, body.name)
    await ensure_usable(session, body.category_id, body.vendor_id)
    today = await administration.center_today(session)
    if not _starts_on_allowed(body.starts_on, today):
        raise ExpenseTemplateStartsOnInvalidError()
    if body.ends_on is not None and body.ends_on < body.starts_on:
        raise ExpenseTemplateEndsOnInvalidError()
    row = ExpenseTemplate(
        id=uuid.uuid7(),
        name=body.name,
        category_id=body.category_id,
        vendor_id=body.vendor_id,
        amount=body.amount,
        frequency=body.frequency,
        starts_on=body.starts_on,
        ends_on=body.ends_on,
        due_offset_days=body.due_offset_days,
        next_period_start=body.starts_on,
        is_active=True,
    )
    await _flush(session, row)
    return await get_template(session, row.id)


async def lock_template(
    session: AsyncSession, template_id: uuid.UUID
) -> ExpenseTemplate | None:
    """The template row ``FOR UPDATE``, re-read: the lock a change and a job
    step take first (#116 E7, D3). ``None`` for no such row."""
    return await session.scalar(
        select(ExpenseTemplate)
        .where(ExpenseTemplate.id == template_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


@dataclass(frozen=True)
class TemplateChange:
    """What :func:`change_template` did: the item, and whether a run should be
    queued after the commit (a reactivation or a new ``ends_on``, #116 E8)."""

    item: ExpenseTemplateItem
    queue_run: bool


async def change_template(
    session: AsyncSession, template_id: uuid.UUID, body: ExpenseTemplateUpdate
) -> TemplateChange:
    """``PATCH /api/v1/finance/expense-templates/{id}`` (#116 C4-C6), after
    the body: the row ``FOR UPDATE`` (``404``) → ``409 version_conflict`` →
    ``409 expense_template_exists`` → the category and vendor (C5) → ``422
    expense_template_ends_on_invalid`` → the update. A reactivation moves
    ``next_period_start`` to ``greatest(next_period_start, the first period on
    or after the center's today)``: no back-fill, never backwards. Pausing
    changes only ``is_active``. Unchanged: nothing written, no new version.
    The job's advance never changes ``version``, so a change read before a run
    still applies after it."""
    row = await lock_template(session, template_id)
    if row is None:
        raise RecordNotFoundError()
    ensure_version(row, body.version)
    given = body.model_fields_set
    name = body.name if body.name is not None else row.name
    if name.lower() != row.name.lower():
        await _refuse_a_taken_name(session, name, other_than=row.id)
    category_id = body.category_id if body.category_id is not None else row.category_id
    vendor_id = body.vendor_id if "vendor_id" in given else row.vendor_id
    ends_on = body.ends_on if "ends_on" in given else row.ends_on
    is_active = body.is_active if body.is_active is not None else row.is_active
    next_period_start = row.next_period_start
    reactivated = is_active and not row.is_active
    if reactivated:
        today = await administration.center_today(session)
        frequency = cast(Frequency, row.frequency)
        next_period_start = max(
            next_period_start, first_on_or_after(row.starts_on, frequency, today)
        )
    stays_live = is_active and (ends_on is None or next_period_start <= ends_on)
    if stays_live:
        await ensure_usable(session, category_id, vendor_id)
    else:
        await _ensure_exists(
            session,
            category_id if category_id != row.category_id else None,
            vendor_id if vendor_id != row.vendor_id else None,
        )
    ends_on_changed = ends_on != row.ends_on
    if ends_on_changed and ends_on is not None:
        last = await last_generated(session, row.id)
        if ends_on < row.starts_on or (last is not None and ends_on < last):
            raise ExpenseTemplateEndsOnInvalidError()
    changes: dict[str, Any] = {
        "name": name,
        "category_id": category_id,
        "vendor_id": vendor_id,
        "amount": body.amount if body.amount is not None else row.amount,
        "due_offset_days": (
            body.due_offset_days
            if body.due_offset_days is not None
            else row.due_offset_days
        ),
        "ends_on": ends_on,
        "is_active": is_active,
        "next_period_start": next_period_start,
    }
    changed = False
    for field, value in changes.items():
        if getattr(row, field) != value:
            setattr(row, field, value)
            changed = True
    if changed:
        # The mapper doesn't generate versions: only staff changes count.
        row.version = row.version + 1
    await _flush(session, row)
    return TemplateChange(
        item=await get_template(session, row.id),
        queue_run=reactivated or ends_on_changed,
    )


# --- The job's reads and unit (#116 D) ------------------------------------------------


async def due_template_ids(
    session: AsyncSession,
    *,
    today: date,
    after: uuid.UUID | None = None,
    limit: int = CANDIDATE_PAGE,
) -> Sequence[uuid.UUID]:
    """A keyset page (by id) of the templates with a period due on ``today``:
    live and ``next_period_start <= today`` (#116 D2). No lock."""
    query = select(ExpenseTemplate.id).where(
        live(), ExpenseTemplate.next_period_start <= today
    )
    if after is not None:
        query = query.where(ExpenseTemplate.id > after)
    ordered = query.order_by(ExpenseTemplate.id).limit(limit)
    return (await session.scalars(ordered)).all()


@dataclass(frozen=True)
class _Snapshot:
    """A locked template's values, read once."""

    id: uuid.UUID
    category_id: uuid.UUID
    vendor_id: uuid.UUID | None
    name: str
    amount: Money
    frequency: Frequency
    starts_on: date
    ends_on: date | None
    due_offset_days: int


@dataclass(frozen=True)
class GeneratedPeriods:
    """What one template's unit did."""

    generated: int = 0
    already_generated: int = 0


async def generate_template_periods(
    session: AsyncSession, template_id: uuid.UUID, *, today: date
) -> GeneratedPeriods:
    """The job's unit for one template (#116 D3), in the caller's Unit of Work:
    see the module docstring. A template paused, ended or advanced by a
    concurrent run is re-read under the lock and skipped or resumed from its
    new ``next_period_start``."""
    row = await lock_template(session, template_id)
    if row is None or not row.is_active:
        return GeneratedPeriods()
    # Plain values: a rolled-back savepoint may expire the row.
    template = _Snapshot(
        id=row.id,
        category_id=row.category_id,
        vendor_id=row.vendor_id,
        name=row.name,
        amount=row.amount,
        frequency=cast(Frequency, row.frequency),
        starts_on=row.starts_on,
        ends_on=row.ends_on,
        due_offset_days=row.due_offset_days,
    )
    first = start = row.next_period_start
    generated = already = 0
    while start <= today and (template.ends_on is None or start <= template.ends_on):
        try:
            async with session.begin_nested():
                await insert_expense(
                    session,
                    category_id=template.category_id,
                    vendor_id=template.vendor_id,
                    description=template.name,
                    amount=template.amount,
                    incurred_on=start,
                    due_on=start + timedelta(days=template.due_offset_days),
                    receipt_reference=None,
                    note=None,
                    created_by_user_id=None,
                    today=today,
                    expense_template_id=template.id,
                    period_start=start,
                )
            generated += 1
        except IntegrityError as exc:
            if TEMPLATE_PERIOD_KEY not in str(exc.orig):
                raise
            already += 1
        start = next_after(template.starts_on, template.frequency, start)
    if start != first:
        row = await lock_template(session, template.id)
        assert row is not None  # noqa: S101 - locked by this transaction
        row.next_period_start = start
        await session.flush()
    return GeneratedPeriods(generated=generated, already_generated=already)
