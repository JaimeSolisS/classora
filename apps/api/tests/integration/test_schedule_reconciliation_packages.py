"""Package-covered sessions (#99 Q9, E7; ADR-0006 E3, E10): a ``charge``
outcome consumes the units (or charges when they are short), a ``credit``
outcome and duration changes touch nothing, and a consumed key is never a
candidate. See ``schedule_reconciliation_support`` and
``package_support``."""

from datetime import timedelta

import pytest

from tests.integration import package_support as ps
from tests.integration import schedule_reconciliation_support as sr

SOLD = sr.DAY - timedelta(days=5)


@pytest.fixture(autouse=True)
def fresh(module_seeded_url: str) -> None:
    sr.reset(module_seeded_url)
    ps.set_floor(module_seeded_url, sr.FLOOR)


def _covered(url: str, *, unit: str = "sessions", quantity: int = 10) -> tuple:  # type: ignore[type-arg]
    w = ps.world(url)
    package = ps.sell(
        url, w.student, ps.product(url, unit=unit, quantity=quantity), SOLD
    )
    session = ps.session_for(url, w, sr.DAY)
    return w, package, session


def test_a_late_cancel_of_a_covered_session_consumes_its_units(module_seeded_url):
    url = module_seeded_url
    w, package, session = _covered(url)
    assert ps.sweep(url).package_covered == 1  # no charge before the cancel
    sr.approved_request(url, session, student=w.student, classification="late")
    sr.cancel(url, session)

    assert sr.run(url).cancellation_package_consumed == 1

    assert ps.remaining(url, package) == 9
    assert ps.session_charges(url, session) == []
    row = sr.decision(url, session, w.student)
    assert (row.result, row.charge_id) == ("package_consumed", None)
    assert sr.run(url).candidates == 0


def test_too_few_units_charge_the_session(module_seeded_url):
    url = module_seeded_url
    w, package, session = _covered(url, unit="minutes", quantity=30)
    sr.approved_request(url, session, student=w.student, classification="late")
    sr.cancel(url, session)

    assert sr.run(url).cancellation_charged == 1

    assert ps.remaining(url, package) == 30
    [charge] = ps.session_charges(url, session)
    assert sr.decision(url, session, w.student).charge_id == charge.id


def test_a_credit_outcome_on_a_covered_session_consumes_nothing(module_seeded_url):
    url = module_seeded_url
    w, package, session = _covered(url)
    sr.cancel(url, session)

    assert sr.run(url).cancellation_no_charge == 1

    assert ps.remaining(url, package) == 10
    assert sr.decision(url, session, w.student).result == "no_charge"


def test_a_covered_session_with_a_new_duration_or_closure_is_untouched(
    module_seeded_url,
):
    url = module_seeded_url
    w, package, session = _covered(url)
    sr.retime(url, session, 45)
    other = ps.session_for(url, w, sr.DAY + timedelta(days=1))
    sr.set_state(url, other, "skipped_closure")

    assert sr.run(url).candidates == 0
    assert ps.remaining(url, package) == 10


def test_a_consumed_key_is_never_a_candidate(module_seeded_url):
    url = module_seeded_url
    w, package, session = _covered(url)
    assert ps.consume(url, session, w.student).outcome == "consumed"
    sr.cancel(url, session)

    assert sr.run(url).candidates == 0
    assert sr.decision(url, session, w.student) is None
    assert ps.remaining(url, package) == 9
