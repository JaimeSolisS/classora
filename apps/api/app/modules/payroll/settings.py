"""The payroll settings singleton (#106 B; data-model §11.1; ADR-0017 E2, E6,
E7).

**The start floor.** ``payroll_settings.payroll_starts_on`` is a center-local
date or ``NULL``. While it is ``NULL`` payroll is off and no period opens.

**The locks** (#106 E6). Billing lets its floor move freely, but payroll can't:
an earlier floor creates days no period can ever cover, a later one strands
existing periods, and a new anchor shifts the biweekly calendar under existing
rows. So once any period exists the floor is locked (``409
payroll_start_locked``), and once a biweekly one exists the anchor is
(``409 biweekly_anchor_locked``). The proration basis stays editable.

- :func:`get_payroll_settings`: the detail, with both lock flags.
- :func:`payroll_starts_on`: the floor; with ``lock=True`` the row is read
  ``FOR SHARE``, so a ``PATCH`` and the job wait for each other.
- :func:`biweekly_anchor`, :func:`salary_proration_basis`: the other readers.
- :func:`update_payroll_settings`: the row ``FOR UPDATE`` → ``409
  version_conflict`` → unchanged (no write, no event, no version bump) → the
  locks → the update (one ``update`` audit event, ``version + 1``). A refused
  field stops the whole update.

A ``PATCH`` queues no job (#106 E7): the daily run is the only trigger, which
leaves staff until 07:50 UTC to correct a wrong floor.

Every function runs in the caller's Unit of Work and never commits.
"""

from datetime import date
from typing import Final, cast

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import ensure_version
from app.modules.payroll.errors import (
    BiweeklyAnchorLockedError,
    PayrollStartLockedError,
)
from app.modules.payroll.models import PayrollPeriod, PayrollSettings
from app.modules.payroll.schemas import (
    PayrollSettingsDetail,
    PayrollSettingsUpdate,
    ProrationBasis,
)

BIWEEKLY: Final = "biweekly"


def _missing() -> RuntimeError:  # pragma: no cover - migration 0073 inserts it
    return RuntimeError("payroll_settings has no row (migration 0073 inserts it)")


async def _locks(session: AsyncSession) -> tuple[bool, bool]:
    """``(start_locked, anchor_locked)``, one statement."""
    row = (
        await session.execute(
            select(
                exists(select(PayrollPeriod.id)).label("start"),
                exists(
                    select(PayrollPeriod.id).where(
                        PayrollPeriod.pay_frequency == BIWEEKLY
                    )
                ).label("anchor"),
            )
        )
    ).one()
    return bool(row.start), bool(row.anchor)


def _detail(
    row: PayrollSettings, *, start_locked: bool, anchor_locked: bool
) -> PayrollSettingsDetail:
    return PayrollSettingsDetail(
        payroll_starts_on=row.payroll_starts_on,
        biweekly_anchor_on=row.biweekly_anchor_on,
        salary_proration_basis=cast(ProrationBasis, row.salary_proration_basis),
        start_locked=start_locked,
        anchor_locked=anchor_locked,
        version=row.version,
        updated_at=row.updated_at,
    )


async def get_payroll_settings(session: AsyncSession) -> PayrollSettingsDetail:
    """The payroll settings (two statements, no lock)."""
    row = await session.scalar(select(PayrollSettings))
    if row is None:  # pragma: no cover - migration 0073 inserts it
        raise _missing()
    start_locked, anchor_locked = await _locks(session)
    return _detail(row, start_locked=start_locked, anchor_locked=anchor_locked)


async def payroll_starts_on(
    session: AsyncSession, *, lock: bool = False
) -> date | None:
    """The payroll start floor (``None``: payroll is off). One statement;
    ``lock=True`` reads the row ``FOR SHARE``."""
    query = select(PayrollSettings.singleton, PayrollSettings.payroll_starts_on)
    if lock:
        query = query.with_for_update(read=True)
    row = (await session.execute(query)).first()
    if row is None:  # pragma: no cover - migration 0073 inserts it
        raise _missing()
    floor: date | None = row.payroll_starts_on
    return floor


async def biweekly_anchor(session: AsyncSession) -> date:
    """The Monday the biweekly blocks count from."""
    anchor: date | None = await session.scalar(
        select(PayrollSettings.biweekly_anchor_on)
    )
    if anchor is None:  # pragma: no cover - migration 0073 inserts it
        raise _missing()
    return anchor


async def salary_proration_basis(session: AsyncSession) -> str:
    """``calendar_days`` or ``weekdays`` (ADR-0017 E7, for #108)."""
    basis: str | None = await session.scalar(
        select(PayrollSettings.salary_proration_basis)
    )
    if basis is None:  # pragma: no cover - migration 0073 inserts it
        raise _missing()
    return basis


async def update_payroll_settings(
    session: AsyncSession, body: PayrollSettingsUpdate
) -> PayrollSettingsDetail:
    """The order of the module docstring. The locks are read after the row
    lock, so a ``PATCH`` that waited for the job sees the periods it opened."""
    row = await session.scalar(
        select(PayrollSettings)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:  # pragma: no cover - migration 0073 inserts it
        raise _missing()
    ensure_version(row, body.version)
    start_locked, anchor_locked = await _locks(session)
    start_changes = row.payroll_starts_on != body.payroll_starts_on
    anchor_changes = row.biweekly_anchor_on != body.biweekly_anchor_on
    basis_changes = row.salary_proration_basis != body.salary_proration_basis
    if not (start_changes or anchor_changes or basis_changes):
        return _detail(row, start_locked=start_locked, anchor_locked=anchor_locked)
    if start_changes and start_locked:
        raise PayrollStartLockedError()
    if anchor_changes and anchor_locked:
        raise BiweeklyAnchorLockedError()
    row.payroll_starts_on = body.payroll_starts_on
    row.biweekly_anchor_on = body.biweekly_anchor_on
    row.salary_proration_basis = body.salary_proration_basis
    await session.flush()
    await session.refresh(row, ["updated_at"])
    return _detail(row, start_locked=start_locked, anchor_locked=anchor_locked)
