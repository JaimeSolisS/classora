"""The pure payroll calculation (#107 D, B2; ADR-0017 E2, E4; #107 E3-E6).

The integration tests walk the real flows; these cover every branch of the
rules, and read the syntax tree of the two calculation files (B2).
"""

import ast
import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.core.money import Money, Rate
from app.modules.payroll.calculation import (
    COVERED,
    ApprovedSession,
    CohortStep,
    CohortWalk,
    CourseWalkError,
    FinalizedCourseLine,
    PayableCancellation,
    PayBasis,
    RateVersion,
    ServiceDateOutsidePeriodError,
    TermsBasisVersion,
    draft_lines,
    planned_sessions,
    resolve_basis,
)
from app.modules.payroll.errors import PayrollAmountOutOfRangeError
from app.modules.payroll.schemas import SessionPayRuleVersion

ROOT = Path(__file__).resolve().parents[2]
DAY = date(2026, 11, 4)
START = date(2026, 11, 2)
END = date(2026, 11, 8)
LONG_AGO = date(2000, 1, 1)


def rule(
    event: str,
    mode: str = "none",
    *,
    on: date = LONG_AGO,
    percent: str | None = None,
    amount: str | None = None,
) -> SessionPayRuleVersion:
    return SessionPayRuleVersion(
        id=uuid.uuid7(),
        event=event,  # type: ignore[arg-type]
        effective_from=on,
        mode=mode,  # type: ignore[arg-type]
        percent=None if percent is None else Rate(percent),
        amount=None if amount is None else Money(amount),
    )


def seeds() -> list[SessionPayRuleVersion]:
    return [
        rule("late_cancellation"),
        rule("no_show", "percent", percent="100"),
        rule("makeup", "percent", percent="100"),
    ]


def rate_row(
    rate: str | None = "120",
    *,
    model: str | None = "per_hour",
    kind: str = "individual",
    on: date = LONG_AGO,
) -> RateVersion:
    return RateVersion(
        id=uuid.uuid7(),
        class_kind=kind,
        effective_from=on,
        rate_model=None if rate is None else model,
        rate=None if rate is None else Rate(rate),
    )


def terms_row(
    *,
    on: date = LONG_AGO,
    frequency: str | None = "weekly",
    salary: str | None = None,
) -> TermsBasisVersion:
    return TermsBasisVersion(
        effective_from=on,
        pay_frequency=frequency,
        salary=None if salary is None else Money(salary),
    )


def basis(
    *,
    terms: list[TermsBasisVersion] | None = None,
    rates: list[RateVersion] | None = None,
    rules: list[SessionPayRuleVersion] | None = None,
) -> PayBasis:
    return PayBasis(
        terms=[terms_row()] if terms is None else terms,
        rates=[rate_row()] if rates is None else rates,
        rules=seeds() if rules is None else rules,
    )


def work(**changes: Any) -> ApprovedSession:
    values: dict[str, Any] = {
        "approval_id": uuid.uuid7(),
        "class_session_id": uuid.uuid7(),
        "class_kind": "individual",
        "service_date": DAY,
        "approved_minutes": 50,
        "all_absent": False,
        "is_makeup": False,
    }
    return ApprovedSession(**(values | changes))


def cancellation(**changes: Any) -> PayableCancellation:
    values: dict[str, Any] = {
        "class_session_id": uuid.uuid7(),
        "class_kind": "individual",
        "service_date": DAY,
        "scheduled_minutes": 60,
        "classification": "late",
    }
    return PayableCancellation(**(values | changes))


def run(
    pay: PayBasis,
    approved: tuple[ApprovedSession, ...] = (),
    cancellations: tuple[PayableCancellation, ...] = (),
) -> list[Any]:
    return draft_lines(
        starts_on=START,
        ends_on=END,
        basis=pay,
        approved=approved,
        cancellations=cancellations,
    )


def one(pay: PayBasis, item: ApprovedSession) -> Any:
    [line] = run(pay, (item,))
    return line


# --- D2, E6: the basis -----


def test_no_terms_no_stop_no_rate_means_no_basis():
    assert resolve_basis(basis(terms=[]), "individual", DAY) is None
    stopped = [terms_row(), terms_row(on=DAY, frequency=None)]
    assert resolve_basis(basis(terms=stopped), "individual", DAY) is None
    assert resolve_basis(basis(rates=[]), "individual", DAY) is None


def test_a_null_rate_version_is_no_rate_with_no_fall_through():
    rates = [rate_row(), rate_row(None, on=DAY)]
    assert resolve_basis(basis(rates=rates), "individual", DAY) is None


def test_a_salary_covers_a_kind_with_no_rate():
    pay = basis(terms=[terms_row(salary="9000.00")], rates=[])
    assert resolve_basis(pay, "group", DAY) == COVERED


def test_a_rate_beats_coverage_and_kinds_stay_apart():
    pay = basis(terms=[terms_row(salary="9000.00")], rates=[rate_row(kind="group")])
    assert resolve_basis(pay, "group", DAY) != COVERED
    assert resolve_basis(pay, "individual", DAY) == COVERED


def test_a_rate_of_zero_is_a_rate():
    line = one(basis(rates=[rate_row("0")]), work())
    assert (line.kind, line.amount, line.rate) == ("session", Money(0), Rate("0"))


def test_d10_the_version_of_the_date_applies():
    old, new = rate_row("100"), rate_row("200", on=DAY)
    pay = basis(rates=[new, old])
    before = DAY - timedelta(days=1)

    assert one(pay, work(service_date=before)).compensation_rate_id == old.id
    assert one(pay, work(service_date=DAY)).compensation_rate_id == new.id
    assert one(pay, work(service_date=END)).compensation_rate_id == new.id


# --- D3: ordinary amounts -----


def test_per_hour_uses_the_approved_minutes():
    line = one(basis(rates=[rate_row("250")]), work(approved_minutes=61))
    assert (line.amount, line.minutes, line.rate_model) == (
        Money("254.17"),
        61,
        "per_hour",
    )


@pytest.mark.parametrize("minutes", [0, 50, 200])
def test_per_session_ignores_the_minutes(minutes):
    pay = basis(rates=[rate_row("300", model="per_session")])
    assert one(pay, work(approved_minutes=minutes)).amount == Money("300.00")


def test_zero_approved_minutes_per_hour_is_a_zero_line():
    assert one(basis(), work(approved_minutes=0)).amount == Money(0)


def test_a_per_course_cancellation_has_no_line():
    pay = basis(rates=[rate_row("9000", model="per_course", kind="course")])
    assert run(pay, (), (cancellation(class_kind="course"),)) == []


# --- D4, D5, E3, E5: the kind and the event amounts -----


def test_an_ordinary_session_has_no_rule():
    line = one(basis(), work())
    assert (line.kind, line.session_pay_rule_id) == ("session", None)
    assert line.amount == Money("100.00")


def test_a_no_show_under_percent_rounds_once():
    rules = [rule("no_show", "percent", percent="15"), rule("makeup")]
    line = one(
        basis(rates=[rate_row("175")], rules=rules),
        work(all_absent=True, approved_minutes=50),
    )
    assert (line.kind, line.amount) == ("no_show", Money("21.88"))
    assert line.session_pay_rule_id == rules[0].id


def test_a_makeup_under_percent():
    rules = [rule("no_show"), rule("makeup", "percent", percent="50")]
    line = one(basis(rules=rules), work(is_makeup=True))
    assert (line.kind, line.amount) == ("makeup", Money("50.00"))
    assert line.session_pay_rule_id == rules[1].id


def test_e3_a_makeup_that_is_a_no_show_is_a_no_show():
    rules = [
        rule("no_show", "percent", percent="10"),
        rule("makeup", "percent", percent="90"),
    ]
    line = one(basis(rules=rules), work(all_absent=True, is_makeup=True))
    assert (line.kind, line.amount) == ("no_show", Money("10.00"))


def test_d5_none_leaves_a_zero_line_of_the_kind():
    rules = [rule("no_show"), rule("makeup")]
    line = one(basis(rules=rules), work(is_makeup=True))
    assert (line.kind, line.amount) == ("makeup", Money(0))
    assert line.session_pay_rule_id == rules[1].id
    assert line.compensation_rate_id is not None


def test_d5_fixed_pays_the_rules_amount():
    rules = [rule("no_show", "fixed", amount="77.50"), rule("makeup")]
    line = one(basis(rules=rules), work(all_absent=True, approved_minutes=0))
    assert (line.kind, line.amount) == ("no_show", Money("77.50"))


def test_the_rule_of_the_service_date_applies():
    rules = [
        rule("no_show", "percent", percent="100"),
        rule("no_show", "percent", percent="50", on=DAY),
    ]
    pay = basis(rules=rules)
    before = work(all_absent=True, service_date=DAY - timedelta(days=1))
    on_it = work(all_absent=True, service_date=DAY)

    assert one(pay, before).amount == Money("100.00")
    assert one(pay, on_it).amount == Money("50.00")


@pytest.mark.parametrize("changes", [{"all_absent": True}, {"is_makeup": True}, {}])
def test_e5_coverage_wins_over_every_event(changes):
    rules = [rule("no_show", "fixed", amount="500.00"), rule("makeup")]
    pay = basis(terms=[terms_row(salary="9000.00")], rates=[], rules=rules)

    line = one(pay, work(**changes))

    assert (line.kind, line.amount, line.minutes) == ("session", Money(0), 50)
    assert (line.rate_model, line.rate, line.compensation_rate_id) == (
        None,
        None,
        None,
    )
    assert line.session_pay_rule_id is None


def test_e6_fixed_needs_a_basis():
    rules = [rule("no_show", "fixed", amount="500.00"), rule("makeup")]
    assert run(basis(rates=[], rules=rules), (work(all_absent=True),)) == []
    assert run(basis(terms=[], rules=rules), (work(all_absent=True),)) == []


# --- D6: cancellations -----


def paying(event: str = "late_cancellation") -> list[SessionPayRuleVersion]:
    return [rule(event, "percent", percent="50"), *seeds()[1:]]


def test_a_late_cancellation_under_percent_uses_the_scheduled_minutes():
    rules = paying()
    [line] = run(basis(rules=rules), (), (cancellation(),))
    assert (line.kind, line.amount, line.minutes) == (
        "late_cancellation",
        Money("60.00"),
        60,
    )
    assert line.approval_id is None
    assert line.session_pay_rule_id == rules[0].id


def test_a_late_cancellation_under_fixed():
    rules = [rule("late_cancellation", "fixed", amount="40.00"), *seeds()[1:]]
    [line] = run(basis(rules=rules), (), (cancellation(),))
    assert line.amount == Money("40.00")


def test_a_late_cancellation_under_none_has_no_line():
    assert run(basis(), (), (cancellation(),)) == []


def test_a_no_show_cancellation():
    [line] = run(basis(), (), (cancellation(classification="no_show"),))
    assert (line.kind, line.amount, line.approval_id) == (
        "no_show",
        Money("120.00"),
        None,
    )


@pytest.mark.parametrize("classification", ["on_time", "excused"])
def test_d8_other_classifications_never_pay(classification):
    pay = basis(rules=paying())
    assert run(pay, (), (cancellation(classification=classification),)) == []


def test_e5_e6_a_cancellation_of_a_covered_or_unpaid_tutor_has_no_line():
    covered = basis(terms=[terms_row(salary="9000.00")], rates=[], rules=paying())
    assert run(covered, (), (cancellation(),)) == []
    assert run(basis(rates=[], rules=paying()), (), (cancellation(),)) == []
    assert run(basis(terms=[], rules=paying()), (), (cancellation(),)) == []


# --- D1, D11, F1 -----


@pytest.mark.parametrize("day", [START - timedelta(days=1), END + timedelta(days=1)])
def test_d1_a_date_outside_the_period_is_an_error_never_a_line(day):
    with pytest.raises(ServiceDateOutsidePeriodError):
        run(basis(), (work(service_date=day),))
    with pytest.raises(ServiceDateOutsidePeriodError):
        run(basis(), (), (cancellation(service_date=day),))


@pytest.mark.parametrize("day", [START, END])
def test_d1_both_ends_are_inclusive(day):
    assert one(basis(), work(service_date=day)).service_date == day


def test_d11_an_amount_beyond_the_range_names_the_session_only():
    item = work(approved_minutes=1440, all_absent=True)
    rules = [rule("no_show", "percent", percent="1000"), rule("makeup")]
    pay = basis(rates=[rate_row("9999999999.9999")], rules=rules)

    with pytest.raises(PayrollAmountOutOfRangeError) as caught:
        run(pay, (item,))

    assert caught.value.details == {"class_session_id": str(item.class_session_id)}


def test_drafts_are_frozen_and_ordered_by_date_then_session():
    first, second = uuid.UUID(int=1), uuid.UUID(int=2)
    items = (
        work(class_session_id=second, service_date=END),
        work(class_session_id=second, service_date=DAY),
        work(class_session_id=first, service_date=END),
    )
    # One session can't have two lines; the sort doesn't care.
    drafts = run(basis(), items)

    assert [(d.service_date, d.class_session_id) for d in drafts] == [
        (DAY, second),
        (END, first),
        (END, second),
    ]
    with pytest.raises(AttributeError):
        drafts[0].minutes = 1


def test_two_runs_on_the_same_facts_make_equal_drafts():
    items = (work(), work(all_absent=True))
    pay = basis()
    assert run(pay, items) == run(pay, items)


# --- B2: the only money paths -----

GUARDED_FILES = (
    "app/modules/payroll/calculation.py",
    "app/workflows/payroll_calculation.py",
)
MONEY_NAMES = {"rate", "percent", "amount"}


def named(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    return node.attr if isinstance(node, ast.Attribute) else None


def b2_breaches(source: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import | ast.ImportFrom):
            names = {alias.name for alias in node.names}
            module = getattr(node, "module", None) or ""
            if names & {"Decimal", "decimal"} or module == "decimal":
                found.append("imports decimal")
        if isinstance(node, ast.Name) and node.id in {"Decimal", "float"}:
            found.append(f"uses {node.id}")
        if isinstance(node, ast.Attribute) and node.attr == "amount":
            found.append("reads .amount")
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult | ast.Div):
            sides = (node.left, node.right)
            if not any(isinstance(side, ast.Constant) for side in sides) and (
                {named(side) for side in sides} & MONEY_NAMES
            ):
                found.append("multiplies money")
    return found


@pytest.mark.parametrize("path", GUARDED_FILES)
def test_b2_the_calculation_files_keep_to_the_money_paths(path):
    assert b2_breaches((ROOT / path).read_text()) == []


@pytest.mark.parametrize(
    "source",
    [
        "from decimal import Decimal",
        "import decimal",
        "x = Decimal('1')",
        "x = float(y)",
        "x = rate.amount",
        "x = rate * minutes",
        "x = minutes / line.amount",
        "x = paid.percent * y",
    ],
)
def test_b2_the_rule_catches_each_breach(source):
    assert b2_breaches(source) != []


# --- #442: per-course pay (ADR-0017 E3, #107 E11). Every expected amount is worked
# by hand from rate x k / N, half away from zero to the centavo. -----

BEFORE = date(2026, 10, 26)
AFTER = date(2026, 11, 9)


def course(on: date = DAY, **changes: Any) -> ApprovedSession:
    return work(class_kind="course", service_date=on, **changes)


def per_course(rate: str = "1000", on: date = LONG_AGO) -> RateVersion:
    return rate_row(rate, model="per_course", kind="course", on=on)


def fixed(k: int, n: int, rate: str = "1000") -> FinalizedCourseLine:
    return FinalizedCourseLine(
        class_session_id=uuid.uuid7(),
        cumulative_sessions=k,
        cohort_sessions=n,
        rate=Rate(rate),
    )


def walk(
    pay: PayBasis,
    n: int,
    *steps: CohortStep,
    first: date = START,
) -> list[Any]:
    period = [step for step in steps if isinstance(step, ApprovedSession)]
    in_period = tuple(item for item in period if START <= item.service_date <= END)
    return draft_lines(
        starts_on=START,
        ends_on=END,
        basis=pay,
        approved=in_period,
        cancellations=(),
        cohorts=[CohortWalk(first, n, steps)],
    )


def amounts(lines: list[Any]) -> list[Money]:
    return [line.amount for line in lines]


def same_day(n: int, **changes: Any) -> tuple[ApprovedSession, ...]:
    """``n`` sessions the walk sees in this order, ids oldest first."""
    return tuple(course(**changes) for _ in range(n))


def by_step(lines: list[Any], steps: tuple[ApprovedSession, ...]) -> list[Money]:
    """``draft_lines`` sorts by date, then id: map back to the walk's order."""
    found = {line.class_session_id: line.amount for line in lines}
    return [found[step.class_session_id] for step in steps]


def test_g1_a_third_at_a_time_sums_to_the_rate():
    steps = same_day(3)
    lines = walk(basis(rates=[per_course()]), 3, *steps)
    # cum: 333.33, 666.67, 1000.00
    assert by_step(lines, steps) == [
        Money("333.33"),
        Money("333.34"),
        Money("333.33"),
    ]
    assert Money.total(amounts(lines)) == Money("1000.00")


def test_g1_the_share_rounds_once_from_the_four_place_rate():
    # 100.0050 / 2 = 50.0025 -> 50.00. Through Money, 100.01 / 2 = 50.005 -> 50.01.
    only = walk(basis(rates=[per_course("100.0050")]), 2, course())
    assert amounts(only) == [Money("50.00")]


def test_c7_a_line_stores_its_share_and_rate():
    version = per_course()
    work_item = course()
    [line] = walk(basis(rates=[version]), 4, work_item)
    assert (line.kind, line.rate_model, line.rate) == (
        "session",
        "per_course",
        Rate("1000"),
    )
    assert line.compensation_rate_id == version.id
    assert (line.cumulative_sessions, line.cohort_sessions) == (1, 4)
    assert (line.approval_id, line.minutes) == (work_item.approval_id, 50)


@pytest.mark.parametrize(
    ("states", "expected"),
    [
        (["completed", "confirmed", "cancelled", "skipped_closure"], 2),
        (["conflict", "unassigned", "offered"], 3),
        (["cancelled"], 0),
    ],
)
def test_c1_n_leaves_out_only_cancelled_and_skipped(states, expected):
    assert planned_sessions(states) == expected


def test_b2_the_first_sessions_version_applies_whatever_later_versions_say():
    early = per_course("1000", on=date(2026, 1, 1))
    later = [
        per_course("2000", on=START),
        rate_row("500", kind="course", on=DAY),
        rate_row(None, kind="course", on=END),
    ]
    [line] = walk(basis(rates=[early, *later]), 2, course(), first=date(2026, 3, 1))
    assert line.compensation_rate_id == early.id
    assert line.amount == Money("500.00")


def test_b3_no_per_course_on_the_first_date_pays_by_the_sessions_own_version():
    hourly = rate_row("120", kind="course", on=date(2026, 1, 1))
    # 120 x 50 / 60 = 100.00
    [line] = walk(basis(rates=[hourly]), 2, course(), first=date(2026, 3, 1))
    assert (line.rate_model, line.amount) == ("per_hour", Money("100.00"))
    assert (line.cumulative_sessions, line.cohort_sessions) == (None, None)


def test_b3_per_course_only_after_the_first_date_is_no_line():
    late = per_course(on=START)
    assert walk(basis(rates=[late]), 2, course(), first=date(2026, 3, 1)) == []
    stopped = [per_course(on=START), rate_row(None, kind="course", on=date(2026, 1, 1))]
    assert walk(basis(rates=stopped), 2, course(), first=date(2026, 3, 1)) == []


def test_b4_a_salaried_tutor_with_no_rate_on_either_date_is_covered():
    pay = basis(terms=[terms_row(salary="9000")], rates=[])
    [line] = walk(pay, 2, course())
    assert (line.kind, line.amount, line.rate_model) == ("session", Money(0), None)


def test_c6_only_the_periods_lines_come_back_but_every_session_counts():
    before = course(BEFORE)
    now = course(DAY)
    after = course(AFTER)
    [line] = walk(basis(rates=[per_course()]), 3, before, now, after)
    assert line.class_session_id == now.class_session_id
    # cum 333.33 -> 666.67
    assert (line.amount, line.cumulative_sessions) == (Money("333.34"), 2)


def test_c3_an_anchor_with_another_n_and_rate():
    # cum0 = 1000 x 2 / 4 = 500.00, then 900 x 3 / 5 = 540.00, 900 x 4 / 5 = 720.00.
    steps = same_day(2)
    lines = walk(basis(rates=[per_course("900")]), 5, fixed(2, 4), *steps)
    assert by_step(lines, steps) == [Money("40.00"), Money("180.00")]
    assert sorted(line.cumulative_sessions for line in lines) == [3, 4]


def test_c4_a_growing_n_floors_at_zero_and_never_goes_negative():
    # cum0 = 1000 x 2 / 3 = 666.67. With N = 6: 500.00, 666.67, 833.33.
    steps = same_day(3)
    lines = walk(basis(rates=[per_course()]), 6, fixed(2, 3), *steps)
    assert by_step(lines, steps) == [Money(0), Money(0), Money("166.66")]


def test_c4_n_shrinking_under_a_finalized_line_catches_up():
    # cum0 = 1000 x 1 / 4 = 250.00. With N = 2: 1000 x 2 / 2 = 1000.00.
    [line] = walk(basis(rates=[per_course()]), 2, fixed(1, 4), course())
    assert line.amount == Money("750.00")


def test_a_session_before_the_anchor_isnt_counted_again():
    """The tutor's first session, in no period, was counted in the anchor's k.
    With the anchor applied first it would count twice: k = 4 of 3."""
    steps = (course(BEFORE), fixed(2, 3), course(DAY))
    [line] = walk(basis(rates=[per_course()]), 3, *steps)
    assert (line.cumulative_sessions, line.amount) == (3, Money("333.33"))


def test_an_open_period_before_a_finalized_one_keeps_its_own_share():
    early = course(START)
    late = course(DAY)
    lines = walk(
        basis(rates=[per_course()]), 4, early, fixed(2, 4), late, course(AFTER)
    )
    assert by_step(lines, (early, late)) == [Money("250.00"), Money("250.00")]
    assert sorted(line.cumulative_sessions for line in lines) == [1, 3]


def test_n1_a_floored_finalized_line_doesnt_lower_what_was_paid():
    # 500.00 paid at 1 of 2. N = 6: 2/6 = 333.33 floors at 0.00 and stores less.
    # Then 500.00, 666.67, 833.33, 1000.00 from the 500.00 already paid.
    steps = same_day(4)
    lines = walk(basis(rates=[per_course()]), 6, fixed(1, 2), fixed(2, 6), *steps)
    assert by_step(lines, steps) == [
        Money(0),
        Money("166.67"),
        Money("166.66"),
        Money("166.67"),
    ]
    assert Money.total(amounts(lines)) == Money("500.00")


def test_state_c_a_finalized_line_can_lower_k_and_cum_after_late_approvals():
    # S3 finalized at 1 of 5, then S1 and S2 approved: 200 each, and S3's own 200
    # stands. k drops to 1, and S4, S5 pay 400 - 200, 600 - 400.
    early = same_day(2, on=START)
    late = same_day(2, on=DAY)
    lines = walk(basis(rates=[per_course()]), 5, *early, fixed(1, 5), *late)
    assert by_step(lines, (*early, *late)) == [Money("200.00")] * 4
    ks = {line.class_session_id: line.cumulative_sessions for line in lines}
    assert [ks[step.class_session_id] for step in (*early, *late)] == [1, 2, 2, 3]


def test_c5_k_beyond_n_is_a_bug_and_never_a_capped_line():
    with pytest.raises(CourseWalkError):
        walk(basis(rates=[per_course()]), 1, course(BEFORE), course(DAY))


def test_no_terms_on_the_date_is_no_line_and_the_next_doesnt_catch_up():
    terms = [terms_row(), terms_row(on=START, frequency=None), terms_row(on=DAY)]
    gap = course(START)
    paid = course(DAY)
    [line] = walk(basis(terms=terms, rates=[per_course()]), 4, gap, paid)
    assert line.class_session_id == paid.class_session_id
    assert (line.cumulative_sessions, line.amount) == (2, Money("250.00"))


def test_a_rate_of_zero_earns_zero_lines():
    [line] = walk(basis(rates=[per_course("0")]), 3, course())
    assert (line.amount, line.rate_model) == (Money(0), "per_course")


# --- #442 D: pay events -----


def absent() -> ApprovedSession:
    return course(all_absent=True)


def test_d2_no_show_at_100_percent_is_the_line():
    no_show = absent()
    [line] = walk(basis(rates=[per_course()]), 3, no_show)
    assert (line.kind, line.amount) == ("no_show", Money("333.33"))
    assert line.session_pay_rule_id is not None


def test_d2_another_percent_rounds_each_side_once():
    # 50% of 1000 x 2/3 = 333.33; of 1000 x 1/3 = 166.67: 166.66. Half of the rounded
    # line, 333.34, would be 166.67.
    rules = [rule("no_show", "percent", percent="50"), rule("makeup")]
    first = course()
    second = absent()
    lines = walk(basis(rates=[per_course()], rules=rules), 3, first, second)
    assert by_step(lines, (first, second)) == [Money("333.33"), Money("166.66")]


def test_g8_a_cut_no_show_isnt_caught_up_by_the_next_session():
    # 50%: 1000 x 1/3 x 50% = 166.67. Next: cum 666.67 - 333.33 = 333.34.
    rules = [rule("no_show", "percent", percent="50"), rule("makeup")]
    first = absent()
    second = course()
    lines = walk(basis(rates=[per_course()], rules=rules), 3, first, second)
    assert by_step(lines, (first, second)) == [Money("166.67"), Money("333.34")]


def test_d2_fixed_and_none():
    fixed_rule = [rule("no_show", "fixed", amount="75.50"), rule("makeup")]
    assert amounts(
        walk(basis(rates=[per_course()], rules=fixed_rule), 3, absent())
    ) == [Money("75.50")]
    none_rule = [rule("no_show"), rule("makeup")]
    [line] = walk(basis(rates=[per_course()], rules=none_rule), 3, absent())
    assert (line.kind, line.amount) == ("no_show", Money(0))


def floored_no_show(*rules: SessionPayRuleVersion) -> list[Money]:
    """S1 finalized at 1 of 2 (500.00). N = 4: S2 is 2 of 4, 500.00, floored at
    0.00, and absent. S3 and S4 taught: 750.00, 1000.00."""
    steps = (absent(), course(), course())
    lines = walk(
        basis(rates=[per_course()], rules=[*rules, rule("makeup")]),
        4,
        fixed(1, 2),
        *steps,
    )
    return by_step(lines, steps)


def test_d8_a_no_show_below_100_percent_is_capped_at_the_floored_share():
    # Uncapped: 50% of 1000 x 2/4 - 50% of 1000 x 1/4 = 125.00.
    paid = floored_no_show(rule("no_show", "percent", percent="50"))
    assert paid == [Money(0), Money("250.00"), Money("250.00")]
    assert Money("500.00") + Money.total(paid) == Money("1000.00")


def test_d8_a_percent_above_100_stays_uncapped_on_a_floored_share():
    # 150% of 1000 x 2/4 - 150% of 1000 x 1/4 = 750.00 - 375.00. Above the rate.
    paid = floored_no_show(rule("no_show", "percent", percent="150"))
    assert paid == [Money("375.00"), Money("250.00"), Money("250.00")]
    assert Money("500.00") + Money.total(paid) == Money("1375.00")


def test_d8_fixed_stays_uncapped_on_a_floored_share():
    paid = floored_no_show(rule("no_show", "fixed", amount="75.50"))
    assert paid == [Money("75.50"), Money("250.00"), Money("250.00")]
    assert Money("500.00") + Money.total(paid) == Money("1075.50")


def test_d1_a_no_show_in_an_earlier_period_still_advances_the_walk():
    rules = [rule("no_show"), rule("makeup")]
    [line] = walk(
        basis(rates=[per_course()], rules=rules),
        3,
        course(BEFORE, all_absent=True),
        course(DAY),
    )
    assert (line.cumulative_sessions, line.amount) == (2, Money("333.34"))


def test_a_share_beyond_the_money_range_names_its_session():
    """Beyond the API's 1000%, which can't reach this: the top rate x 1 x 1000%
    still fits ``Money``, so only this pure test gets here."""
    rules = [rule("no_show", "percent", percent="100000"), rule("makeup")]
    no_show = absent()
    with pytest.raises(PayrollAmountOutOfRangeError) as caught:
        walk(basis(rates=[per_course("9999999999.9999")], rules=rules), 1, no_show)
    assert caught.value.details == {"class_session_id": str(no_show.class_session_id)}
