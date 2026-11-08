"""The simulation's payroll and expenses plans (#553 HF-01e: A1, A2 and the
plan's minimums), pure."""

from collections import Counter
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from tests.factories.simulation.anchor import (
    SIM_ANCHOR,
    SIM_HORIZON,
    SIM_NOW,
    ZONE,
    sim_clock,
)
from tests.factories.simulation.expenses_plan import (
    DEACTIVATED_AT,
    DEACTIVATED_VENDOR,
    Adjust,
    Pay,
    Reverse,
    build_expenses,
)
from tests.factories.simulation.payroll_plan import (
    CLOSED,
    PAYROLL_STARTS_ON,
    PeriodPlan,
    build_payroll,
    estimated_pay,
)
from tests.factories.simulation.plan import SimulationPlan, plan_simulation
from tests.factories.simulation.texts import TextPool
from tests.factories.simulation.timetable import (
    DecideChange,
    RequestChange,
    fortnight_end,
)

PLANS = {
    (language, profile): plan_simulation(language, profile, 1)
    for language in ("es", "en")
    for profile in ("small", "full")
}
KEYS = sorted(PLANS)
FORTNIGHT_ENDS = [
    date(2026, 9, 13),
    date(2026, 9, 27),
    date(2026, 10, 11),
    date(2026, 10, 25),
    date(2026, 11, 8),
]


def local(at: datetime) -> date:
    result: date = at.astimezone(ZONE).date()
    return result


def period_of(plan: SimulationPlan, tutor: str, day: date) -> PeriodPlan:
    return next(
        p for p in plan.payroll.tutor_periods(tutor) if p.starts_on <= day <= p.ends_on
    )


# --- A1: pure and the same whatever the run date --------------------------------------


@pytest.mark.parametrize("key", KEYS)
def test_the_plans_are_pure(key):
    plan = PLANS[key]

    assert build_payroll(plan) == plan.payroll
    assert build_expenses(plan) == plan.expenses


def test_the_run_date_does_not_change_the_plans():
    plans = []
    for now in (
        datetime(2026, 11, 8, 12, 0, tzinfo=UTC),
        datetime(2026, 12, 15, 12, 0, tzinfo=UTC),
    ):
        with sim_clock(now):
            plans.append(plan_simulation("es", "small", 1))

    assert plans[0].payroll == plans[1].payroll
    assert plans[0].expenses == plans[1].expenses


def test_both_languages_share_the_structure():
    for profile in ("small", "full"):
        es, en = PLANS[("es", profile)], PLANS[("en", profile)]
        assert es.payroll.rates == en.payroll.rates
        assert es.payroll.periods == en.payroll.periods
        assert [m.amount for m in es.payroll.manuals] == [
            m.amount for m in en.payroll.manuals
        ]
        assert [(x.key, x.amount, x.steps[-1].at) for x in es.expenses.expenses] == [
            (x.key, x.amount, x.steps[-1].at) for x in en.expenses.expenses
        ]


# --- A2: the paid work is the timetable's --------------------------------------------


@pytest.mark.parametrize("key", KEYS)
def test_every_kind_of_work_has_a_rate_or_a_salary(key):
    """Every tutor's paid lines come only from held classes and paying
    (``late``) family cancellations, and each of them has a basis, so no
    work is unresolved and ``prepare`` is never refused."""
    plan = PLANS[key]
    payroll = plan.payroll
    timetable = plan.timetable
    late = {
        r.cls
        for r in timetable.change_requests
        if r.classification == "late" and r.decision == "approved"
    }
    for c in timetable.classes:
        tutor = c.final.tutor
        paid = c.status == "completed" or (c.status == "cancelled" and c.key in late)
        if not paid or tutor is None or c.final.day >= SIM_ANCHOR:
            continue
        if tutor in payroll.salaried:
            assert c.kind == "individual", c.key
        else:
            assert payroll.rate(tutor, c.kind) is not None, (c.key, c.kind)
    for terms in payroll.terms:
        assert (terms.frequency == "monthly") == (terms.tutor in payroll.salaried)
        assert (terms.salary is not None) == (terms.tutor in payroll.salaried)
    assert not {r.tutor for r in payroll.rates} & set(payroll.salaried)


@pytest.mark.parametrize("key", KEYS)
def test_the_frequencies_and_rate_models(key):
    plan = PLANS[key]
    payroll = plan.payroll
    full = plan.profile == "full"
    frequencies = Counter(t.frequency for t in payroll.terms)

    assert frequencies == {
        "monthly": 2 if full else 1,
        "biweekly": len(plan.tutors) - (2 if full else 1),
    }
    models = Counter((r.class_kind, r.model) for r in payroll.rates)
    assert models[("individual", "per_session")] == (2 if full else 1)
    assert models[("course", "per_course")] == len(
        {c.series.tutor for c in plan.timetable.cohorts}
    )
    assert models[("group", "per_hour")] >= 1


# --- Periods and their working days ---------------------------------------------------


@pytest.mark.parametrize("key", KEYS)
def test_ended_periods_are_closed_in_order_on_working_days(key):
    plan = PLANS[key]
    payroll = plan.payroll

    for tutor in plan.tutors:
        periods = payroll.tutor_periods(tutor.key)
        assert periods[-1].starts_on <= SIM_ANCHOR <= periods[-1].ends_on
        assert all(p.ended for p in periods[:-1])
        for before, after in zip(periods, periods[1:], strict=False):
            assert after.starts_on == before.ends_on + timedelta(days=1)
        if tutor.key in payroll.salaried:
            assert [(p.starts_on, p.ends_on) for p in periods] == [
                (date(2026, 9, 1), date(2026, 9, 30)),
                (date(2026, 10, 1), date(2026, 10, 31)),
                (date(2026, 11, 1), date(2026, 11, 30)),
            ]
        else:
            ends = [p.ends_on for p in periods[:-1]]
            assert ends == [e for e in FORTNIGHT_ENDS if e >= tutor.joins_on]
            assert periods[0].starts_on == max(PAYROLL_STARTS_ON, tutor.joins_on)
            assert periods[-1].ends_on == date(2026, 11, 22)
    for period in payroll.ended:
        assert period.prepare_at and period.finalize_at and period.pay_at
        steps = (period.prepare_at, period.finalize_at, period.pay_at)
        assert local(steps[0]) > period.ends_on
        assert steps[0] < steps[1] < steps[2] < SIM_NOW
        for at in steps:
            assert local(at).isoweekday() <= 5 and local(at) not in CLOSED


@pytest.mark.parametrize("key", KEYS)
def test_no_class_changes_between_prepare_and_finalize(key):
    """``finalize`` refuses a period whose work changed after ``prepare``
    (drift): no timetable event on one of the period's classes falls between
    the two."""
    plan = PLANS[key]
    timetable = plan.timetable
    requests = {r.key: r.cls for r in timetable.change_requests}
    for event in timetable.events:
        if isinstance(event, RequestChange):
            key_ = event.request.cls
        elif isinstance(event, DecideChange):
            key_ = requests[event.request]
        else:
            key_ = getattr(event, "cls", None)
        if key_ is None:
            continue
        c = timetable.cls(key_)
        for placement in {c.placed, c.final}:
            if placement.tutor is None or placement.day >= SIM_ANCHOR:
                continue
            period = period_of(plan, placement.tutor, placement.day)
            if not period.ended:
                continue
            assert period.prepare_at and period.finalize_at
            assert not period.prepare_at <= event.at <= period.finalize_at, (
                event,
                period,
            )


# --- Work after lock, extras ----------------------------------------------------------


@pytest.mark.parametrize("key", KEYS)
def test_late_reviews_and_the_correction_come_after_the_finalize(key):
    plan = PLANS[key]
    payroll = plan.payroll
    timetable = plan.timetable
    reviews = {
        e.cls: e.at
        for e in timetable.events
        if type(e).__name__ == "ReviewCloseout" and e.approve
    }

    assert len(timetable.late_reviews) >= 2
    for key_ in timetable.late_reviews:
        c = timetable.cls(key_)
        assert c.kind == "group" and c.final.tutor not in payroll.salaried
        period = period_of(plan, c.final.tutor, c.final.day)
        assert period.finalize_at is not None
        assert period.finalize_at < reviews[key_] < SIM_NOW
        assert local(reviews[key_]) in payroll.adjustment_days
        rate = payroll.rate(c.final.tutor, "group")
        assert rate is not None and rate.model == "per_hour"
    after_lock = []
    for event in timetable.closeout_corrections:
        c = timetable.cls(event.cls)
        if fortnight_end(c.final.day) + timedelta(days=4) >= SIM_ANCHOR:
            continue
        period = period_of(plan, c.final.tutor, c.final.day)
        assert period.finalize_at is not None and period.finalize_at < event.at
        assert local(event.at) in payroll.adjustment_days
        rate = payroll.rate(c.final.tutor, c.kind)
        assert rate is not None
        if rate.model == "per_hour":
            after_lock.append(event.cls)
    assert after_lock


@pytest.mark.parametrize("key", KEYS)
def test_the_extras(key):
    plan = PLANS[key]
    payroll = plan.payroll
    texts = TextPool(plan.language)

    assert {i.kind for i in payroll.items} == {"bonus", "deduction"}
    assert {i.recurrence for i in payroll.items} == {"recurring", "one_time"}
    assert set(payroll.item_types.values()) == set(
        texts.mapping("pay_item_types").values()
    )
    assert {i.description for i in payroll.items} <= set(
        texts.mapping("pay_item_descriptions").values()
    )
    assert len(payroll.manuals) >= 2
    assert {m.reason for m in payroll.manuals} <= set(
        texts.mapping("payroll_manual_reasons").values()
    )
    assert all(m.at < SIM_NOW for m in payroll.manuals)
    reversal = payroll.reversal
    assert reversal.reason in texts.choices("payroll_reversal_reasons")
    assert reversal.paid_at < reversal.reversed_at < reversal.repaid_at < SIM_NOW


@pytest.mark.parametrize("key", KEYS)
def test_the_joiners_advance_is_carried_forward(key):
    """The advance exceeds the first period's pay, and what is carried is
    less than the second period's pay."""
    plan = PLANS[key]
    payroll = plan.payroll
    assert payroll.joiner is not None and payroll.carry is not None
    first, second = payroll.tutor_periods(payroll.joiner)[:2]
    [advance] = [m for m in payroll.manuals if m.key == "advance"]
    pay1 = estimated_pay(
        plan, payroll.rates, payroll.joiner, first.starts_on, first.ends_on
    )
    pay2 = estimated_pay(
        plan, payroll.rates, payroll.joiner, second.starts_on, second.ends_on
    )

    assert payroll.carry.estimated_pay == pay1
    assert advance.amount < 0 and -advance.amount > pay1
    assert -advance.amount - pay1 < pay2
    assert first.starts_on <= local(advance.at) <= first.ends_on
    assert first.prepare_at is not None
    assert local(payroll.carry.at) == local(first.prepare_at)
    assert payroll.carry.at < first.prepare_at


# --- Expenses -------------------------------------------------------------------------


@pytest.mark.parametrize("key", KEYS)
def test_every_expense_before_the_anchor_is_paid_or_voided(key):
    expenses = PLANS[key].expenses
    reversed_count = 0

    for expense in expenses.expenses:
        assert expense.incurred_on < SIM_ANCHOR
        assert expense.due_on >= expense.incurred_on
        paid: list[Decimal] = []
        for step in expense.steps:
            assert step.at < SIM_NOW
            if expense.template is None:
                assert step.at > expense.recorded_at
            if isinstance(step, Pay):
                assert local(step.at) >= expense.incurred_on
                paid.append(step.amount)
            elif isinstance(step, Reverse):
                paid[step.index] = Decimal(0)
                reversed_count += 1
        assert sum(paid, Decimal(0)) == expense.net
    assert reversed_count >= 1
    assert sum(1 for x in expenses.expenses if x.voided) >= 1
    adjustments = [
        s for x in expenses.expenses for s in x.steps if isinstance(s, Adjust)
    ]
    assert len(adjustments) >= 2
    assert len(expenses.one_offs) >= 10


@pytest.mark.parametrize("key", KEYS)
def test_vendors_templates_and_categories(key):
    plan = PLANS[key]
    expenses = plan.expenses
    texts = TextPool(plan.language)

    assert len(expenses.vendors) >= 6
    assert {v.name for v in expenses.vendors} == set(texts.mapping("vendors").values())
    assert expenses.category_name == texts.text("teaching_materials_category")
    users = [x for x in expenses.expenses if x.vendor == DEACTIVATED_VENDOR]
    assert users and all(local(x.recorded_at) < local(DEACTIVATED_AT) for x in users)
    assert DEACTIVATED_VENDOR not in {t.vendor for t in expenses.templates}
    assert {t.key for t in expenses.templates} == {
        "rent",
        "power",
        "cleaning",
        "software",
        "insurance",
    }
    assert all(t.ends_on <= SIM_HORIZON for t in expenses.templates)
    cleaning = [x.incurred_on for x in expenses.generated if x.template == "cleaning"]
    assert date(2026, 10, 12) in cleaning and date(2026, 11, 2) in cleaning
    assert not {date(2026, 10, 19), date(2026, 10, 26)} & set(cleaning)
    insurance = [x.incurred_on for x in expenses.generated if x.template == "insurance"]
    assert insurance == [date(2026, 9, 15)]
    assert {x.description for x in expenses.one_offs} <= set(
        texts.mapping("expense_descriptions").values()
    )
    reasons = set(texts.mapping("expense_adjustment_reasons").values()) | set(
        texts.choices("expense_reversal_reasons")
    )
    assert {
        s.reason for x in expenses.expenses for s in x.steps if not isinstance(s, Pay)
    } <= reasons


@pytest.mark.parametrize("seed", range(2, 7))
def test_other_seeds_build(seed):
    for profile in ("small", "full"):
        plan = plan_simulation("es", profile, seed)
        assert plan.payroll.ended and plan.expenses.generated
