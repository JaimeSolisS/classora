"""Available individual-session times for families (#59 B3, B4; spec §3
"slots visible, request only"; ADR-0005 D1, D2; ADR-0004).

Pure rules, no database: the loader is in
:mod:`~app.modules.scheduling.requests`.

**Candidates** (:func:`family_candidates`): on every local date of the
range, starts on a **30-minute local grid** (``:00`` / ``:30``) from that
weekday's opening time (rounded up to the grid) to the last start whose end
is not after the closing time. A closed weekday (no operating hours) or a
closure date gives none. Wall-clock times are attached with ``fold=0``; a
start in a spring-forward gap doesn't exist and is skipped; a candidate
stays on one local date (#47 ``validate_session_bounds``).

**A candidate is shown** (:func:`family_slot_ok`) only if all hold:

1. no ``notice_window`` (#61 ``violates_booking_notice`` with the policy in
   force now);
2. no ``outside_operating_hours`` and no ``center_closed``;
3. no ``student_overlap`` (the student's sessions in
   ``STUDENT_OCCUPYING_STATES``);
4. **at least one eligible tutor**, #51's rule in memory
   (:mod:`~app.modules.scheduling.eligibility`): an approved qualification
   for the subject (the loader starts from ``staff.service.qualified_tutor_ids``,
   so revoked ones are gone), an active account with the ``tutor`` role,
   weekly windows covering the interval with no time off (a center closure
   never drops a tutor, #51 B2; a tutor without windows is never available)
   and no overlap with the tutor's sessions in ``TUTOR_OCCUPYING_STATES``.

Checks 1-3 are #50's :func:`~app.modules.scheduling.suggestions.slot_conflicts`
over a :class:`~app.modules.scheduling.suggestions.SlotContext` without a
tutor or a room. **Not considered:** rooms (#60 picks one), workload
warnings (never blocking) and pending requests (they hold no time, W2.2).
A slot overlapping one of this student's pending requests is still shown,
flagged ``already_requested``.

Families never learn which tutor (or how many) made a slot available (#59
E1): nothing here returns a tutor.
"""

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from app.modules.scheduling.conflicts import covers_operating_hours, local_slot
from app.modules.scheduling.errors import InvalidSessionTimesError
from app.modules.scheduling.suggestions import (
    Busy,
    Candidate,
    OperatingDay,
    SlotContext,
    local_dates,
    slot_conflicts,
)
from app.modules.scheduling.times import validate_session_bounds
from app.modules.staff import service as staff

GRID_MINUTES: Final = 30
"""The candidate starts' step in local wall-clock minutes (``:00`` / ``:30``)."""

NO_CLOSED_DATES: Final[frozenset[date]] = frozenset()
"""A center closure never drops a tutor (#51 B2): it is a slot conflict."""

_DAY_MINUTES: Final = 24 * 60


@dataclass(frozen=True)
class TutorSlotFacts:
    """One qualified, active tutor: their availability rules for the range
    and their occupying sessions in it."""

    user_id: uuid.UUID
    rules: staff.TutorAvailabilityRules
    sessions: tuple[Busy, ...]


@dataclass(frozen=True)
class FamilySlotContext:
    """Everything :func:`family_slot_ok` needs, loaded once per request."""

    slot: SlotContext
    """The #50 context without a tutor or a room: the student's occupying
    sessions, the closures, the operating hours, the policy and now."""
    tutors: tuple[TutorSlotFacts, ...]
    """The qualified tutors with an active ``tutor`` account (#51 B1)."""
    pending: tuple[tuple[datetime, datetime], ...]
    """This student's ``pending`` requests overlapping the range."""


def _local_start(day: date, minute: int, tz: ZoneInfo) -> datetime | None:
    """The instant (UTC) of the local wall-clock time, ``fold=0``; ``None``
    in a spring-forward gap."""
    local = datetime.combine(day, time(minute // 60, minute % 60), tzinfo=tz)
    instant = local.astimezone(UTC)
    if instant.astimezone(tz).replace(tzinfo=None) != local.replace(tzinfo=None):
        return None
    return instant


def _minutes(value: str) -> int:
    parsed = time.fromisoformat(value)
    return parsed.hour * 60 + parsed.minute


def _hours(
    operating_hours: Iterable[OperatingDay],
) -> list[tuple[int, time, time]]:
    return [
        (
            day.weekday,
            time.fromisoformat(day.opens_at),
            time.fromisoformat(day.closes_at),
        )
        for day in operating_hours
    ]


def candidates_on(
    day: date,
    zone: str,
    duration_minutes: int,
    operating_hours: Sequence[OperatingDay],
    closed_dates: Iterable[date] = NO_CLOSED_DATES,
) -> list[Candidate]:
    """The B3 candidates of one local date, in time order."""
    if day in set(closed_dates):
        return []
    weekday = day.isoweekday()
    opening = next((item for item in operating_hours if item.weekday == weekday), None)
    if opening is None:
        return []
    tz = ZoneInfo(zone)
    hours = _hours(operating_hours)
    first = -(-_minutes(opening.opens_at) // GRID_MINUTES) * GRID_MINUTES
    duration = timedelta(minutes=duration_minutes)
    found: list[Candidate] = []
    for minute in range(first, _DAY_MINUTES, GRID_MINUTES):
        starts_at = _local_start(day, minute, tz)
        if starts_at is None:
            continue
        ends_at = starts_at + duration
        try:
            validate_session_bounds(starts_at, ends_at, zone)
        except InvalidSessionTimesError:
            continue
        slot = local_slot(starts_at, ends_at, zone)
        if covers_operating_hours(hours, slot.weekday, slot.start, slot.end):
            found.append(Candidate(starts_at, ends_at))
    return found


def family_candidates(
    first: date,
    last: date,
    zone: str,
    duration_minutes: int,
    operating_hours: Sequence[OperatingDay],
    closed_dates: Iterable[date] = NO_CLOSED_DATES,
) -> dict[date, list[Candidate]]:
    """The B3 candidates of every local date ``first``..``last``, by date
    (every date is a key, in order)."""
    closed = frozenset(closed_dates)
    return {
        day: candidates_on(day, zone, duration_minutes, operating_hours, closed)
        for day in local_dates(first, last)
    }


def _overlaps(
    intervals: Iterable[Busy | tuple[datetime, datetime]], candidate: Candidate
) -> bool:
    for interval in intervals:
        starts_at, ends_at = (
            (interval.starts_at, interval.ends_at)
            if isinstance(interval, Busy)
            else interval
        )
        if starts_at < candidate.ends_at and ends_at > candidate.starts_at:
            return True
    return False


def tutor_is_eligible(tutor: TutorSlotFacts, candidate: Candidate, zone: str) -> bool:
    """#51's availability and overlap rule for one tutor (the qualification
    and the account were checked by the loader)."""
    if _overlaps(tutor.sessions, candidate):
        return False
    return staff.evaluate_interval(
        tutor.rules.windows,
        tutor.rules.exceptions,
        NO_CLOSED_DATES,
        candidate.starts_at,
        candidate.ends_at,
        zone,
    ).available


def _without(
    context: FamilySlotContext, session_ids: frozenset[uuid.UUID]
) -> FamilySlotContext:
    """The context without the sessions ``session_ids`` (#62 E4)."""
    return replace(
        context,
        slot=replace(
            context.slot,
            student_sessions=tuple(
                busy
                for busy in context.slot.student_sessions
                if busy.class_session_id not in session_ids
            ),
        ),
        tutors=tuple(
            replace(
                tutor,
                sessions=tuple(
                    busy
                    for busy in tutor.sessions
                    if busy.class_session_id not in session_ids
                ),
            )
            for tutor in context.tutors
        ),
    )


def family_slot_ok(
    candidate: Candidate,
    context: FamilySlotContext,
    *,
    exclude_session_ids: frozenset[uuid.UUID] = frozenset(),
) -> bool:
    """Whether the candidate is shown (module docstring, B4 1-4).

    ``exclude_session_ids`` (#62 E4: a family's reschedule proposal for the
    session being moved) leaves those sessions out of the student's and
    the tutors' occupying sessions, so a proposal never conflicts with the
    session it replaces. ``GET /available-slots`` passes none."""
    if exclude_session_ids:
        context = _without(context, exclude_session_ids)
    if slot_conflicts(candidate, context.slot):
        return False
    return any(
        tutor_is_eligible(tutor, candidate, context.slot.zone)
        for tutor in context.tutors
    )


def already_requested(candidate: Candidate, context: FamilySlotContext) -> bool:
    """The candidate overlaps one of this student's pending requests."""
    return _overlaps(context.pending, candidate)


def available_days(
    context: FamilySlotContext, first: date, last: date, duration_minutes: int
) -> list[tuple[date, list[tuple[Candidate, bool]]]]:
    """Every local date of the range with its shown slots and their
    ``already_requested`` flag."""
    by_day = family_candidates(
        first,
        last,
        context.slot.zone,
        duration_minutes,
        context.slot.operating_hours,
        context.slot.closed_dates,
    )
    if not context.tutors:
        return [(day, []) for day in by_day]
    return [
        (
            day,
            [
                (candidate, already_requested(candidate, context))
                for candidate in candidates
                if family_slot_ok(candidate, context)
            ],
        )
        for day, candidates in by_day.items()
    ]
