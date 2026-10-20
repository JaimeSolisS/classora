"""Tutor availability (#45; spec §3; ADR-0004; re-exported by
``staff.service``).

A tutor publishes **recurring weekly windows** and **exceptions** (time off,
extra hours on given dates), all in the center's local wall-clock time. The
rules are the pure functions of :mod:`app.modules.staff.availability`.

**Who acts** (the routes already require ``tutor.view``): whoever may see the
tutor (#43 record policy) reads and checks; others get ``404``. The tutor
themselves and holders of ``tutor.manage`` change it
(:func:`~app.modules.staff.permissions.can_edit_availability`); another
viewer (an Academic Coordinator) gets ``403``.

**Weekly ``PUT`` order** (after the body's ``422``): ``404`` → ``403`` →
``409 version_conflict`` (the settings row is locked ``FOR UPDATE``; a
concurrent first change loses on its unique ``user_id``) → the impact check →
the update. Only the windows that changed are deleted and inserted (a
changed window is a delete plus an insert), the deletes flushed first so the
exclusion constraint never meets the old row. The settings row is created by
the first change (``version`` 0 before) or its version bumped once. A body
that changes nothing changes no version and writes no event.

**Exceptions** are created (``404`` → ``403`` → impact check for
``unavailable`` → insert) and deleted (``404`` → ``403`` → ``404`` when the
id is not one of this tutor's → impact check for ``available`` → delete),
never edited; they don't touch the weekly ``version``.

**Impact check** (``staff/assignment_impact.py``, the M4-18 seam): the weekly
``PUT`` asks only when it takes weekly time away
(:func:`~app.modules.staff.availability.uncovered_ranges`); a new
``unavailable`` exception and a deleted ``available`` one always ask. A
non-empty answer is ``409 availability_change_requires_approval`` and
nothing changes.

**For other modules** (M4-03 #48, M4-05, M4-06): :func:`is_tutor_available`
(local date and times) and :func:`is_tutor_available_at` (instants, split
into local pieces in the center zone). Neither checks the tutor role or the
account state.

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from collections.abc import Iterable, Sequence
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import PermissionDeniedError, Principal, RecordNotFoundError
from app.core.models import StaleVersionError, ensure_version
from app.modules.administration import service as administration
from app.modules.catalog import service as catalog
from app.modules.catalog.schemas import AvailabilityWindow
from app.modules.identity.schemas import RoleMember
from app.modules.staff.assignment_impact import AvailabilityImpactSource
from app.modules.staff.availability import (
    AvailabilityResult,
    DatedException,
    ExceptionKind,
    WeeklyWindow,
    evaluate,
    merge_results,
    to_local_intervals,
    uncovered_ranges,
)
from app.modules.staff.errors import AvailabilityChangeRequiresApprovalError
from app.modules.staff.models import (
    AVAILABLE,
    SETTINGS_USER_ID_UNIQUE,
    UNAVAILABLE,
    TutorAvailabilityException,
    TutorAvailabilitySettings,
    TutorAvailabilityWindow,
)
from app.modules.staff.permissions import can_edit_availability
from app.modules.staff.qualifications import visible_tutor
from app.modules.staff.schemas import (
    AvailabilityCheck,
    AvailabilityExceptionCreate,
    AvailabilityExceptionItem,
    TutorAvailabilityDetail,
    WeeklyAvailabilityUpdate,
)

type WindowKey = tuple[int, time, time]


def _hhmm(value: time | None) -> str | None:
    return value.strftime("%H:%M") if value is not None else None


def _key(window: TutorAvailabilityWindow) -> WindowKey:
    return (window.weekday, window.starts_at, window.ends_at)


def _wanted_key(window: AvailabilityWindow) -> WindowKey:
    return (
        window.weekday,
        time.fromisoformat(window.starts_at),
        time.fromisoformat(window.ends_at),
    )


def _rule_window(key: WindowKey) -> WeeklyWindow:
    return WeeklyWindow(*key)


def _kind(row: TutorAvailabilityException) -> ExceptionKind:
    return "available" if row.kind == AVAILABLE else "unavailable"


def _rule_exception(row: TutorAvailabilityException) -> DatedException:
    return DatedException(
        _kind(row), row.starts_on, row.ends_on, row.starts_at, row.ends_at
    )


def _exception_item(row: TutorAvailabilityException) -> AvailabilityExceptionItem:
    return AvailabilityExceptionItem(
        id=row.id,
        kind=_kind(row),
        starts_on=row.starts_on,
        ends_on=row.ends_on,
        starts_at=_hhmm(row.starts_at),
        ends_at=_hhmm(row.ends_at),
        note=row.note,
        created_at=row.created_at,
    )


def _exception_order(
    row: TutorAvailabilityException,
) -> tuple[date, bool, time, datetime]:
    # By starts_on, then starts_at with whole days first, then creation.
    return (
        row.starts_on,
        row.starts_at is not None,
        row.starts_at or time(0),
        row.created_at,
    )


async def _windows(
    session: AsyncSession, tutor_user_id: uuid.UUID, weekday: int | None = None
) -> list[TutorAvailabilityWindow]:
    query = select(TutorAvailabilityWindow).where(
        TutorAvailabilityWindow.tutor_user_id == tutor_user_id
    )
    if weekday is not None:
        query = query.where(TutorAvailabilityWindow.weekday == weekday)
    return list(await session.scalars(query))


async def _exceptions(
    session: AsyncSession,
    tutor_user_id: uuid.UUID,
    between: tuple[date, date] | None = None,
) -> list[TutorAvailabilityException]:
    """The tutor's exceptions; with ``between``, only those sharing a day
    with that inclusive range."""
    query = select(TutorAvailabilityException).where(
        TutorAvailabilityException.tutor_user_id == tutor_user_id
    )
    if between is not None:
        first, last = between
        query = query.where(
            TutorAvailabilityException.starts_on <= last,
            TutorAvailabilityException.ends_on >= first,
        )
    return list(await session.scalars(query))


async def _settings(
    session: AsyncSession, tutor_user_id: uuid.UUID, *, for_update: bool = False
) -> TutorAvailabilitySettings | None:
    query = select(TutorAvailabilitySettings).where(
        TutorAvailabilitySettings.user_id == tutor_user_id
    )
    if for_update:
        query = query.with_for_update().execution_options(populate_existing=True)
    settings: TutorAvailabilitySettings | None = await session.scalar(query)
    return settings


def _detail(
    settings: TutorAvailabilitySettings | None,
    windows: Iterable[WindowKey],
    exceptions: Iterable[TutorAvailabilityException],
) -> TutorAvailabilityDetail:
    return TutorAvailabilityDetail(
        weekly=[
            AvailabilityWindow(
                weekday=weekday,
                starts_at=start.strftime("%H:%M"),
                ends_at=end.strftime("%H:%M"),
            )
            for weekday, start, end in sorted(windows)
        ],
        exceptions=[
            _exception_item(row) for row in sorted(exceptions, key=_exception_order)
        ],
        version=settings.version if settings is not None else 0,
    )


async def _editable_tutor(
    session: AsyncSession, principal: Principal, user_id: uuid.UUID
) -> RoleMember:
    """``404`` when the tutor isn't visible, then ``403`` unless the caller
    may change their availability."""
    member = await visible_tutor(session, principal, user_id)
    if not can_edit_availability(principal, member.id):
        raise PermissionDeniedError()
    return member


async def _ensure_no_impact(
    session: AsyncSession,
    impact: AvailabilityImpactSource,
    tutor_user_id: uuid.UUID,
    *,
    removed_windows: Sequence[WeeklyWindow] = (),
    added_unavailability: Sequence[DatedException] = (),
) -> None:
    affected = await impact.affected_session_ids(
        session,
        tutor_user_id,
        removed_windows=removed_windows,
        added_unavailability=added_unavailability,
    )
    if affected:
        raise AvailabilityChangeRequiresApprovalError()


async def _flush(session: AsyncSession) -> None:
    """Flush; a concurrent first change that lost on the unique ``user_id``
    is ``409 version_conflict``. (Overlapping windows never reach the
    exclusion constraint: the body is checked, and the settings row
    serializes changes of one tutor.)"""
    try:
        await session.flush()
    except IntegrityError as exc:
        if SETTINGS_USER_ID_UNIQUE in str(exc.orig):
            raise StaleVersionError() from None
        raise


# --- Reads ---------------------------------------------------------------------------


async def get_availability(
    session: AsyncSession, *, principal: Principal, user_id: uuid.UUID
) -> TutorAvailabilityDetail:
    """The tutor's weekly windows, exceptions and weekly ``version``."""
    await visible_tutor(session, principal, user_id)
    return _detail(
        await _settings(session, user_id),
        [_key(window) for window in await _windows(session, user_id)],
        await _exceptions(session, user_id),
    )


async def is_tutor_available(
    session: AsyncSession,
    tutor_user_id: uuid.UUID,
    local_date: date,
    start: time,
    end: time,
) -> AvailabilityResult:
    """Whether the tutor is available on the local date during ``[start,
    end)`` (local wall-clock times, ``start < end``), center closures
    included (``catalog.service.is_center_closed_on``). Three statements.
    ``start >= end`` raises :class:`ValueError`."""
    if start >= end:
        raise ValueError("start must be before end")
    windows = await _windows(session, tutor_user_id, local_date.isoweekday())
    exceptions = await _exceptions(session, tutor_user_id, (local_date, local_date))
    closed = await catalog.is_center_closed_on(session, local_date)
    return evaluate(
        [_rule_window(_key(window)) for window in windows],
        [_rule_exception(row) for row in exceptions],
        closed,
        local_date,
        start,
        end,
    )


def _closed_dates(closures: Iterable[tuple[date, date]]) -> set[date]:
    closed: set[date] = set()
    for starts_on, ends_on in closures:
        day = starts_on
        while day <= ends_on:
            closed.add(day)
            day += timedelta(days=1)
    return closed


async def is_tutor_available_at(
    session: AsyncSession,
    tutor_user_id: uuid.UUID,
    starts_at: datetime,
    ends_at: datetime,
) -> AvailabilityResult:
    """Whether the tutor is available during the instant interval ``[starts_at,
    ends_at)``: it is split into local wall-clock pieces in the center's time
    zone (``administration.service.get_center_time_zone``;
    :func:`~app.modules.staff.availability.to_local_intervals`), and is
    available only if every piece is; the reasons are merged. A piece that
    runs to midnight is never covered (windows end by 23:59). Naive datetimes
    or ``starts_at >= ends_at`` raise :class:`ValueError`."""
    zone = await administration.get_center_time_zone(session)
    pieces = to_local_intervals(starts_at, ends_at, zone)
    first, last = pieces[0][0], pieces[-1][0]
    windows = [
        _rule_window(_key(window)) for window in await _windows(session, tutor_user_id)
    ]
    exceptions = [
        _rule_exception(row)
        for row in await _exceptions(session, tutor_user_id, (first, last))
    ]
    closed = _closed_dates(
        (closure.starts_on, closure.ends_on)
        for closure in await catalog.closures_between(session, first, last)
    )
    return merge_results(
        evaluate(windows, exceptions, day in closed, day, start, end)
        for day, start, end in pieces
    )


async def check_availability(
    session: AsyncSession,
    *,
    principal: Principal,
    user_id: uuid.UUID,
    local_date: date,
    start: time,
    end: time,
) -> AvailabilityCheck:
    """``GET …/availability/check``: whoever may see the tutor."""
    await visible_tutor(session, principal, user_id)
    result = await is_tutor_available(session, user_id, local_date, start, end)
    return AvailabilityCheck(available=result.available, reasons=list(result.reasons))


# --- Writes --------------------------------------------------------------------------


async def replace_weekly_availability(
    session: AsyncSession,
    *,
    principal: Principal,
    user_id: uuid.UUID,
    body: WeeklyAvailabilityUpdate,
    impact: AvailabilityImpactSource,
) -> TutorAvailabilityDetail:
    """Replace the weekly windows (see the module docstring for the order)."""
    await _editable_tutor(session, principal, user_id)
    settings = await _settings(session, user_id, for_update=True)
    if settings is None:
        if body.version != 0:
            raise StaleVersionError()
    else:
        ensure_version(settings, body.version)

    current = await _windows(session, user_id)
    wanted = {_wanted_key(window) for window in body.windows}
    current_keys = {_key(window) for window in current}
    deletes = [window for window in current if _key(window) not in wanted]
    insert_keys = sorted(wanted - current_keys)
    if not deletes and not insert_keys:
        return _detail(settings, current_keys, await _exceptions(session, user_id))

    removed = uncovered_ranges(
        [_rule_window(key) for key in current_keys],
        [_rule_window(key) for key in wanted],
    )
    if removed:
        await _ensure_no_impact(session, impact, user_id, removed_windows=removed)

    # The settings row first: its versioned UPDATE (or the unique user_id of
    # a first INSERT) locks out a concurrent change before the windows.
    if settings is None:
        settings = TutorAvailabilitySettings(user_id=user_id)
        session.add(settings)
    else:
        # Only version/updated_at change: no settings audit event (#22).
        settings.updated_at = func.now()
    await _flush(session)
    await session.refresh(settings, ["updated_at", "version"])

    # Deletes first, in their own flush (#39): a changed window must not
    # meet its old row in the exclusion constraint.
    for window in deletes:
        await session.delete(window)
    if deletes:
        await _flush(session)
    session.add_all(
        TutorAvailabilityWindow(
            tutor_user_id=user_id, weekday=weekday, starts_at=start, ends_at=end
        )
        for weekday, start, end in insert_keys
    )
    if insert_keys:
        await _flush(session)
    return _detail(settings, wanted, await _exceptions(session, user_id))


async def create_exception(
    session: AsyncSession,
    *,
    principal: Principal,
    user_id: uuid.UUID,
    body: AvailabilityExceptionCreate,
    impact: AvailabilityImpactSource,
) -> AvailabilityExceptionItem:
    """Add an exception (see the module docstring for the order)."""
    await _editable_tutor(session, principal, user_id)
    row = TutorAvailabilityException(
        tutor_user_id=user_id,
        kind=body.kind,
        starts_on=body.starts_on,
        ends_on=body.ends_on,
        starts_at=time.fromisoformat(body.starts_at) if body.starts_at else None,
        ends_at=time.fromisoformat(body.ends_at) if body.ends_at else None,
        note=body.note,
    )
    if body.kind == UNAVAILABLE:
        await _ensure_no_impact(
            session, impact, user_id, added_unavailability=[_rule_exception(row)]
        )
    session.add(row)
    await _flush(session)
    await session.refresh(row, ["created_at"])
    return _exception_item(row)


async def delete_exception(
    session: AsyncSession,
    *,
    principal: Principal,
    user_id: uuid.UUID,
    exception_id: uuid.UUID,
    impact: AvailabilityImpactSource,
) -> None:
    """Delete one of the tutor's exceptions (see the module docstring)."""
    await _editable_tutor(session, principal, user_id)
    row = await session.scalar(
        select(TutorAvailabilityException)
        .where(TutorAvailabilityException.id == exception_id)
        .where(TutorAvailabilityException.tutor_user_id == user_id)
        .with_for_update()
    )
    if row is None:
        raise RecordNotFoundError()
    if row.kind == AVAILABLE:
        await _ensure_no_impact(
            session, impact, user_id, added_unavailability=[_rule_exception(row)]
        )
    await session.delete(row)
    await _flush(session)
