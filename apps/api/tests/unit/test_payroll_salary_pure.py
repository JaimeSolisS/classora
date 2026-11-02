"""The pure salary pieces (#108 B; ADR-0017 E1, E7; #106 E5). Every amount in
the examples is worked by hand: salary x paid / natural, half away from zero to
the centavo. The syntax-tree test of ``salary.py`` is in
``test_payroll_calculation_pure.py``.
"""

import uuid
from datetime import date, timedelta

import pytest

from app.core.money import Money
from app.modules.payroll.calculation import LineDraft, TermsBasisVersion
from app.modules.payroll.periods import natural_bounds
from app.modules.payroll.salary import counted_days, salary_drafts

CALENDAR = "calendar_days"
WEEKDAYS = "weekdays"
ANCHOR = date(2026, 1, 5)
LONG_AGO = date(2000, 1, 1)
NOV_1 = date(2026, 11, 1)
NOV_30 = date(2026, 11, 30)


def version(
    on: date = LONG_AGO,
    salary: str | None = "12000.00",
    frequency: str | None = "monthly",
) -> TermsBasisVersion:
    return TermsBasisVersion(
        id=uuid.uuid7(),
        effective_from=on,
        pay_frequency=frequency,
        salary=None if salary is None else Money(salary),
    )


def stop(on: date) -> TermsBasisVersion:
    return version(on, None, None)


def drafts(
    starts_on: date,
    ends_on: date,
    terms: list[TermsBasisVersion],
    basis: str = CALENDAR,
) -> list[LineDraft]:
    return salary_drafts(
        starts_on=starts_on,
        ends_on=ends_on,
        terms=terms,
        anchor=ANCHOR,
        proration_basis=basis,
    )


def shown(lines: list[LineDraft]) -> list[tuple[str, int | None, int | None]]:
    return [
        (str(line.amount), line.proration_numerator, line.proration_denominator)
        for line in lines
    ]


# --- B3: the examples -----


def test_full_month():
    terms = [version()]
    assert shown(drafts(NOV_1, NOV_30, terms)) == [("12000.00", 30, 30)]
    assert shown(drafts(NOV_1, NOV_30, terms, WEEKDAYS)) == [("12000.00", 21, 21)]


def test_stop_from_the_12th():
    terms = [version(), stop(date(2026, 11, 12))]
    assert shown(drafts(NOV_1, NOV_30, terms)) == [("4400.00", 11, 30)]
    assert shown(drafts(NOV_1, NOV_30, terms, WEEKDAYS)) == [("4571.43", 8, 21)]


def test_raise_from_the_16th():
    terms = [version(), version(date(2026, 11, 16), "15000.00")]
    calendar = drafts(NOV_1, NOV_30, terms)
    assert shown(calendar) == [("6000.00", 15, 30), ("7500.00", 15, 30)]
    assert str(Money.total(line.amount for line in calendar)) == "13500.00"
    weekdays = drafts(NOV_1, NOV_30, terms, WEEKDAYS)
    assert shown(weekdays) == [("5714.29", 10, 21), ("7857.14", 11, 21)]
    assert str(Money.total(line.amount for line in weekdays)) == "13571.43"


def test_weekly_stop_from_thursday():
    terms = [version(salary="3000.00", frequency="weekly"), stop(date(2026, 11, 5))]
    monday, sunday = date(2026, 11, 2), date(2026, 11, 8)
    assert shown(drafts(monday, sunday, terms)) == [("1285.71", 3, 7)]
    assert shown(drafts(monday, sunday, terms, WEEKDAYS)) == [("1800.00", 3, 5)]


def test_floor_on_a_saturday_is_a_line_on_weekdays():
    """E2: a piece with no paid days is a ``0.00`` line."""
    terms = [version(salary="3000.00", frequency="weekly")]
    saturday, sunday = date(2026, 11, 7), date(2026, 11, 8)
    assert shown(drafts(saturday, sunday, terms)) == [("857.14", 2, 7)]
    assert shown(drafts(saturday, sunday, terms, WEEKDAYS)) == [("0.00", 0, 5)]


def test_a_stop_on_saturday_pays_a_full_week_on_weekdays():
    terms = [version(salary="3000.00", frequency="weekly"), stop(date(2026, 11, 7))]
    monday, sunday = date(2026, 11, 2), date(2026, 11, 8)
    assert shown(drafts(monday, sunday, terms)) == [("2142.86", 5, 7)]
    assert shown(drafts(monday, sunday, terms, WEEKDAYS)) == [("3000.00", 5, 5)]


@pytest.mark.parametrize(
    ("year", "expected"), [(2027, ("357.14", 10, 28)), (2028, ("344.83", 10, 29))]
)
def test_february(year, expected):
    terms = [version(salary="1000.00"), stop(date(year, 2, 11))]
    first, last = date(year, 2, 1), date(year, 3, 1) - timedelta(days=1)
    assert shown(drafts(first, last, terms)) == [expected]


@pytest.mark.parametrize("basis", [CALENDAR, WEEKDAYS])
def test_same_salary_on_two_versions_keeps_the_centavo(basis):
    """E3: each piece rounds once, and the pair pays 100.02, pinned."""
    terms = [
        version(salary="100.01", frequency="biweekly"),
        version(date(2026, 11, 2), "100.01", "biweekly"),
    ]
    lines = drafts(date(2026, 10, 26), date(2026, 11, 8), terms, basis)
    assert [str(line.amount) for line in lines] == ["50.01", "50.01"]
    assert str(Money.total(line.amount for line in lines)) == "100.02"


@pytest.mark.parametrize(("basis", "days"), [(CALENDAR, (7, 7)), (WEEKDAYS, (5, 5))])
def test_natural_boundaries_split_one_version(basis, days):
    """E1: the long returning period of a weekly tutor. One version pays two
    natural weeks, so two lines reference it."""
    back = version(date(2026, 10, 12), "3000.00", "weekly")
    later = version(date(2026, 10, 26), "3000.00", "weekly")
    terms = [
        version(salary="3000.00", frequency="weekly"),
        stop(date(2026, 10, 5)),
        back,
        later,
    ]
    lines = drafts(date(2026, 10, 5), date(2026, 11, 1), terms, basis)
    assert shown(lines) == [("3000.00", *days)] * 3
    assert [line.tutor_pay_terms_id for line in lines] == [back.id, back.id, later.id]
    assert [line.salary_starts_on for line in lines] == [
        date(2026, 10, 12),
        date(2026, 10, 19),
        date(2026, 10, 26),
    ]
    assert [line.salary_ends_on for line in lines] == [
        date(2026, 10, 18),
        date(2026, 10, 25),
        date(2026, 11, 1),
    ]


def test_a_line_says_how_it_was_reached():
    terms = [version(), stop(date(2026, 11, 12))]
    [line] = drafts(NOV_1, NOV_30, terms, WEEKDAYS)
    assert line == LineDraft(
        kind="salary",
        amount=Money("4571.43"),
        tutor_pay_terms_id=terms[0].id,
        salary_amount=Money("12000.00"),
        salary_starts_on=NOV_1,
        salary_ends_on=date(2026, 11, 11),
        proration_numerator=8,
        proration_denominator=21,
        proration_basis=WEEKDAYS,
    )
    assert (line.class_session_id, line.rate_model, line.service_date) == (
        None,
        None,
        None,
    )


def test_the_versions_may_come_in_any_order():
    first = version()
    second = version(date(2026, 11, 16), "15000.00")
    assert drafts(NOV_1, NOV_30, [second, first]) == drafts(
        NOV_1, NOV_30, [first, second]
    )


def test_a_frequency_change_keeps_the_old_calendar_in_the_stub():
    """A stub of the old frequency, then the new calendar's period."""
    terms = [
        version(salary="12000.00", frequency="monthly"),
        version(date(2026, 11, 16), "3000.00", "weekly"),
    ]
    assert shown(drafts(NOV_1, date(2026, 11, 15), terms)) == [("6000.00", 15, 30)]
    assert shown(drafts(date(2026, 11, 16), date(2026, 11, 22), terms)) == [
        ("3000.00", 7, 7)
    ]


def test_a_stop_after_the_natural_end_keeps_the_period_and_stops_the_pay():
    """#106 E2: the period keeps its dates, salary stops at the stop."""
    terms = [version(salary="3000.00", frequency="weekly"), stop(date(2026, 11, 4))]
    lines = drafts(date(2026, 11, 2), date(2026, 11, 8), terms)
    assert lines[0].salary_ends_on == date(2026, 11, 3)


# --- B5: no line -----


@pytest.mark.parametrize(
    "terms",
    [
        [],
        [version(salary=None)],
        [stop(LONG_AGO)],
        [version(date(2026, 12, 1))],
        [version(), stop(date(2026, 10, 1))],
        [version(), version(date(2026, 11, 1), None)],
    ],
    ids=[
        "no terms",
        "no salary",
        "a stop",
        "terms begin later",
        "terms ended before",
        "the salary ended on the first day",
    ],
)
def test_no_line(terms):
    assert drafts(NOV_1, NOV_30, terms) == []


def test_a_gap_between_stop_and_return_pays_nothing():
    terms = [
        version(salary="3000.00", frequency="weekly"),
        stop(date(2026, 10, 5)),
        version(date(2026, 10, 26), "3000.00", "weekly"),
    ]
    lines = drafts(date(2026, 10, 5), date(2026, 11, 1), terms)
    assert [line.salary_starts_on for line in lines] == [date(2026, 10, 26)]


def test_days_before_the_period_pay_nothing_and_the_natural_days_stay_whole():
    terms = [version()]
    assert shown(drafts(date(2026, 11, 16), NOV_30, terms)) == [("6000.00", 15, 30)]


def test_a_version_after_a_salary_with_no_salary_ends_it():
    terms = [version(), version(date(2026, 11, 16), None)]
    assert shown(drafts(NOV_1, NOV_30, terms)) == [("6000.00", 15, 30)]


# --- counted_days -----


def test_counted_days():
    monday, sunday = date(2026, 11, 2), date(2026, 11, 8)
    assert counted_days(monday, sunday, CALENDAR) == 7
    assert counted_days(monday, sunday, WEEKDAYS) == 5
    assert counted_days(sunday, sunday, WEEKDAYS) == 0
    with pytest.raises(ValueError, match="basis"):
        counted_days(monday, sunday, "fortnights")


# --- B4: exhaustive, every start day of a year with a leap February -----

FIRST_DAY = date(2028, 1, 1)
LEAP_YEAR = [FIRST_DAY + timedelta(days=offset) for offset in range(366)]
SALARY = "1000.01"


def salaried_days(
    terms: list[TermsBasisVersion], starts_on: date, ends_on: date
) -> set[date]:
    """Every day of the period whose version in effect has a salary."""
    ordered = sorted(terms, key=lambda item: item.effective_from)
    days = set()
    for offset in range((ends_on - starts_on).days + 1):
        day = starts_on + timedelta(days=offset)
        in_effect = [item for item in ordered if item.effective_from <= day]
        if in_effect and in_effect[-1].salary is not None:
            days.add(day)
    return days


def assert_pieces_hold(
    frequency: str,
    basis: str,
    terms: list[TermsBasisVersion],
    starts_on: date,
    ends_on: date,
) -> None:
    lines = drafts(starts_on, ends_on, terms, basis)
    covered: list[date] = []
    for line in lines:
        first, last = line.salary_starts_on, line.salary_ends_on
        assert first is not None
        assert last is not None
        assert starts_on <= first <= last <= ends_on
        natural_first, natural_last = natural_bounds(frequency, first, ANCHOR)
        assert natural_first <= first
        assert last <= natural_last
        covered += [
            first + timedelta(days=offset) for offset in range((last - first).days + 1)
        ]
        if (first, last) == (natural_first, natural_last):
            assert line.amount == line.salary_amount
        assert line.proration_denominator == counted_days(
            natural_first, natural_last, basis
        )
    assert len(covered) == len(set(covered))  # no overlap
    assert set(covered) == salaried_days(terms, starts_on, ends_on)


@pytest.mark.parametrize("basis", [CALENDAR, WEEKDAYS])
@pytest.mark.parametrize("frequency", ["weekly", "biweekly", "monthly"])
def test_b4_every_start_day_of_a_leap_year(frequency, basis):
    for start in LEAP_YEAR:
        stopped, back, raised = (start + timedelta(days=days) for days in (9, 13, 37))
        terms = [
            version(start, SALARY, frequency),
            stop(stopped),
            version(back, SALARY, frequency),
            version(raised, "2000.03", frequency),
        ]
        assert_pieces_hold(
            frequency,
            basis,
            terms,
            start - timedelta(days=2),
            start + timedelta(days=61),
        )
