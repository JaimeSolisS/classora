"""Custom student and family prices: reads and writes (#95 E; H6, H7).

The routes are ``app.workflows.custom_prices_router``; the workflow
(``app.workflows.custom_prices``) checks the student or family in
``families``, names the response and passes the center's local ``today``.
Every function runs in the caller's Unit of Work and never commits.

- **Items:** a custom price is for one ``per_session`` or ``full_course``
  catalog item (the models the #89 routes serve; ``422
  pricing_model_not_allowed`` otherwise, #93/#94 may widen it).
- **Dates (H7):** ``effective_from`` is today or later (``422
  custom_price_effective_from_invalid``: no backdating, so a past lookup
  never changes); ``expires_on`` is inclusive, ``NULL`` = open-ended. Two
  rows of one target and item never overlap (``409 custom_price_overlap``: a
  check, and the exclusion constraints mapped by name for the concurrent
  case).
- **Ending (E5):** only ``expires_on`` changes, to today or later, on or
  after ``effective_from`` and on or after the last ``service_date`` of a
  charge at this price (``422 custom_price_expiry_invalid``); ``null``
  re-opens it. The amount, target, item and start never change (E7).
- **Delete (E6):** only an upcoming row (``409 custom_price_in_effect``) that
  no charge uses (the guard's 42501 → ``409 custom_price_in_use``).
- **Status** against ``today``: ``upcoming`` (starts later), ``past``
  (ended before today), else ``current``; ``in_use`` = a charge references
  it.
"""

import uuid
from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final

from sqlalchemy import ColumnElement, case, exists, func, or_, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import RecordNotFoundError
from app.core.models import ensure_version
from app.core.money import Money
from app.modules.billing.discount_config import TargetNames
from app.modules.billing.errors import (
    CustomPriceExpiryInvalidError,
    CustomPriceInEffectError,
    CustomPriceInUseError,
    CustomPriceOverlapError,
)
from app.modules.billing.models import (
    CUSTOM_PRICE_FAMILY_OVERLAP,
    CUSTOM_PRICE_STUDENT_OVERLAP,
    Charge,
    CustomPrice,
    ServicePrice,
)
from app.modules.billing.prices import (
    CATALOG_MODELS,
    INSUFFICIENT_PRIVILEGE,
    CatalogItemRow,
    CatalogNames,
    catalog_query,
    catalog_row,
)
from app.modules.billing.schemas import CustomPriceCreate, CustomPriceUpdate

CUSTOM_PRICE_STATUSES: Final = ("past", "current", "upcoming")


@dataclass(frozen=True)
class CustomPriceRow:
    """One custom price with its item, its target's name, status and use."""

    id: uuid.UUID
    item: CatalogItemRow
    student_id: uuid.UUID | None
    family_id: uuid.UUID | None
    target_name: str
    effective_from: date
    expires_on: date | None
    amount: Money
    status: str
    in_use: bool
    version: int
    created_at: datetime


def _status(today: date) -> ColumnElement[str]:
    return case(
        (CustomPrice.effective_from > today, "upcoming"),
        (CustomPrice.expires_on < today, "past"),
        else_="current",
    )


def _in_use() -> ColumnElement[bool]:
    return exists().where(Charge.custom_price_id == CustomPrice.id)


def _query(names: CatalogNames, targets: TargetNames, today: date) -> tuple[Any, Any]:
    query, item_order = catalog_query(names)
    students = targets.students.subquery("custom_student_names")
    families = targets.families.subquery("custom_family_names")
    target_name = func.coalesce(students.c.name, families.c.name)
    query = (
        query.add_columns(
            CustomPrice,
            target_name.label("target_name"),
            _status(today).label("custom_status"),
            _in_use().label("in_use"),
        )
        .join(CustomPrice, CustomPrice.service_price_id == ServicePrice.id)
        .outerjoin(students, students.c.id == CustomPrice.student_id)
        .outerjoin(families, families.c.id == CustomPrice.family_id)
    )
    order = [
        func.lower(target_name),
        *item_order[:-1],
        CustomPrice.effective_from.desc(),
        CustomPrice.id,
    ]
    return query, order


def _row(found: Any) -> CustomPriceRow:
    custom: CustomPrice = found.CustomPrice
    return CustomPriceRow(
        id=custom.id,
        item=catalog_row(found),
        student_id=custom.student_id,
        family_id=custom.family_id,
        target_name=found.target_name or "",
        effective_from=custom.effective_from,
        expires_on=custom.expires_on,
        amount=custom.amount,
        status=found.custom_status,
        in_use=bool(found.in_use),
        version=custom.version,
        created_at=custom.created_at,
    )


async def list_custom_prices(
    session: AsyncSession,
    names: CatalogNames,
    targets: TargetNames,
    *,
    student_id: uuid.UUID | None,
    family_id: uuid.UUID | None,
    service_price_id: uuid.UUID | None,
    statuses: Collection[str],
    today: date,
    limit: int,
    offset: int,
) -> tuple[list[CustomPriceRow], int]:
    """A page of custom prices (E2) by target name, the #89 item order,
    ``effective_from`` (newest first) and id, with the total. One statement
    (the total is a window count; a count when the page is past the end)."""
    query, order = _query(names, targets, today)
    conditions: list[ColumnElement[bool]] = []
    if student_id is not None:
        conditions.append(CustomPrice.student_id == student_id)
    if family_id is not None:
        conditions.append(CustomPrice.family_id == family_id)
    if service_price_id is not None:
        conditions.append(CustomPrice.service_price_id == service_price_id)
    if statuses:
        conditions.append(_status(today).in_(set(statuses)))
    found = (
        await session.execute(
            query.add_columns(func.count().over().label("total"))
            .where(*conditions)
            .order_by(*order)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    if found:
        return [_row(row) for row in found], int(found[0].total)
    total = await session.scalar(
        select(func.count(CustomPrice.id))
        .join(ServicePrice, ServicePrice.id == CustomPrice.service_price_id)
        .where(ServicePrice.pricing_model.in_(CATALOG_MODELS), *conditions)
    )
    return [], int(total or 0)


async def get_custom_price(
    session: AsyncSession,
    names: CatalogNames,
    targets: TargetNames,
    custom_price_id: uuid.UUID,
    *,
    today: date,
) -> CustomPriceRow:
    """One custom price (``404 not_found``). One statement."""
    query, _ = _query(names, targets, today)
    found = (
        await session.execute(query.where(CustomPrice.id == custom_price_id))
    ).first()
    if found is None:
        raise RecordNotFoundError()
    return _row(found)


async def service_price_model(
    session: AsyncSession, service_price_id: uuid.UUID
) -> str | None:
    """The pricing model of a catalog item, or ``None`` when unknown."""
    model: str | None = await session.scalar(
        select(ServicePrice.pricing_model).where(ServicePrice.id == service_price_id)
    )
    return model


def _overlapping(
    *,
    service_price_id: uuid.UUID,
    student_id: uuid.UUID | None,
    family_id: uuid.UUID | None,
    effective_from: date,
    expires_on: date | None,
) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = [
        CustomPrice.service_price_id == service_price_id,
        CustomPrice.student_id == student_id
        if student_id is not None
        else CustomPrice.family_id == family_id,
        or_(
            CustomPrice.expires_on.is_(None),
            CustomPrice.expires_on >= effective_from,
        ),
    ]
    if expires_on is not None:
        conditions.append(CustomPrice.effective_from <= expires_on)
    return conditions


async def _flush(session: AsyncSession) -> None:
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError as exc:
        message = str(exc.orig)
        if (
            CUSTOM_PRICE_STUDENT_OVERLAP in message
            or CUSTOM_PRICE_FAMILY_OVERLAP in message
        ):
            raise CustomPriceOverlapError() from None
        raise


async def create_custom_price(
    session: AsyncSession, body: CustomPriceCreate
) -> uuid.UUID:
    """Insert a custom price (E3; after the workflow's date and reference
    checks): ``409 custom_price_overlap`` → insert."""
    clash = await session.scalar(
        select(CustomPrice.id)
        .where(
            *_overlapping(
                service_price_id=body.service_price_id,
                student_id=body.student_id,
                family_id=body.family_id,
                effective_from=body.effective_from,
                expires_on=body.expires_on,
            )
        )
        .limit(1)
    )
    if clash is not None:
        raise CustomPriceOverlapError()
    custom = CustomPrice(
        service_price_id=body.service_price_id,
        student_id=body.student_id,
        family_id=body.family_id,
        effective_from=body.effective_from,
        expires_on=body.expires_on,
        amount=body.amount,
    )
    session.add(custom)
    await _flush(session)
    return custom.id


async def _locked(session: AsyncSession, custom_price_id: uuid.UUID) -> CustomPrice:
    custom = await session.scalar(
        select(CustomPrice)
        .where(CustomPrice.id == custom_price_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if custom is None:
        raise RecordNotFoundError()
    return custom


async def update_custom_price(
    session: AsyncSession,
    custom_price_id: uuid.UUID,
    body: CustomPriceUpdate,
    *,
    today: date,
) -> None:
    """End or re-open a custom price (E5): the row ``FOR UPDATE`` (``404``)
    → ``409 version_conflict`` → unchanged: nothing written → ``422
    custom_price_expiry_invalid`` → ``409 custom_price_overlap`` → the
    update."""
    custom = await _locked(session, custom_price_id)
    ensure_version(custom, body.version)
    if body.expires_on == custom.expires_on:
        return
    if body.expires_on is not None:
        latest = await session.scalar(
            select(func.max(Charge.service_date)).where(
                Charge.custom_price_id == custom.id
            )
        )
        if (
            body.expires_on < today
            or body.expires_on < custom.effective_from
            or (latest is not None and body.expires_on < latest)
        ):
            raise CustomPriceExpiryInvalidError()
    clash = await session.scalar(
        select(CustomPrice.id)
        .where(
            CustomPrice.id != custom.id,
            *_overlapping(
                service_price_id=custom.service_price_id,
                student_id=custom.student_id,
                family_id=custom.family_id,
                effective_from=custom.effective_from,
                expires_on=body.expires_on,
            ),
        )
        .limit(1)
    )
    if clash is not None:
        raise CustomPriceOverlapError()
    custom.expires_on = body.expires_on
    await _flush(session)


async def delete_custom_price(
    session: AsyncSession, custom_price_id: uuid.UUID, *, today: date
) -> None:
    """Delete an upcoming, unused custom price (E6): the row ``FOR UPDATE``
    (``404``) → ``409 custom_price_in_effect`` → delete; the guard's 42501
    → ``409 custom_price_in_use``."""
    custom = await _locked(session, custom_price_id)
    if custom.effective_from <= today:
        raise CustomPriceInEffectError()
    await session.delete(custom)
    try:
        await session.flush()
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == INSUFFICIENT_PRIVILEGE:
            raise CustomPriceInUseError() from None
        raise
