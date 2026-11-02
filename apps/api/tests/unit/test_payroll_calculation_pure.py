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
    PayableCancellation,
    PayBasis,
    RateVersion,
    ServiceDateOutsidePeriodError,
    TermsBasisVersion,
    draft_lines,
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


def test_per_course_writes_no_line_here():
    pay = basis(rates=[rate_row("9000", model="per_course", kind="course")])
    assert run(pay, (work(class_kind="course"),)) == []
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
