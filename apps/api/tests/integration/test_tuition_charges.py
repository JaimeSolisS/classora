"""The daily tuition job (#93 E, H2, H3, H5): one charge per plan per period
on the center's local dates, priced on the period's start from the pinned
item, discounts snapshotted, reruns and catch-up, the floor, billing off,
and the K9 account binding."""

import asyncio
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.exc import IntegrityError

from app.modules.billing import service as billing
from app.workflows import tuition_charges
from tests.factories.billing import assign_discount, persist_discount
from tests.integration import tuition_support as ts
from tests.integration.conftest import run_sql, settings_for
from tests.integration.lifecycle_support import events, in_uow
from tests.integration.session_charge_support import set_offset


@pytest.fixture(autouse=True)
def _reset(module_seeded_url: str) -> None:
    ts.reset(module_seeded_url)


def test_a_monthly_period_is_due_from_00_00_local_on_its_first_day(
    module_seeded_url,
):
    url = module_seeded_url
    w = ts.world(url)
    plan = ts.plan(url, w, starts_on=date(2031, 1, 15))
    ts.set_floor(url, date(2031, 1, 1))

    first = ts.run(url, ts.local(date(2031, 1, 20)))
    assert (first.plans, first.posted) == (1, 1)
    # 23:59 local on 14 Feb is 05:59 UTC on 15 Feb: still the first period.
    late = ts.local(date(2031, 2, 14), "23:59")
    assert late.date() == date(2031, 2, 15)
    assert ts.run(url, late).posted == 0
    # 00:01 local on 15 Feb (06:01 UTC) is the second period's first day.
    assert ts.run(url, ts.local(date(2031, 2, 15), "00:01")).posted == 1

    assert ts.starts(url, plan) == [date(2031, 1, 15), date(2031, 2, 15)]


def test_the_charge_fields_due_date_snapshot_and_audit(module_seeded_url):
    url = module_seeded_url
    w = ts.world(url, amount="1200.00")
    set_offset(url, w.family, 3)
    plan = ts.plan(url, w, starts_on=date(2031, 3, 1), due_offset_days=5)
    ts.set_floor(url, date(2031, 1, 1))

    assert ts.run(url, ts.local(date(2031, 3, 1), "08:00")).posted == 1

    [charge] = ts.charges(url, plan)
    assert charge.kind == "tuition_period"
    assert charge.tuition_plan_id == plan
    assert charge.service_date == date(2031, 3, 1)
    assert charge.service_period_end == date(2031, 3, 31)
    assert charge.price_version_id is not None
    assert charge.custom_price_id is None
    assert charge.education_level_id == w.level
    assert f"{charge.list_amount:.2f}" == "1200.00"
    assert f"{charge.amount:.2f}" == "1200.00"
    assert charge.due_date == date(2031, 3, 9)  # start + 5 (plan) + 3 (account)
    assert charge.posted_by_user_id is None
    assert charge.class_session_id is None and charge.scheduled_minutes is None
    assert len(events(url, record_type="charges", record_id=charge.id)) == 1


def test_a_new_version_prices_later_periods_and_a_null_one_is_no_price(
    module_seeded_url,
):
    url = module_seeded_url
    w = ts.world(url, amount="1000.00")
    plan = ts.plan(url, w, starts_on=date(2031, 1, 15))
    ts.set_floor(url, date(2031, 1, 1))
    ts.run(url, ts.local(date(2031, 2, 20)))
    [jan_before, feb_before] = ts.charges(url, plan)
    ts.add_version(url, w.item, date(2031, 3, 1), "1100.00")
    ts.add_version(url, w.item, date(2031, 4, 1), None)

    march = ts.run(url, ts.local(date(2031, 5, 20)))

    assert (march.posted, march.no_price) == (1, 2)  # 15 Apr and 15 May
    jan, feb, mar = ts.charges(url, plan)
    assert (jan, feb) == (jan_before, feb_before)  # unchanged, byte for byte
    assert f"{mar.amount:.2f}" == "1100.00"
    ts.add_version(url, w.item, date(2031, 6, 1), "1200.00")
    # A version effective later doesn't price April or May: still no_price.
    assert ts.run(url, ts.local(date(2031, 5, 21))).no_price == 2
    run_sql(
        url,
        "DELETE FROM price_versions WHERE service_price_id = :p"
        " AND effective_from = '2031-04-01'",
        {"p": w.item},
    )

    caught_up = ts.run(url, ts.local(date(2031, 5, 21)))

    assert (caught_up.posted, caught_up.no_price) == (2, 0)
    assert ts.starts(url, plan)[-2:] == [date(2031, 4, 15), date(2031, 5, 15)]


def test_discounts_apply_after_the_base_price(module_seeded_url):
    url = module_seeded_url
    w = ts.world(url, amount="1000.00")
    assert w.family is not None
    ts.add_sibling(url, w.family)
    plan = ts.plan(url, w, starts_on=date(2031, 6, 1))
    ts.set_floor(url, date(2031, 1, 1))

    async def _discounts(session):
        sibling = await persist_discount(
            session,
            kind="sibling",
            percent="10",
            eligibility={"min_family_students": 2, "subject_ids": [str(w.subject)]},
        )
        fixed = await persist_discount(
            session,
            kind="discount",
            audience="assigned",
            amount="50.00",
            eligibility={"subject_ids": [str(w.subject)]},
        )
        await assign_discount(session, fixed.id, student_id=w.student)
        return sibling.id, fixed.id

    in_uow(url, _discounts)

    assert ts.run(url, ts.local(date(2031, 6, 1))).posted == 1

    [charge] = ts.charges(url, plan)
    assert f"{charge.list_amount:.2f}" == "1000.00"
    assert f"{charge.discount_amount:.2f}" == "150.00"
    assert f"{charge.amount:.2f}" == "850.00"
    lines = run_sql(
        url,
        "SELECT kind, amount FROM charge_discounts WHERE charge_id = :c"
        " ORDER BY position",
        {"c": charge.id},
    )
    assert [(row.kind, f"{row.amount:.2f}") for row in lines] == [
        ("sibling", "100.00"),
        ("discount", "50.00"),
    ]


def test_weekly_and_biweekly_plans_over_five_weeks(module_seeded_url):
    url = module_seeded_url
    weekly = ts.world(url, frequency="weekly", amount="250.00")
    biweekly = ts.world(url, frequency="biweekly", amount="480.00")
    start = date(2031, 9, 1)
    weekly_plan = ts.plan(url, weekly, starts_on=start)
    biweekly_plan = ts.plan(url, biweekly, starts_on=start)
    ts.set_floor(url, date(2031, 1, 1))

    for day in range(35):
        ts.run(url, ts.local(start + timedelta(days=day), "09:00"))

    assert len(ts.charges(url, weekly_plan)) == 5
    assert ts.starts(url, biweekly_plan) == [
        start,
        start + timedelta(days=14),
        start + timedelta(days=28),
    ]
    assert ts.charges(url, weekly_plan)[1].service_period_end == date(2031, 9, 14)


def test_two_runs_on_one_day_post_one_charge_and_one_snapshot(module_seeded_url):
    url = module_seeded_url
    w = ts.world(url)
    plan = ts.plan(url, w, starts_on=date(2032, 1, 10))
    ts.set_floor(url, date(2032, 1, 1))

    async def _discount(session):
        await persist_discount(
            session,
            kind="discount",
            percent="5",
            eligibility={"subject_ids": [str(w.subject)]},
        )

    in_uow(url, _discount)
    now = ts.local(date(2032, 1, 10))
    assert ts.run(url, now).posted == 1
    again = ts.run(url, now)

    assert (again.posted, again.already_posted, again.periods_due) == (0, 0, 0)
    [charge] = ts.charges(url, plan)
    lines = run_sql(
        url,
        "SELECT count(*) AS n FROM charge_discounts WHERE charge_id = :c",
        {"c": charge.id},
    )
    assert lines[0].n == 1


def test_a_three_week_outage_is_caught_up_oldest_first(module_seeded_url):
    url = module_seeded_url
    w = ts.world(url, frequency="weekly", amount="200.00")
    start = date(2032, 3, 1)
    plan = ts.plan(url, w, starts_on=start)
    ts.set_floor(url, date(2032, 1, 1))

    result = ts.run(url, ts.local(start + timedelta(days=20)))

    assert (result.periods_due, result.posted) == (3, 3)
    rows = ts.charges(url, plan)
    assert [r.service_date for r in rows] == [
        start,
        start + timedelta(days=7),
        start + timedelta(days=14),
    ]
    posted_at = [r.posted_at for r in rows]
    assert posted_at == sorted(posted_at)


def test_the_floor_and_billing_off(module_seeded_url):
    url = module_seeded_url
    w = ts.world(url)
    plan = ts.plan(url, w, starts_on=date(2032, 5, 20))
    now = ts.local(date(2032, 7, 25))

    off = ts.run(url, now)
    assert off == tuition_charges.TuitionChargeRunResult()
    assert ts.charges(url, plan) == []

    ts.set_floor(url, date(2032, 6, 1))  # after the 20 May period's start
    on = ts.run(url, now)

    assert on.posted == 2
    assert ts.starts(url, plan) == [date(2032, 6, 20), date(2032, 7, 20)]


def test_two_concurrent_runs_post_each_period_once(module_seeded_url):
    url = module_seeded_url
    plans = []
    for _ in range(10):
        w = ts.world(url, frequency="weekly", amount="100.00")
        plans.append(ts.plan(url, w, starts_on=date(2033, 1, 3)))
    ts.set_floor(url, date(2033, 1, 1))
    now = ts.local(date(2033, 1, 18))

    async def _both():
        settings = settings_for(url)
        return await asyncio.gather(
            tuition_charges.run_post_tuition_charges(settings, now=now),
            tuition_charges.run_post_tuition_charges(settings, now=now),
        )

    first, second = asyncio.run(_both())

    assert first.failed == second.failed == 0
    assert first.posted + second.posted == 30
    for plan in plans:
        assert ts.starts(url, plan) == [
            date(2033, 1, 3),
            date(2033, 1, 10),
            date(2033, 1, 17),
        ]


def test_a_family_change_is_family_changed_listed_and_never_posted(
    module_seeded_url,
):
    url = module_seeded_url
    w = ts.world(url)
    plan = ts.plan(url, w, starts_on=date(2033, 3, 1))
    ts.set_floor(url, date(2033, 1, 1))
    assert ts.run(url, ts.local(date(2033, 3, 2))).posted == 1
    new_family = ts.move_to_new_family(url, w.student)

    result = ts.run(url, ts.local(date(2033, 4, 2)))

    assert (result.posted, result.family_changed) == (0, 1)
    assert ts.starts(url, plan) == [date(2033, 3, 1)]
    rows = run_sql(
        url,
        "SELECT c.id FROM charges c JOIN billing_accounts a"
        " ON a.id = c.billing_account_id WHERE a.family_id = :f",
        {"f": new_family},
    )
    assert rows == []


def test_a_tuition_charge_on_another_account_is_refused(module_seeded_url):
    url = module_seeded_url
    w = ts.world(url)
    other = ts.world(url)
    plan = ts.plan(url, w, starts_on=date(2033, 5, 1))
    assert other.family is not None
    account_id = in_uow(url, lambda s: billing.ensure_billing_account(s, other.family))

    with pytest.raises(IntegrityError) as caught:
        run_sql(
            url,
            "INSERT INTO charges (billing_account_id, student_id, kind,"
            " tuition_plan_id, service_date, service_period_end, list_amount,"
            " amount, due_date, posted_at) VALUES (:a, :s, 'tuition_period', :p,"
            " '2033-05-01', '2033-05-31', 10, 10, '2033-05-01', now())",
            {"a": account_id, "s": w.student, "p": plan},
        )
    assert getattr(caught.value.orig, "sqlstate", None) == "23503"
    assert "fk_charges_tuition_plan" in str(caught.value.orig)


def test_a_run_on_new_york_dates_across_a_dst_change(module_seeded_url):
    url = module_seeded_url
    ts.set_zone(url, "America/New_York")
    try:
        w = ts.world(url, frequency="weekly")
        plan = ts.plan(url, w, starts_on=date(2034, 3, 6))  # Monday
        ts.set_floor(url, date(2034, 1, 1))
        # 12 Mar 2034: clocks go forward at 02:00 EST (UTC-5 → UTC-4).
        # 23:30 local on Sunday 12 Mar is 03:30 UTC on 13 Mar.
        ny = ZoneInfo("America/New_York")
        sunday = datetime(2034, 3, 12, 23, 30, tzinfo=ny).astimezone(UTC)
        assert sunday.date() == date(2034, 3, 13)
        assert ts.run(url, sunday).posted == 1  # only the 6 Mar period
        monday = datetime(2034, 3, 13, 0, 30, tzinfo=ny).astimezone(UTC)
        assert ts.run(url, monday).posted == 1

        assert ts.starts(url, plan) == [date(2034, 3, 6), date(2034, 3, 13)]
    finally:
        ts.set_zone(url, "America/Mexico_City")
