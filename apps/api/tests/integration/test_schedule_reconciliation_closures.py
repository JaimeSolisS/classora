"""Closures over charged occurrences (#99 D5, E5; ADR-0005 D6, Q3, Q4): the
credit, the restoration when the date reopens, a second skip, zero charges,
a restore to ``conflict`` and course occurrences. See
``schedule_reconciliation_support``."""

import pytest

from app.core.money import Money
from tests.integration import schedule_reconciliation_support as sr
from tests.integration import session_charge_support as scs


@pytest.fixture(autouse=True)
def fresh(module_seeded_url: str) -> None:
    sr.reset(module_seeded_url)


def _keys(url: str, charge: object) -> list[tuple[str, Money]]:
    return [
        (row.source_key, Money(row.amount))
        for row in sr.adjustments(url, charge, "closure_credit")  # type: ignore[arg-type]
    ]


def test_skip_restore_and_skip_again(module_seeded_url):
    url = module_seeded_url
    c = sr.charged(url)
    allocation = sr.pay(url, c.charge, "100.00")

    sr.set_state(url, c.session, "skipped_closure")
    assert sr.run(url).closure_credited == 1
    assert sr.net(url, c.charge) == Money.zero()
    assert sr.released(url, allocation)

    sr.set_state(url, c.session, "confirmed")
    assert sr.run(url).closure_restored == 1
    assert sr.net(url, c.charge) == Money("250.00")

    sr.set_state(url, c.session, "skipped_closure")
    assert sr.run(url).closure_credited == 1
    assert sr.run(url).candidates == 0

    assert _keys(url, c.charge) == [
        (f"closure_credit:{c.charge}:1", Money("-250.00")),
        (f"closure_credit:{c.charge}:2", Money("250.00")),
        (f"closure_credit:{c.charge}:3", Money("-250.00")),
    ]
    assert sr.net(url, c.charge) == Money.zero()


def test_a_completed_occurrence_is_restored_too(module_seeded_url):
    url = module_seeded_url
    c = sr.charged(url)
    sr.set_state(url, c.session, "skipped_closure")
    sr.run(url)
    sr.set_state(url, c.session, "completed")

    assert sr.one(url, c.session, c.student) == ["closure_restored"]


def test_a_zero_charge_is_never_credited_for_a_closure(module_seeded_url):
    url = module_seeded_url
    c = sr.charged(url, price="0.00")
    sr.set_state(url, c.session, "skipped_closure")

    assert sr.run(url).candidates == 0
    assert sr.one(url, c.session, c.student) == ["skipped_not_applicable"]
    assert sr.adjustments(url, c.charge) == []


@pytest.mark.parametrize("state", ["conflict", "offered"])
def test_the_credit_stays_until_the_occurrence_is_confirmed(module_seeded_url, state):
    url = module_seeded_url
    c = sr.charged(url)
    sr.set_state(url, c.session, "skipped_closure")
    sr.run(url)
    sr.set_state(url, c.session, state)

    assert sr.run(url).candidates == 0
    assert sr.one(url, c.session, c.student) == ["skipped_not_applicable"]
    assert sr.net(url, c.charge) == Money.zero()

    sr.set_state(url, c.session, "confirmed")
    assert sr.run(url).closure_restored == 1
    assert sr.net(url, c.charge) == Money("250.00")


def test_an_uncharged_or_course_occurrence_is_never_touched(module_seeded_url):
    url = module_seeded_url
    uncharged = scs.item(url, starts_at=sr.local(sr.DAY), state="skipped_closure")
    course = scs.item(
        url, starts_at=sr.local(sr.DAY), kind="course", state="skipped_closure"
    )

    assert sr.run(url).candidates == 0
    assert sr.one(url, uncharged.session, uncharged.student) == [
        "skipped_not_applicable"
    ]
    assert sr.one(url, course.session, course.student) == ["skipped_not_applicable"]
    assert sr.charges_of(url, uncharged.session) == []


def test_a_restored_then_cancelled_late_session_keeps_its_charge(module_seeded_url):
    # D4/D5: a charge outcome on a closure-credited charge restores it.
    url = module_seeded_url
    c = sr.charged(url)
    sr.set_state(url, c.session, "skipped_closure")
    sr.run(url)
    sr.set_state(url, c.session, "conflict")
    sr.approved_request(url, c.session, student=c.student, classification="late")
    sr.cancel(url, c.session)

    assert sr.one(url, c.session, c.student) == [
        "cancellation_charge_stands",
        "closure_restored",
    ]
    assert sr.net(url, c.charge) == Money("250.00")
