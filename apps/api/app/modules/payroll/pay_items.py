"""Bonuses and deductions: types and items (#109; ADR-0017 E8, its #109 notes;
data-model §11.7, §11.8, §14.2, §15).

The routes are ``app.workflows.payroll_pay_items_router``; the workflow
(``app.workflows.payroll_pay_items``) reads the tutor and the center's local
``today``. Which period an item lands in is the pure
:mod:`~app.modules.payroll.pay_item_lines`. Every function runs in the caller's
Unit of Work and never commits.

**Types.** The name is the natural key: a savepoint maps the unique index to
``409 pay_item_type_exists``, because ``ON CONFLICT`` would pass the audit by.
``direction`` never changes, and a type is never deleted. Deactivating takes the
type ``FOR UPDATE`` and refuses while a recurring item of it is running (#109 E8).
A new item reads its type ``FOR SHARE``, so the two take turns and an item never
lands on a type that just went inactive.

**Items.** There is no edit (#109 E9): ``PATCH`` sets ``effective_to`` only, and
anything else is a delete and a new item. Both take the tutor's not-finalized
periods ``FOR UPDATE``, oldest first, then the item, the order of a run, so
neither deadlocks with a run or races a finalize. A delete removes the item's
lines in ``open`` periods, because a run can't free a line of an item that still
exists. A line of a ``prepared`` period stays, and the foreign key refuses
(``23001``); the guard refuses a finalized one (``42501``). Both are ``409
pay_item_in_use``, and the rollback keeps the open lines.

**Reads.** :func:`pay_item_facts` takes the tutor's items ``FOR KEY SHARE`` after
the period lock (§15), like the versions of a run: a concurrent delete waits for
the run. The others write no line and take no lock.
"""

import uuid
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Final, cast

from sqlalchemy import Row, Select, func, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import RecordNotFoundError
from app.core.models import ensure_version
from app.core.money import Money
from app.core.pagination import OffsetPage, OffsetParams
from app.modules.payroll.errors import (
    PayItemEndBeforePaidPeriodError,
    PayItemEndBeforeStartError,
    PayItemInUseError,
    PayItemNotRecurringError,
    PayItemTypeExistsError,
    PayItemTypeInactiveError,
    PayItemTypeInUseError,
    PayItemTypeUnknownError,
)
from app.modules.payroll.lines import (
    FINALIZED,
    FOREIGN_KEY_REFUSALS,
    delete_lines_of_pay_item,
    lock_unfinalized_periods_of_tutor,
)
from app.modules.payroll.models import (
    PAY_ITEM_TYPE_NAME_KEY,
    PayItem,
    PayItemType,
    PayrollLine,
    PayrollPeriod,
)
from app.modules.payroll.pay_item_lines import (
    RECURRING,
    PayItemFact,
    PeriodDates,
    placed_period,
)
from app.modules.payroll.schemas import (
    PayItemDetail,
    PayItemDirection,
    PayItemPeriodStatus,
    PayItemPlacedPeriod,
    PayItemRecurrence,
    PayItemTypeCreate,
    PayItemTypeItem,
    PayItemTypeReference,
    PayItemTypeUpdate,
    PayItemUpdate,
)

INSUFFICIENT_PRIVILEGE: Final = "42501"


@dataclass(frozen=True)
class StatusedPeriod(PeriodDates):
    """A period, with the status a read shows."""

    status: PayItemPeriodStatus


# --- Types ---------------------------------------------------------------------------


def _type_item(row: PayItemType) -> PayItemTypeItem:
    return PayItemTypeItem(
        id=row.id,
        name=row.name,
        direction=cast(PayItemDirection, row.direction),
        is_active=row.is_active,
        version=row.version,
    )


async def _flush_type(session: AsyncSession, row: PayItemType) -> None:
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError as exc:
        if PAY_ITEM_TYPE_NAME_KEY in str(exc.orig):
            raise PayItemTypeExistsError() from None
        raise


async def list_pay_item_types(session: AsyncSession) -> list[PayItemTypeItem]:
    """Every type, active first, then by name, ignoring case. One statement."""
    rows = await session.scalars(
        select(PayItemType).order_by(
            PayItemType.is_active.desc(), func.lower(PayItemType.name)
        )
    )
    return [_type_item(row) for row in rows]


async def add_pay_item_type(
    session: AsyncSession, body: PayItemTypeCreate
) -> PayItemTypeItem:
    """A new type; ``409 pay_item_type_exists`` on a taken name."""
    row = PayItemType(name=body.name, direction=body.direction)
    await _flush_type(session, row)
    await session.refresh(row, ["is_active", "version"])
    return _type_item(row)


async def change_pay_item_type(
    session: AsyncSession, type_id: uuid.UUID, body: PayItemTypeUpdate, *, today: date
) -> PayItemTypeItem:
    """The type ``FOR UPDATE`` (``404``) → ``409 version_conflict`` → ``409
    pay_item_type_in_use`` when deactivating → the update (``409
    pay_item_type_exists``). Unchanged: nothing written, no new version."""
    row = await session.scalar(
        select(PayItemType)
        .where(PayItemType.id == type_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise RecordNotFoundError()
    ensure_version(row, body.version)
    if body.is_active is False and row.is_active:
        running = await session.scalar(
            select(PayItem.id)
            .where(
                PayItem.pay_item_type_id == type_id,
                PayItem.recurrence == RECURRING,
                PayItem.effective_to.is_(None) | (PayItem.effective_to >= today),
            )
            .limit(1)
        )
        if running is not None:
            raise PayItemTypeInUseError()
    if body.is_active is not None:
        row.is_active = body.is_active
    if body.name is not None:
        row.name = body.name
    await _flush_type(session, row)
    return _type_item(row)


# --- Items: reads --------------------------------------------------------------------


def _item_columns() -> Select[*tuple[Any, ...]]:
    return select(
        PayItem.id,
        PayItem.tutor_user_id,
        PayItem.description,
        PayItem.amount,
        PayItem.recurrence,
        PayItem.effective_from,
        PayItem.effective_to,
        PayItem.version,
        PayItem.created_at,
        PayItemType.id.label("type_id"),
        PayItemType.name.label("type_name"),
        PayItemType.direction,
    ).join(PayItemType, PayItemType.id == PayItem.pay_item_type_id)


def _fact(row: Row[Any]) -> PayItemFact:
    pay: Money = row.amount
    return PayItemFact(
        id=row.id,
        direction=row.direction,
        pay=pay,
        recurrence=row.recurrence,
        effective_from=row.effective_from,
        effective_to=row.effective_to,
    )


async def pay_item_facts(
    session: AsyncSession, tutor_user_id: uuid.UUID
) -> list[PayItemFact]:
    """Every item of the tutor, ``FOR KEY SHARE`` (module docstring). One
    statement."""
    rows = await session.execute(
        _item_columns()
        .where(PayItem.tutor_user_id == tutor_user_id)
        .with_for_update(read=True, key_share=True, of=PayItem)
    )
    return [_fact(row) for row in rows]


async def unfinalized_periods(
    session: AsyncSession, tutor_user_id: uuid.UUID
) -> list[StatusedPeriod]:
    """The tutor's not-finalized periods. One statement, no lock."""
    rows = await session.execute(
        select(
            PayrollPeriod.id,
            PayrollPeriod.starts_on,
            PayrollPeriod.ends_on,
            PayrollPeriod.status,
        ).where(
            PayrollPeriod.tutor_user_id == tutor_user_id,
            PayrollPeriod.status != FINALIZED,
        )
    )
    return [
        StatusedPeriod(
            row.id, row.starts_on, row.ends_on, cast(PayItemPeriodStatus, row.status)
        )
        for row in rows
    ]


async def finalized_pay_item_ids(
    session: AsyncSession, item_ids: Collection[uuid.UUID]
) -> set[uuid.UUID]:
    """Those of these items with a line in a finalized period. One statement,
    none for no ids, no lock."""
    if not item_ids:
        return set()
    return set(
        await session.scalars(
            select(PayrollLine.pay_item_id)
            .join(PayrollPeriod, PayrollPeriod.id == PayrollLine.payroll_period_id)
            .where(
                PayrollPeriod.status == FINALIZED,
                PayrollLine.pay_item_id.in_(set(item_ids)),
            )
            .distinct()
        )
    )


async def _details(
    session: AsyncSession, tutor_user_id: uuid.UUID, rows: Sequence[Row[Any]]
) -> list[PayItemDetail]:
    """Two statements, whatever the number of rows."""
    periods = await unfinalized_periods(session, tutor_user_id)
    finalized = await finalized_pay_item_ids(session, [row.id for row in rows])

    def detail(row: Row[Any]) -> PayItemDetail:
        placed = placed_period(_fact(row), periods, finalized)
        return PayItemDetail(
            id=row.id,
            tutor_user_id=row.tutor_user_id,
            pay_item_type=PayItemTypeReference(
                id=row.type_id,
                name=row.type_name,
                direction=cast(PayItemDirection, row.direction),
            ),
            description=row.description,
            amount=row.amount,
            recurrence=cast(PayItemRecurrence, row.recurrence),
            effective_from=row.effective_from,
            effective_to=row.effective_to,
            version=row.version,
            applied=row.id in finalized,
            placed_period=None
            if placed is None
            else PayItemPlacedPeriod(
                id=placed.id,
                starts_on=placed.starts_on,
                ends_on=placed.ends_on,
                status=placed.status,
            ),
            created_at=row.created_at,
        )

    return [detail(row) for row in rows]


async def list_pay_items(
    session: AsyncSession, tutor_user_id: uuid.UUID, page: OffsetParams
) -> OffsetPage[PayItemDetail]:
    """The tutor's items, newest ``effective_from`` first, then ``id``. Four
    statements, three with an empty page, whatever its size."""
    total = await session.scalar(
        select(func.count())
        .select_from(PayItem)
        .where(PayItem.tutor_user_id == tutor_user_id)
    )
    rows = (
        await session.execute(
            _item_columns()
            .where(PayItem.tutor_user_id == tutor_user_id)
            .order_by(PayItem.effective_from.desc(), PayItem.id)
            .limit(page.limit)
            .offset(page.offset)
        )
    ).all()
    return OffsetPage[PayItemDetail](
        items=await _details(session, tutor_user_id, rows),
        total=total or 0,
        limit=page.limit,
        offset=page.offset,
    )


async def get_pay_item(
    session: AsyncSession, tutor_user_id: uuid.UUID, item_id: uuid.UUID
) -> PayItemDetail:
    """One item as the list shows it. Three statements."""
    row = (await session.execute(_item_columns().where(PayItem.id == item_id))).one()
    [detail] = await _details(session, tutor_user_id, [row])
    return detail


# --- Items: writes -------------------------------------------------------------------


async def add_pay_item(
    session: AsyncSession,
    tutor_user_id: uuid.UUID,
    *,
    pay_item_type_id: uuid.UUID,
    description: str,
    amount: Money,
    recurrence: str,
    effective_from: date,
    effective_to: date | None,
) -> uuid.UUID:
    """A new item: the type ``FOR SHARE``, ``422 pay_item_type_unknown``, ``409
    pay_item_type_inactive``, the insert. It takes no period lock: a run sees it
    or doesn't, and the next one shows it."""
    is_active = await session.scalar(
        select(PayItemType.is_active)
        .where(PayItemType.id == pay_item_type_id)
        .with_for_update(read=True)
    )
    if is_active is None:
        raise PayItemTypeUnknownError()
    if not is_active:
        raise PayItemTypeInactiveError()
    item = PayItem(
        tutor_user_id=tutor_user_id,
        pay_item_type_id=pay_item_type_id,
        description=description,
        amount=amount,
        recurrence=recurrence,
        effective_from=effective_from,
        effective_to=effective_to,
    )
    session.add(item)
    await session.flush()
    return item.id


async def _locked_item(
    session: AsyncSession, tutor_user_id: uuid.UUID, item_id: uuid.UUID
) -> tuple[list[uuid.UUID], PayItem]:
    """The not-finalized periods, then the item of this tutor ``FOR UPDATE``
    (``404``). The order of the module docstring."""
    open_ids = await lock_unfinalized_periods_of_tutor(session, tutor_user_id)
    item = await session.scalar(
        select(PayItem)
        .where(PayItem.id == item_id, PayItem.tutor_user_id == tutor_user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if item is None:
        raise RecordNotFoundError()
    return open_ids, item


async def end_pay_item(
    session: AsyncSession,
    tutor_user_id: uuid.UUID,
    item_id: uuid.UUID,
    body: PayItemUpdate,
) -> None:
    """Set a recurring item's end, or reopen it with ``None``: the locks →
    ``409 version_conflict`` → ``409 pay_item_not_recurring`` → ``422
    pay_item_end_before_start`` → ``422 pay_item_end_before_paid_period``, so an
    item's dates never disown a paid line. Allowed on an applied item."""
    _, item = await _locked_item(session, tutor_user_id, item_id)
    ensure_version(item, body.version)
    if item.recurrence != RECURRING:
        raise PayItemNotRecurringError()
    if body.effective_to is not None:
        if body.effective_to < item.effective_from:
            raise PayItemEndBeforeStartError()
        paid_until = await session.scalar(
            select(func.max(PayrollPeriod.starts_on))
            .join(PayrollLine, PayrollLine.payroll_period_id == PayrollPeriod.id)
            .where(
                PayrollPeriod.status == FINALIZED, PayrollLine.pay_item_id == item_id
            )
        )
        if paid_until is not None and body.effective_to < paid_until:
            raise PayItemEndBeforePaidPeriodError()
    item.effective_to = body.effective_to
    await session.flush()


async def delete_pay_item(
    session: AsyncSession, tutor_user_id: uuid.UUID, item_id: uuid.UUID
) -> None:
    """Delete an item with its lines in ``open`` periods (module docstring):
    ``404`` for an item of another tutor, ``409 pay_item_in_use`` for one a
    prepared or finalized period uses."""
    open_ids, item = await _locked_item(session, tutor_user_id, item_id)
    await delete_lines_of_pay_item(session, item_id, open_ids)
    await session.delete(item)
    try:
        await session.flush()
    except DBAPIError as exc:
        state = getattr(exc.orig, "sqlstate", None)
        if state == INSUFFICIENT_PRIVILEGE or state in FOREIGN_KEY_REFUSALS:
            raise PayItemInUseError() from None
        raise
