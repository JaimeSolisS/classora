"""The tuition period grid and a period's state (#93 C1, C2; K3-K6). Pure:
no I/O.

**The grid (K3).** A plan's periods are anchored at its ``starts_on`` and
always whole:

- ``weekly``: period *k* starts ``starts_on + 7k`` days;
- ``biweekly``: ``starts_on + 14k`` days;
- ``monthly``: ``starts_on`` plus *k* calendar months, the day of month
  clamped to the month's length and **always computed from** ``starts_on``,
  never from the previous period (31 Jan → 28/29 Feb → 31 Mar → 30 Apr).

A period ends the day before the next one starts. Every date is a
center-local calendar date (ADR-0004). No proration, ever: a plan's
``ends_on`` is the end of one of its periods.

**A period's state** (:func:`plan_period_state`, the single rule the daily
job, the unbilled read and the plan detail use), in this order:

1. ``not_due``: ``period.start`` is after the center's local ``today``
   (tuition is billed at the start of its period, K5);
2. ``below_floor``: billing is off (``floor`` ``None``) or ``period.start``
   is before ``billing_starts_on`` (history from before go-live is never
   charged);
3. ``after_end``: ``period.start`` is after the plan's ``ends_on``;
4. ``paused``: ``period.start`` falls inside a pause (K4; never charged,
   never caught up);
5. ``enrollment_ended``: the linked group enrollment was withdrawn on or
   before ``period.start`` (local date, K6);
6. ``due``: the period is charged (unless already posted).
"""

import calendar
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final, Literal

type Frequency = Literal["weekly", "biweekly", "monthly"]
type PeriodState = Literal[
    "not_due", "below_floor", "after_end", "paused", "enrollment_ended", "due"
]

PERIOD_STATES: Final[tuple[PeriodState, ...]] = (
    "not_due",
    "below_floor",
    "after_end",
    "paused",
    "enrollment_ended",
    "due",
)
_DAYS: Final[dict[str, int]] = {"weekly": 7, "biweekly": 14}


@dataclass(frozen=True, order=True)
class Period:
    """One period: ``start`` to ``end``, both inclusive local dates."""

    start: date
    end: date


@dataclass(frozen=True)
class PlanTerms:
    """What a period's state needs of a plan."""

    starts_on: date
    frequency: Frequency
    ends_on: date | None = None


def _add_months(anchor: date, months: int) -> date:
    month_index = anchor.month - 1 + months
    year, month = anchor.year + month_index // 12, month_index % 12 + 1
    day = min(anchor.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def period_start(starts_on: date, frequency: Frequency, k: int) -> date:
    """The start of period ``k`` (``k >= 0``) of a plan from ``starts_on``."""
    if k < 0:
        raise ValueError("period indexes start at 0")
    if frequency == "monthly":
        return _add_months(starts_on, k)
    return starts_on + timedelta(days=_DAYS[frequency] * k)


def period_at(starts_on: date, frequency: Frequency, k: int) -> Period:
    """Period ``k``: its start and the day before period ``k + 1``."""
    return Period(
        start=period_start(starts_on, frequency, k),
        end=period_start(starts_on, frequency, k + 1) - timedelta(days=1),
    )


def period_index(starts_on: date, frequency: Frequency, day: date) -> int | None:
    """The index of the period that contains ``day`` (``None`` before
    ``starts_on``)."""
    if day < starts_on:
        return None
    if frequency != "monthly":
        return (day - starts_on).days // _DAYS[frequency]
    k = (day.year - starts_on.year) * 12 + day.month - starts_on.month
    while k > 0 and period_start(starts_on, frequency, k) > day:
        k -= 1
    while period_start(starts_on, frequency, k + 1) <= day:
        k += 1
    return k


def period_containing(
    starts_on: date, frequency: Frequency, day: date
) -> Period | None:
    """The period that contains ``day``, or ``None`` before ``starts_on``."""
    k = period_index(starts_on, frequency, day)
    return None if k is None else period_at(starts_on, frequency, k)


def is_period_start(starts_on: date, frequency: Frequency, day: date) -> bool:
    """Whether ``day`` is the first day of one of the plan's periods."""
    found = period_containing(starts_on, frequency, day)
    return found is not None and found.start == day


def is_period_end(starts_on: date, frequency: Frequency, day: date) -> bool:
    """Whether ``day`` is the last day of one of the plan's periods."""
    found = period_containing(starts_on, frequency, day)
    return found is not None and found.end == day


def periods_between(
    starts_on: date, frequency: Frequency, first: date, last: date
) -> list[Period]:
    """The periods whose **start** lies in ``[first, last]``, oldest first
    (empty when ``last < first``)."""
    if last < first or last < starts_on:
        return []
    k = period_index(starts_on, frequency, max(first, starts_on))
    assert k is not None  # noqa: S101 - max(first, starts_on) >= starts_on
    found: list[Period] = []
    period = period_at(starts_on, frequency, k)
    if period.start < first:
        k += 1
        period = period_at(starts_on, frequency, k)
    while period.start <= last:
        found.append(period)
        k += 1
        period = period_at(starts_on, frequency, k)
    return found


def in_pause(day: date, pauses: Sequence[tuple[date, date | None]]) -> bool:
    """Whether ``day`` lies in one of ``pauses`` (``(starts_on, ends_on)``,
    both inclusive, ``None`` = open-ended)."""
    return any(
        starts_on <= day and (ends_on is None or day <= ends_on)
        for starts_on, ends_on in pauses
    )


def plan_period_state(
    plan: PlanTerms,
    pauses: Sequence[tuple[date, date | None]],
    period: Period,
    *,
    today: date,
    floor: date | None,
    enrollment: date | None = None,
) -> PeriodState:
    """The state of ``period`` of ``plan`` (the order of the module
    docstring). ``today`` is the center's local date, ``floor`` the billing
    start (``None``: billing off) and ``enrollment`` the local date the
    linked group enrollment was withdrawn (``None``: not withdrawn, or a
    standalone plan)."""
    if period.start > today:
        return "not_due"
    if floor is None or period.start < floor:
        return "below_floor"
    if plan.ends_on is not None and period.start > plan.ends_on:
        return "after_end"
    if in_pause(period.start, pauses):
        return "paused"
    if enrollment is not None and enrollment <= period.start:
        return "enrollment_ended"
    return "due"
