"""Academic periods and center closures (#38; spec §3, §4; ADR-0004).

Both are inclusive ranges of local calendar dates (``starts_on`` and
``ends_on`` are both days of the range), deactivated instead of deleted, and
changed with the ``version`` the caller loaded (``409 version_conflict``); a
body that changes nothing changes no version and writes no audit event.
Re-exported from :mod:`app.modules.catalog.service`, the module's public API.

**Academic periods** (school years, semesters, terms): names are unique
ignoring case (inactive rows count, ``409 name_taken``). Active periods of
the same type never share a day (``409 period_overlap``): the service checks
first and maps a violation of the exclusion constraint
``ex_academic_periods_no_overlap`` at flush to the same error, so two
concurrent overlapping creates give one success and one ``409``. Inactive
periods are ignored by the rule. A period's type never changes.

**Closures** (holidays, closures, vacations): whole local days. Names repeat
and closures may overlap each other. Only active closures close the center.
Other modules ask through :func:`closures_between`,
:func:`is_center_closed_on` and :func:`is_center_closed_at` (an instant,
converted to the local date in the center time zone of
``administration.service``).

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, Select, func, literal_column, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.core.authz import RecordNotFoundError
from app.core.models import ensure_version
from app.core.pagination import OffsetPage, OffsetParams
from app.modules.administration import service as administration
from app.modules.catalog.errors import (
    DateRangeInvalidError,
    NameTakenError,
    PeriodOverlapError,
)
from app.modules.catalog.models import (
    CLOSURE_MAX_SPAN_DAYS,
    PERIOD_MAX_SPAN_DAYS,
    PERIOD_NAME_INDEX,
    PERIOD_OVERLAP_CONSTRAINT,
    AcademicPeriod,
    Closure,
)
from app.modules.catalog.schemas import (
    AcademicPeriodCreate,
    AcademicPeriodDetail,
    AcademicPeriodUpdate,
    CatalogStatus,
    ClosureCheck,
    ClosureCreate,
    ClosureDetail,
    ClosureType,
    ClosureUpdate,
    PeriodType,
    range_error,
)

# The inclusive bounds of daterange(), inlined (not a bind parameter) so the
# expression matches ix_closures_active_range.
_INCLUSIVE: ColumnElement[str] = literal_column("'[]'")


def _status_condition(
    column: InstrumentedAttribute[bool], status: CatalogStatus
) -> list[ColumnElement[bool]]:
    if status == "active":
        return [column.is_(True)]
    if status == "inactive":
        return [column.is_(False)]
    return []


def _closure_range() -> ColumnElement[object]:
    return func.daterange(Closure.starts_on, Closure.ends_on, _INCLUSIVE)


def _period_detail(period: AcademicPeriod) -> AcademicPeriodDetail:
    return AcademicPeriodDetail.model_validate(period, from_attributes=True)


def _closure_detail(closure: Closure) -> ClosureDetail:
    return ClosureDetail.model_validate(closure, from_attributes=True)


def _ensure_range(starts_on: date, ends_on: date, max_span: int) -> None:
    message = range_error(starts_on, ends_on, max_span)
    if message is not None:
        raise DateRangeInvalidError(message)


# --- Academic periods: reads ---------------------------------------------------------


async def list_academic_periods(
    session: AsyncSession,
    *,
    status: CatalogStatus = "active",
    period_type: PeriodType | None = None,
    on: date | None = None,
    page: OffsetParams,
) -> OffsetPage[AcademicPeriodDetail]:
    """The periods with ``status`` (of one type, and containing the date
    ``on``, when given), by ``starts_on`` (newest first), ``lower(name)``,
    then ``id``. Two statements."""
    conditions = _status_condition(AcademicPeriod.is_active, status)
    if period_type is not None:
        conditions.append(AcademicPeriod.period_type == period_type)
    if on is not None:
        conditions.append(AcademicPeriod.starts_on <= on)
        conditions.append(AcademicPeriod.ends_on >= on)
    periods = await session.scalars(
        select(AcademicPeriod)
        .where(*conditions)
        .order_by(
            AcademicPeriod.starts_on.desc(),
            func.lower(AcademicPeriod.name),
            AcademicPeriod.id,
        )
        .limit(page.limit)
        .offset(page.offset)
    )
    items = [_period_detail(period) for period in periods]
    total = await session.scalar(
        select(func.count(AcademicPeriod.id)).where(*conditions)
    )
    return OffsetPage[AcademicPeriodDetail](
        items=items, total=total or 0, limit=page.limit, offset=page.offset
    )


async def get_academic_period(
    session: AsyncSession, period_id: uuid.UUID
) -> AcademicPeriodDetail:
    """One period, active or not; ``404 not_found`` when unknown."""
    period = await session.get(AcademicPeriod, period_id)
    if period is None:
        raise RecordNotFoundError()
    return _period_detail(period)


# --- Academic periods: writes --------------------------------------------------------


async def _flush_period(session: AsyncSession) -> None:
    """Flush; a concurrent request that took the name or an overlapping range
    after the pre-checks gives ``409 name_taken`` / ``409 period_overlap``."""
    try:
        await session.flush()
    except IntegrityError as exc:
        message = str(exc.orig)
        if PERIOD_NAME_INDEX in message:
            raise NameTakenError() from None
        if PERIOD_OVERLAP_CONSTRAINT in message:
            raise PeriodOverlapError() from None
        raise


async def _ensure_period_name_free(
    session: AsyncSession, name: str, *, except_id: uuid.UUID | None = None
) -> None:
    query = select(AcademicPeriod.id).where(
        func.lower(AcademicPeriod.name) == func.lower(name)
    )
    if except_id is not None:
        query = query.where(AcademicPeriod.id != except_id)
    if await session.scalar(query.limit(1)) is not None:
        raise NameTakenError()


async def _ensure_no_overlap(
    session: AsyncSession,
    period_type: str,
    starts_on: date,
    ends_on: date,
    *,
    except_id: uuid.UUID | None = None,
) -> None:
    """``409 period_overlap`` when an active period of ``period_type`` shares
    a day with ``[starts_on, ends_on]``."""
    query = (
        select(AcademicPeriod.id)
        .where(AcademicPeriod.is_active.is_(True))
        .where(AcademicPeriod.period_type == period_type)
        .where(AcademicPeriod.starts_on <= ends_on)
        .where(AcademicPeriod.ends_on >= starts_on)
    )
    if except_id is not None:
        query = query.where(AcademicPeriod.id != except_id)
    if await session.scalar(query.limit(1)) is not None:
        raise PeriodOverlapError()


async def create_academic_period(
    session: AsyncSession, body: AcademicPeriodCreate
) -> AcademicPeriodDetail:
    """A new active period: ``409 name_taken``, then ``409 period_overlap``."""
    await _ensure_period_name_free(session, body.name)
    await _ensure_no_overlap(session, body.period_type, body.starts_on, body.ends_on)
    period = AcademicPeriod(
        name=body.name,
        period_type=body.period_type,
        starts_on=body.starts_on,
        ends_on=body.ends_on,
        is_active=True,
    )
    session.add(period)
    await _flush_period(session)
    return _period_detail(period)


async def update_academic_period(
    session: AsyncSession, period_id: uuid.UUID, body: AcademicPeriodUpdate
) -> AcademicPeriodDetail:
    """Rename, change the dates, deactivate or reactivate a period.

    Order (after the body's ``422``): ``404`` → ``409 version_conflict`` →
    ``422`` when one sent date makes the stored range invalid → ``409
    name_taken`` → ``409 period_overlap`` (when the result is active and its
    dates changed or it is reactivated) → the update. The row is locked
    ``FOR UPDATE`` first.
    """
    period = await session.scalar(
        select(AcademicPeriod)
        .where(AcademicPeriod.id == period_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if period is None:
        raise RecordNotFoundError()
    ensure_version(period, body.version)
    sent = body.model_fields_set
    starts_on = body.starts_on if "starts_on" in sent and body.starts_on else None
    ends_on = body.ends_on if "ends_on" in sent and body.ends_on else None
    new_starts = starts_on or period.starts_on
    new_ends = ends_on or period.ends_on
    _ensure_range(new_starts, new_ends, PERIOD_MAX_SPAN_DAYS)
    new_name = body.name if "name" in sent and body.name is not None else period.name
    new_active = (
        body.is_active
        if "is_active" in sent and body.is_active is not None
        else period.is_active
    )
    dates_changed = (new_starts, new_ends) != (period.starts_on, period.ends_on)
    if new_name != period.name:
        await _ensure_period_name_free(session, new_name, except_id=period.id)
    if new_active and (dates_changed or not period.is_active):
        await _ensure_no_overlap(
            session, period.period_type, new_starts, new_ends, except_id=period.id
        )
    changed = new_name != period.name or dates_changed or new_active != period.is_active
    if changed:
        period.name = new_name
        period.starts_on = new_starts
        period.ends_on = new_ends
        period.is_active = new_active
        await _flush_period(session)
    return _period_detail(period)


# --- Closures: reads -----------------------------------------------------------------


def _closures_order(query: Select[Closure]) -> Select[Closure]:
    return query.order_by(Closure.starts_on, Closure.ends_on, Closure.id)


async def list_closures(
    session: AsyncSession,
    *,
    status: CatalogStatus = "active",
    closure_type: ClosureType | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    page: OffsetParams,
) -> OffsetPage[ClosureDetail]:
    """The closures with ``status`` (of one type, and overlapping
    ``[from_date, to_date]`` inclusive, when given), by ``starts_on``,
    ``ends_on``, then ``id``. Two statements."""
    conditions = _status_condition(Closure.is_active, status)
    if closure_type is not None:
        conditions.append(Closure.closure_type == closure_type)
    if from_date is not None:
        conditions.append(Closure.ends_on >= from_date)
    if to_date is not None:
        conditions.append(Closure.starts_on <= to_date)
    closures = await session.scalars(
        _closures_order(select(Closure).where(*conditions))
        .limit(page.limit)
        .offset(page.offset)
    )
    items = [_closure_detail(closure) for closure in closures]
    total = await session.scalar(select(func.count(Closure.id)).where(*conditions))
    return OffsetPage[ClosureDetail](
        items=items, total=total or 0, limit=page.limit, offset=page.offset
    )


async def get_closure(session: AsyncSession, closure_id: uuid.UUID) -> ClosureDetail:
    """One closure, active or not; ``404 not_found`` when unknown."""
    closure = await session.get(Closure, closure_id)
    if closure is None:
        raise RecordNotFoundError()
    return _closure_detail(closure)


async def closures_between(
    session: AsyncSession, start: date, end: date
) -> list[ClosureDetail]:
    """The active closures that share at least one day with ``[start, end]``
    (inclusive), by ``starts_on``, ``ends_on``, ``id``. One statement.
    ``start`` after ``end`` raises :class:`ValueError`."""
    if start > end:
        raise ValueError("start must not be after end")
    closures = await session.scalars(
        _closures_order(
            select(Closure)
            .where(Closure.is_active.is_(True))
            .where(_closure_range().op("&&")(func.daterange(start, end, _INCLUSIVE)))
        )
    )
    return [_closure_detail(closure) for closure in closures]


async def check_closures(session: AsyncSession, local_date: date) -> ClosureCheck:
    """``GET /closures/check``: the active closures that contain
    ``local_date``. One statement."""
    closures = await closures_between(session, local_date, local_date)
    return ClosureCheck(date=local_date, closed=bool(closures), closures=closures)


async def is_center_closed_on(session: AsyncSession, local_date: date) -> bool:
    """Whether an active closure contains the local date. One statement."""
    found = await session.scalar(
        select(Closure.id)
        .where(Closure.is_active.is_(True))
        .where(_closure_range().op("@>")(local_date))
        .limit(1)
    )
    return found is not None


async def is_center_closed_at(session: AsyncSession, instant: datetime) -> bool:
    """Whether the center is closed at an instant: the instant's local date
    in the center time zone (``administration.service.get_center_time_zone``)
    is in an active closure. A naive datetime raises :class:`ValueError`."""
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("instant must be timezone-aware")
    zone = ZoneInfo(await administration.get_center_time_zone(session))
    return await is_center_closed_on(session, instant.astimezone(zone).date())


# --- Closures: writes ----------------------------------------------------------------


async def create_closure(session: AsyncSession, body: ClosureCreate) -> ClosureDetail:
    """A new active closure. No name or overlap rule applies."""
    closure = Closure(
        name=body.name,
        closure_type=body.closure_type,
        starts_on=body.starts_on,
        ends_on=body.ends_on,
        is_active=True,
    )
    session.add(closure)
    await session.flush()
    return _closure_detail(closure)


async def update_closure(
    session: AsyncSession, closure_id: uuid.UUID, body: ClosureUpdate
) -> ClosureDetail:
    """Rename, retype, change the dates, deactivate or reactivate a closure.

    Order (after the body's ``422``): ``404`` → ``409 version_conflict`` →
    ``422`` when one sent date makes the stored range invalid → the update.
    """
    closure = await session.scalar(
        select(Closure)
        .where(Closure.id == closure_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if closure is None:
        raise RecordNotFoundError()
    ensure_version(closure, body.version)
    sent = body.model_fields_set
    new_starts = body.starts_on if "starts_on" in sent and body.starts_on else None
    new_ends = body.ends_on if "ends_on" in sent and body.ends_on else None
    starts_on = new_starts or closure.starts_on
    ends_on = new_ends or closure.ends_on
    _ensure_range(starts_on, ends_on, CLOSURE_MAX_SPAN_DAYS)
    values = {
        "name": body.name if "name" in sent and body.name is not None else None,
        "closure_type": (
            body.closure_type
            if "closure_type" in sent and body.closure_type is not None
            else None
        ),
        "starts_on": starts_on,
        "ends_on": ends_on,
        "is_active": (
            body.is_active
            if "is_active" in sent and body.is_active is not None
            else None
        ),
    }
    changed = False
    for field, value in values.items():
        if value is not None and value != getattr(closure, field):
            setattr(closure, field, value)
            changed = True
    if changed:
        await session.flush()
    return _closure_detail(closure)
