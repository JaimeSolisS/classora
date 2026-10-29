"""Price catalog reads and writes (#89 B2–B8; E3, E5, E7).

The routes are ``app.workflows.price_catalog_router``; the workflow
(``app.workflows.price_catalog``) checks the references in other modules
and passes the center's local ``today``. Every function runs in the
caller's Unit of Work and never commits.

**Scope of the routes (E5):** ``per_session``, ``full_course`` and (since
#93 B1) ``per_period`` items are listed, read or written here
(:data:`CATALOG_MODELS`); ``package`` rows (#94) are invisible (``404``). A
``per_period`` item carries its ``period_frequency`` (part of the key, K2).

**Writes:**

- :func:`create_service_price`: ``409 service_price_exists`` when the key is
  taken (a check, and the unique index mapped by name for the concurrent
  case) → the item and its first version (any ``effective_from``: E3).
- :func:`add_price_version`: the item row ``FOR UPDATE`` (``404``; it
  serializes the writers of one item) → when the item has versions,
  ``effective_from`` must be on or after ``today`` **and** later than the
  latest version (``422 price_effective_from_invalid``) → insert. An item
  without versions (its only upcoming version was deleted) takes any date
  again, like a first version.
- :func:`delete_price_version`: the item row ``FOR UPDATE`` → ``404`` for a
  version of another item → ``409 price_version_in_effect`` unless it is
  upcoming (``effective_from > today``) → delete; the guard's 42501
  (a referenced version, #90/#94) is ``409 price_version_in_use``.

Versions are never edited (no update function), and items are never
deleted: retiring a price is a ``null`` version.

**Reads** join other modules' names through unexecuted ``SELECT``s the
workflow passes in (:class:`CatalogNames`, the #80 F2 pattern), so a page
and its names and order come in one statement.
"""

import uuid
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final

from sqlalchemy import (
    ColumnElement,
    Select,
    case,
    func,
    or_,
    select,
)
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import RecordNotFoundError
from app.core.money import Money
from app.modules.billing.errors import (
    PriceEffectiveFromInvalidError,
    PriceVersionInEffectError,
    PriceVersionInUseError,
    ServicePriceExistsError,
)
from app.modules.billing.models import CATALOG_KEY_INDEX, PriceVersion, ServicePrice
from app.modules.billing.schemas import PriceVersionItem, PriceVersionStatus

CATALOG_MODELS: Final = ("per_session", "full_course", "per_period")
"""The pricing models the #89 routes list, read and write (E5; ``per_period``
since #93 B1)."""
CUSTOM_PRICE_MODELS: Final = ("per_session", "full_course")
"""The pricing models a custom price may be for (#95; #93 K8: not
``per_period``)."""

INSUFFICIENT_PRIVILEGE: Final = "42501"

_CLASS_TYPE_ORDER: Final = {"individual": 0, "group": 1, "course": 2}
_MODEL_ORDER: Final = {"per_session": 0, "full_course": 1, "per_period": 2}
_FREQUENCY_ORDER: Final = {"weekly": 0, "biweekly": 1, "monthly": 2}


@dataclass(frozen=True)
class CatalogKey:
    """The key of a catalog item (data-model §3.4)."""

    class_type: str
    pricing_model: str
    subject_id: uuid.UUID
    education_level_id: uuid.UUID | None
    duration_minutes: int | None
    cohort_id: uuid.UUID | None
    group_id: uuid.UUID | None
    period_frequency: str | None = None
    """Set iff ``pricing_model = 'per_period'`` (#93 K2)."""


@dataclass(frozen=True)
class CatalogNames:
    """Unexecuted ``SELECT``s of other modules' names, joined as subqueries
    (ADR-0002: the workflow builds them through each module's service).

    - ``subjects``: ``id``, ``name``, ``education_level_id``,
      ``education_level_name``, ``education_level_sort_order``;
    - ``levels``: ``id``, ``name``, ``sort_order``;
    - ``cohorts`` and ``groups``: ``id``, ``name``.
    """

    subjects: Select[*tuple[Any, ...]]
    levels: Select[*tuple[Any, ...]]
    cohorts: Select[*tuple[Any, ...]]
    groups: Select[*tuple[Any, ...]]


@dataclass(frozen=True)
class CatalogItemRow:
    """One item with the names of what it points at (empty when a row was
    not found, which a foreign key prevents)."""

    id: uuid.UUID
    class_type: str
    pricing_model: str
    subject_id: uuid.UUID
    subject_name: str
    subject_level_id: uuid.UUID
    subject_level_name: str
    education_level_id: uuid.UUID | None
    education_level_name: str
    duration_minutes: int | None
    cohort_id: uuid.UUID | None
    cohort_name: str
    group_id: uuid.UUID | None
    group_name: str
    created_at: datetime
    period_frequency: str | None = None


@dataclass(frozen=True)
class CurrentAndNext:
    """The version in effect today and the earliest upcoming one."""

    current: PriceVersionItem | None
    next: PriceVersionItem | None


# --- Writes --------------------------------------------------------------------------


def _key_conditions(key: CatalogKey) -> list[ColumnElement[bool]]:
    return [
        ServicePrice.class_type == key.class_type,
        ServicePrice.pricing_model == key.pricing_model,
        ServicePrice.subject_id == key.subject_id,
        *(
            getattr(ServicePrice, name).is_(None)
            if getattr(key, name) is None
            else getattr(ServicePrice, name) == getattr(key, name)
            for name in (
                "education_level_id",
                "duration_minutes",
                "cohort_id",
                "group_id",
                "period_frequency",
            )
        ),
    ]


async def _existing_id(session: AsyncSession, key: CatalogKey) -> uuid.UUID | None:
    found: uuid.UUID | None = await session.scalar(
        select(ServicePrice.id).where(*_key_conditions(key)).limit(1)
    )
    return found


async def create_service_price(
    session: AsyncSession,
    key: CatalogKey,
    *,
    effective_from: date,
    amount: Money,
) -> uuid.UUID:
    """A new item and its first version (B3). ``409 service_price_exists``
    (with the existing id) when the key is taken, also by a concurrent
    request (the unique index, flushed in a savepoint)."""
    existing = await _existing_id(session, key)
    if existing is not None:
        raise ServicePriceExistsError(existing)
    item = ServicePrice(
        class_type=key.class_type,
        pricing_model=key.pricing_model,
        subject_id=key.subject_id,
        education_level_id=key.education_level_id,
        duration_minutes=key.duration_minutes,
        cohort_id=key.cohort_id,
        group_id=key.group_id,
        period_frequency=key.period_frequency,
    )
    try:
        async with session.begin_nested():
            session.add(item)
            await session.flush()
    except IntegrityError as exc:
        if CATALOG_KEY_INDEX in str(exc.orig):
            raise ServicePriceExistsError(await _existing_id(session, key)) from None
        raise
    session.add(
        PriceVersion(
            service_price_id=item.id, effective_from=effective_from, amount=amount
        )
    )
    await session.flush()
    return item.id


async def _lock_item(session: AsyncSession, service_price_id: uuid.UUID) -> None:
    found = await session.scalar(
        select(ServicePrice.id)
        .where(
            ServicePrice.id == service_price_id,
            ServicePrice.pricing_model.in_(CATALOG_MODELS),
        )
        .with_for_update()
    )
    if found is None:
        raise RecordNotFoundError()


async def add_price_version(
    session: AsyncSession,
    service_price_id: uuid.UUID,
    *,
    effective_from: date,
    amount: Money | None,
    today: date,
) -> uuid.UUID:
    """A new version (B5; E3): the item lock → the date rule → insert."""
    await _lock_item(session, service_price_id)
    latest = await session.scalar(
        select(func.max(PriceVersion.effective_from)).where(
            PriceVersion.service_price_id == service_price_id
        )
    )
    if latest is not None and (effective_from < today or effective_from <= latest):
        raise PriceEffectiveFromInvalidError()
    version = PriceVersion(
        service_price_id=service_price_id,
        effective_from=effective_from,
        amount=amount,
    )
    session.add(version)
    await session.flush()
    return version.id


async def delete_price_version(
    session: AsyncSession,
    service_price_id: uuid.UUID,
    price_version_id: uuid.UUID,
    *,
    today: date,
) -> None:
    """Delete an upcoming version (B6; E2, E3)."""
    await _lock_item(session, service_price_id)
    version = await session.scalar(
        select(PriceVersion).where(
            PriceVersion.id == price_version_id,
            PriceVersion.service_price_id == service_price_id,
        )
    )
    if version is None:
        raise RecordNotFoundError()
    if version.effective_from <= today:
        raise PriceVersionInEffectError()
    await session.delete(version)
    try:
        await session.flush()
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == INSUFFICIENT_PRIVILEGE:
            raise PriceVersionInUseError() from None
        raise


# --- Reads ---------------------------------------------------------------------------


def _ordered_case(column: Any, order: dict[str, int]) -> ColumnElement[int]:
    return case(order, value=column, else_=len(order))


def catalog_query(names: CatalogNames) -> tuple[Select[Any], list[Any]]:
    """The named item select of the catalog reads and its B2 order (the
    item id last); #95's custom price reads extend it."""
    subjects = names.subjects.subquery("subject_names")
    levels = names.levels.subquery("level_names")
    cohorts = names.cohorts.subquery("cohort_names")
    groups = names.groups.subquery("group_names")
    query = (
        select(
            ServicePrice,
            subjects.c.name,
            subjects.c.education_level_id,
            subjects.c.education_level_name,
            levels.c.name,
            cohorts.c.name,
            groups.c.name,
        )
        .outerjoin(subjects, subjects.c.id == ServicePrice.subject_id)
        .outerjoin(levels, levels.c.id == ServicePrice.education_level_id)
        .outerjoin(cohorts, cohorts.c.id == ServicePrice.cohort_id)
        .outerjoin(groups, groups.c.id == ServicePrice.group_id)
        .where(ServicePrice.pricing_model.in_(CATALOG_MODELS))
    )
    scoped = or_(
        ServicePrice.cohort_id.is_not(None), ServicePrice.group_id.is_not(None)
    )
    order = [
        _ordered_case(ServicePrice.class_type, _CLASS_TYPE_ORDER),
        subjects.c.education_level_sort_order,
        func.lower(subjects.c.education_level_name),
        func.lower(subjects.c.name),
        _ordered_case(ServicePrice.pricing_model, _MODEL_ORDER),
        _ordered_case(ServicePrice.period_frequency, _FREQUENCY_ORDER),
        case((scoped, 1), else_=0),
        func.lower(func.coalesce(cohorts.c.name, groups.c.name)),
        case((ServicePrice.education_level_id.is_(None), 0), else_=1),
        levels.c.sort_order,
        ServicePrice.duration_minutes.asc().nulls_first(),
        ServicePrice.id,
    ]
    return query, order


def catalog_row(found: Any) -> CatalogItemRow:
    """The :class:`CatalogItemRow` of a :func:`catalog_query` row (its first
    seven columns)."""
    item: ServicePrice = found[0]
    assert item.subject_id is not None  # noqa: S101 (CATALOG_MODELS have one)
    return CatalogItemRow(
        id=item.id,
        class_type=item.class_type,
        pricing_model=item.pricing_model,
        subject_id=item.subject_id,
        subject_name=found[1] or "",
        subject_level_id=found[2],
        subject_level_name=found[3] or "",
        education_level_id=item.education_level_id,
        education_level_name=found[4] or "",
        duration_minutes=item.duration_minutes,
        cohort_id=item.cohort_id,
        cohort_name=found[5] or "",
        group_id=item.group_id,
        group_name=found[6] or "",
        created_at=item.created_at,
        period_frequency=item.period_frequency,
    )


async def list_service_prices(
    session: AsyncSession,
    names: CatalogNames,
    *,
    class_type: str | None,
    pricing_model: str | None,
    subject_id: uuid.UUID | None,
    limit: int,
    offset: int,
) -> tuple[list[CatalogItemRow], int]:
    """A page of ``per_session``/``full_course``/``per_period`` items in the
    B2 order (``per_period`` last, weekly → biweekly → monthly), and
    the total. One statement (the total is a window count), plus a count
    when the page is past the end."""
    query, order = catalog_query(names)
    conditions: list[ColumnElement[bool]] = []
    if class_type is not None:
        conditions.append(ServicePrice.class_type == class_type)
    if pricing_model is not None:
        conditions.append(ServicePrice.pricing_model == pricing_model)
    if subject_id is not None:
        conditions.append(ServicePrice.subject_id == subject_id)
    result = (
        await session.execute(
            query.add_columns(func.count().over())
            .where(*conditions)
            .order_by(*order)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    if result:
        return [catalog_row(found) for found in result], int(result[0][-1])
    total = await session.scalar(
        select(func.count(ServicePrice.id)).where(
            ServicePrice.pricing_model.in_(CATALOG_MODELS), *conditions
        )
    )
    return [], int(total or 0)


async def get_service_price(
    session: AsyncSession, names: CatalogNames, service_price_id: uuid.UUID
) -> CatalogItemRow:
    """One item (``404 not_found`` for an unknown id or a ``package`` row).
    One statement."""
    query, _ = catalog_query(names)
    found = (
        await session.execute(query.where(ServicePrice.id == service_price_id))
    ).first()
    if found is None:
        raise RecordNotFoundError()
    return catalog_row(found)


async def get_service_prices(
    session: AsyncSession, names: CatalogNames, ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, CatalogItemRow]:
    """The items with these ids (unknown or ``package`` ids are left out),
    by id (#93: the tuition plans' items). One statement (none for no
    id)."""
    if not ids:
        return {}
    query, _ = catalog_query(names)
    rows = await session.execute(query.where(ServicePrice.id.in_(set(ids))))
    return {found[0].id: catalog_row(found) for found in rows}


def _status(
    version: PriceVersion, current_id: uuid.UUID | None, today: date
) -> PriceVersionStatus:
    if version.effective_from > today:
        return "upcoming"
    return "current" if version.id == current_id else "past"


def _item(version: PriceVersion, status: PriceVersionStatus) -> PriceVersionItem:
    return PriceVersionItem(
        id=version.id,
        effective_from=version.effective_from,
        amount=version.amount,
        status=status,
        created_at=version.created_at,
    )


async def list_versions(
    session: AsyncSession, service_price_id: uuid.UUID, *, today: date
) -> list[PriceVersionItem]:
    """Every version of an item, newest ``effective_from`` first, with its
    status against ``today``. One statement."""
    versions = (
        await session.scalars(
            select(PriceVersion)
            .where(PriceVersion.service_price_id == service_price_id)
            .order_by(PriceVersion.effective_from.desc())
        )
    ).all()
    current_id = next((v.id for v in versions if v.effective_from <= today), None)
    return [_item(v, _status(v, current_id, today)) for v in versions]


def current_and_next_of(versions: Sequence[PriceVersionItem]) -> CurrentAndNext:
    """The current and the earliest upcoming version of a list from
    :func:`list_versions`."""
    current = next((v for v in versions if v.status == "current"), None)
    upcoming = [v for v in versions if v.status == "upcoming"]
    return CurrentAndNext(current=current, next=upcoming[-1] if upcoming else None)


async def current_and_next(
    session: AsyncSession, service_price_ids: Collection[uuid.UUID], *, today: date
) -> dict[uuid.UUID, CurrentAndNext]:
    """For each item, the version in effect on ``today`` and the earliest
    upcoming one. One statement (none for no ids): per item and side of
    ``today``, the version closest to it."""
    if not service_price_ids:
        return {}
    in_effect = PriceVersion.effective_from <= today
    closest = (
        func.row_number()
        .over(
            partition_by=(PriceVersion.service_price_id, in_effect),
            order_by=func.abs(PriceVersion.effective_from - today),
        )
        .label("closest")
    )
    ranked = (
        select(PriceVersion.id, closest)
        .where(PriceVersion.service_price_id.in_(set(service_price_ids)))
        .subquery("ranked")
    )
    versions = (
        await session.scalars(
            select(PriceVersion)
            .join(ranked, ranked.c.id == PriceVersion.id)
            .where(ranked.c.closest == 1)
        )
    ).all()
    found: dict[uuid.UUID, dict[str, PriceVersionItem]] = {}
    for version in versions:
        status: PriceVersionStatus = (
            "current" if version.effective_from <= today else "upcoming"
        )
        found.setdefault(version.service_price_id, {})[status] = _item(version, status)
    return {
        item_id: CurrentAndNext(
            current=found.get(item_id, {}).get("current"),
            next=found.get(item_id, {}).get("upcoming"),
        )
        for item_id in service_price_ids
    }
