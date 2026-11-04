"""The period grid of a recurring expense template (#116 B; ADR-0017 E17).

Pure date arithmetic, no I/O. Period ``k`` (``k >= 0``) of a template starts on
``starts_on + 7k`` days (weekly), ``starts_on`` plus ``k`` months (monthly) or
``k`` years (annual). The day of the month is clamped to the month's length and
is **always computed from** ``starts_on``, never from the previous period (the
#93 K3 rule): a template anchored on 31 January runs 28 (or 29) February, 31
March, 30 April. Dates only, never instants, so a DST change can't move a
period.

The finance module has its own copy of the rule: module boundaries keep
``billing``'s internals out of reach (ADR-0002).
"""

import calendar
from datetime import date, timedelta
from typing import Final, Literal

type Frequency = Literal["weekly", "monthly", "annual"]

FREQUENCIES: Final[tuple[Frequency, ...]] = ("weekly", "monthly", "annual")

_WEEK: Final = 7
_MONTHS: Final[dict[Frequency, int]] = {"monthly": 1, "annual": 12}


def _add_months(anchor: date, months: int) -> date:
    month_index = anchor.month - 1 + months
    year, month = anchor.year + month_index // 12, month_index % 12 + 1
    day = min(anchor.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def period_start(starts_on: date, frequency: Frequency, k: int) -> date:
    """The start of period ``k`` (``k >= 0``) of a template from
    ``starts_on``."""
    if k < 0:
        raise ValueError("period indexes start at 0")
    if frequency == "weekly":
        return starts_on + timedelta(days=_WEEK * k)
    return _add_months(starts_on, _MONTHS[frequency] * k)


def _index_on_or_before(starts_on: date, frequency: Frequency, day: date) -> int:
    """The index of the last period starting on or before ``day`` (``day >=
    starts_on``)."""
    if frequency == "weekly":
        return (day - starts_on).days // _WEEK
    months = (day.year - starts_on.year) * 12 + day.month - starts_on.month
    k = months // _MONTHS[frequency]
    while k > 0 and period_start(starts_on, frequency, k) > day:
        k -= 1
    return k


def first_on_or_after(starts_on: date, frequency: Frequency, day: date) -> date:
    """The first period start on or after ``day`` (``starts_on`` when ``day``
    is before it)."""
    if day <= starts_on:
        return starts_on
    k = _index_on_or_before(starts_on, frequency, day)
    start = period_start(starts_on, frequency, k)
    if start == day:
        return start
    return period_start(starts_on, frequency, k + 1)


def next_after(starts_on: date, frequency: Frequency, start: date) -> date:
    """The start of the period after the one starting on (or containing)
    ``start``."""
    if start < starts_on:
        raise ValueError("the day is before the template's first period")
    return period_start(
        starts_on, frequency, _index_on_or_before(starts_on, frequency, start) + 1
    )
