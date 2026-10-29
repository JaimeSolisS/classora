"""Cohort course fees (#92 G2, G5, G7; B1-B4, C1, C3, J3, J5, J6, J8, J10):
posted by the #66 enrollment hook in the enrollment's Unit of Work, or by
``billing:post_enrollment_charges`` once their cause is fixed; prorated by
the sessions left (materialized and projected); priced through ``quote``
(scopes, custom prices, discounts after the proration); never twice."""

import uuid
from datetime import date, timedelta
from typing import Any

import pytest

from app.modules.billing import service as billing_service
from app.modules.families.models import Student
from app.workflows import enrollment_charges
from tests.conftest import frozen_clock
from tests.factories.billing import persist_custom_price, persist_discount
from tests.integration import cohort_support
from tests.integration import enrollment_charge_support as ec
from tests.integration.auth_support import auth_client
from tests.integration.conftest import run_sql
from tests.integration.lifecycle_support import events, in_uow
from tests.integration.series_support import owner_cookie, timeline
from tests.integration.session_charge_support import _family as make_family
from tests.integration.session_charge_support import set_offset
from tests.integration.test_class_session_create import new_student, new_subject


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Any:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture
def subject(module_seeded_url: str) -> uuid.UUID:
    return new_subject(module_seeded_url)


@pytest.fixture
def student(module_seeded_url: str) -> uuid.UUID:
    return new_student(module_seeded_url)


def _cohort_enrollments(url: str, cohort_id: Any) -> list[Any]:
    return run_sql(
        url,
        "SELECT id, enrolled_by_user_id FROM cohort_enrollments"
        " WHERE cohort_id = :c ORDER BY enrolled_at, id",
        {"c": str(cohort_id)},
    )


def test_an_enrollment_before_the_first_session_pays_the_full_fee(
    module_seeded_url, client, subject, student
):
    url = module_seeded_url
    tl = timeline()
    ec.set_floor(url, ec.floor_of(tl))
    family = ec.add_family(url, student)
    set_offset(url, family, 5)
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        cohort = ec.make_cohort(client, cookie, subject, tl.monday)
        ec.cohort_fee(url, subject, "1200.00", cohort_id=uuid.UUID(cohort["id"]))
        enrolled = ec.enroll_cohort(client, cookie, cohort["id"], student)

    [charge] = ec.fees(url, cohort_enrollment=enrolled["id"])
    assert charge.kind == "cohort_enrollment"
    assert f"{charge.list_amount:.2f}" == f"{charge.amount:.2f}" == "1200.00"
    assert (charge.proration_remaining, charge.proration_total) == (10, 10)
    assert charge.service_date == tl.now.date()  # the enrollment's local date
    assert charge.due_date == tl.monday + timedelta(days=5)  # J6 base + offset
    [period] = _cohort_enrollments(url, cohort["id"])
    assert charge.posted_by_user_id == period.enrolled_by_user_id
    [event] = events(url, record_type="charges", record_id=charge.id)
    assert event.action == "create"
    assert event.actor_user_id == period.enrolled_by_user_id


def test_scopes_custom_prices_and_discounts_price_the_fee(
    module_seeded_url, client, subject
):
    url = module_seeded_url
    tl = timeline()
    ec.set_floor(url, ec.floor_of(tl))
    first, second, third = (new_student(url) for _ in range(3))

    async def _family(session: Any) -> uuid.UUID:
        rows = [await session.get(Student, s) for s in (first, second)]
        return await make_family(session, *rows)

    family = in_uow(url, _family)
    ec.add_family(url, third)
    ec.cohort_fee(url, subject, "999.00")  # unscoped: loses to the scoped item
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        cohort = ec.make_cohort(client, cookie, subject, tl.monday)
        cohort_id = uuid.UUID(cohort["id"])
        item = ec.cohort_fee(url, subject, "1000.00", cohort_id=cohort_id)

        async def _pricing(session: Any) -> None:
            await persist_custom_price(
                session,
                service_price_id=item,
                amount="800.00",
                effective_from=date(2000, 1, 1),
                family_id=family,
            )
            await persist_discount(
                session,
                kind="sibling",
                percent="10",
                eligibility={"subject_ids": [str(subject)], "min_family_students": 2},
            )

        in_uow(url, _pricing)
        a = ec.enroll_cohort(client, cookie, cohort["id"], first)
        c = ec.enroll_cohort(client, cookie, cohort["id"], third)

    [custom] = ec.fees(url, cohort_enrollment=a["id"])
    assert custom.custom_price_id is not None
    assert f"{custom.list_amount:.2f}" == "800.00"
    assert f"{custom.discount_amount:.2f}" == "80.00"  # sibling 10 %
    assert f"{custom.amount:.2f}" == "720.00"
    lines = run_sql(
        url, "SELECT * FROM charge_discounts WHERE charge_id = :c", {"c": custom.id}
    )
    assert [line.kind for line in lines] == ["sibling"]
    [scoped] = ec.fees(url, cohort_enrollment=c["id"])
    assert scoped.custom_price_id is None and scoped.price_version_id is not None
    assert f"{scoped.amount:.2f}" == "1000.00"  # the cohort item, no discount


def test_a_late_enrollment_pays_for_the_sessions_left(
    module_seeded_url, client, subject, student
):
    url = module_seeded_url
    tl = timeline()
    ec.set_floor(url, ec.floor_of(tl))
    ec.add_family(url, student)
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        cohort = ec.make_cohort(client, cookie, subject, tl.monday)
    ec.cohort_fee(url, subject, "1200.00", cohort_id=uuid.UUID(cohort["id"]))
    sessions = ec.sessions_of(url, cohort_id=cohort["id"])
    ec.cancel_session(url, sessions[5].id)  # a future one
    ec.skip_session(url, sessions[6].id)  # a future closure
    # Week 2, Monday 18:00: three sessions have started (W1 Mon, W1 Wed, W2 Mon).
    with frozen_clock(tl.at(tl.week(2, 1), "18:00")):
        cookie = owner_cookie(url, client)
        enrolled = ec.enroll_cohort(client, cookie, cohort["id"], student)

    [charge] = ec.fees(url, cohort_enrollment=enrolled["id"])
    assert (charge.proration_remaining, charge.proration_total) == (5, 10)
    assert f"{charge.list_amount:.2f}" == "600.00"  # 1200 × 5 / 10
    assert charge.service_date == tl.week(2, 1)
    assert charge.due_date == tl.week(2, 1)  # enrolled after the first session


def test_occurrences_beyond_the_horizon_count(
    module_seeded_url, client, subject, student
):
    url = module_seeded_url
    tl = timeline()
    ec.set_floor(url, ec.floor_of(tl))
    ec.add_family(url, student)
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        cohort = ec.make_cohort(
            client, cookie, subject, tl.monday, session_count=20, weekdays=[1]
        )
    assert len(ec.sessions_of(url, cohort_id=cohort["id"])) == 12  # the horizon
    ec.cohort_fee(url, subject, "2000.00", cohort_id=uuid.UUID(cohort["id"]))
    # After session 1 started (Monday 16:00), before session 2.
    with frozen_clock(tl.at(tl.week(1, 1), "16:30")):
        cookie = owner_cookie(url, client)
        enrolled = ec.enroll_cohort(client, cookie, cohort["id"], student)

    [charge] = ec.fees(url, cohort_enrollment=enrolled["id"])
    assert (charge.proration_remaining, charge.proration_total) == (19, 20)
    assert f"{charge.list_amount:.2f}" == "1900.00"


def test_every_remaining_session_cancelled_gives_a_zero_fee(
    module_seeded_url, client, subject, student
):
    url = module_seeded_url
    tl = timeline()
    ec.set_floor(url, ec.floor_of(tl))
    ec.add_family(url, student)
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        cohort = ec.make_cohort(client, cookie, subject, tl.monday, session_count=2)
    ec.cohort_fee(url, subject, "300.00", cohort_id=uuid.UUID(cohort["id"]))
    [_, last] = ec.sessions_of(url, cohort_id=cohort["id"])
    ec.cancel_session(url, last.id)
    with frozen_clock(tl.at(tl.week(1, 1), "18:00")):
        cookie = owner_cookie(url, client)
        enrolled = ec.enroll_cohort(client, cookie, cohort["id"], student)

    [charge] = ec.fees(url, cohort_enrollment=enrolled["id"])
    assert (charge.proration_remaining, charge.proration_total) == (0, 2)
    assert f"{charge.amount:.2f}" == "0.00"


def test_missing_price_family_or_floor_is_retried_by_the_sweeper(
    module_seeded_url, client, subject
):
    url = module_seeded_url
    tl = timeline()
    no_price, no_family, billing_off = (new_student(url) for _ in range(3))
    ec.add_family(url, no_price)
    ec.add_family(url, billing_off)
    ec.set_floor(url, None)
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        cohort = ec.make_cohort(client, cookie, subject, tl.monday)
        off = ec.enroll_cohort(client, cookie, cohort["id"], billing_off)
    assert ec.fees(url, cohort_enrollment=off["id"]) == []  # billing off
    ec.set_floor(url, ec.floor_of(tl))
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        priceless = ec.enroll_cohort(client, cookie, cohort["id"], no_price)
    assert ec.fees(url, cohort_enrollment=priceless["id"]) == []

    listed = _unbilled(url)
    assert listed[str(priceless["id"])] == "no_price"
    assert listed[str(off["id"])] == "no_price"
    first = ec.run_enrollments(url)
    assert (first.no_price, first.failed) == (2, 0)

    ec.cohort_fee(url, subject, "1000.00", cohort_id=uuid.UUID(cohort["id"]))
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        orphan = ec.enroll_cohort(client, cookie, cohort["id"], no_family)
    assert ec.fees(url, cohort_enrollment=orphan["id"]) == []
    assert _unbilled(url) == {str(orphan["id"]): "no_billing_account"}

    second = ec.run_enrollments(url)
    assert second.posted == 2 and second.no_billing_account == 1
    assert len(ec.fees(url, cohort_enrollment=off["id"])) == 1
    assert len(ec.fees(url, cohort_enrollment=priceless["id"])) == 1

    ec.add_family(url, no_family)
    third = ec.run_enrollments(url)
    assert third.posted == 1
    assert len(ec.fees(url, cohort_enrollment=orphan["id"])) == 1
    assert _unbilled(url) == {}
    assert ec.run_enrollments(url).candidates == 0


def _unbilled(url: str) -> dict[str, str]:
    from app.core.pagination import OffsetParams

    page = in_uow(
        url,
        lambda s: enrollment_charges.list_unbilled_enrollments(
            s, page=OffsetParams(limit=100, offset=0)
        ),
    )
    return {str(item.enrollment_id): item.reason for item in page.items}


def test_the_floor_is_compared_with_the_due_date_base(
    module_seeded_url, client, subject
):
    url = module_seeded_url
    tl = timeline()
    early, late = new_student(url), new_student(url)
    ec.add_family(url, early)
    ec.add_family(url, late)
    # Go-live on the first session's day: an advance enrollment on Sunday is
    # charged (its base is Monday).
    ec.set_floor(url, tl.monday)
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        cohort = ec.make_cohort(client, cookie, subject, tl.monday)
        ec.cohort_fee(url, subject, "500.00", cohort_id=uuid.UUID(cohort["id"]))
        advance = ec.enroll_cohort(client, cookie, cohort["id"], early)
    assert len(ec.fees(url, cohort_enrollment=advance["id"])) == 1

    # A later go-live: the course and the enrollment are history.
    ec.set_floor(url, tl.week(3, 1))
    with frozen_clock(tl.now + timedelta(minutes=5)):
        cookie = owner_cookie(url, client)
        old = ec.enroll_cohort(client, cookie, cohort["id"], late)
    assert ec.fees(url, cohort_enrollment=old["id"]) == []
    assert ec.run_enrollments(url).posted == 0
    assert ec.fees(url, cohort_enrollment=old["id"]) == []
    assert str(old["id"]) not in _unbilled(url)


def test_the_hook_and_the_sweeper_post_one_fee(
    module_seeded_url, client, subject, student
):
    url = module_seeded_url
    tl = timeline()
    ec.set_floor(url, ec.floor_of(tl))
    ec.add_family(url, student)
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        cohort = ec.make_cohort(client, cookie, subject, tl.monday)
        ec.cohort_fee(url, subject, "700.00", cohort_id=uuid.UUID(cohort["id"]))
        enrolled = ec.enroll_cohort(client, cookie, cohort["id"], student)
    period = uuid.UUID(enrolled["id"])

    # The hook's step again, the sweeper twice.
    again = in_uow(
        url,
        lambda s: enrollment_charges.post_one(s, "cohort_enrollment", period),
    )
    assert again == "already_posted"
    assert ec.run_enrollments(url).candidates == 0
    assert ec.run_enrollments(url).candidates == 0
    [charge] = ec.fees(url, cohort_enrollment=period)
    assert len(events(url, record_type="charges", record_id=charge.id)) == 1


def test_withdrawal_and_re_enrollment_never_charge_twice(
    module_seeded_url, client, subject, student
):
    url = module_seeded_url
    tl = timeline()
    ec.set_floor(url, ec.floor_of(tl))
    ec.add_family(url, student)
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        cohort = ec.make_cohort(client, cookie, subject, tl.monday)
        ec.cohort_fee(url, subject, "1000.00", cohort_id=uuid.UUID(cohort["id"]))
        first = ec.enroll_cohort(client, cookie, cohort["id"], student)
    with frozen_clock(tl.now + timedelta(minutes=5)):
        cookie = owner_cookie(url, client)
        ec.withdraw_cohort(url, client, cookie, cohort["id"], first["id"])
    [fee] = ec.fees(url, cohort_enrollment=first["id"])
    assert ec.adjustments_of(url, fee.id) == []  # J4: no automatic credit
    with frozen_clock(tl.now + timedelta(minutes=10)):
        cookie = owner_cookie(url, client)
        second = ec.enroll_cohort(client, cookie, cohort["id"], student)
    assert ec.fees(url, cohort_enrollment=second["id"]) == []  # J5
    assert ec.run_enrollments(url).already_charged == 1

    # #101 credits the first fee to 0.00: the next period is charged again.
    run_sql(
        url,
        "INSERT INTO charge_adjustments (charge_id, billing_account_id, student_id,"
        " kind, amount, source_key, posted_at) SELECT id, billing_account_id,"
        " student_id, 'withdrawal', -amount, :k, now() FROM charges WHERE id = :c",
        {"k": f"test:{uuid.uuid4()}", "c": fee.id},
    )
    with frozen_clock(tl.now + timedelta(minutes=15)):
        cookie = owner_cookie(url, client)
        ec.withdraw_cohort(url, client, cookie, cohort["id"], second["id"])
    with frozen_clock(tl.at(tl.week(2, 1), "18:00")):
        cookie = owner_cookie(url, client)
        third = ec.enroll_cohort(client, cookie, cohort["id"], student)
    [charged] = ec.fees(url, cohort_enrollment=third["id"])
    assert (charged.proration_remaining, charged.proration_total) == (7, 10)
    assert f"{charged.amount:.2f}" == "700.00"


def test_an_unexpected_failure_rolls_the_enrollment_back(
    module_seeded_url, client, subject, student, monkeypatch
):
    url = module_seeded_url
    tl = timeline()
    ec.set_floor(url, ec.floor_of(tl))
    ec.add_family(url, student)
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        cohort = ec.make_cohort(client, cookie, subject, tl.monday)
    ec.cohort_fee(url, subject, "700.00", cohort_id=uuid.UUID(cohort["id"]))

    async def broken(session: Any, draft: Any) -> Any:
        raise RuntimeError("ledger down")

    monkeypatch.setattr(billing_service, "post_charge", broken)
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        before_events = len(events(url))
        response = cohort_support.enroll(client, cookie, cohort["id"], student)

    assert response.status_code == 500
    assert _cohort_enrollments(url, cohort["id"]) == []
    assert all(
        cohort_support.participants(url, row.id) == set()
        for row in ec.sessions_of(url, cohort_id=cohort["id"])
    )
    assert len(events(url)) == before_events


def test_expected_skips_never_fail_the_enrollment(module_seeded_url, client, subject):
    url = module_seeded_url
    tl = timeline()
    orphan, priced = new_student(url), new_student(url)
    ec.add_family(url, priced)
    ec.set_floor(url, ec.floor_of(tl))
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        cohort = ec.make_cohort(client, cookie, subject, tl.monday)
        no_price = ec.enroll_cohort(client, cookie, cohort["id"], priced)
        ec.cohort_fee(url, subject, "700.00", cohort_id=uuid.UUID(cohort["id"]))
        no_family = ec.enroll_cohort(client, cookie, cohort["id"], orphan)
    assert ec.fees(url, cohort_enrollment=no_price["id"]) == []
    assert ec.fees(url, cohort_enrollment=no_family["id"]) == []


def test_a_late_fee_is_prorated_before_its_discount(
    module_seeded_url, client, subject, student
):
    # B3: 1000.00 × 7/10 = 700.00, then a fixed 100.00 → 600.00 (discounting
    # first would give (1000 − 100) × 7/10 = 630.00).
    url = module_seeded_url
    tl = timeline()
    ec.set_floor(url, ec.floor_of(tl))
    ec.add_family(url, student)
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        cohort = ec.make_cohort(client, cookie, subject, tl.monday)
    ec.cohort_fee(url, subject, "1000.00", cohort_id=uuid.UUID(cohort["id"]))

    async def _discount(session: Any) -> None:
        await persist_discount(
            session,
            kind="discount",
            amount="100.00",
            eligibility={"subject_ids": [str(subject)]},
        )

    in_uow(url, _discount)
    with frozen_clock(tl.at(tl.week(2, 1), "18:00")):
        cookie = owner_cookie(url, client)
        enrolled = ec.enroll_cohort(client, cookie, cohort["id"], student)

    [charge] = ec.fees(url, cohort_enrollment=enrolled["id"])
    assert f"{charge.list_amount:.2f}" == "700.00"
    assert f"{charge.discount_amount:.2f}" == "100.00"
    assert f"{charge.amount:.2f}" == "600.00"
