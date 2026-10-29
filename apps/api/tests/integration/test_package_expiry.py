"""The daily package expiry (#94 F; L1, L2, L5; G7): one ``expire`` row for
the remainder the day after ``expires_on`` (reruns write nothing); nothing
for an empty package; the L1 fallback consumes the pending covered sessions
first (a later approval is a no-op; a short one is charged by the sweeper);
an approved closeout is never fallback-consumed; billing off doesn't stop
it."""

import logging
from datetime import date, timedelta

import pytest

from tests.integration import package_support as ps

SOLD = date(2031, 3, 1)
EXPIRES = date(2031, 3, 31)  # SOLD + 30 days
AFTER = EXPIRES + timedelta(days=1)
FLOOR = date(2031, 1, 1)


@pytest.fixture(autouse=True)
def _reset(module_seeded_url: str) -> None:
    ps.reset(module_seeded_url)


def sold(url: str, w: ps.World, **product: object) -> object:
    return ps.sell(url, w.student, ps.product(url, validity_days=30, **product), SOLD)


def test_the_day_after_expiry_the_remainder_expires_once(module_seeded_url, caplog):
    url = module_seeded_url
    w = ps.world(url)
    package = sold(url, w, quantity=4)

    assert ps.expire(url, EXPIRES).packages == 0  # still valid on its last day
    with caplog.at_level(logging.INFO, logger="app.workflows.package_expiry"):
        run = ps.expire(url, AFTER)

    assert (run.packages, run.expired, run.failed) == (1, 1, 0)
    expired = [t for t in ps.transactions(url, package) if t.kind == "expire"]
    assert [t.units for t in expired] == [-4]
    assert ps.remaining(url, package) == 0
    [line] = [r for r in caplog.records if r.message == "Expired packages"]
    assert line.expired_count == 1  # type: ignore[attr-defined]
    rerun = ps.expire(url, AFTER + timedelta(days=1))
    assert (rerun.packages, rerun.expired) == (0, 0)
    assert len([t for t in ps.transactions(url, package) if t.kind == "expire"]) == 1


def test_an_empty_package_gets_no_expire_row(module_seeded_url):
    url = module_seeded_url
    w = ps.world(url)
    package = sold(url, w, quantity=1)
    ps.hook(url, ps.session_for(url, w, date(2031, 3, 10)))

    run = ps.expire(url, AFTER)

    assert (run.packages, run.expired) == (0, 0)
    assert [t.kind for t in ps.transactions(url, package)] == ["grant", "consume"]


def test_a_session_never_closed_out_is_consumed_before_expiry(module_seeded_url):
    url = module_seeded_url
    w = ps.world(url)
    package = sold(url, w, quantity=5)
    session = ps.session_for(url, w, date(2031, 3, 20))

    run = ps.expire(url, AFTER)

    assert (run.fallback_consumed, run.expired) == (1, 1)
    assert [(t.kind, t.units) for t in ps.transactions(url, package)] == [
        ("grant", 5),
        ("consume", -1),
        ("expire", -4),
    ]
    ps.approved(url, session)  # the later approval is a no-op
    assert len(ps.consumes(url, session)) == 1
    assert ps.remaining(url, package) == 0


def test_a_short_fallback_is_charged_by_the_next_sweep(module_seeded_url):
    url = module_seeded_url
    w = ps.world(url)
    package = sold(url, w, unit="minutes", quantity=60)
    session = ps.session_for(url, w, date(2031, 3, 20), minutes=90)

    run = ps.expire(url, AFTER)

    assert (run.fallback_short, run.expired) == (1, 1)
    assert ps.consumes(url, session) == []
    assert ps.remaining(url, package) == 0
    ps.set_floor(url, FLOOR)
    assert ps.sweep(url).posted == 1
    assert len(ps.session_charges(url, session)) == 1


def test_after_expiry_a_session_in_the_old_window_is_not_covered(
    module_seeded_url,
):
    url = module_seeded_url
    w = ps.world(url)
    package = sold(url, w)
    ps.expire(url, AFTER)
    late_booked = ps.session_for(url, w, date(2031, 3, 25))
    ps.set_floor(url, FLOOR)

    assert ps.sweep(url).posted == 1
    assert len(ps.session_charges(url, late_booked)) == 1
    assert ps.remaining(url, package) == 0


def test_an_approved_closeout_is_not_fallback_consumed(module_seeded_url):
    url = module_seeded_url
    w = ps.world(url)
    package = sold(url, w, unit="minutes", quantity=60)
    session = ps.session_for(url, w, date(2031, 3, 20), minutes=90)
    ps.approved(url, session)  # short at approval
    assert ps.remaining(url, package) == 60

    run = ps.expire(url, AFTER)

    assert (run.fallback_consumed, run.fallback_short, run.expired) == (0, 0, 1)
    assert ps.consumes(url, session) == []


def test_billing_off_does_not_stop_expiry(module_seeded_url):
    url = module_seeded_url
    w = ps.world(url)
    package = sold(url, w)
    ps.session_for(url, w, date(2031, 3, 5))
    ps.set_floor(url, None)

    run = ps.expire(url, AFTER)

    assert (run.fallback_consumed, run.expired) == (1, 1)
    assert ps.remaining(url, package) == 0


def test_the_fallback_uses_the_earlier_expiring_package_first(module_seeded_url):
    url = module_seeded_url
    w = ps.world(url)
    thirty = sold(url, w)
    twenty = ps.sell(url, w.student, ps.product(url, validity_days=20), SOLD)
    session = ps.session_for(url, w, date(2031, 3, 10))

    run = ps.expire(url, AFTER)

    # The 20-day package expires first, so it pays for the session; then
    # both are expired (both are past their date).
    assert (run.packages, run.fallback_consumed, run.expired) == (2, 1, 2)
    [row] = ps.consumes(url, session)
    assert row.package_id == twenty
    assert ps.remaining(url, thirty) == 0


def test_a_failing_fallback_item_keeps_the_package_for_the_next_run(
    module_seeded_url, monkeypatch
):
    url = module_seeded_url
    w = ps.world(url)
    package = sold(url, w)
    session = ps.session_for(url, w, date(2031, 3, 10))

    async def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(ps.package_expiry, "consume_pending", boom)
    failed = ps.expire(url, AFTER)
    assert (failed.failed, failed.expired) == (1, 0)
    assert ps.remaining(url, package) == 10
    monkeypatch.undo()

    retried = ps.expire(url, AFTER)

    assert (retried.fallback_consumed, retried.expired) == (1, 1)
    assert len(ps.consumes(url, session)) == 1
