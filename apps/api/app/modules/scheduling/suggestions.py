"""Alternative slots for a one-off individual session (#50 B3-B6; spec §3
"Recommend alternate slots"; ADR-0005; ADR-0004).

Pure rules, no database: the candidates, the in-memory conflict check over a
preloaded :class:`SlotContext`, and the ranking. The route and the loader
are in :mod:`~app.modules.scheduling.slot_search`.

**Only the time changes** (#50 E4): the student, subject, tutor, classroom
and duration stay as requested; preferred classrooms are not used.

**Candidates** (:func:`candidate_slots`): starts on a 15-minute grid of local
wall-clock time (``:00``, ``:15``, ``:30``, ``:45``) on every local date of
the window, each lasting the requested elapsed minutes. A local start is
turned into an instant with ``fold=0``: a start in a spring-forward gap
doesn't exist and is skipped, an ambiguous (fall-back) one gives only the
first instant (the ADR-0005 D4 rule). A candidate failing #47's
``validate_session_bounds`` (one that crosses the local date) is skipped,
and the requested start itself is never a candidate.

**The check** (:func:`slot_conflicts`) is the create path's
(:func:`app.modules.scheduling.booking.find_conflicts`) over preloaded data,
with the same pure rules and data sources:

- ``student_overlap`` / ``tutor_overlap`` / ``room_overlap``: the student's,
  tutor's and room's sessions in ``STUDENT_`` / ``TUTOR_`` /
  ``ROOM_OCCUPYING_STATES`` that overlap the candidate;
- ``room_capacity_exceeded``: ``capacity_violations`` with the room's
  capacity (``catalog.service.get_classroom``);
- ``tutor_unavailable``: ``staff.service.evaluate_interval`` over the
  tutor's windows and exceptions (``staff.service.tutor_availability_rules``)
  and the closures, without the ``center_closed`` reason;
- ``room_unavailable``: ``catalog.service.windows_cover`` over the room's
  windows (``catalog.service.classroom_weekly_windows``);
- ``center_closed``: the closures of the window
  (``catalog.service.closures_between``);
- ``outside_operating_hours``: ``covers_operating_hours`` over
  ``administration.service.get_business_settings``;
- ``tutor_not_qualified``: ``staff.service.is_qualified``, read once; it
  doesn't depend on the time, so no candidate can pass
  (:func:`search_slots` returns nothing);
- ``notice_window``: ``administration.service.violates_booking_notice``
  with the policy in force now (``get_scheduling_policy``).

The tutor checks run only with a tutor and the room checks only with a room.

**Ranking** (:func:`search_slots`): the passing candidates by absolute
distance from the requested start, a tie to the earlier start; the first
``limit``. Each slot carries the workload warnings a create would get
(:func:`~app.modules.scheduling.workload.workload_warnings`); warnings don't
affect the ranking.
"""

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from app.modules.administration import service as administration
from app.modules.catalog import service as catalog
from app.modules.scheduling.capacity import capacity_violations
from app.modules.scheduling.conflicts import (
    CENTER_CLOSED,
    NOTICE_WINDOW,
    OUTSIDE_OPERATING_HOURS,
    ROOM_OVERLAP,
    ROOM_UNAVAILABLE,
    STUDENT_OVERLAP,
    TUTOR_NOT_QUALIFIED,
    TUTOR_OVERLAP,
    TUTOR_UNAVAILABLE,
    Conflict,
    LocalSlot,
    covers_operating_hours,
    local_slot,
    sort_conflicts,
    tutor_unavailable_reasons,
)
from app.modules.scheduling.errors import InvalidSessionTimesError
from app.modules.scheduling.times import validate_session_bounds
from app.modules.scheduling.workload import (
    WorkloadSession,
    WorkloadWarning,
    workload_warnings,
)
from app.modules.staff import service as staff

GRID_MINUTES: Final = 15
"""The candidate starts' step in local wall-clock minutes."""

OVERLAP_IDS_LIMIT: Final = 10
"""At most this many overlapping session ids in a conflict's details (as
the create path)."""

_DAY_MINUTES: Final = 24 * 60


@dataclass(frozen=True)
class Busy:
    """An occupying session of the student or the room."""

    class_session_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime


@dataclass(frozen=True)
class OperatingDay:
    """One weekday's opening hours, as the business settings give them."""

    weekday: int
    opens_at: str
    closes_at: str


@dataclass(frozen=True)
class SlotContext:
    """Everything :func:`slot_conflicts` needs, loaded once per request
    (``scheduling.slot_search.load_slot_context``)."""

    zone: str
    kind: str
    student_id: uuid.UUID
    subject_id: uuid.UUID
    tutor_user_id: uuid.UUID | None
    classroom_id: uuid.UUID | None
    student_sessions: tuple[Busy, ...]
    tutor_sessions: tuple[WorkloadSession, ...]
    room_sessions: tuple[Busy, ...]
    tutor_rules: staff.TutorAvailabilityRules | None
    tutor_qualified: bool
    limits: staff.TeachingLimits
    room_windows: tuple[tuple[int, time, time], ...]
    room_capacity: int | None
    closed_dates: frozenset[date]
    operating_hours: tuple[OperatingDay, ...]
    policy: administration.SchedulingPolicyValues
    now: datetime


@dataclass(frozen=True)
class Candidate:
    """A candidate interval (UTC instants)."""

    starts_at: datetime
    ends_at: datetime


@dataclass(frozen=True)
class FoundSlot:
    """A candidate that passed, with its workload warnings."""

    starts_at: datetime
    ends_at: datetime
    warnings: tuple[WorkloadWarning, ...]


def local_dates(first: date, last: date) -> list[date]:
    """The local dates ``first``..``last``, inclusive."""
    return [first + timedelta(days=n) for n in range((last - first).days + 1)]


def _local_start(day: date, minute: int, tz: ZoneInfo) -> datetime | None:
    """The instant (UTC) of the local wall-clock time, ``fold=0``; ``None``
    when it doesn't exist (a spring-forward gap)."""
    local = datetime.combine(day, time(minute // 60, minute % 60), tzinfo=tz)
    instant = local.astimezone(UTC)
    if instant.astimezone(tz).replace(tzinfo=None) != local.replace(tzinfo=None):
        return None
    return instant


def candidate_slots(
    search_from: date,
    search_to: date,
    zone: str,
    duration_minutes: int,
    requested_starts_at: datetime,
) -> list[Candidate]:
    """Every candidate of the window, in time order (see the module
    docstring)."""
    tz = ZoneInfo(zone)
    duration = timedelta(minutes=duration_minutes)
    requested = requested_starts_at.astimezone(UTC)
    found: list[Candidate] = []
    for day in local_dates(search_from, search_to):
        for minute in range(0, _DAY_MINUTES, GRID_MINUTES):
            starts_at = _local_start(day, minute, tz)
            if starts_at is None or starts_at == requested:
                continue
            ends_at = starts_at + duration
            try:
                validate_session_bounds(starts_at, ends_at, zone)
            except InvalidSessionTimesError:
                continue
            found.append(Candidate(starts_at, ends_at))
    return found


def _overlap_ids(
    sessions: Iterable[Busy | WorkloadSession], candidate: Candidate
) -> list[str]:
    overlapping = sorted(
        (
            s
            for s in sessions
            if s.starts_at < candidate.ends_at and s.ends_at > candidate.starts_at
        ),
        key=lambda s: (s.starts_at, str(s.class_session_id)),
    )
    return [str(s.class_session_id) for s in overlapping[:OVERLAP_IDS_LIMIT]]


def _overlap_conflicts(candidate: Candidate, context: SlotContext) -> list[Conflict]:
    conflicts: list[Conflict] = []
    students = _overlap_ids(context.student_sessions, candidate)
    if students:
        conflicts.append(
            Conflict(
                STUDENT_OVERLAP,
                {"student_id": str(context.student_id), "class_session_ids": students},
            )
        )
    if context.tutor_user_id is not None:
        tutors = _overlap_ids(context.tutor_sessions, candidate)
        if tutors:
            conflicts.append(Conflict(TUTOR_OVERLAP, {"class_session_ids": tutors}))
    if context.classroom_id is not None:
        rooms = _overlap_ids(context.room_sessions, candidate)
        if rooms:
            conflicts.append(
                Conflict(
                    ROOM_OVERLAP,
                    {
                        "classroom_id": str(context.classroom_id),
                        "class_session_ids": rooms,
                    },
                )
            )
    return conflicts


def _tutor_conflicts(candidate: Candidate, context: SlotContext) -> list[Conflict]:
    conflicts: list[Conflict] = []
    rules = context.tutor_rules
    if rules is not None:
        availability = staff.evaluate_interval(
            rules.windows,
            rules.exceptions,
            context.closed_dates,
            candidate.starts_at,
            candidate.ends_at,
            context.zone,
        )
        reasons = tutor_unavailable_reasons(availability.reasons)
        if reasons:
            conflicts.append(Conflict(TUTOR_UNAVAILABLE, {"reasons": reasons}))
    if not context.tutor_qualified:
        conflicts.append(
            Conflict(TUTOR_NOT_QUALIFIED, {"subject_id": str(context.subject_id)})
        )
    return conflicts


def _center_conflicts(slot: LocalSlot, context: SlotContext) -> list[Conflict]:
    conflicts: list[Conflict] = []
    if slot.local_date in context.closed_dates:
        conflicts.append(Conflict(CENTER_CLOSED, {"date": slot.local_date.isoformat()}))
    hours = [
        (
            day.weekday,
            time.fromisoformat(day.opens_at),
            time.fromisoformat(day.closes_at),
        )
        for day in context.operating_hours
    ]
    if not covers_operating_hours(hours, slot.weekday, slot.start, slot.end):
        day = next(
            (item for item in context.operating_hours if item.weekday == slot.weekday),
            None,
        )
        conflicts.append(
            Conflict(
                OUTSIDE_OPERATING_HOURS,
                {
                    "date": slot.local_date.isoformat(),
                    "opens_at": None if day is None else day.opens_at,
                    "closes_at": None if day is None else day.closes_at,
                },
            )
        )
    return conflicts


def slot_conflicts(candidate: Candidate, context: SlotContext) -> list[Conflict]:
    """Every hard and soft conflict of the candidate, sorted
    (:func:`~app.modules.scheduling.conflicts.sort_conflicts`): the create
    path's checks over the preloaded ``context`` (module docstring)."""
    slot = local_slot(candidate.starts_at, candidate.ends_at, context.zone)
    conflicts = _overlap_conflicts(candidate, context)
    conflicts += [
        Conflict(code, {"participants": 1, "room_capacity": context.room_capacity})
        for code in capacity_violations(
            context.kind, 1, group_max=None, room_capacity=context.room_capacity
        )
    ]
    if context.tutor_user_id is not None:
        conflicts += _tutor_conflicts(candidate, context)
    if context.classroom_id is not None and not catalog.windows_cover(
        context.room_windows, slot.weekday, slot.start, slot.end
    ):
        conflicts.append(
            Conflict(ROOM_UNAVAILABLE, {"classroom_id": str(context.classroom_id)})
        )
    conflicts += _center_conflicts(slot, context)
    if administration.violates_booking_notice(
        context.policy, starts_at=candidate.starts_at, at=context.now
    ):
        conflicts.append(
            Conflict(
                NOTICE_WINDOW,
                {"booking_notice_hours": context.policy.booking_notice_hours},
            )
        )
    return sort_conflicts(conflicts)


def rank(
    candidates: Iterable[Candidate], requested_starts_at: datetime
) -> list[Candidate]:
    """By absolute distance between the start and the requested start; a tie
    goes to the earlier start."""
    requested = requested_starts_at.astimezone(UTC)
    return sorted(
        candidates,
        key=lambda c: (abs(c.starts_at - requested), c.starts_at),
    )


def search_slots(
    context: SlotContext,
    candidates: Sequence[Candidate],
    requested_starts_at: datetime,
    limit: int,
) -> list[FoundSlot]:
    """The first ``limit`` candidates in :func:`rank` order that
    :func:`slot_conflicts` passes, each with its workload warnings (none
    without a tutor). A tutor who isn't qualified for the subject gives
    nothing, without checking any candidate."""
    if context.tutor_user_id is not None and not context.tutor_qualified:
        return []
    found: list[FoundSlot] = []
    for candidate in rank(candidates, requested_starts_at):
        if len(found) == limit:
            break
        if slot_conflicts(candidate, context):
            continue
        warnings: list[WorkloadWarning] = []
        if context.tutor_user_id is not None:
            warnings = workload_warnings(
                context.limits,
                context.tutor_sessions,
                (candidate.starts_at, candidate.ends_at),
                context.zone,
            )
        found.append(FoundSlot(candidate.starts_at, candidate.ends_at, tuple(warnings)))
    return found
