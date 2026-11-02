"""Classifying unresolved work (#443 A; ADR-0017 E2, #106 E4, #107 E6, E12, E13).

The A1 test feeds one table of cases to both :func:`draft_lines` and
:func:`unresolved_reason`: a line and an item must never disagree.
"""

import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import pytest

from app.modules.payroll.calculation import (
    UNRESOLVED_REASONS,
    CohortWalk,
    PayableCancellation,
    PayBasis,
    UnresolvedCandidate,
    UnresolvedItem,
    classify_unresolved,
    draft_lines,
    has_basis,
    unresolved_reason,
)
from tests.unit.test_payroll_calculation_pure import (
    DAY,
    END,
    START,
    basis,
    cancellation,
    rate_row,
    rule,
    seeds,
    terms_row,
    work,
)

AFTER = DAY + timedelta(days=1)
BEFORE = DAY - timedelta(days=1)
NO_RATE = basis(rates=[])
NO_TERMS = basis(terms=[])
STOPPED = basis(terms=[terms_row(frequency=None)])
SALARIED = basis(terms=[terms_row(salary="9000")], rates=[])
PER_COURSE = rate_row("1000", model="per_course", kind="course")
PAYING_LATE = [
    rule("late_cancellation", "percent", percent="50"),
    *seeds()[1:],
]


def candidate(**changes: Any) -> UnresolvedCandidate:
    values: dict[str, Any] = {
        "class_session_id": uuid.uuid7(),
        "tutor_user_id": uuid.uuid7(),
        "class_kind": "individual",
        "service_date": DAY,
        "source": "closeout",
    }
    return UnresolvedCandidate(**(values | changes))


def cancelled(**changes: Any) -> UnresolvedCandidate:
    return candidate(source="cancellation", classification="late", **changes)


def reason(
    pay: PayBasis, item: UnresolvedCandidate, first: date | None = None
) -> str | None:
    return unresolved_reason(pay, item, first)


# --- A1: one implementation ----------------------------------------------------------


@dataclass(frozen=True)
class Case:
    """A closeout, with or without a cohort, on ``DAY``."""

    pay: PayBasis
    kind: str = "individual"
    first_session_date: date | None = None


def course_basis(*rates: Any, **changes: Any) -> PayBasis:
    return basis(rates=list(rates), **changes)


CLOSEOUT_CASES = {
    "hourly": Case(basis()),
    "per_session": Case(basis(rates=[rate_row("300", model="per_session")])),
    "a zero rate is a rate": Case(basis(rates=[rate_row("0")])),
    "a version with no model": Case(basis(rates=[rate_row(None)])),
    "no rate version": Case(NO_RATE),
    "another kind's rate": Case(basis(rates=[rate_row(kind="group")])),
    "a rate from tomorrow": Case(basis(rates=[rate_row(on=AFTER)])),
    "no terms": Case(NO_TERMS),
    "a stop": Case(STOPPED),
    "terms from tomorrow": Case(basis(terms=[terms_row(on=AFTER)])),
    "salary, no rate": Case(SALARIED),
    "salary and a stop": Case(
        basis(terms=[terms_row(frequency=None, salary=None)], rates=[])
    ),
    "salary, a rate": Case(basis(terms=[terms_row(salary="9000")])),
    "course, hourly": Case(
        basis(rates=[rate_row(kind="course")]), "course", first_session_date=START
    ),
    "course, per_course": Case(course_basis(PER_COURSE), "course", START),
    "course, per_course, no terms": Case(
        course_basis(PER_COURSE, terms=[]), "course", START
    ),
    "course, per_course, a stop": Case(
        course_basis(PER_COURSE, terms=[terms_row(frequency=None)]), "course", START
    ),
    "course, per_course after the first date (B3)": Case(
        course_basis(
            rate_row("1000", model="per_course", kind="course", on=BEFORE),
        ),
        "course",
        START,
    ),
    "course, per_course after the first date, salaried": Case(
        course_basis(
            rate_row("1000", model="per_course", kind="course", on=BEFORE),
            terms=[terms_row(salary="9000")],
        ),
        "course",
        START,
    ),
    "course, hourly on the first date, per_course on the day": Case(
        course_basis(
            rate_row(kind="course"),
            rate_row("1000", model="per_course", kind="course", on=BEFORE),
        ),
        "course",
        START,
    ),
    "course, salary, no rate": Case(
        basis(terms=[terms_row(salary="9000")], rates=[]), "course", START
    ),
}


def line_of(case: Case) -> Any:
    approved = work(class_kind=case.kind, service_date=DAY)
    cohorts = (
        ()
        if case.first_session_date is None
        else (CohortWalk(case.first_session_date, 3, [approved]),)
    )
    return draft_lines(
        starts_on=START,
        ends_on=END,
        basis=case.pay,
        approved=[approved],
        cancellations=[],
        cohorts=cohorts,
    )


@pytest.mark.parametrize("name", CLOSEOUT_CASES)
def test_a1_a_closeout_is_an_item_exactly_when_it_gets_no_line(name):
    case = CLOSEOUT_CASES[name]
    item = candidate(
        class_kind=case.kind,
        first_session_date=case.first_session_date,
    )

    assert bool(line_of(case)) == (reason(case.pay, item) is None)


CANCELLATION_CASES = {
    "paying, a rate": (basis(rules=PAYING_LATE), "late", True, False),
    "paying, no rate": (basis(rates=[], rules=PAYING_LATE), "late", False, True),
    "paying, no terms": (basis(terms=[], rules=PAYING_LATE), "late", False, True),
    "paying, salary": (
        basis(terms=[terms_row(salary="9000")], rates=[], rules=PAYING_LATE),
        "late",
        False,
        False,
    ),
    "none, a rate": (basis(), "late", False, False),
    "none, no rate": (NO_RATE, "late", False, False),
    "none, no terms": (NO_TERMS, "late", False, False),
    "no_show, no rate": (NO_RATE, "no_show", False, True),
    "an unknown outcome": (NO_RATE, "excused", False, False),
    "per_course, paying": (
        basis(rates=[PER_COURSE], rules=PAYING_LATE),
        "late",
        False,
        True,
    ),
}


@pytest.mark.parametrize("name", CANCELLATION_CASES)
def test_a1_a_cancellation_line_and_item_agree(name):
    pay, classification, has_line, is_item = CANCELLATION_CASES[name]
    kind = "course" if name.startswith("per_course") else "individual"
    source: PayableCancellation = cancellation(
        classification=classification, class_kind=kind
    )
    lines = draft_lines(
        starts_on=START,
        ends_on=END,
        basis=pay,
        approved=[],
        cancellations=[source],
    )
    item = candidate(
        source="cancellation", classification=classification, class_kind=kind
    )

    assert bool(lines) == has_line
    assert (reason(pay, item) is not None) == is_item
    assert not (has_line and is_item)


def test_a1_has_basis_takes_the_cohorts_first_date():
    # The version on the first date pays, whatever is in effect on the day.
    pay = basis(
        rates=[
            PER_COURSE,
            rate_row(None, kind="course", on=BEFORE),
        ]
    )

    assert has_basis(pay, "course", DAY, START)
    assert not has_basis(pay, "course", DAY)


# --- A3, A4: the reasons -------------------------------------------------------------


@pytest.mark.parametrize(
    ("pay", "expected"),
    [(NO_TERMS, "no_pay_terms"), (STOPPED, "no_pay_terms"), (NO_RATE, "no_rate")],
)
def test_a3_the_reason_after_the_first_period(pay, expected):
    assert reason(pay, candidate(), START) == expected
    assert reason(pay, candidate(service_date=START), START) == expected


@pytest.mark.parametrize("pay", [NO_TERMS, STOPPED, NO_RATE])
def test_a3_before_first_period_comes_first(pay):
    assert reason(pay, candidate(), AFTER) == "before_first_period"


def test_a3_terms_are_read_before_the_rate():
    assert reason(basis(terms=[], rates=[]), candidate(), START) == "no_pay_terms"


def test_a3_work_on_the_first_periods_first_day_isnt_before_it():
    assert reason(NO_RATE, candidate(service_date=DAY), DAY) == "no_rate"


def test_a4_a_tutor_with_no_periods_is_never_before_the_first():
    assert reason(NO_TERMS, candidate(), None) == "no_pay_terms"


def test_before_the_first_period_with_a_basis_is_no_item():
    assert reason(basis(), candidate(), AFTER) is None


def test_the_reasons_are_the_schemas():
    assert set(UNRESOLVED_REASONS) == {
        "before_first_period",
        "no_pay_terms",
        "no_rate",
    }


# --- A5: not an item -----------------------------------------------------------------


def test_a5_a_resolvable_closeout_and_a_covered_one_arent_items():
    assert reason(basis(), candidate()) is None
    assert reason(SALARIED, candidate()) is None
    assert reason(SALARIED, cancelled()) is None


def test_e13_a_cancellation_needs_a_paying_rule_on_its_own_date():
    upcoming = [
        rule("late_cancellation"),
        rule("late_cancellation", "fixed", amount="50", on=AFTER),
        *seeds()[1:],
    ]
    pay = basis(rates=[], rules=upcoming)

    assert reason(pay, cancelled()) is None
    assert reason(pay, cancelled(service_date=AFTER)) == "no_rate"


def test_e13_a_cancellation_keeps_the_order_of_the_reasons():
    pay = basis(terms=[], rates=[], rules=PAYING_LATE)

    assert reason(pay, cancelled(), AFTER) == "before_first_period"
    assert reason(pay, cancelled(), START) == "no_pay_terms"


# --- B2: blocking --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pay", "first", "blocks"),
    [
        (NO_TERMS, AFTER, False),
        (NO_TERMS, START, True),
        (NO_RATE, None, True),
    ],
)
def test_b2_only_before_first_period_doesnt_block(pay, first, blocks):
    tutor = uuid.uuid7()
    made = candidate(tutor_user_id=tutor)

    [item] = classify_unresolved(pay, [made], first)

    assert item == UnresolvedItem(
        class_session_id=made.class_session_id,
        tutor_user_id=tutor,
        class_kind="individual",
        service_date=DAY,
        source="closeout",
        reason=item.reason,
    )
    assert item.blocking is blocks


def test_classify_leaves_out_what_has_a_basis():
    resolvable = candidate(service_date=AFTER)
    pay = basis(terms=[terms_row(on=AFTER)])

    unpaid = candidate()

    found = classify_unresolved(pay, [unpaid, resolvable], None)

    assert [item.class_session_id for item in found] == [unpaid.class_session_id]
