"""The session charge sweeper ``billing:post_session_charges`` (#91 C, F1-F12).

Each test starts from :func:`~tests.integration.session_charge_support.reset`
(billing off, earlier uncharged sessions cancelled), so a run's counts are
the test's own. The center zone is Mexico City (UTC-6).

- F1 posting: confirmed and completed, every stored field, the due date
  with offsets 0 and 7, no actor; F2 the service date in the center zone;
  F3 the states and kinds never charged; F4 billing off and floor changes;
  F5 idempotent reruns; F6 two concurrent runs (5 attempts); F7 the
  historical price, the ranks, "not offered" and a zero price; F8 recovery
  of skipped items; F9 no starvation; F10 failure isolation; F11 the
  package seam; F12 kept charges.
"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import Money
from app.modules.billing import service as billing
from app.modules.families.models import Family, FamilyStudent, Student
from app.workflows import session_charges
from app.workflows.session_charges import SessionChargeRunResult
from tests.conftest import frozen_clock
from tests.factories.billing import family_with_students
from tests.factories.families import add_to_family
from tests.integration.conftest import run_sql, settings_for
from tests.integration.lifecycle_support import events, in_uow
from tests.integration.session_charge_support import (
    FLOOR,
    PRICE_FROM,
    add_family,
    add_price,
    add_version,
    charges_of,
    item,
    local,
    post_one,
    reset,
    run,
    set_floor,
    set_offset,
)

DAY = FLOOR + timedelta(days=9)
POSTED_AT = datetime(2031, 3, 20, 18, 0, tzinfo=UTC)
REPEATS = 5


@pytest.fixture(autouse=True)
def fresh(module_seeded_url: str) -> None:
    reset(module_seeded_url)


def only_charge(url: str, it: Any) -> Any:
    [row] = charges_of(url, it.session)
    return row


# --- F1 posting ----------------------------------------------------------------------


def test_confirmed_and_completed_sessions_are_charged_once(module_seeded_url):
    url = module_seeded_url
    confirmed = item(url, starts_at=local(DAY, 10), price="250.00")
    completed = item(url, starts_at=local(DAY, 12), state="completed", level=False)
    set_offset(url, completed.family, 7)
    set_floor(url, FLOOR)

    with frozen_clock(POSTED_AT):
        result = run(url)

    assert result == SessionChargeRunResult(candidates=2, posted=2)
    first, second = only_charge(url, confirmed), only_charge(url, completed)
    [version] = run_sql(
        url,
        "SELECT id FROM price_versions WHERE service_price_id = :p",
        {"p": confirmed.price},
    )
    assert first.price_version_id == version.id
    assert (first.list_amount, first.discount_amount, first.amount) == (
        Money("250.00").amount,
        Money("0.00").amount,
        Money("250.00").amount,
    )
    assert first.student_id == confirmed.student
    assert first.scheduled_minutes == 60
    assert first.education_level_id == confirmed.level
    assert second.education_level_id is None
    assert first.service_date == DAY == second.service_date
    assert first.due_date == DAY
    assert second.due_date == DAY + timedelta(days=7)
    assert first.posted_at == POSTED_AT
    assert first.posted_by_user_id is None
    assert first.description is None
    [event] = events(url, record_type="charges", record_id=first.id)
    assert (event.action, event.actor_user_id) == ("create", None)


# --- F2 the service date in the center zone ------------------------------------------


def test_the_service_date_is_the_local_date(module_seeded_url):
    url = module_seeded_url
    # 23:30 local on the floor date is the next day in UTC; the price changes
    # on the next day, so a UTC date would take the other one.
    late = item(
        url,
        starts_at=local(FLOOR, 23, 30),
        minutes=30,
        versions=[(PRICE_FROM, "100.00"), (FLOOR + timedelta(days=1), "300.00")],
    )
    before = item(url, starts_at=local(FLOOR - timedelta(days=1), 23, 30), minutes=30)
    assert local(FLOOR, 23, 30).date() == FLOOR + timedelta(days=1)
    set_floor(url, FLOOR)

    result = run(url)

    assert result.posted == 1
    charge = only_charge(url, late)
    assert charge.service_date == FLOOR
    assert charge.amount == Money("100.00").amount
    assert charges_of(url, before.session) == []


# --- F3 states and kinds -------------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "kind"),
    [
        ("unassigned", "individual"),
        ("offered", "individual"),
        ("conflict", "individual"),
        ("cancelled", "individual"),
        ("skipped_closure", "individual"),
        ("confirmed", "group"),
        ("confirmed", "course"),
    ],
)
def test_other_states_and_kinds_are_never_charged(module_seeded_url, state, kind):
    url = module_seeded_url
    it = item(url, starts_at=local(DAY), state=state, kind=kind)
    set_floor(url, FLOOR)

    result = run(url)

    assert result == SessionChargeRunResult()
    assert charges_of(url, it.session) == []


# --- F4 billing off and floor changes ------------------------------------------------


def test_billing_off_posts_nothing_and_reads_nothing_else(module_seeded_url):
    url = module_seeded_url
    it = item(url, starts_at=local(DAY))

    assert run(url) == SessionChargeRunResult()
    assert charges_of(url, it.session) == []


def test_the_floor_decides_and_clearing_it_keeps_posted_charges(module_seeded_url):
    url = module_seeded_url
    early = item(url, starts_at=local(FLOOR - timedelta(days=3)))
    on_floor = item(url, starts_at=local(FLOOR, 0, 0))
    set_floor(url, FLOOR)

    assert run(url).posted == 1
    assert charges_of(url, early.session) == []
    posted = only_charge(url, on_floor)

    set_floor(url, None)
    later = item(url, starts_at=local(DAY))
    assert run(url) == SessionChargeRunResult()
    assert charges_of(url, later.session) == []
    assert only_charge(url, on_floor) == posted

    # A floor moved earlier picks up the older session.
    set_floor(url, FLOOR - timedelta(days=5))
    assert run(url).posted == 2
    assert len(charges_of(url, early.session)) == 1


# --- F5 idempotent reruns ------------------------------------------------------------


def test_a_second_run_finds_nothing(module_seeded_url):
    url = module_seeded_url
    items = [item(url, starts_at=local(DAY, hour)) for hour in (9, 10, 11)]
    set_floor(url, FLOOR)

    assert run(url) == SessionChargeRunResult(candidates=3, posted=3)
    assert run(url) == SessionChargeRunResult()
    for it in items:
        assert len(charges_of(url, it.session)) == 1


def test_the_step_twice_posts_then_reports_already_posted(module_seeded_url):
    url = module_seeded_url
    it = item(url, starts_at=local(DAY))
    set_floor(url, FLOOR)

    assert post_one(url, it) == "posted"
    assert post_one(url, it) == "already_posted"

    charge = only_charge(url, it)
    assert len(events(url, record_type="charges", record_id=charge.id)) == 1


# --- F6 concurrent workers -----------------------------------------------------------


@pytest.mark.parametrize("attempt", range(REPEATS))
def test_two_concurrent_runs_post_each_charge_once(module_seeded_url, attempt):
    url = module_seeded_url
    items = [item(url, starts_at=local(DAY, 8 + n)) for n in range(10)]
    set_floor(url, FLOOR)

    async def _both() -> list[SessionChargeRunResult]:
        settings = settings_for(url)
        return list(
            await asyncio.gather(
                session_charges.run_post_session_charges(settings),
                session_charges.run_post_session_charges(settings),
            )
        )

    first, second = asyncio.run(_both())

    assert first.posted + second.posted == 10
    assert first.failed == second.failed == 0
    for it in items:
        assert len(charges_of(url, it.session)) == 1


# --- F7 the historical price ---------------------------------------------------------


def test_a_later_version_never_changes_a_posted_charge(module_seeded_url):
    url = module_seeded_url
    it = item(
        url,
        starts_at=local(DAY),
        # A version effective after the session's date but before the run.
        versions=[(PRICE_FROM, "200.00"), (DAY + timedelta(days=2), "300.00")],
    )
    set_floor(url, FLOOR)

    with frozen_clock(local(DAY + timedelta(days=5))):
        run(url)

    posted = only_charge(url, it)
    assert posted.amount == Money("200.00").amount
    assert it.price is not None
    add_version(url, it.price, DAY + timedelta(days=10), "500.00")
    later = item(
        url, starts_at=local(DAY + timedelta(days=11)), subject=it.subject, price=None
    )

    run(url)

    assert only_charge(url, it) == posted
    assert only_charge(url, later).amount == Money("500.00").amount


def test_level_and_duration_prices_win_by_rank(module_seeded_url):
    url = module_seeded_url
    by_level = item(url, starts_at=local(DAY, 9), price="100.00")
    add_price(
        url,
        by_level.subject,
        [(PRICE_FROM, "180.00")],
        education_level_id=by_level.level,
    )
    by_duration = item(url, starts_at=local(DAY, 11), minutes=90, price="100.00")
    add_price(url, by_duration.subject, [(PRICE_FROM, "190.00")], duration_minutes=90)
    exact = item(url, starts_at=local(DAY, 14), minutes=45, price="100.00")
    add_price(
        url,
        exact.subject,
        [(PRICE_FROM, "170.00")],
        education_level_id=exact.level,
        duration_minutes=45,
    )
    set_floor(url, FLOOR)

    assert run(url).posted == 3

    assert only_charge(url, by_level).amount == Money("180.00").amount
    assert only_charge(url, by_duration).amount == Money("190.00").amount
    assert only_charge(url, exact).amount == Money("170.00").amount
    assert only_charge(url, exact).scheduled_minutes == 45


def test_not_offered_at_the_deciding_rank_is_no_price(module_seeded_url):
    url = module_seeded_url
    it = item(url, starts_at=local(DAY), price="100.00")
    add_price(
        url,
        it.subject,
        [(PRICE_FROM, "150.00"), (DAY - timedelta(days=1), None)],
        education_level_id=it.level,
    )
    set_floor(url, FLOOR)

    result = run(url)

    assert result == SessionChargeRunResult(candidates=1, no_price=1)
    assert charges_of(url, it.session) == []


def test_a_zero_price_posts_a_zero_charge(module_seeded_url):
    url = module_seeded_url
    it = item(url, starts_at=local(DAY), price="0.00")
    set_floor(url, FLOOR)

    assert run(url).posted == 1

    charge = only_charge(url, it)
    assert (charge.list_amount, charge.amount) == (
        Money("0.00").amount,
        Money("0.00").amount,
    )
    [balance] = in_uow(
        url,
        lambda s: billing.charge_balances(
            s, charge.billing_account_id, today=DAY + timedelta(days=30)
        ),
    )
    assert balance.status == "void"


# --- F8 / C9 recovery ----------------------------------------------------------------


def test_skipped_items_are_posted_once_fixed(module_seeded_url):
    url = module_seeded_url
    orphan = item(url, starts_at=local(DAY, 9), family=False)
    unpriced = item(url, starts_at=local(DAY, 10), price=None)
    closed = item(url, starts_at=local(DAY, 11), state="skipped_closure")
    set_floor(url, FLOOR)

    first = run(url)
    assert first == SessionChargeRunResult(
        candidates=2, no_billing_account=1, no_price=1
    )

    family = add_family(url, orphan.student)
    add_price(url, unpriced.subject, [(PRICE_FROM, "120.00")])
    run_sql(
        url,
        "UPDATE class_sessions SET state = 'confirmed' WHERE id = :s",
        {"s": closed.session},
    )

    second = run(url)

    assert second == SessionChargeRunResult(candidates=3, posted=3)
    account = in_uow(url, lambda s: billing.ensure_billing_account(s, family))
    assert only_charge(url, orphan).billing_account_id == account
    assert only_charge(url, unpriced).amount == Money("120.00").amount
    assert len(charges_of(url, closed.session)) == 1


# --- F9 no starvation ----------------------------------------------------------------


def test_a_backlog_of_unbillable_items_never_starves_a_new_one(module_seeded_url):
    url = module_seeded_url

    async def _backlog(session: AsyncSession) -> None:
        from tests.factories.families import persist_student
        from tests.factories.scheduling import (
            add_participant,
            persist_class_session,
            persist_subject,
        )

        subject = await persist_subject(session)
        start = local(FLOOR, 6)
        for n in range(250):
            starts_at = start + timedelta(minutes=30 * n)
            row = await persist_class_session(
                session,
                subject_id=subject.id,
                starts_at=starts_at,
                ends_at=starts_at + timedelta(minutes=30),
            )
            student = await persist_student(session, contacts=0)
            await add_participant(session, row, student.id)

    in_uow(url, _backlog)
    newer = item(url, starts_at=local(FLOOR + timedelta(days=20)))
    set_floor(url, FLOOR)

    result = run(url)

    assert result.candidates == 251
    assert result.no_billing_account == 250
    assert result.posted == 1
    assert len(charges_of(url, newer.session)) == 1


# --- F10 failure isolation -----------------------------------------------------------


def test_a_failing_item_is_logged_and_retried(module_seeded_url, monkeypatch, caplog):
    url = module_seeded_url
    good = item(url, starts_at=local(DAY, 9))
    bad = item(url, starts_at=local(DAY, 10))
    [student] = run_sql(
        url,
        "SELECT given_names, family_names FROM students WHERE id = :s",
        {"s": bad.student},
    )
    set_floor(url, FLOOR)
    # Since #95 the sweeper prices through billing.service.base_price/quote.
    real = billing.base_price

    async def _lookup(session: AsyncSession, query: Any) -> Any:
        if query.subject_id == bad.subject:
            raise RuntimeError("catalog unavailable")
        return await real(session, query)

    monkeypatch.setattr(billing, "base_price", _lookup)
    with caplog.at_level(logging.ERROR, logger=session_charges.__name__):
        result = run(url)

    assert result == SessionChargeRunResult(candidates=2, posted=1, failed=1)
    assert len(charges_of(url, good.session)) == 1
    assert charges_of(url, bad.session) == []
    [record] = caplog.records
    assert record.message == "Posting a session charge failed"
    assert (record.class_session_id, record.student_id, record.error) == (
        str(bad.session),
        str(bad.student),
        "RuntimeError",
    )
    logged = str(vars(record))
    assert student.given_names not in logged
    assert student.family_names not in logged

    monkeypatch.setattr(billing, "base_price", real)
    assert run(url) == SessionChargeRunResult(candidates=1, posted=1)
    assert len(charges_of(url, bad.session)) == 1


# --- F11 the package seam ------------------------------------------------------------


def test_a_package_covered_key_is_not_charged(module_seeded_url, monkeypatch):
    url = module_seeded_url
    covered = item(url, starts_at=local(DAY, 9))
    other = item(url, starts_at=local(DAY, 10))
    set_floor(url, FLOOR)
    asked: list[Any] = []

    async def _covered(session: AsyncSession, items: Any) -> set[Any]:
        asked.extend(items)
        return {(covered.session, covered.student)}

    monkeypatch.setattr(billing, "package_covered_keys", _covered)

    result = run(url)

    assert result == SessionChargeRunResult(candidates=2, posted=1, package_covered=1)
    assert charges_of(url, covered.session) == []
    assert len(charges_of(url, other.session)) == 1
    query = next(q for q in asked if q.class_session_id == covered.session)
    assert (query.student_id, query.subject_id, query.service_date) == (
        covered.student,
        covered.subject,
        DAY,
    )
    assert query.scheduled_minutes == 60


# --- F12 kept charges ----------------------------------------------------------------


def _move_to_new_family(student_id: uuid.UUID) -> Any:
    async def _move(session: AsyncSession) -> uuid.UUID:
        member = await session.scalar(
            select(FamilyStudent).where(FamilyStudent.student_id == student_id)
        )
        assert member is not None
        await session.delete(member)
        await session.flush()
        new_family, _ = await family_with_students(session, 0)
        family = await session.get(Family, new_family)
        student = await session.get(Student, student_id)
        assert family is not None and student is not None
        await add_to_family(session, family, student)
        return new_family

    return _move


def test_a_reschedule_a_new_duration_or_a_family_move_keep_the_charge(
    module_seeded_url,
):
    url = module_seeded_url
    it = item(url, starts_at=local(DAY, 10))
    assert it.price is not None
    add_price(url, it.subject, [(PRICE_FROM, "400.00")], duration_minutes=90)
    set_floor(url, FLOOR)
    run(url)
    posted = only_charge(url, it)

    # Moved two days later and lengthened to 90 minutes (a reschedule and a
    # duration change of a confirmed session), and the student moves.
    new_start = local(DAY + timedelta(days=2), 15)
    run_sql(
        url,
        "UPDATE class_sessions SET starts_at = :s, ends_at = :e,"
        " scheduled_minutes = 90, version = version + 1 WHERE id = :id",
        {"s": new_start, "e": new_start + timedelta(minutes=90), "id": it.session},
    )
    in_uow(url, _move_to_new_family(it.student))

    assert run(url) == SessionChargeRunResult()

    kept = only_charge(url, it)
    assert kept == posted
    assert (kept.service_date, kept.scheduled_minutes, kept.amount) == (
        DAY,
        60,
        Money("250.00").amount,
    )


# --- #95 C4: no custom price and no discount posts as before -------------------------


def test_a_plain_session_has_no_custom_price_and_no_lines(module_seeded_url):
    url = module_seeded_url
    it = item(url, starts_at=local(DAY, 10), price="250.00")
    set_floor(url, FLOOR)

    run(url)

    charge = only_charge(url, it)
    assert charge.custom_price_id is None and charge.price_version_id is not None
    assert (charge.list_amount, charge.discount_amount, charge.amount) == (
        Money("250.00").amount,
        Money("0.00").amount,
        Money("250.00").amount,
    )
    assert (
        run_sql(
            url,
            "SELECT id FROM charge_discounts WHERE charge_id = :c",
            {"c": charge.id},
        )
        == []
    )
