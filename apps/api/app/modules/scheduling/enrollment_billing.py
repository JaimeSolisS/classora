"""The scheduling seam of enrollment charges (#92 B2, C2, D3, D4, E2; ADR-0006
E1, E9; ``_docs/data-model.md`` "Enrollment charges (#92)").

``app.workflows.enrollment_charges`` and ``app.workflows.session_charges``
compose it with ``billing``, ``courses`` and ``families``; scheduling never
imports ``billing``.

- :func:`owner_schedules` / :func:`enrollment_session_counts`: the
  sessions of a group or a cohort that **count** for a late-enrollment
  proration (J3): every series of the owner (splits included), its
  materialized rows **plus** the occurrences of each series rule that are
  not materialized yet (the horizon is 12 weeks, a course may be longer),
  excluding ``cancelled``, ``skipped_closure`` and projected dates on a
  known closure. A projected occurrence counts only from its series'
  creation on (the materialization window never reaches back). ``remaining``
  counts the ones that start **after** the enrollment instant; ``total`` is
  ``None`` when any series of the owner is open-ended (no ``until_on`` and
  no ``count``: an open-ended group's fee is not prorated, so its projected
  occurrences beyond the first are never expanded). ``first_start`` is the
  first counted occurrence (the J6/E11 due-date base).
- :func:`lock_group_for_billing`: the group row ``FOR SHARE`` (the
  enrollment sweeper's first lock; the M4 enrollment writers take it ``FOR
  UPDATE``, so a sweeper step and an enrollment change serialize).
- The unexecuted selects the workflows join (ADR-0002: cross-module joins
  only in ``app/workflows/``): :func:`group_enrollment_columns` (the
  periods with the group's subject) and :func:`owner_session_columns` (the
  group and course sessions that are not cancelled or skipped).
- :func:`group_enrollment_period`, :func:`covering_group_periods` (the
  period of a group session's participant, E2) and
  :func:`group_sessions_after` (the J4 removal credits and restorations).

Every function runs in the caller's Unit of Work, never commits and takes
no lock but the one its name says.
"""

import uuid
from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Final

from sqlalchemy import Select, and_, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalog import service as catalog
from app.modules.scheduling.materialize import closed_dates
from app.modules.scheduling.models import (
    ClassGroup,
    ClassSeries,
    ClassSession,
    ClassSessionParticipant,
    GroupEnrollment,
)
from app.modules.scheduling.recurrence import iter_local_starts, to_instant
from app.modules.scheduling.series_participants import SeriesOwner
from app.modules.scheduling.states import SessionState

NOT_COUNTED_STATES: Final = (
    SessionState.CANCELLED.value,
    SessionState.SKIPPED_CLOSURE.value,
)
"""Occurrences that never take place (J3: not "remaining", not in a
group's ``total``; J6: not the "first session")."""


@dataclass(frozen=True)
class SessionCounts:
    """The counted sessions of an owner around an instant (J3).
    ``total`` is ``None`` for an open-ended owner; ``first_start`` is the
    first counted occurrence, ``None`` when there is none."""

    total: int | None
    remaining: int
    first_start: datetime | None


@dataclass
class OwnerSchedule:
    """The counted occurrence starts of one owner (materialized and
    projected), by start, and whether a series of it is open-ended."""

    starts: list[datetime] = field(default_factory=list)
    open_ended: bool = False

    def counts(self, at: datetime) -> SessionCounts:
        """The counts for an enrollment at ``at``: ``remaining`` = the
        counted occurrences that start after ``at``."""
        remaining = sum(1 for start in self.starts if start > at)
        return SessionCounts(
            total=None if self.open_ended else len(self.starts),
            remaining=remaining,
            first_start=self.starts[0] if self.starts else None,
        )


@dataclass(frozen=True)
class _Projected:
    owner: SeriesOwner
    starts_at: datetime
    local_date: date


def _owner_of(kind: str, group_id: Any, cohort_id: Any) -> SeriesOwner:
    if kind == "group":
        return SeriesOwner.group(group_id)
    return SeriesOwner.cohort(cohort_id)


def _owners_condition(owners: Collection[SeriesOwner], *, sessions: bool) -> Any:
    groups = [o.id for o in owners if o.kind == "group"]
    cohorts = [o.id for o in owners if o.kind == "course"]
    model: Any = ClassSession if sessions else ClassSeries
    conditions = []
    if groups:
        conditions.append(model.group_id.in_(groups))
    if cohorts:
        conditions.append(model.cohort_id.in_(cohorts))
    return or_(*conditions)


def _projected(
    series: ClassSeries,
    materialized: set[datetime],
    owner: SeriesOwner,
    *,
    first_only: bool,
) -> Iterable[_Projected]:
    """The rule occurrences of ``series`` that have no row (by
    ``original_start``) and start at or after its creation; only the first
    one when ``first_only`` (an open-ended rule)."""
    created = series.created_at.astimezone(UTC)
    for local in iter_local_starts(
        series.rrule, starts_on=series.starts_on, start_time=series.start_time
    ):
        instant = to_instant(local, series.time_zone)
        if instant < created or instant in materialized:
            continue
        yield _Projected(owner=owner, starts_at=instant, local_date=local.date())
        if first_only:
            return


async def owner_schedules(
    session: AsyncSession, owners: Collection[SeriesOwner]
) -> dict[SeriesOwner, OwnerSchedule]:
    """The counted occurrences of each owner (see the module docstring).
    At most three statements (series, rows, closures over the projected
    dates), none for no owner, plus the pure rule expansion (bounded by
    ``count`` ≤ 200 or ``until_on``)."""
    found: dict[SeriesOwner, OwnerSchedule] = {o: OwnerSchedule() for o in owners}
    if not owners:
        return found
    series_rows = list(
        await session.scalars(
            select(ClassSeries)
            .where(
                ClassSeries.kind.in_(("group", "course")),
                _owners_condition(owners, sessions=False),
            )
            .order_by(ClassSeries.id)
        )
    )
    rows = (
        await session.execute(
            select(
                ClassSession.kind,
                ClassSession.group_id,
                ClassSession.cohort_id,
                ClassSession.series_id,
                ClassSession.original_start,
                ClassSession.starts_at,
                ClassSession.state,
            ).where(
                ClassSession.kind.in_(("group", "course")),
                _owners_condition(owners, sessions=True),
            )
        )
    ).all()
    materialized: dict[uuid.UUID, set[datetime]] = {}
    for row in rows:
        if row.series_id is not None and row.original_start is not None:
            materialized.setdefault(row.series_id, set()).add(
                row.original_start.astimezone(UTC)
            )
        owner = _owner_of(row.kind, row.group_id, row.cohort_id)
        if owner in found and row.state not in NOT_COUNTED_STATES:
            found[owner].starts.append(row.starts_at.astimezone(UTC))
    projected: list[_Projected] = []
    for series in series_rows:
        owner = _owner_of(series.kind, series.group_id, series.cohort_id)
        if owner not in found:
            continue
        open_ended = series.until_on is None and series.count is None
        if open_ended:
            found[owner].open_ended = True
        projected.extend(
            _projected(
                series,
                materialized.get(series.id, set()),
                owner,
                first_only=open_ended,
            )
        )
    if projected:
        closed = closed_dates(
            await catalog.closures_between(
                session,
                min(p.local_date for p in projected),
                max(p.local_date for p in projected),
            )
        )
        for item in projected:
            if item.local_date not in closed:
                found[item.owner].starts.append(item.starts_at)
    for schedule in found.values():
        schedule.starts.sort()
    return found


async def enrollment_session_counts(
    session: AsyncSession, owner: SeriesOwner, at: datetime
) -> SessionCounts:
    """The J3 counts of ``owner`` for an enrollment at ``at`` (B2). At most
    three statements plus the pure rule expansion. For a cohort the caller
    takes the snapshot's ``session_count`` as the denominator
    (``courses.service.cohort_session_count``) and clamps ``remaining``."""
    schedules = await owner_schedules(session, [owner])
    return schedules[owner].counts(at)


# --- Groups --------------------------------------------------------------------------


@dataclass(frozen=True)
class GroupForBilling:
    """A group row read for billing: its id and subject."""

    id: uuid.UUID
    subject_id: uuid.UUID


async def lock_group_for_billing(
    session: AsyncSession, group_id: uuid.UUID
) -> GroupForBilling | None:
    """The group row ``FOR SHARE`` (re-read), or ``None`` when unknown (#92
    D4). The M4 enrollment and withdrawal writers take it ``FOR UPDATE``
    after the students and the zone, so an enrollment sweeper step waits for
    them (or they wait for it). One statement."""
    row = (
        await session.execute(
            select(ClassGroup.id, ClassGroup.subject_id)
            .where(ClassGroup.id == group_id)
            .with_for_update(read=True)
        )
    ).first()
    if row is None:
        return None
    return GroupForBilling(id=row.id, subject_id=row.subject_id)


def group_enrollment_columns() -> Select[
    uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID, datetime, datetime | None
]:
    """``SELECT enrollment_id, owner_id (the group), student_id,
    subject_id (the group's), enrolled_at, withdrawn_at`` of every group
    enrollment period, **not executed** (the #92 sweeper's candidates and
    the J5 "other periods" read)."""
    return select(
        GroupEnrollment.id.label("enrollment_id"),
        GroupEnrollment.group_id.label("owner_id"),
        GroupEnrollment.student_id,
        ClassGroup.subject_id,
        GroupEnrollment.enrolled_at,
        GroupEnrollment.withdrawn_at,
    ).join(ClassGroup, ClassGroup.id == GroupEnrollment.group_id)


def owner_session_columns(
    *, counted_only: bool = True
) -> Select[uuid.UUID, uuid.UUID | None, uuid.UUID | None, datetime]:
    """``SELECT id, group_id, cohort_id, starts_at`` of the group and course
    sessions, by default only those that are not ``cancelled`` or
    ``skipped_closure`` (the J6 first session of the sweeper's candidates);
    ``counted_only=False``: every state (the J2 "session charge inside the
    period" join: a charged session cancelled later keeps its charge).
    **Not executed.**"""
    query = select(
        ClassSession.id,
        ClassSession.group_id,
        ClassSession.cohort_id,
        ClassSession.starts_at,
    ).where(ClassSession.kind.in_(("group", "course")))
    if counted_only:
        query = query.where(ClassSession.state.not_in(NOT_COUNTED_STATES))
    return query


@dataclass(frozen=True)
class GroupPeriod:
    """One group enrollment period with the group's subject."""

    enrollment_id: uuid.UUID
    group_id: uuid.UUID
    student_id: uuid.UUID
    subject_id: uuid.UUID
    enrolled_at: datetime
    withdrawn_at: datetime | None


def _group_period(row: Any) -> GroupPeriod:
    return GroupPeriod(
        enrollment_id=row.enrollment_id,
        group_id=row.owner_id,
        student_id=row.student_id,
        subject_id=row.subject_id,
        enrolled_at=row.enrolled_at.astimezone(UTC),
        withdrawn_at=(
            None if row.withdrawn_at is None else row.withdrawn_at.astimezone(UTC)
        ),
    )


async def group_enrollment_period(
    session: AsyncSession, enrollment_id: uuid.UUID
) -> GroupPeriod | None:
    """One period (re-read, so a withdrawal flushed in this Unit of Work is
    seen), or ``None`` when unknown. One statement."""
    row = (
        await session.execute(
            group_enrollment_columns()
            .where(GroupEnrollment.id == enrollment_id)
            .execution_options(populate_existing=True)
        )
    ).first()
    return None if row is None else _group_period(row)


async def covering_group_periods(
    session: AsyncSession, pairs: Collection[tuple[uuid.UUID, uuid.UUID]]
) -> list[GroupPeriod]:
    """Every period of these ``(group_id, student_id)`` pairs (the caller
    picks the one whose ``[enrolled_at, withdrawn_at)`` contains a session's
    start, E2). One statement, none for no pair."""
    if not pairs:
        return []
    rows = await session.execute(
        group_enrollment_columns().where(
            tuple_(GroupEnrollment.group_id, GroupEnrollment.student_id).in_(
                list(set(pairs))
            )
        )
    )
    return [_group_period(row) for row in rows]


def covers(period: GroupPeriod, starts_at: datetime) -> bool:
    """Whether a session starting at ``starts_at`` falls in the period
    (``enrolled_at <= starts_at < withdrawn_at``)."""
    return period.enrolled_at <= starts_at and (
        period.withdrawn_at is None or starts_at < period.withdrawn_at
    )


@dataclass(frozen=True)
class GroupSessionSeat:
    """A group session after an instant and whether the student is one of
    its participants now (J4)."""

    class_session_id: uuid.UUID
    starts_at: datetime
    is_participant: bool


async def group_sessions_after(
    session: AsyncSession,
    group_id: uuid.UUID,
    student_id: uuid.UUID,
    after: datetime,
) -> list[GroupSessionSeat]:
    """The group's sessions that start after ``after`` (any state), by
    start, each with whether ``student_id`` is a participant now. The M4
    hook calls it after its own writes (the sessions are held ``FOR
    UPDATE`` by the change). One statement."""
    participant = and_(
        ClassSessionParticipant.class_session_id == ClassSession.id,
        ClassSessionParticipant.student_id == student_id,
    )
    rows = await session.execute(
        select(
            ClassSession.id,
            ClassSession.starts_at,
            ClassSessionParticipant.student_id.is_not(None).label("is_participant"),
        )
        .outerjoin(ClassSessionParticipant, participant)
        .where(ClassSession.group_id == group_id, ClassSession.starts_at > after)
        .order_by(ClassSession.starts_at, ClassSession.id)
    )
    return [
        GroupSessionSeat(
            class_session_id=row.id,
            starts_at=row.starts_at.astimezone(UTC),
            is_participant=bool(row.is_participant),
        )
        for row in rows
    ]
