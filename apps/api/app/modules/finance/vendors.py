"""Vendors (#114 C, E; data-model §12.2).

Every function runs in the caller's Unit of Work and never commits.

The name is the natural key, unique ignoring case (#114 E4): a savepoint maps
the unique index to ``409 vendor_exists``, so a double click is a ``409``, not
a second vendor, and no ``Idempotency-Key`` is needed. No label rule applies.
Deactivation always passes in #114 (E2). :func:`usable_vendor` reads the row
``FOR SHARE`` (E7), like :func:`~app.modules.finance.categories.usable_category`.
"""

import uuid

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import RecordNotFoundError
from app.core.models import ensure_version
from app.core.pagination import OffsetPage, OffsetParams
from app.modules.finance.errors import (
    VendorExistsError,
    VendorInactiveError,
    VendorUnknownError,
)
from app.modules.finance.models import VENDOR_NAME_KEY, Vendor
from app.modules.finance.schemas import VendorCreate, VendorItem, VendorUpdate


def _item(row: Vendor) -> VendorItem:
    return VendorItem(
        id=row.id,
        name=row.name,
        contact=row.contact,
        is_active=row.is_active,
        version=row.version,
    )


async def _flush(session: AsyncSession, row: Vendor) -> None:
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError as exc:
        if VENDOR_NAME_KEY in str(exc.orig):
            raise VendorExistsError() from None
        raise


def _like(q: str) -> str:
    """``q`` as an ``ILIKE`` pattern that matches ``%``, ``_`` and ``\\``
    literally (the ``billing/discount_config.py`` escape)."""
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


async def list_vendors(
    session: AsyncSession, *, q: str | None, active: bool | None, page: OffsetParams
) -> OffsetPage[VendorItem]:
    """A page of vendors, active first, then by name ignoring case, then id,
    with the total. One statement (the total is a window count; a count when
    the page is past the end)."""
    conditions: list[ColumnElement[bool]] = []
    if q:
        conditions.append(Vendor.name.ilike(_like(q), escape="\\"))
    if active is not None:
        conditions.append(Vendor.is_active.is_(active))
    found = (
        await session.execute(
            select(Vendor, func.count().over().label("total"))
            .where(*conditions)
            .order_by(Vendor.is_active.desc(), func.lower(Vendor.name), Vendor.id)
            .limit(page.limit)
            .offset(page.offset)
        )
    ).all()
    if found:
        total = int(found[0].total)
    else:
        total = int(
            await session.scalar(select(func.count(Vendor.id)).where(*conditions)) or 0
        )
    return OffsetPage(
        items=[_item(row.Vendor) for row in found],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


async def add_vendor(session: AsyncSession, body: VendorCreate) -> VendorItem:
    """A new vendor: ``409 vendor_exists`` on a taken name."""
    row = Vendor(name=body.name, contact=body.contact)
    await _flush(session, row)
    await session.refresh(row, ["is_active", "version"])
    return _item(row)


async def change_vendor(
    session: AsyncSession, vendor_id: uuid.UUID, body: VendorUpdate
) -> VendorItem:
    """The row ``FOR UPDATE`` (``404``) → ``409 version_conflict`` → the update
    (``409 vendor_exists``). ``contact`` sent as ``null`` clears it; left out,
    it stays. Unchanged: nothing written, no new version."""
    row = await session.scalar(
        select(Vendor)
        .where(Vendor.id == vendor_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise RecordNotFoundError()
    ensure_version(row, body.version)
    if body.name is not None:
        row.name = body.name
    if "contact" in body.model_fields_set:
        row.contact = body.contact
    if body.is_active is not None:
        row.is_active = body.is_active
    await _flush(session, row)
    return _item(row)


async def usable_vendor(session: AsyncSession, vendor_id: uuid.UUID) -> Vendor:
    """The vendor a new expense or template may use, read ``FOR SHARE``:
    ``422 vendor_unknown``, ``409 vendor_inactive``."""
    row = await session.scalar(
        select(Vendor)
        .where(Vendor.id == vendor_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise VendorUnknownError()
    if not row.is_active:
        raise VendorInactiveError()
    return row
