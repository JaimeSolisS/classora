"""Analytics KPI rules, pure (#126; definitions in ``_docs/kpis.md``).

No I/O: :mod:`~app.modules.reporting.kpis` reads the rows and passes plain
values here.

- :func:`months`: the center-local calendar months that meet a range, each
  clipped to it, oldest first (E4).
- :func:`percent`: #84's rule (``academics`` ``progress_dashboard_rules``):
  half-up to two places, ``None`` for a zero denominator.
- :func:`previous_window`: the window of the same length just before a range
  (K5, E8).
- :func:`available_minutes`: one room's open minutes on one local date (K4,
  E7): operating hours ∩ the room's windows, ``0`` on a closure day, in
  wall-clock minutes.
- :func:`closed_days`: the local dates of a range that active closures cover.
- :func:`retention`: the K5 set arithmetic.
"""

from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, time, timedelta
from decimal import Decimal

from app.modules.academics import service as academics


@dataclass(frozen=True)
class Month:
    """A center-local calendar month clipped to the range: ``month`` is
    ``"YYYY-MM"``, ``start`` and ``end`` inclusive."""

    month: str
    start: date
    end: date


def months(start: date, end: date) -> list[Month]:
    """The months that meet ``[start, end]``, oldest first, each clipped to
    the range (13 for a 366-date span that starts mid-month)."""
    if start > end:
        raise ValueError("start must not be after end")
    found: list[Month] = []
    first = start
    while first <= end:
        following = (first.replace(day=1) + timedelta(days=32)).replace(day=1)
        last = min(end, following - timedelta(days=1))
        found.append(Month(f"{first.year:04d}-{first.month:02d}", first, last))
        first = following
    return found


def month_key(day: date) -> str:
    """The ``"YYYY-MM"`` of a local date."""
    return f"{day.year:04d}-{day.month:02d}"


def percent(numerator: int, denominator: int) -> Decimal | None:
    """#84 E6: ``numerator / denominator × 100``, half-up to two places
    (``None`` when ``denominator`` is 0)."""
    return academics.progress_dashboard_rules.percent(numerator, denominator)


def attendance_percent(present: int, late: int, absent: int) -> Decimal | None:
    """#84 E6: late counts as attended; unrecorded marks are left out."""
    return academics.progress_dashboard_rules.attendance_percent(present, late, absent)


def previous_window(start: date, end: date) -> tuple[date, date]:
    """``[from − n, from − 1]`` with ``n = to − from + 1`` (K5)."""
    days = (end - start).days + 1
    return start - timedelta(days=days), start - timedelta(days=1)


def _minutes(value: time) -> int:
    return value.hour * 60 + value.minute


def _overlap(a: tuple[time, time], b: tuple[time, time]) -> int:
    low = max(_minutes(a[0]), _minutes(b[0]))
    high = min(_minutes(a[1]), _minutes(b[1]))
    return max(0, high - low)


def available_minutes(
    day: date,
    *,
    hours: dict[int, tuple[time, time]],
    windows: Sequence[tuple[int, time, time]],
    closed: bool,
) -> int:
    """K4: one room's available minutes on local date ``day``.

    ``hours`` maps an ISO weekday to the center's ``[opens_at, closes_at)``
    (a missing weekday is closed); ``windows`` are the room's ``(weekday,
    starts_at, ends_at)`` (empty: no restriction). ``0`` when ``closed`` (an
    active closure) or the weekday has no hours; otherwise ``|H|`` with no
    windows, else ``Σ |w ∩ H|`` over that weekday's windows. Wall-clock
    minutes (DST ignored)."""
    weekday = day.isoweekday()
    if closed or weekday not in hours:
        return 0
    open_hours = hours[weekday]
    if not windows:
        return _minutes(open_hours[1]) - _minutes(open_hours[0])
    return sum(
        _overlap((starts, ends), open_hours)
        for window_day, starts, ends in windows
        if window_day == weekday
    )


def closed_days(
    spans: Iterable[tuple[date, date]], start: date, end: date
) -> frozenset[date]:
    """The dates of ``[start, end]`` inside at least one ``(starts_on,
    ends_on)`` span (inclusive)."""
    found: set[date] = set()
    for starts_on, ends_on in spans:
        day = max(starts_on, start)
        while day <= min(ends_on, end):
            found.add(day)
            day += timedelta(days=1)
    return frozenset(found)


def days(start: date, end: date) -> list[date]:
    """Every date of ``[start, end]``."""
    return [start + timedelta(days=n) for n in range((end - start).days + 1)]


@dataclass(frozen=True)
class Retention:
    """K5 counts: ``|A(P)|``, ``|A(R)|``, ``|A(P) ∩ A(R)|``, ``|A(R) − A(P)|``,
    ``|A(P) − A(R)|`` and ``percent(retained, active_previous)``."""

    active_previous: int
    active_current: int
    retained: int
    new: int
    lost: int
    retention_percent: Decimal | None


def retention[T](previous: Collection[T], current: Collection[T]) -> Retention:
    before, now = set(previous), set(current)
    retained = len(before & now)
    return Retention(
        active_previous=len(before),
        active_current=len(now),
        retained=retained,
        new=len(now - before),
        lost=len(before - now),
        retention_percent=percent(retained, len(before)),
    )


__all__ = [
    "Month",
    "Retention",
    "attendance_percent",
    "available_minutes",
    "closed_days",
    "days",
    "month_key",
    "months",
    "percent",
    "previous_window",
    "retention",
]
