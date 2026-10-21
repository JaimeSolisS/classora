"""Weekly recurrence rules of class series (#53 C; spec §3; ADR-0005 D4, D5).

Pure rules, no database. A series stores its rule as **structured fields**
(:class:`RecurrenceFields`: ISO weekdays, an interval of 1-4 weeks, and an
inclusive local ``until`` date **or** a ``count`` of 1-200) plus the derived,
normalized RFC 5545 text from :func:`build_rrule`, its local start date and
wall-clock start time, a duration and the center zone (ADR-0004). The API
takes only the structured fields; the ``RRULE`` text is never parsed from a
client.

**Normalized text** (:func:`build_rrule`):
``FREQ=WEEKLY;INTERVAL=<n>;WKST=MO;BYDAY=<MO,TU,… in ISO order>`` followed
by ``;UNTIL=<YYYYMMDD>T235959`` (a floating local time: the end of the local
day) or ``;COUNT=<n>``, or by nothing (open-ended). ``WKST=MO`` is always
written, so ``INTERVAL>1`` with several weekdays expands the same way
everywhere.

**Expansion and the DST rule** (:func:`expand`): the rule is expanded with
``dateutil.rrule.rrulestr`` in **naive local wall-clock time**, from
``dtstart = datetime.combine(starts_on, start_time)``. Each local start then
gets ``ZoneInfo(zone)`` with ``fold=0`` and is converted to UTC:

- an **ambiguous** local time (the repeated hour when clocks fall back)
  takes the **first** instant;
- a **nonexistent** local time (the skipped hour when clocks spring
  forward) takes the offset from before the change, so it moves **forward
  by the gap** (02:30 becomes 03:30);
- the end is the start plus the duration in **elapsed** minutes, so
  ``scheduled_minutes`` always equals the series duration, even across the
  repeated hour.

``COUNT`` counts **every** rule occurrence, including the ones later stored
as ``skipped_closure`` (RFC 5545; no makeup, ADR-0005 D6). A ``starts_on``
that isn't one of the weekdays is allowed: the first occurrence is the first
matching date on or after it (``dateutil``'s semantics).
"""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from dateutil.rrule import rrule, rrulestr

WEEKDAY_CODES: Final = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
"""RFC 5545 day codes by ISO weekday (1 = Monday … 7 = Sunday)."""

INTERVAL_MIN: Final = 1
INTERVAL_MAX: Final = 4
COUNT_MIN: Final = 1
COUNT_MAX: Final = 200

_MARGIN: Final = timedelta(days=1)
"""Local expansion margin around a window given in instants (offsets are
less than a day)."""


class InvalidRecurrenceError(ValueError):
    """The rule is not valid: ``field`` is ``weekdays``, ``interval``,
    ``until``, ``count`` or ``recurrence`` (the rule as a whole) and
    ``reason`` one of ``weekdays_empty``, ``weekday_out_of_range``,
    ``weekdays_duplicate``, ``interval_out_of_range``, ``until_and_count``,
    ``count_out_of_range``, ``until_before_start`` and ``no_occurrences``."""

    def __init__(self, field: str, reason: str) -> None:
        super().__init__(f"{field}: {reason}")
        self.field = field
        self.reason = reason


@dataclass(frozen=True)
class RecurrenceFields:
    """A weekly rule as structured fields (ADR-0005 D4 subset)."""

    weekdays: tuple[int, ...]
    interval: int
    until_on: date | None
    count: int | None


@dataclass(frozen=True)
class Occurrence:
    """One occurrence of a series: ``original_start`` is the rule's instant
    (it equals ``starts_at`` when generated), ``local_date`` its local date
    in the series zone."""

    original_start: datetime
    starts_at: datetime
    ends_at: datetime
    local_date: date


def validate_recurrence(fields: RecurrenceFields, starts_on: date) -> None:
    """Raise :class:`InvalidRecurrenceError` for the first broken rule, in
    the order weekdays, interval, until/count, count, until."""
    if not fields.weekdays:
        raise InvalidRecurrenceError("weekdays", "weekdays_empty")
    if any(not 1 <= day <= 7 for day in fields.weekdays):
        raise InvalidRecurrenceError("weekdays", "weekday_out_of_range")
    if len(set(fields.weekdays)) != len(fields.weekdays):
        raise InvalidRecurrenceError("weekdays", "weekdays_duplicate")
    if not INTERVAL_MIN <= fields.interval <= INTERVAL_MAX:
        raise InvalidRecurrenceError("interval", "interval_out_of_range")
    if fields.until_on is not None and fields.count is not None:
        raise InvalidRecurrenceError("until", "until_and_count")
    if fields.count is not None and not COUNT_MIN <= fields.count <= COUNT_MAX:
        raise InvalidRecurrenceError("count", "count_out_of_range")
    if fields.until_on is not None and fields.until_on < starts_on:
        raise InvalidRecurrenceError("until", "until_before_start")


def build_rrule(fields: RecurrenceFields) -> str:
    """The normalized ``RRULE`` text of valid fields (see the module
    docstring)."""
    days = ",".join(WEEKDAY_CODES[day - 1] for day in sorted(set(fields.weekdays)))
    text = f"FREQ=WEEKLY;INTERVAL={fields.interval};WKST=MO;BYDAY={days}"
    if fields.until_on is not None:
        return f"{text};UNTIL={fields.until_on:%Y%m%d}T235959"
    if fields.count is not None:
        return f"{text};COUNT={fields.count}"
    return text


def _rule(series_rule: str, starts_on: date, start_time: time) -> rrule:
    parsed = rrulestr(series_rule, dtstart=datetime.combine(starts_on, start_time))
    if not isinstance(parsed, rrule):  # pragma: no cover - one RRULE, no set
        raise ValueError("expected a single RRULE")
    return parsed


def to_instant(local_start: datetime, zone: str) -> datetime:
    """A naive local wall-clock time as a UTC instant, ``fold=0`` (the DST
    rule of the module docstring)."""
    return local_start.replace(tzinfo=ZoneInfo(zone), fold=0).astimezone(UTC)


def _occurrence(local_start: datetime, duration_minutes: int, zone: str) -> Occurrence:
    starts_at = to_instant(local_start, zone)
    return Occurrence(
        original_start=starts_at,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(minutes=duration_minutes),
        local_date=local_start.date(),
    )


def expand(
    series_rule: str,
    *,
    starts_on: date,
    start_time: time,
    duration_minutes: int,
    zone: str,
    window_start: datetime,
    window_end: datetime,
) -> list[Occurrence]:
    """The occurrences with ``window_start <= starts_at < window_end``
    (half-open on instants; both bounds aware), by start."""
    tz = ZoneInfo(zone)
    low = window_start.astimezone(tz).replace(tzinfo=None) - _MARGIN
    high = window_end.astimezone(tz).replace(tzinfo=None) + _MARGIN
    rule = _rule(series_rule, starts_on, start_time)
    found = (
        _occurrence(local, duration_minutes, zone)
        for local in rule.between(low, high, inc=True)
    )
    return [o for o in found if window_start <= o.starts_at < window_end]


def iter_local_starts(
    series_rule: str, *, starts_on: date, start_time: time
) -> Iterator[datetime]:
    """Every naive local start of the rule, in order (infinite when the rule
    is open-ended)."""
    yield from _rule(series_rule, starts_on, start_time)


def last_occurrence_on(fields: RecurrenceFields, starts_on: date) -> date | None:
    """The local date of the last occurrence of a finite rule (``UNTIL`` or
    ``COUNT``); ``None`` when it is open-ended. A finite rule with no
    occurrence raises ``InvalidRecurrenceError("recurrence",
    "no_occurrences")``. The fields must be valid."""
    if fields.until_on is None and fields.count is None:
        return None
    # The time of day doesn't change the dates (UNTIL is the end of its day).
    dates = list(
        iter_local_starts(build_rrule(fields), starts_on=starts_on, start_time=time())
    )
    if not dates:
        raise InvalidRecurrenceError("recurrence", "no_occurrences")
    return dates[-1].date()


def horizon_end(now: datetime, weeks: int) -> datetime:
    """The end of the rolling materialization window ``[now, horizon_end)``."""
    return now + timedelta(weeks=weeks)
