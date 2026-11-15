"""Salary pieces (#108 B; ADR-0017 E1, E7; #106 E5; data-model §11.6).

Pure functions on frozen values: no I/O, no ``datetime``, no float. The workflow
:mod:`app.workflows.payroll_calculation` reads the versions, the anchor and the
basis; this module decides the lines. Salary doesn't depend on today, so an
``open`` period previews the whole amount before it ends. Closures, absences and
the number of sessions never reduce it.

Inside a period, for the terms versions in date order:

1. A version's **window** runs from ``max(effective_from, starts_on)`` to
   ``min(next version's effective_from - 1, ends_on)``. Every version counts as
   "next", a stop included. An empty window, a stop or a version with no salary
   gives no line.
2. The window splits at **natural boundaries**
   (:func:`~app.modules.payroll.periods.natural_bounds`), repeated from the day
   after each piece. A period's own dates are never a natural period: it can be
   a stub or span a gap (#106 E5). One line per piece (#108 E1).
3. :func:`counted_days` counts on the basis: ``natural_days`` over the natural
   period, ``paid_days`` over the piece.
4. The amount is ``salary.prorate(paid_days, natural_days)``, rounded once. A
   piece equal to its natural period pays exactly the salary.

A piece with no paid days is still a line, at ``0.00`` (#108 E2). Two versions
inside one natural period each round once, so they can differ from the whole by
a centavo (#108 E3).

Only ``Money.prorate`` rounds, and nothing here touches a ``Decimal`` or an
``.amount``: ``tests/unit/test_payroll_calculation_pure.py`` reads this file's
syntax tree too.
"""

from collections.abc import Iterable, Iterator
from datetime import date, timedelta
from operator import attrgetter
from typing import Final

from app.modules.payroll.calculation import LineDraft, TermsBasisVersion
from app.modules.payroll.periods import natural_bounds

SALARY: Final = "salary"
CALENDAR_DAYS: Final = "calendar_days"
WEEKDAYS: Final = "weekdays"
SATURDAY: Final = 5
ONE_DAY: Final = timedelta(days=1)


def counted_days(first: date, last: date, proration_basis: str) -> int:
    """The days of ``[first, last]`` on the basis: every day, or Monday to
    Friday."""
    days = (last - first).days + 1
    if proration_basis == CALENDAR_DAYS:
        return days
    if proration_basis == WEEKDAYS:
        return sum(
            1
            for offset in range(days)
            if (first + timedelta(days=offset)).weekday() < SATURDAY
        )
    raise ValueError(f"Unknown proration basis: {proration_basis}")


def _windows(
    terms: Iterable[TermsBasisVersion], starts_on: date, ends_on: date
) -> Iterator[tuple[TermsBasisVersion, date, date]]:
    """Each version with a non-empty window, in date order."""
    ordered = sorted(terms, key=attrgetter("effective_from"))
    for index, version in enumerate(ordered):
        first = max(version.effective_from, starts_on)
        following = ordered[index + 1 : index + 2]
        last = (
            min(following[0].effective_from - ONE_DAY, ends_on)
            if following
            else ends_on
        )
        if first <= last:
            yield version, first, last


def _pieces(
    version: TermsBasisVersion,
    first: date,
    last: date,
    *,
    anchor: date,
    proration_basis: str,
) -> Iterator[LineDraft]:
    """The window's lines, one per natural period it touches."""
    if version.pay_frequency is None or version.salary is None:
        return
    day = first
    while day <= last:
        natural_start, natural_end = natural_bounds(version.pay_frequency, day, anchor)
        piece_end = min(natural_end, last)
        paid_days = counted_days(day, piece_end, proration_basis)
        natural_days = counted_days(natural_start, natural_end, proration_basis)
        yield LineDraft(
            kind=SALARY,
            amount=version.salary.prorate(paid_days, natural_days),
            tutor_pay_terms_id=version.id,
            salary_amount=version.salary,
            salary_starts_on=day,
            salary_ends_on=piece_end,
            proration_numerator=paid_days,
            proration_denominator=natural_days,
            proration_basis=proration_basis,
        )
        day = piece_end + ONE_DAY


def salary_drafts(
    *,
    starts_on: date,
    ends_on: date,
    terms: Iterable[TermsBasisVersion],
    anchor: date,
    proration_basis: str,
) -> list[LineDraft]:
    """The period's salary lines, by ``salary_starts_on`` (see the module
    docstring)."""
    return [
        draft
        for version, first, last in _windows(terms, starts_on, ends_on)
        for draft in _pieces(
            version, first, last, anchor=anchor, proration_basis=proration_basis
        )
    ]
