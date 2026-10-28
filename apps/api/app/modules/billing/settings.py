"""The billing settings singleton and the package coverage seam (#91 B1,
B3, E1; data-model §3.2; ADR-0006 E1, E10).

**Billing start floor.** ``billing_settings.billing_starts_on`` (a
center-local date, or ``NULL``). While it is ``NULL`` the sweepers post
nothing; afterwards they post only for service dates on or after it. Any
date may be set, past included (a go-live on the 15th may bill from the
1st); moving it later or back to ``NULL`` pauses posting for the dates it
excludes and never touches posted charges.

- :func:`get_billing_settings`: the detail (one statement, no lock).
- :func:`billing_starts_on`: the floor; with ``lock=True`` the row is read
  ``FOR SHARE`` (never cached), so a concurrent change waits until the
  caller's transaction ends. The sweepers take it after their own
  scheduling locks and before the account lock (data-model §6).
- :func:`update_billing_settings`: the row ``FOR UPDATE`` → ``409
  version_conflict`` → unchanged (no write, no event, no version bump; the
  #37 pattern) → the update (one ``update`` audit event with the old and
  new date, ``version + 1``).

**Package coverage** (:func:`package_covered_keys`): the no-op seam #94
implements (ADR-0006 E10).

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from collections.abc import Sequence
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import ensure_version
from app.modules.billing.models import BillingSettings
from app.modules.billing.schemas import (
    BillingSettingsDetail,
    BillingSettingsUpdate,
    CoverageQuery,
)


def _missing() -> RuntimeError:  # pragma: no cover - migration 0062 inserts it
    return RuntimeError("billing_settings has no row (migration 0062 inserts it)")


def _detail(row: BillingSettings) -> BillingSettingsDetail:
    return BillingSettingsDetail(
        billing_starts_on=row.billing_starts_on,
        version=row.version,
        updated_at=row.updated_at,
    )


async def get_billing_settings(session: AsyncSession) -> BillingSettingsDetail:
    """The billing settings (one statement, no lock)."""
    row = await session.scalar(select(BillingSettings))
    if row is None:  # pragma: no cover - migration 0062 inserts it
        raise _missing()
    return _detail(row)


async def billing_starts_on(
    session: AsyncSession, *, lock: bool = False
) -> date | None:
    """The billing start floor (``None``: billing is off). One statement;
    ``lock=True`` reads the row ``FOR SHARE``."""
    query = select(BillingSettings.singleton, BillingSettings.billing_starts_on)
    if lock:
        query = query.with_for_update(read=True)
    row = (await session.execute(query)).first()
    if row is None:  # pragma: no cover - migration 0062 inserts it
        raise _missing()
    floor: date | None = row.billing_starts_on
    return floor


async def update_billing_settings(
    session: AsyncSession, body: BillingSettingsUpdate
) -> BillingSettingsDetail:
    """Set (or clear) the floor: the row ``FOR UPDATE`` → ``409
    version_conflict`` → unchanged: the current detail, nothing written →
    the update."""
    row = await session.scalar(
        select(BillingSettings)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:  # pragma: no cover - migration 0062 inserts it
        raise _missing()
    ensure_version(row, body.version)
    if row.billing_starts_on == body.billing_starts_on:
        return _detail(row)
    row.billing_starts_on = body.billing_starts_on
    await session.flush()
    await session.refresh(row, ["updated_at"])
    return _detail(row)


async def package_covered_keys(
    session: AsyncSession, items: Sequence[CoverageQuery]
) -> set[tuple[uuid.UUID, uuid.UUID]]:
    """The ``(class_session_id, student_id)`` keys of ``items`` that a
    package covers, so they take the package path instead of a session
    charge (ADR-0006 E10). Always empty in #91: **#94 implements it** (an
    eligible, unexpired package with credit for the student and the
    subject on the service date).

    The session sweeper and the unbilled list (#91) both call it, and #94's
    consumption must too: invariant I9 (at most one of {session charge,
    package consumption} per key) depends on both sides asking this one
    function."""
    del session, items
    return set()
