"""Tutor availability rules (#45 B; spec §3; ADR-0004). Pure functions, no I/O.

Availability is stored and evaluated in the center's **local wall-clock
time** (``DATE`` and ``TIME``), never as instants: a weekly window 16:00-20:00
means 16:00-20:00 local time on every such weekday, before and after a DST
change.

:func:`evaluate` answers "is the tutor available on ``local_date`` during
``[start, end)``?", checking in this order and returning **every** reason
that applies, in this order:

1. the center is closed that day → ``center_closed``;
2. an ``unavailable`` exception covers the date and is whole-day, or its
   hours overlap ``[start, end)`` → ``time_off`` (``unavailable`` wins over
   ``available``);
3. ``[start, end)`` is covered by the **union** of that weekday's windows
   plus the hours of the ``available`` exceptions on that date (touching
   ranges join, as for rooms in #39) → otherwise
   ``outside_weekly_availability``.

A tutor without windows and without an ``available`` exception is never
available (a tutor must publish availability).

:func:`to_local_intervals` turns an instant interval into local wall-clock
pieces, so M4 can check stored instants with :func:`evaluate`;
:func:`evaluate_interval` does both for an instant interval over preloaded
rules (``staff.service.is_tutor_available_at`` and the M4-05 slot search use
it, so the two can't drift).
:func:`uncovered_ranges` gives what a weekly change takes away (the input of
the M4-18 impact seam, ``staff/assignment_impact.py``).
"""

from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Final, Literal
from zoneinfo import ZoneInfo

type AvailabilityReason = Literal[
    "center_closed", "time_off", "outside_weekly_availability"
]
type ExceptionKind = Literal["unavailable", "available"]

CENTER_CLOSED: Final = "center_closed"
TIME_OFF: Final = "time_off"
OUTSIDE_WEEKLY_AVAILABILITY: Final = "outside_weekly_availability"
REASON_ORDER: Final[tuple[AvailabilityReason, ...]] = (
    CENTER_CLOSED,
    TIME_OFF,
    OUTSIDE_WEEKLY_AVAILABILITY,
)

END_OF_DAY: Final = time.max
"""How a local piece ending at the next midnight ("24:00") is represented."""

_TICK: Final = timedelta(microseconds=1)


@dataclass(frozen=True, slots=True)
class WeeklyWindow:
    """A recurring window: ISO ``weekday`` and ``[starts_at, ends_at)``."""

    weekday: int
    starts_at: time
    ends_at: time


@dataclass(frozen=True, slots=True)
class DatedException:
    """An exception on the inclusive dates ``[starts_on, ends_on]``; without
    hours (both ``None``) it is the whole day (``unavailable`` only)."""

    kind: ExceptionKind
    starts_on: date
    ends_on: date
    starts_at: time | None = None
    ends_at: time | None = None

    def applies_on(self, local_date: date) -> bool:
        return self.starts_on <= local_date <= self.ends_on


@dataclass(frozen=True, slots=True)
class TutorAvailabilityRules:
    """A tutor's weekly windows (every weekday) and the exceptions that share
    a day with a date range: the input of :func:`evaluate` /
    :func:`evaluate_interval` (M4-05 slot search; M4-18 the current and the
    proposed rules of a change)."""

    windows: tuple[WeeklyWindow, ...]
    exceptions: tuple[DatedException, ...]


@dataclass(frozen=True, slots=True)
class AvailabilityResult:
    """Whether the tutor is available, and every reason they are not."""

    available: bool
    reasons: tuple[AvailabilityReason, ...]


def _check_interval(start: time, end: time) -> None:
    if start >= end:
        raise ValueError("start must be before end (no interval across midnight)")


def merge_ranges(ranges: Iterable[tuple[time, time]]) -> list[tuple[time, time]]:
    """The union of ``[start, end)`` ranges, sorted; touching ranges join."""
    merged: list[tuple[time, time]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            if end > merged[-1][1]:
                merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return merged


def covers(ranges: Iterable[tuple[time, time]], start: time, end: time) -> bool:
    """Whether the union of ``ranges`` covers ``[start, end)``."""
    return any(low <= start and end <= high for low, high in merge_ranges(ranges))


def evaluate(
    windows: Iterable[WeeklyWindow],
    exceptions: Iterable[DatedException],
    center_closed: bool,
    local_date: date,
    start: time,
    end: time,
) -> AvailabilityResult:
    """Whether the tutor is available on ``local_date`` during ``[start,
    end)`` (local wall-clock, ``start < end``; see the module docstring).
    ``windows`` may hold every weekday and ``exceptions`` any dates: only
    those that apply count. ``start >= end`` raises :class:`ValueError`."""
    _check_interval(start, end)
    todays = [item for item in exceptions if item.applies_on(local_date)]
    reasons: list[AvailabilityReason] = []
    if center_closed:
        reasons.append(CENTER_CLOSED)
    for item in todays:
        if item.kind != "unavailable":
            continue
        if item.starts_at is None or item.ends_at is None:
            reasons.append(TIME_OFF)
            break
        if item.starts_at < end and start < item.ends_at:
            reasons.append(TIME_OFF)
            break
    ranges = [
        (window.starts_at, window.ends_at)
        for window in windows
        if window.weekday == local_date.isoweekday()
    ]
    ranges.extend(
        (item.starts_at, item.ends_at)
        for item in todays
        if item.kind == "available"
        and item.starts_at is not None
        and item.ends_at is not None
    )
    if not covers(ranges, start, end):
        reasons.append(OUTSIDE_WEEKLY_AVAILABILITY)
    return AvailabilityResult(available=not reasons, reasons=tuple(reasons))


def merge_results(results: Iterable[AvailabilityResult]) -> AvailabilityResult:
    """Available only if every result is; the reasons of all, deduplicated,
    in the :data:`REASON_ORDER`."""
    found = {reason for result in results for reason in result.reasons}
    reasons = tuple(reason for reason in REASON_ORDER if reason in found)
    return AvailabilityResult(available=not reasons, reasons=reasons)


# --- Instants to local wall-clock pieces ---------------------------------------------


def _wall(instant: datetime, offset: timedelta) -> datetime:
    """The naive local wall-clock time of a UTC ``instant`` at ``offset``."""
    return instant.replace(tzinfo=None) + offset


def _offset(instant: datetime, zone: ZoneInfo) -> timedelta:
    offset = instant.astimezone(zone).utcoffset()
    assert offset is not None  # noqa: S101 - a ZoneInfo always has one
    return offset


def _next_midnight(after: datetime, local_day: date, zone: ZoneInfo) -> datetime:
    """The first instant after ``after`` (UTC) at which the local date is
    past ``local_day``: the next local midnight. A non-existent midnight (a
    DST gap) maps to the transition; an ambiguous one to the later instant
    when the earlier one isn't after ``after``."""
    midnight = datetime.combine(local_day + timedelta(days=1), time(0), tzinfo=zone)
    instant = midnight.astimezone(UTC)
    if instant <= after:
        instant = midnight.replace(fold=1).astimezone(UTC)
    return instant


def _backward_shift(start: datetime, end: datetime, zone: ZoneInfo) -> datetime | None:
    """The first instant in ``(start, end)`` (UTC) at which the zone's
    offset falls (the clocks go back), or ``None``. A forward shift (a DST
    gap) keeps the wall clock increasing, so it doesn't split a piece."""
    before = _offset(start, zone)
    if _offset(end - _TICK, zone) >= before:
        return None
    low, high = start, end - _TICK  # offset(low) == before > offset(high)
    while high - low > _TICK:
        middle = low + (high - low) / 2
        if _offset(middle, zone) == before:
            low = middle
        else:
            high = middle
    return high


def to_local_intervals(
    starts_at: datetime, ends_at: datetime, zone: str
) -> list[tuple[date, time, time]]:
    """The local wall-clock pieces ``(local_date, start, end)`` of the
    instant interval ``[starts_at, ends_at)`` in the IANA ``zone``.

    - An interval across local midnight gives one piece per local day; a
      piece that runs to the next midnight ends at :data:`END_OF_DAY`
      ("24:00").
    - A DST gap (clocks forward) stays one piece: 01:30-03:30 local wall
      time for one real hour.
    - A DST overlap (clocks back) splits the piece where the wall clock
      jumps back, so the repeated hour gives two pieces with the same wall
      times, both checked.

    Naive datetimes and ``starts_at >= ends_at`` raise :class:`ValueError`.
    """
    for value in (starts_at, ends_at):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("starts_at and ends_at must be timezone-aware")
    if starts_at >= ends_at:
        raise ValueError("starts_at must be before ends_at")
    tz = ZoneInfo(zone)
    end = ends_at.astimezone(UTC)
    cursor = starts_at.astimezone(UTC)
    pieces: list[tuple[date, time, time]] = []
    while cursor < end:
        start_wall = _wall(cursor, _offset(cursor, tz))
        day = start_wall.date()
        boundary = min(end, _next_midnight(cursor, day, tz))
        shift = _backward_shift(cursor, boundary, tz)
        if shift is not None:
            boundary = shift
        end_wall = _wall(boundary, _offset(boundary - _TICK, tz))
        piece_end = END_OF_DAY if end_wall.date() > day else end_wall.time()
        pieces.append((day, start_wall.time(), piece_end))
        cursor = boundary
    return pieces


def evaluate_interval(
    windows: Iterable[WeeklyWindow],
    exceptions: Iterable[DatedException],
    closed_dates: Collection[date],
    starts_at: datetime,
    ends_at: datetime,
    zone: str,
) -> AvailabilityResult:
    """Whether the tutor is available during the instant interval
    ``[starts_at, ends_at)``: every local piece in ``zone``
    (:func:`to_local_intervals`) is checked with :func:`evaluate`, the piece's
    date counting as closed when it is in ``closed_dates``, and the results
    are merged (:func:`merge_results`). Naive datetimes or ``starts_at >=
    ends_at`` raise :class:`ValueError`."""
    window_list, exception_list = list(windows), list(exceptions)
    return merge_results(
        evaluate(window_list, exception_list, day in closed_dates, day, start, end)
        for day, start, end in to_local_intervals(starts_at, ends_at, zone)
    )


# --- What a weekly change takes away (the impact seam's input) ---------------------


def _subtract(
    ranges: Sequence[tuple[time, time]], removed: Sequence[tuple[time, time]]
) -> list[tuple[time, time]]:
    """``ranges`` minus ``removed`` (both merged), as ``[start, end)``."""
    left: list[tuple[time, time]] = []
    for low, high in ranges:
        cursor = low
        for cut_low, cut_high in removed:
            if cut_high <= cursor or cut_low >= high:
                continue
            if cut_low > cursor:
                left.append((cursor, cut_low))
            cursor = max(cursor, cut_high)
        if cursor < high:
            left.append((cursor, high))
    return left


def uncovered_ranges(
    old: Iterable[WeeklyWindow], new: Iterable[WeeklyWindow]
) -> list[WeeklyWindow]:
    """The weekly time that ``old`` covered and ``new`` no longer covers,
    by weekday and start (touching ranges joined): what a weekly change
    takes away. Empty when the change only adds time."""
    old_list, new_list = list(old), list(new)
    removed: list[WeeklyWindow] = []
    for weekday in range(1, 8):
        before = merge_ranges(
            (w.starts_at, w.ends_at) for w in old_list if w.weekday == weekday
        )
        after = merge_ranges(
            (w.starts_at, w.ends_at) for w in new_list if w.weekday == weekday
        )
        removed.extend(
            WeeklyWindow(weekday, low, high) for low, high in _subtract(before, after)
        )
    return removed
