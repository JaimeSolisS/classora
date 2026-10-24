"""Kind-neutral enrollment mechanics of group and course series (#66 B1, B2;
the #56 B3 rule; spec §3 "additions/removals after a start date", W1.5;
ADR-0005 D1, D2, D7).

An enrollment in a tutoring group (``scheduling.groups``, #56) and in a
course cohort (``app.workflows.cohort_enrollment``, #66) change the same
rows: the **producing series'** participants (``class_series_participants``)
and the participants of the owner's **affected occurrences**. The enrollment
record itself belongs to its owner (``group_enrollments`` here,
``cohort_enrollments`` in ``courses``); these helpers never read or write
it, so one implementation of each rule serves both kinds.

**The owner** (:class:`SeriesOwner`) is the group or the cohort: its
occurrences are found by ``class_sessions.group_id`` / ``cohort_id``, never
by the series id, so an occurrence moved on its own (#54) beyond its series'
last date is still reached, and a withdrawal with no producing series left
still finds the not-started occurrences (there are none in practice).

**The affected occurrences (B3)** of a change at ``now``: the owner's
occurrences that **start after now** (``starts_at > now``; a row starting
exactly now has started) in ``unassigned``, ``offered``, ``confirmed``,
``conflict`` or ``skipped_closure``. Past, started, ``completed`` and
``cancelled`` occurrences keep their participants (W1.5). Only the occupying
ones (``unassigned``, ``offered``, ``confirmed``) are checked
(``student_overlap``, the hard ``room_capacity_exceeded``); ``conflict``
and ``skipped_closure`` rows take the participant without checks (the
#53 G2 approve and the #232 restore re-check every participant).
Occurrences beyond the materialization horizon don't exist yet: the nightly
run checks them when it creates them (#53 F1).

**The capacity (D7).** :func:`plan_series_participant_add` returns the soft
``group_capacity_exceeded`` (the active count after the change against the
owner's maximum; ``details.group_id`` or ``details.cohort_id``) first, then
each checked occurrence's conflicts by start (the #56 E1 step 9 order and
shape). A withdrawal never fails a check and changes no session state.

**Versions.** Every change bumps the ``version`` of the series and the
occurrences whose participants it changes (only ``updated_at`` and
``version``; no audit event), so a series edit or ``PATCH`` working from an
older read gets ``409 version_conflict``.

**Locks.** The caller holds, in this order: ``lock_students`` (the
student), the zone ``FOR SHARE``, the owner row ``FOR UPDATE`` (the group,
or the cohort through ``courses.service.lock_cohort``) and the producing
series ``FOR UPDATE`` by id (:func:`lock_producing_series`); the helpers
take the affected occurrences ``FOR UPDATE`` by id. They refuse a series
that is not a group or course series of the owner (``ValueError``: a
programming error). Every function runs in the caller's Unit of Work and
never commits.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalog import service as catalog
from app.modules.scheduling.checks import student_overlap_ids
from app.modules.scheduling.conflicts import STUDENT_OVERLAP, Conflict, sort_conflicts
from app.modules.scheduling.groups_rules import (
    AFFECTED_STATES,
    GroupOccurrence,
    affected_occurrences,
    group_capacity_conflicts,
    is_checked,
    room_capacity_conflicts,
)
from app.modules.scheduling.models import (
    ClassSeries,
    ClassSeriesParticipant,
    ClassSession,
    ClassSessionParticipant,
)
from app.modules.scheduling.states import SessionKind

OwnerKind = Literal["group", "course"]


@dataclass(frozen=True)
class SeriesOwner:
    """The group (``kind = 'group'``) or the cohort (``kind = 'course'``)
    whose series and occurrences an enrollment changes."""

    kind: OwnerKind
    id: uuid.UUID

    @classmethod
    def group(cls, group_id: uuid.UUID) -> SeriesOwner:
        return cls(kind="group", id=group_id)

    @classmethod
    def cohort(cls, cohort_id: uuid.UUID) -> SeriesOwner:
        return cls(kind="course", id=cohort_id)

    def series_condition(self) -> ColumnElement[bool]:
        if self.kind == "group":
            return ClassSeries.group_id == self.id
        return ClassSeries.cohort_id == self.id

    def session_condition(self) -> ColumnElement[bool]:
        if self.kind == "group":
            return ClassSession.group_id == self.id
        return ClassSession.cohort_id == self.id

    def owns(self, series: ClassSeries) -> bool:
        owner_id = series.group_id if self.kind == "group" else series.cohort_id
        return series.kind == self.kind and owner_id == self.id


def producing_condition(today: date) -> ColumnElement[bool]:
    """A series that may still produce occurrences on or after ``today``."""
    return or_(
        ClassSeries.last_occurrence_on.is_(None),
        ClassSeries.last_occurrence_on >= today,
    )


async def lock_producing_series(
    session: AsyncSession, owner: SeriesOwner, today: date
) -> list[ClassSeries]:
    """The owner's producing series (``last_occurrence_on`` null or on/after
    the local ``today``) ``FOR UPDATE``, by id."""
    return list(
        await session.scalars(
            select(ClassSeries)
            .where(owner.series_condition(), producing_condition(today))
            .order_by(ClassSeries.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )


async def _locked_series(
    session: AsyncSession, series_ids: Sequence[uuid.UUID], owner: SeriesOwner
) -> list[ClassSeries]:
    """B2: the caller's locked series (from the identity map), each a group
    or course series of ``owner``; anything else is a ``ValueError``."""
    found: list[ClassSeries] = []
    for series_id in series_ids:
        series = await session.get(ClassSeries, series_id)
        if series is None:
            raise ValueError(f"no class series {series_id}")
        if series.kind not in (SessionKind.GROUP, SessionKind.COURSE):
            raise ValueError(f"series {series_id} is a {series.kind} series")
        if not owner.owns(series):
            raise ValueError(f"series {series_id} is not of {owner}")
        found.append(series)
    return found


def _facts(
    rows: Sequence[ClassSession],
    zone: str = "UTC",
    capacities: dict[uuid.UUID, int] | None = None,
) -> list[GroupOccurrence]:
    tz = ZoneInfo(zone)
    caps = capacities or {}
    return [
        GroupOccurrence(
            class_session_id=row.id,
            state=row.state,
            starts_at=row.starts_at,
            local_date=row.starts_at.astimezone(tz).date(),
            classroom_id=row.classroom_id,
            room_capacity=(
                None if row.classroom_id is None else caps.get(row.classroom_id)
            ),
        )
        for row in rows
    ]


async def lock_affected_rows(
    session: AsyncSession, owner: SeriesOwner, now: datetime
) -> list[ClassSession]:
    """The owner's B3 occurrences ``FOR UPDATE``, by id (then ordered by
    start)."""
    candidates = list(
        await session.scalars(
            select(ClassSession)
            .where(
                owner.session_condition(),
                ClassSession.starts_at > now,
                ClassSession.state.in_(sorted(AFFECTED_STATES)),
            )
            .order_by(ClassSession.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    rows = [row for row in candidates if row.starts_at > now]
    kept = {o.class_session_id for o in affected_occurrences(_facts(rows), now)}
    return sorted(
        (row for row in rows if row.id in kept), key=lambda r: (r.starts_at, r.id)
    )


async def _participants_of(
    session: AsyncSession, row_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, set[uuid.UUID]]:
    found: dict[uuid.UUID, set[uuid.UUID]] = {row_id: set() for row_id in row_ids}
    if not row_ids:
        return found
    for session_id, student_id in await session.execute(
        select(
            ClassSessionParticipant.class_session_id,
            ClassSessionParticipant.student_id,
        ).where(ClassSessionParticipant.class_session_id.in_(row_ids))
    ):
        found[session_id].add(student_id)
    return found


async def _bump(session: AsyncSession, records: Sequence[Any]) -> None:
    """One versioned ``UPDATE`` per record (only ``updated_at`` and
    ``version`` change, which the audit leaves out): their version covers
    their participants."""
    for record in records:
        record.updated_at = func.now()
    await session.flush()
    for record in records:
        await session.refresh(record, ["updated_at"])


def _iso_z(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _capacity_conflicts(
    owner: SeriesOwner, active_count_after: int, max_students: int
) -> list[Conflict]:
    if owner.kind == "group":
        return group_capacity_conflicts(
            active_count_after, max_students=max_students, group_id=owner.id
        )
    return group_capacity_conflicts(
        active_count_after, max_students=max_students, cohort_id=owner.id
    )


async def _owner_of(
    session: AsyncSession, series_ids: Sequence[uuid.UUID]
) -> tuple[SeriesOwner, str]:
    """The owner and the zone of the caller's locked series (from the
    identity map, no statement): they all belong to one group or one cohort
    (B2, else ``ValueError``). The zone is the series' ``time_zone``, the
    center zone, which can't change while series exist (#37 E1: the
    ``scheduling.class_series`` time-zone dependency)."""
    if not series_ids:
        raise ValueError("an enrollment needs at least one producing series")
    first = await session.get(ClassSeries, series_ids[0])
    if first is None:
        raise ValueError(f"no class series {series_ids[0]}")
    if first.kind == SessionKind.GROUP and first.group_id is not None:
        owner = SeriesOwner.group(first.group_id)
    elif first.kind == SessionKind.COURSE and first.cohort_id is not None:
        owner = SeriesOwner.cohort(first.cohort_id)
    else:
        raise ValueError(f"series {first.id} is a {first.kind} series")
    await _locked_series(session, series_ids, owner)
    return owner, first.time_zone


async def plan_series_participant_add(
    session: AsyncSession,
    *,
    series_ids: Sequence[uuid.UUID],
    student_id: uuid.UUID,
    max_students: int,
    active_count: int,
    now: datetime,
) -> list[Conflict]:
    """The conflicts of adding ``student_id`` at ``now`` (#56 E1 step 9; the
    #66 B1 signature): ``group_capacity_exceeded`` (soft) when
    ``active_count + 1`` is above ``max_students``, then, on each checked B3
    occurrence the student isn't already on (by start), ``student_overlap``
    and the hard ``room_capacity_exceeded`` (the occurrence's own room
    against its participants plus one). The owner (the group or the
    cohort) and the zone come from the given locked series, which must all
    be group or course series of one owner (``ValueError``). Locks the B3
    occurrences; writes nothing."""
    owner, zone = await _owner_of(session, series_ids)
    conflicts = _capacity_conflicts(owner, active_count + 1, max_students)
    rows = await lock_affected_rows(session, owner, now)
    rooms = {row.classroom_id for row in rows if row.classroom_id is not None}
    capacities = {
        room_id: room.capacity
        for room_id, room in (
            await catalog.classroom_capacities(session, rooms)
        ).items()
    }
    facts = {fact.class_session_id: fact for fact in _facts(rows, zone, capacities)}
    participants = await _participants_of(session, [row.id for row in rows])
    for row in rows:
        if not is_checked(row.state) or student_id in participants[row.id]:
            continue
        fact = facts[row.id]
        found: list[Conflict] = []
        overlapping = await student_overlap_ids(
            session,
            student_id,
            row.starts_at,
            row.ends_at,
            zone=zone,
            exclude_session_ids=frozenset({row.id}),
        )
        if overlapping:
            found.append(
                Conflict(
                    STUDENT_OVERLAP,
                    {
                        "student_id": str(student_id),
                        "class_session_ids": overlapping,
                        "occurrence_start": _iso_z(row.starts_at),
                        "date": fact.local_date.isoformat(),
                    },
                )
            )
        found += room_capacity_conflicts([(fact, len(participants[row.id]) + 1)])
        conflicts += sort_conflicts(found)
    return conflicts


async def add_series_participant(
    session: AsyncSession,
    *,
    series_ids: Sequence[uuid.UUID],
    student_id: uuid.UUID,
    now: datetime,
    owner: SeriesOwner,
) -> None:
    """Add ``student_id`` to the given (producing, locked) series and to
    every B3 occurrence of the owner that doesn't have them yet, then bump
    the versions of what changed. The caller has inserted the enrollment."""
    series = await _locked_series(session, series_ids, owner)
    rows = await lock_affected_rows(session, owner, now)
    participants = await _participants_of(session, [row.id for row in rows])
    session.add_all(
        ClassSeriesParticipant(class_series_id=one.id, student_id=student_id)
        for one in series
    )
    joined = [row for row in rows if student_id not in participants[row.id]]
    session.add_all(
        ClassSessionParticipant(class_session_id=row.id, student_id=student_id)
        for row in joined
    )
    await session.flush()
    await _bump(session, [*series, *joined])


async def remove_series_participant(
    session: AsyncSession,
    *,
    series_ids: Sequence[uuid.UUID],
    student_id: uuid.UUID,
    now: datetime,
    owner: SeriesOwner,
) -> None:
    """Remove ``student_id`` from the given (producing, locked) series and
    from every B3 occurrence of the owner (audited ``delete`` events, #47
    D6), then bump the versions of what changed. Past, started,
    ``completed`` and ``cancelled`` occurrences keep the student; no state
    changes. The caller has closed the enrollment."""
    series = await _locked_series(session, series_ids, owner)
    left_series: list[ClassSeries] = []
    if series:
        members = list(
            await session.scalars(
                select(ClassSeriesParticipant).where(
                    ClassSeriesParticipant.class_series_id.in_(
                        [one.id for one in series]
                    ),
                    ClassSeriesParticipant.student_id == student_id,
                )
            )
        )
        by_id = {one.id: one for one in series}
        left_series = [by_id[member.class_series_id] for member in members]
        for member in members:
            await session.delete(member)
    rows = await lock_affected_rows(session, owner, now)
    left_rows: list[ClassSession] = []
    if rows:
        by_row = {row.id: row for row in rows}
        found = list(
            await session.scalars(
                select(ClassSessionParticipant).where(
                    ClassSessionParticipant.class_session_id.in_(list(by_row)),
                    ClassSessionParticipant.student_id == student_id,
                )
            )
        )
        left_rows = [by_row[p.class_session_id] for p in found]
        for participant in found:
            await session.delete(participant)
    await session.flush()
    await _bump(session, [*left_series, *left_rows])
