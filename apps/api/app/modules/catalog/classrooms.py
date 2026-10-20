"""Classrooms and their weekly availability windows (#39; spec §3; ADR-0004).

A basic room catalog: a name (unique ignoring case, inactive rooms count,
``409 name_taken``), a capacity (1 to 500) and whether the room is active.
No equipment, nothing is deleted: a room is deactivated instead. Re-exported
from :mod:`app.modules.catalog.service`, the module's public API.

**Windows:** a room may list weekly windows (ISO weekday, ``[starts_at,
ends_at)`` in local wall-clock time, never across midnight). A room **without
windows has no time restriction**; a room with windows is available only
inside them. Windows of one weekday may touch but never overlap (the body
check, backed by the exclusion constraint ``ex_classroom_windows_no_overlap``,
whose violation at flush maps to ``422 validation_error``). A ``PATCH`` that
sends ``availability`` replaces all the windows: only the windows that
changed are deleted and inserted (a changed window is a delete plus an
insert), so the audit trail shows real changes. The classroom ``version``
covers its windows: any change bumps it once.

**Other modules** (#45 preferred rooms, M4 scheduling) use
:func:`get_classroom`, :func:`active_classroom_ids` and
:func:`is_classroom_available`.

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from collections.abc import Collection, Iterable
from datetime import time

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import RecordNotFoundError
from app.core.models import ensure_version
from app.core.pagination import OffsetPage, OffsetParams
from app.modules.catalog.errors import NameTakenError, WindowOverlapError
from app.modules.catalog.models import (
    CLASSROOM_NAME_INDEX,
    CLASSROOM_WINDOW_OVERLAP_CONSTRAINT,
    FIRST_WEEKDAY,
    LAST_WEEKDAY,
    Classroom,
    ClassroomAvailabilityWindow,
)
from app.modules.catalog.schemas import (
    AvailabilityWindow,
    CatalogStatus,
    ClassroomCreate,
    ClassroomDetail,
    ClassroomUpdate,
)

type WindowKey = tuple[int, time, time]
"""``(weekday, starts_at, ends_at)``: what identifies a window of a room."""


def _status_condition(status: CatalogStatus) -> list[ColumnElement[bool]]:
    # The plain column (not "IS true"), so a partial index WHERE is_active
    # could serve it (#204).
    active: ColumnElement[bool] = Classroom.is_active.expression
    if status == "active":
        return [active]
    if status == "inactive":
        return [~active]
    return []


def _hhmm(value: time) -> str:
    return value.strftime("%H:%M")


def _key(window: ClassroomAvailabilityWindow) -> WindowKey:
    return (window.weekday, window.starts_at, window.ends_at)


def _wanted_key(window: AvailabilityWindow) -> WindowKey:
    return (
        window.weekday,
        time.fromisoformat(window.starts_at),
        time.fromisoformat(window.ends_at),
    )


def _detail(
    classroom: Classroom, windows: Iterable[ClassroomAvailabilityWindow]
) -> ClassroomDetail:
    return ClassroomDetail(
        id=classroom.id,
        name=classroom.name,
        capacity=classroom.capacity,
        is_active=classroom.is_active,
        availability=[
            AvailabilityWindow(
                weekday=window.weekday,
                starts_at=_hhmm(window.starts_at),
                ends_at=_hhmm(window.ends_at),
            )
            for window in sorted(windows, key=_key)
        ],
        version=classroom.version,
        created_at=classroom.created_at,
        updated_at=classroom.updated_at,
    )


async def _windows_of(
    session: AsyncSession, classroom_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, list[ClassroomAvailabilityWindow]]:
    """The windows of the given rooms, by room. One statement (none when no
    id is given)."""
    by_room: dict[uuid.UUID, list[ClassroomAvailabilityWindow]] = {
        classroom_id: [] for classroom_id in classroom_ids
    }
    if not by_room:
        return by_room
    windows = await session.scalars(
        select(ClassroomAvailabilityWindow).where(
            ClassroomAvailabilityWindow.classroom_id.in_(list(by_room))
        )
    )
    for window in windows:
        by_room[window.classroom_id].append(window)
    return by_room


# --- Reads ---------------------------------------------------------------------------


async def list_classrooms(
    session: AsyncSession, *, status: CatalogStatus = "active", page: OffsetParams
) -> OffsetPage[ClassroomDetail]:
    """The classrooms with ``status``, by ``lower(name)`` then ``id``, with
    their windows. Three statements whatever the page size."""
    conditions = _status_condition(status)
    classrooms = list(
        await session.scalars(
            select(Classroom)
            .where(*conditions)
            .order_by(func.lower(Classroom.name), Classroom.id)
            .limit(page.limit)
            .offset(page.offset)
        )
    )
    total = await session.scalar(select(func.count(Classroom.id)).where(*conditions))
    windows = await _windows_of(session, [classroom.id for classroom in classrooms])
    return OffsetPage[ClassroomDetail](
        items=[_detail(classroom, windows[classroom.id]) for classroom in classrooms],
        total=total or 0,
        limit=page.limit,
        offset=page.offset,
    )


async def get_classroom(
    session: AsyncSession, classroom_id: uuid.UUID
) -> ClassroomDetail:
    """One classroom with its windows, active or not; ``404 not_found`` when
    unknown."""
    classroom = await session.get(Classroom, classroom_id)
    if classroom is None:
        raise RecordNotFoundError()
    windows = await _windows_of(session, [classroom.id])
    return _detail(classroom, windows[classroom.id])


async def active_classroom_ids(
    session: AsyncSession, ids: Collection[uuid.UUID]
) -> set[uuid.UUID]:
    """Which of ``ids`` are existing, active classrooms. One statement (none
    for an empty collection)."""
    if not ids:
        return set()
    found = await session.scalars(
        select(Classroom.id)
        .where(Classroom.id.in_(list(ids)))
        .where(Classroom.is_active)
    )
    return set(found)


def _covers(windows: list[ClassroomAvailabilityWindow], start: time, end: time) -> bool:
    """Whether the union of ``windows`` (one weekday) covers ``[start, end)``;
    touching windows join."""
    reached = start
    for window in sorted(windows, key=_key):
        if window.starts_at > reached:
            break
        reached = max(reached, window.ends_at)
        if reached >= end:
            return True
    return False


async def is_classroom_available(
    session: AsyncSession,
    classroom_id: uuid.UUID,
    weekday: int,
    start: time,
    end: time,
) -> bool:
    """Whether the room may be used on the ISO ``weekday`` during ``[start,
    end)`` (local wall-clock times, ``start < end``, same day): false for an
    unknown or inactive room; true when the room has no windows at all;
    otherwise true only if the union of that weekday's windows covers the
    interval (09:00-12:00 plus 12:00-14:00 covers 11:00-13:00). Two
    statements. A weekday outside 1-7 or ``start >= end`` raises
    :class:`ValueError`."""
    if not FIRST_WEEKDAY <= weekday <= LAST_WEEKDAY:
        raise ValueError("weekday must be an ISO weekday (1-7)")
    if start >= end:
        raise ValueError("start must be before end")
    is_active = await session.scalar(
        select(Classroom.is_active).where(Classroom.id == classroom_id)
    )
    if not is_active:
        return False
    windows = (await _windows_of(session, [classroom_id]))[classroom_id]
    if not windows:
        return True
    return _covers([w for w in windows if w.weekday == weekday], start, end)


# --- Writes --------------------------------------------------------------------------


async def _flush(session: AsyncSession) -> None:
    """Flush; a concurrent request that took the name after the pre-check
    gives ``409 name_taken``; overlapping windows the body check let through
    give ``422 validation_error``."""
    try:
        await session.flush()
    except IntegrityError as exc:
        message = str(exc.orig)
        if CLASSROOM_NAME_INDEX in message:
            raise NameTakenError() from None
        if CLASSROOM_WINDOW_OVERLAP_CONSTRAINT in message:
            raise WindowOverlapError() from None
        raise


async def _ensure_name_free(
    session: AsyncSession, name: str, *, except_id: uuid.UUID | None = None
) -> None:
    query = select(Classroom.id).where(func.lower(Classroom.name) == func.lower(name))
    if except_id is not None:
        query = query.where(Classroom.id != except_id)
    if await session.scalar(query.limit(1)) is not None:
        raise NameTakenError()


def _new_window(classroom_id: uuid.UUID, key: WindowKey) -> ClassroomAvailabilityWindow:
    weekday, starts_at, ends_at = key
    return ClassroomAvailabilityWindow(
        classroom_id=classroom_id, weekday=weekday, starts_at=starts_at, ends_at=ends_at
    )


async def create_classroom(
    session: AsyncSession, body: ClassroomCreate
) -> ClassroomDetail:
    """A new active classroom with its windows: ``409 name_taken`` first."""
    await _ensure_name_free(session, body.name)
    classroom = Classroom(name=body.name, capacity=body.capacity, is_active=True)
    session.add(classroom)
    await _flush(session)
    windows = [
        _new_window(classroom.id, _wanted_key(window)) for window in body.availability
    ]
    session.add_all(windows)
    await _flush(session)
    return _detail(classroom, windows)


async def update_classroom(
    session: AsyncSession, classroom_id: uuid.UUID, body: ClassroomUpdate
) -> ClassroomDetail:
    """Rename, change the capacity, deactivate, reactivate or replace the
    windows of a classroom.

    Order (after the body's ``422``): ``404`` → ``409 version_conflict`` →
    ``409 name_taken`` → the update. The row is locked ``FOR UPDATE`` first.
    A body that changes nothing changes no version and writes no audit
    event; any change bumps the version once.
    """
    classroom = await session.scalar(
        select(Classroom)
        .where(Classroom.id == classroom_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if classroom is None:
        raise RecordNotFoundError()
    ensure_version(classroom, body.version)
    sent = body.model_fields_set
    changes = {
        field: getattr(body, field)
        for field in ("name", "capacity", "is_active")
        if field in sent
        and getattr(body, field) is not None
        and getattr(body, field) != getattr(classroom, field)
    }
    current = (await _windows_of(session, [classroom.id]))[classroom.id]
    deletes: list[ClassroomAvailabilityWindow] = []
    inserts: list[ClassroomAvailabilityWindow] = []
    if "availability" in sent and body.availability is not None:
        wanted = {_wanted_key(window) for window in body.availability}
        current_keys = {_key(window) for window in current}
        deletes = [window for window in current if _key(window) not in wanted]
        inserts = [
            _new_window(classroom.id, key) for key in sorted(wanted - current_keys)
        ]
    if not changes and not deletes and not inserts:
        return _detail(classroom, current)
    if "name" in changes:
        await _ensure_name_free(session, changes["name"], except_id=classroom.id)

    # One versioned UPDATE of the classroom: the changed columns, or, for a
    # windows-only change, only updated_at (left out of the audit, so no
    # classrooms event is written).
    if changes:
        for field, value in changes.items():
            setattr(classroom, field, value)
    else:
        classroom.updated_at = func.now()
    await _flush(session)
    await session.refresh(classroom, ["updated_at"])

    # Deletes first, in their own flush: the unit of work would insert
    # before deleting, and a changed window could meet its old row in the
    # exclusion constraint.
    for window in deletes:
        await session.delete(window)
    if deletes:
        await _flush(session)
    session.add_all(inserts)
    if inserts:
        await _flush(session)
    kept = [window for window in current if window not in deletes]
    return _detail(classroom, [*kept, *inserts])
