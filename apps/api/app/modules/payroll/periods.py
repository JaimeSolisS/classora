"""The payroll calendar (#106 C, F; ADR-0017 E6; data-model §11.4).

Pure functions on ``date`` values: no I/O, no ``datetime``, no float. The
caller reads the terms and periods, and today comes from
``administration.center_today``.

**Natural periods** (:func:`natural_bounds`): weekly is Monday to Sunday,
monthly the calendar month, biweekly 14-day blocks counted from the anchor (a
Monday), also before it. #108 uses it for ``natural_days``, because a period's
own dates can be a stub or span a gap (#106 E5).

**The next period** (:func:`next_period`):

- **Start:** the day after the previous period. A tutor's first period starts
  on the first covered date on or after the floor, so terms that began before
  the floor start at the floor.
- **First covered date:** the first date on or after the start with a
  non-``NULL`` frequency (#106 E5). It gives the period's ``pay_frequency``
  and its calendar, so a tutor who left in March and returns weekly in
  November has one long period from April to the first Sunday on or after
  the return.
- **End:** the natural end around the first covered date, or the day before
  the next version with another frequency (``NULL`` included), whichever comes
  first. A version with the same frequency doesn't split a period.
- **Nothing** when no covered date is on or before today.

A generated period is never re-dated (#106 E2): the stub exists only if the
stop or the change is recorded when the period is generated. Otherwise the
period keeps its natural end, and the days after a stop carry no terms.

**A leaver's adjustment** (:func:`adjustment_period`, #111 D4; ADR-0017 E6,
E11): a tutor with no period left to open gets one for the adjustment, from the
day after the last one, with the last non-``NULL`` frequency of the terms in
force by then (the last period's when there is none), to that calendar's natural
end, or the day before a later version with another frequency (a return).
Returning terms inside it follow #106 E3 like any generated period.

**The refusals** (:func:`terms_refusal`), on the terms as they would be after
a write, are the rules that keep the chain above true:

- ``inside_generated_period`` (#106 E3): on some date inside a generated
  period, the frequency in effect is neither ``NULL`` nor that period's.
- ``before_first_period`` (#106 E4): some date on or after the floor and
  before the first period's start has a frequency. No period can ever cover
  that date, so the work would be silently unpaid.
"""

import calendar
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final, Literal

BIWEEKLY_DAYS: Final = 14
WEEK_DAYS: Final = 7

type TermsRefusal = Literal["inside_generated_period", "before_first_period"]


@dataclass(frozen=True)
class TermsVersion:
    """What the calendar needs of one ``tutor_pay_terms`` row."""

    effective_from: date
    pay_frequency: str | None


@dataclass(frozen=True)
class PeriodSpan:
    """The dates of one period, generated or about to be."""

    starts_on: date
    ends_on: date
    pay_frequency: str
    is_first_period: bool = False


def natural_bounds(frequency: str, day: date, anchor: date) -> tuple[date, date]:
    """The first and last day of the natural period of ``frequency`` around
    ``day``. ``anchor`` matters only for ``biweekly``."""
    if frequency == "weekly":
        start = day - timedelta(days=day.weekday())
        return start, start + timedelta(days=WEEK_DAYS - 1)
    if frequency == "biweekly":
        # Floor division, so a date before the anchor falls in its own block.
        blocks = (day - anchor).days // BIWEEKLY_DAYS
        start = anchor + timedelta(days=blocks * BIWEEKLY_DAYS)
        return start, start + timedelta(days=BIWEEKLY_DAYS - 1)
    if frequency == "monthly":
        last = calendar.monthrange(day.year, day.month)[1]
        return day.replace(day=1), day.replace(day=last)
    raise ValueError(f"Unknown pay frequency: {frequency}")


def _ordered(terms: Iterable[TermsVersion]) -> list[TermsVersion]:
    return sorted(terms, key=lambda version: version.effective_from)


def _frequencies_between(
    terms: Sequence[TermsVersion], first: date, last: date
) -> set[str | None]:
    """Every frequency in effect on some date of ``[first, last]``, with
    ``None`` for "no terms" and for a stop. ``terms`` is ordered."""
    current: str | None = None
    found: set[str | None] = set()
    for version in terms:
        if version.effective_from > last:
            break
        if version.effective_from <= first:
            current = version.pay_frequency
        else:
            found.add(version.pay_frequency)
    return found | {current}


def _first_covered(
    terms: Sequence[TermsVersion], start: date
) -> tuple[date, str] | None:
    """The first date on or after ``start`` with a frequency, and that
    frequency. ``terms`` is ordered."""
    current: str | None = None
    for version in terms:
        if version.effective_from > start:
            break
        current = version.pay_frequency
    if current is not None:
        return start, current
    for version in terms:
        if version.effective_from > start and version.pay_frequency is not None:
            return version.effective_from, version.pay_frequency
    return None


def next_period(
    *,
    previous_ends_on: date | None,
    terms: Iterable[TermsVersion],
    floor: date,
    anchor: date,
    today: date,
) -> PeriodSpan | None:
    """The tutor's next period, or ``None`` when none is due (see the module
    docstring)."""
    ordered = _ordered(terms)
    is_first = previous_ends_on is None
    start = floor if previous_ends_on is None else previous_ends_on + timedelta(days=1)
    covered = _first_covered(ordered, start)
    if covered is None or covered[0] > today:
        return None
    day, frequency = covered
    if is_first:
        start = day
    end = natural_bounds(frequency, day, anchor)[1]
    for version in ordered:
        if version.effective_from > day and version.pay_frequency != frequency:
            end = min(end, version.effective_from - timedelta(days=1))
            break
    return PeriodSpan(start, end, frequency, is_first)


def adjustment_period(
    *,
    last_ends_on: date,
    last_frequency: str,
    terms: Iterable[TermsVersion],
    anchor: date,
) -> PeriodSpan:
    """The period a leaver's adjustment opens (see the module docstring)."""
    ordered = _ordered(terms)
    start = last_ends_on + timedelta(days=1)
    paid_before = [
        v.pay_frequency
        for v in ordered
        if v.effective_from <= start and v.pay_frequency is not None
    ]
    frequency = paid_before[-1] if paid_before else last_frequency
    end = natural_bounds(frequency, start, anchor)[1]
    for version in ordered:
        if version.effective_from > start and version.pay_frequency != frequency:
            end = max(start, min(end, version.effective_from - timedelta(days=1)))
            break
    return PeriodSpan(start, end, frequency)


def terms_refusal(
    terms: Iterable[TermsVersion], periods: Sequence[PeriodSpan], floor: date
) -> TermsRefusal | None:
    """Why these terms can't stand against the generated ``periods`` (ordered
    by date), or ``None``. ``terms`` are the versions as they would be after
    the write. No periods: neither rule applies."""
    if not periods:
        return None
    ordered = _ordered(terms)
    for period in periods:
        allowed = {None, period.pay_frequency}
        if _frequencies_between(ordered, period.starts_on, period.ends_on) - allowed:
            return "inside_generated_period"
    first = periods[0].starts_on
    if floor < first and _frequencies_between(
        ordered, floor, first - timedelta(days=1)
    ) - {None}:
        return "before_first_period"
    return None
