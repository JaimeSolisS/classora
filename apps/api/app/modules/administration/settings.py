"""The center's business settings (#37; spec §9, §16; ADR-0004).

One record (``business_settings``, inserted by migration 0020) with the
center name, contact details and the **center time zone**, plus the weekly
operating hours (``operating_hours``, one interval per open weekday).

**Reads** (every signed-in user): :func:`get_business_settings`, two
statements. Other modules read the zone with :func:`get_center_time_zone`
and "today" with :func:`center_today` (today's date in the center's zone,
from ``app.core.clock.now()``). The zone is cached in ``session.info`` for
the Unit of Work, so a request reads it at most once.

**Writes** (``settings.manage``): :func:`update_business_settings`. Order
after the body's ``422``: a time-zone change (a sent ``time_zone`` that
differs from the stored one) needs a recent reauthentication (``403
reauth_required``, ADR-0007 D10) → the row is locked ``FOR UPDATE`` and its
``version`` checked (``409 version_conflict``) → for a time-zone change, every
registered time-zone dependency check (``409 tz_change_requires_migration``)
→ the update. Any change bumps ``business_settings.version`` once (an
hours-only change touches only ``updated_at``, which the audit leaves out); a
body that changes nothing changes no version and writes no audit event.

**Time-zone dependencies (#37 D, E1 (a)).** *Scheduled data* means rows whose
stored instants were computed from the center's local time (class sessions
and recurrence series, M4-02 #47 and M4-08 #53). Rows that hold only dates or
local wall-clock times (periods, closures, availability, operating hours)
keep their meaning when the zone changes and don't count. A module that owns
scheduled data registers a check with :func:`register_time_zone_dependency`
when the app is built; the checks run under the settings row lock. Those
modules read the zone with ``get_center_time_zone(session, for_share=True)``
while they materialize instants, so a concurrent zone change waits for them.

**Audit:** automatic only (#22): ``update`` events of ``business_settings``
and ``create``/``update``/``delete`` events of ``operating_hours``.

Every function runs in the caller's Unit of Work and never commits.
"""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import date, time
from types import MappingProxyType
from typing import Any, Final

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import today_in
from app.core.models import ensure_version
from app.core.reauth import RecentAuthGuard
from app.modules.administration.errors import TzChangeRequiresMigrationError
from app.modules.administration.schemas import (
    BusinessSettingsDetail,
    BusinessSettingsUpdate,
    OperatingHoursItem,
)
from app.modules.administration.settings_models import (
    BusinessSettings,
    OperatingHours,
)

TimeZoneDependency = Callable[[AsyncSession], Awaitable[bool]]
"""A check that returns true when its module holds scheduled data."""

ZONE_INFO_KEY: Final = "classora.center_time_zone"
"""``session.info`` key of the cached center time zone."""

CONTACT_FIELDS: Final = ("center_name", "contact_phone", "contact_email", "address")

_dependencies: dict[str, TimeZoneDependency] = {}


def register_time_zone_dependency(name: str, has_data: TimeZoneDependency) -> None:
    """Register a module's scheduled-data check under ``name``.

    Raises ``ValueError`` when ``name`` is already registered with another
    check. Registering the same check again under its name changes nothing,
    so building the app more than once in a process (tests, the OpenAPI
    export) is safe.
    """
    registered = _dependencies.get(name)
    if registered is not None and registered is not has_data:
        raise ValueError(f"time-zone dependency already registered: {name!r}")
    _dependencies[name] = has_data


def unregister_time_zone_dependency(name: str) -> None:
    """Remove a registered check (tests only); an unknown name is ignored."""
    _dependencies.pop(name, None)


def time_zone_dependencies() -> Mapping[str, TimeZoneDependency]:
    """The registered checks, by name (read-only)."""
    return MappingProxyType(dict(_dependencies))


# --- Reads -----------------------------------------------------------------------


def _hhmm(value: time) -> str:
    return value.strftime("%H:%M")


def _parse(value: str) -> time:
    return time.fromisoformat(value)


def _detail(
    settings: BusinessSettings, hours: list[OperatingHours]
) -> BusinessSettingsDetail:
    return BusinessSettingsDetail(
        center_name=settings.center_name,
        contact_phone=settings.contact_phone,
        contact_email=settings.contact_email,
        address=settings.address,
        time_zone=settings.time_zone,
        operating_hours=[
            OperatingHoursItem(
                weekday=row.weekday,
                opens_at=_hhmm(row.opens_at),
                closes_at=_hhmm(row.closes_at),
            )
            for row in sorted(hours, key=lambda row: row.weekday)
        ],
        version=settings.version,
        updated_at=settings.updated_at,
    )


def _missing() -> RuntimeError:  # pragma: no cover - migration 0020 inserts it
    return RuntimeError("the business_settings row is missing (run make migrate)")


async def _hours(session: AsyncSession) -> list[OperatingHours]:
    return list(
        (
            await session.scalars(
                select(OperatingHours).order_by(OperatingHours.weekday)
            )
        ).all()
    )


async def get_business_settings(session: AsyncSession) -> BusinessSettingsDetail:
    """The settings and the operating hours (two statements)."""
    settings = await session.scalar(select(BusinessSettings))
    if settings is None:  # pragma: no cover - migration 0020 inserts it
        raise _missing()
    return _detail(settings, await _hours(session))


async def get_center_time_zone(
    session: AsyncSession, *, for_share: bool = False
) -> str:
    """The center's IANA time zone.

    Cached in ``session.info`` for the Unit of Work. ``for_share=True`` reads
    the row ``FOR SHARE`` (never from the cache), so a concurrent zone change
    waits until this transaction ends (M4-02, M4-08).
    """
    if not for_share and ZONE_INFO_KEY in session.info:
        cached: str = session.info[ZONE_INFO_KEY]
        return cached
    query = select(BusinessSettings.time_zone)
    if for_share:
        query = query.with_for_update(read=True)
    zone = await session.scalar(query)
    if zone is None:  # pragma: no cover - migration 0020 inserts it
        raise _missing()
    session.info[ZONE_INFO_KEY] = zone
    return zone


async def center_today(session: AsyncSession) -> date:
    """Today's calendar date in the center's time zone
    (``app.core.clock.now()`` there)."""
    return today_in(await get_center_time_zone(session))


# --- Writes ----------------------------------------------------------------------


@dataclass
class _HoursPlan:
    """What a whole-week replace changes."""

    updates: list[tuple[OperatingHours, time, time]]
    inserts: list[OperatingHours]
    deletes: list[OperatingHours]

    @property
    def changes_anything(self) -> bool:
        return bool(self.updates or self.inserts or self.deletes)


def _plan_hours(
    current: list[OperatingHours], wanted: list[OperatingHoursItem]
) -> _HoursPlan:
    by_weekday = {row.weekday: row for row in current}
    plan = _HoursPlan(updates=[], inserts=[], deletes=[])
    for item in wanted:
        opens_at, closes_at = _parse(item.opens_at), _parse(item.closes_at)
        row = by_weekday.get(item.weekday)
        if row is None:
            plan.inserts.append(
                OperatingHours(
                    weekday=item.weekday, opens_at=opens_at, closes_at=closes_at
                )
            )
        elif (row.opens_at, row.closes_at) != (opens_at, closes_at):
            plan.updates.append((row, opens_at, closes_at))
    sent = {item.weekday for item in wanted}
    plan.deletes = [row for row in current if row.weekday not in sent]
    return plan


async def _settings_for_update(session: AsyncSession) -> BusinessSettings:
    settings = await session.scalar(
        select(BusinessSettings)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if settings is None:  # pragma: no cover - migration 0020 inserts it
        raise _missing()
    return settings


async def _ensure_no_scheduled_data(session: AsyncSession) -> None:
    for has_data in list(_dependencies.values()):
        if await has_data(session):
            raise TzChangeRequiresMigrationError()


async def update_business_settings(
    session: AsyncSession, body: BusinessSettingsUpdate, *, guard: RecentAuthGuard
) -> BusinessSettingsDetail:
    """Change the sent fields; ``operating_hours`` replaces the whole week.

    Order (after the body's ``422``): ``403 reauth_required`` (only when the
    time zone changes) → ``409 version_conflict`` (the row is locked ``FOR
    UPDATE`` first) → ``409 tz_change_requires_migration`` (only when the
    time zone changes) → the update.
    """
    sent = body.model_fields_set
    if "time_zone" in sent and body.time_zone is not None:
        stored = await session.scalar(select(BusinessSettings.time_zone))
        if body.time_zone != stored:
            guard.ensure()
    settings = await _settings_for_update(session)
    ensure_version(settings, body.version)
    changes: dict[str, Any] = {
        name: getattr(body, name)
        for name in (*CONTACT_FIELDS, "time_zone")
        if name in sent and getattr(body, name) != getattr(settings, name)
    }
    if "time_zone" in changes:
        # Already checked above against the unlocked value; checked again
        # against the locked row, so the rule never depends on that read.
        guard.ensure()
        await _ensure_no_scheduled_data(session)
    current = await _hours(session)
    plan = _HoursPlan(updates=[], inserts=[], deletes=[])
    if "operating_hours" in sent and body.operating_hours is not None:
        plan = _plan_hours(current, body.operating_hours)
    if not changes and not plan.changes_anything:
        return _detail(settings, current)

    # One versioned UPDATE of the settings row: the changed columns, or, for
    # an hours-only change, only updated_at (left out of the audit, so no
    # business_settings event is written).
    if changes:
        for name, value in changes.items():
            setattr(settings, name, value)
    else:
        settings.updated_at = func.now()
    await session.flush()
    await session.refresh(settings, ["updated_at"])

    for row, opens_at, closes_at in plan.updates:
        row.opens_at = opens_at
        row.closes_at = closes_at
    session.add_all(plan.inserts)
    for row in plan.deletes:
        await session.delete(row)
    await session.flush()
    session.info[ZONE_INFO_KEY] = settings.time_zone
    deleted = {row.weekday for row in plan.deletes}
    hours = [row for row in current if row.weekday not in deleted] + plan.inserts
    return _detail(settings, hours)
