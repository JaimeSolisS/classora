"""The simulation's billing plan (#552 HF-01d: A1, A2 and the plan's
minimums), pure."""

from collections import Counter
from datetime import date

import pytest

from tests.factories.simulation import billing_plan
from tests.factories.simulation.anchor import SIM_ANCHOR, SIM_START
from tests.factories.simulation.billing_plan import (
    MODE_CHANGE_ON,
    build_billing,
    linked,
    month_starts,
    session_amount,
    weekly_subjects,
)
from tests.factories.simulation.plan import (
    ELEMENTARY,
    HIGH_SCHOOL,
    MIDDLE_SCHOOL,
    UNIVERSITY,
    plan_simulation,
)
from tests.factories.simulation.texts import POOLS, TextPool

PLANS = {
    (language, profile): plan_simulation(language, profile, 1)
    for language in ("es", "en")
    for profile in ("small", "full")
}


@pytest.mark.parametrize("key", sorted(PLANS))
def test_the_plan_is_pure(key):
    plan = PLANS[key]

    assert build_billing(plan, 1) == plan.billing


def test_both_languages_share_the_structure():
    for profile in ("small", "full"):
        es = PLANS[("es", profile)].billing
        en = PLANS[("en", profile)].billing
        assert es.modes == en.modes
        assert [p.at for p in es.payments] == [p.at for p in en.payments]
        assert [i.amount for i in es.items] == [i.amount for i in en.items]
        # HF-01g A2: every money field of the plan.
        assert [(i.key, i.amount) for i in es.items] == [
            (i.key, i.amount) for i in en.items
        ]
        assert es.versions == en.versions
        assert [(c.student, c.item, c.amount) for c in es.custom_prices] == [
            (c.student, c.item, c.amount) for c in en.custom_prices
        ]
        assert [(p.key, p.amount) for p in es.products] == [
            (p.key, p.amount) for p in en.products
        ]
        assert es.tuition_plans == en.tuition_plans
        assert [(m.key, m.amount) for m in es.manual_charges] == [
            (m.key, m.amount) for m in en.manual_charges
        ]
        assert [(d.key, d.percent, d.students) for d in es.discounts] == [
            (d.key, d.percent, d.students) for d in en.discounts
        ]


@pytest.mark.parametrize("key", sorted(PLANS))
def test_one_mode_at_a_time_changing_on_the_first_of_a_month(key):
    plan = PLANS[key]
    billing = plan.billing

    assert set(billing.modes) == {s.key for s in plan.students}
    for modes in billing.modes.values():
        starts = [start for start, _ in modes]
        assert starts == sorted(set(starts))
        assert starts[0] == SIM_START
        assert all(start.day == 1 for start in starts)
    changes = [modes for modes in billing.modes.values() if len(modes) > 1]
    assert changes == [((SIM_START, "package"), (MODE_CHANGE_ON, "plan"))]


@pytest.mark.parametrize("key", sorted(PLANS))
def test_the_mode_shares_and_minimums(key):
    plan = PLANS[key]
    billing = plan.billing
    counts = Counter(modes[0][1] for modes in billing.modes.values())
    total = len(plan.students)

    assert counts["plan"] == round(total * 0.5)
    assert counts["package"] == round(total * 0.2)
    assert sum(1 for p in billing.packages if p.intensive) == 1
    assert sum(1 for p in billing.packages if p.cancelled) == 1
    assert sum(1 for p in billing.packages if p.extra is not None) == 1
    assert len(billing.products) >= 2
    paused = {t.student for t in billing.tuition_plans if t.pause is not None}
    assert len(paused) >= 1
    assert all(
        t.pause is not None for t in billing.tuition_plans if t.student in paused
    )
    kinds = {d.kind for d in billing.discounts}
    assert kinds == {"sibling", "scholarship", "promotion", "discount"}
    assert sum(len(d.students) for d in billing.discounts) == 2
    assert any(
        d.expires_on is not None and d.expires_on < SIM_ANCHOR
        for d in billing.discounts
    )
    assert any(
        d.starts_on is not None and d.starts_on > SIM_ANCHOR for d in billing.discounts
    )
    assert [v.effective_from for v in billing.versions] == [date(2026, 10, 1)]
    paid = {c.student for c in billing.custom_prices if c.amount > 0}
    assert len({c.student for c in billing.custom_prices}) >= 2
    assert len(paid) >= (2 if plan.profile == "full" else 1)
    assert len(billing.manual_charges) >= 2
    assert len(billing.adjustments) >= 1
    assert len(billing.due_date_changes) >= 2
    full = plan.profile == "full"
    assert len(billing.overdue_families) == (3 if full else 1)
    assert len(billing.ahead_families) == (2 if full else 1)


@pytest.mark.parametrize("key", sorted(PLANS))
def test_payments_are_before_the_anchor_and_stop_for_the_overdue(key):
    billing = PLANS[key].billing

    assert all(p.at.date() < SIM_ANCHOR for p in billing.payments)
    assert billing.reversal.at.date() < SIM_ANCHOR
    for family in billing.overdue_families:
        assert all(
            p.at.date() < date(2026, 11, 9)
            for p in billing.payments
            if p.family == family
        )
    reversed_payment = [
        p
        for p in billing.payments
        if p.family == billing.reversal.family and p.at == billing.reversal.payment_at
    ]
    assert [p.method for p in reversed_payment] == ["transfer"]


@pytest.mark.parametrize("key", sorted(PLANS))
def test_no_tuition_plan_posts_after_the_anchor_before_december(key):
    billing = PLANS[key].billing

    assert month_starts()[-1] < SIM_ANCHOR
    assert all(t.starts_on in month_starts() for t in billing.tuition_plans)


@pytest.mark.parametrize("key", sorted(PLANS))
def test_texts_come_from_the_run_languages_pool(key):
    plan = PLANS[key]
    billing = plan.billing
    pool = POOLS[plan.language]
    products = pool["billing_products"]
    assert isinstance(products, dict)
    discounts = pool["billing_discounts"]
    assert isinstance(discounts, dict)
    charges = pool["manual_charges"]
    assert isinstance(charges, dict)

    for product in billing.products:
        template = products[product.key]
        assert product.name == template or product.name.startswith(
            template.split("{")[0]
        )
    assert {d.name for d in billing.discounts} <= set(discounts.values())
    assert {m.description for m in billing.manual_charges} <= set(charges.values())
    texts = TextPool(plan.language)
    assert {a.reason for a in billing.adjustments} <= set(
        texts.choices("adjustment_reasons")
    )
    assert {c.reason for c in billing.due_date_changes} <= set(
        texts.choices("due_date_reasons")
    )
    assert billing.reversal.reason in texts.choices("reversal_reasons")
    assert billing.reasons["withdrawal"] in texts.choices("withdrawal_reasons")


@pytest.mark.parametrize("seed", range(2, 12))
def test_other_seeds_build(seed):
    for profile in ("small", "full"):
        plan = plan_simulation("es", profile, seed)
        assert plan.billing.packages


# --- HF-01g: plausible prices and the plan structure (A1, A3) -------------------------

LEVELS = (ELEMENTARY, MIDDLE_SCHOOL, HIGH_SCHOOL, UNIVERSITY)


def test_the_price_constants_are_in_their_plausibility_ranges():
    prices = [billing_plan.PER_SESSION[level] for level in LEVELS]
    assert all(280 <= p <= 450 for p in prices)
    assert prices == sorted(set(prices))  # strictly rising by level
    for level in LEVELS:
        ratio = billing_plan.MONTHLY[level] / billing_plan.PER_SESSION[level]
        assert 3.6 <= ratio <= 4.2, level
    middle = billing_plan.PER_SESSION[MIDDLE_SCHOOL]
    quantities = {"pack8": 8, "pack12": 12, "intensive": 10, "subject4": 4}
    for key, amount in billing_plan.PRODUCT_AMOUNTS.items():
        assert 0.85 <= amount / quantities[key] / middle <= 1.0, key
    assert 900 <= billing_plan.GROUP_MONTHLY <= 1300
    assert 2.7 <= billing_plan.GROUP_FEE / billing_plan.GROUP_MONTHLY <= 3.2
    assert billing_plan.COURSE_FEES == (3200, 2800)


@pytest.mark.parametrize("key", sorted(PLANS))
def test_the_plan_items_carry_the_constants(key):
    plan = PLANS[key]
    billing = plan.billing
    levels = {s.key: s.level for s in plan.subjects}
    products = {p.key: p.amount for p in billing.products}
    assert products == billing_plan.PRODUCT_AMOUNTS
    for item in billing.items:
        if item.pricing_model == "per_session":
            assert item.minutes is not None
            assert item.amount == session_amount(levels[item.subject], item.minutes)
        elif item.class_type == "individual":
            assert item.amount == billing_plan.MONTHLY[levels[item.subject]]
        elif item.class_type == "group":
            assert item.amount == (
                billing_plan.GROUP_MONTHLY
                if item.pricing_model == "per_period"
                else billing_plan.GROUP_FEE
            )


@pytest.mark.parametrize("key", sorted(PLANS))
def test_one_plan_per_weekly_subject_and_linked_groups(key):
    """A3: a plan student has one plan per weekly individual subject (no two
    pin the same item); each enrollment of a linked group has one linked plan
    from 1 Sep; the last group keeps its flat fee and has none. (A 90-min
    slot can't carry a 1.5x monthly price: a ``per_period`` item takes no
    custom price, #93 K8; HF-01g D1.)"""
    plan = PLANS[key]
    billing = plan.billing
    items = {i.key: i for i in billing.items}
    individual = [t for t in billing.tuition_plans if t.group is None]
    students = {t.student for t in individual}
    planned = {
        s
        for s, modes in billing.modes.items()
        if any(mode == "plan" for _, mode in modes)
    }
    assert students == planned
    for student in students:
        mine = [t for t in individual if t.student == student]
        subjects = [items[t.item].subject for t in mine]
        assert sorted(subjects) == sorted(weekly_subjects(plan, student))
        assert len({t.item for t in mine}) == len(mine)
        assert all(items[t.item].pricing_model == "per_period" for t in mine)
    assert not any(
        c.amount > 0 and items[c.item].pricing_model == "per_period"
        for c in billing.custom_prices
    )
    groups = plan.timetable.groups
    assert linked(plan) == {g.key for g in groups[:-1]}
    for group in groups:
        mine = [t for t in billing.tuition_plans if t.group == group.key]
        if group.key in linked(plan):
            assert sorted(t.student for t in mine) == sorted(group.series.students)
            assert all(t.starts_on == date(2026, 9, 1) for t in mine)
            assert all(items[t.item].group == group.key for t in mine)
            assert all(
                i.group != group.key or i.pricing_model == "per_period"
                for i in billing.items
            )
        else:
            assert mine == []
            assert [i.pricing_model for i in billing.items if i.group == group.key] == [
                "full_course"
            ]
