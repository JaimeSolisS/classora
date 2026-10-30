"""Withdrawal confirms with the refund outcome (#101 F3; S4, C2 step 11, D2,
E1, E3).

A fee paid by two payments → two ``refunds`` rows with ``withdrawal_id``
whose sum is ``refund_amount``; a partly paid fee refunds only the freed
part; a group's #92 J4 credits free money that is refunded too; older,
unrelated credit is never refunded; nothing freed → ``409
withdrawal_nothing_to_refund``; the date and method checks; a reversed
source payment frees nothing."""

from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.conftest import frozen_clock
from tests.integration import corrections_support as cs
from tests.integration import enrollment_charge_support as ec
from tests.integration import withdrawal_support as ws
from tests.integration.auth_support import auth_client
from tests.integration.conftest import run_sql
from tests.integration.lifecycle_support import code_of, events
from tests.integration.series_support import timeline


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Any:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


def _refunds(url: str, withdrawal_id: Any) -> list[Any]:
    return run_sql(
        url,
        "SELECT * FROM refunds WHERE withdrawal_id = :w ORDER BY created_at, id",
        {"w": withdrawal_id},
    )


def test_a_fee_paid_by_two_payments_gets_two_refunds(
    module_seeded_url: str, client: TestClient
) -> None:
    url = module_seeded_url
    tl = timeline()
    with frozen_clock(tl.now) as clock:
        world = ws.cohort_world(url, client, tl)
        [fee] = ec.fees(url, cohort_enrollment=world.enrollment_id)
        staff = ws.owner(url, client)
        first = ws.pay(
            client,
            staff.cookie,
            world.family,
            "1000.00",
            [(fee.id, "1000.00")],
            ws.day(tl.now),
        )
        clock.advance(timedelta(days=1))
        staff = ws.owner(url, client)
        second = ws.pay(
            client,
            staff.cookie,
            world.family,
            "2000.00",
            [(fee.id, "2000.00")],
            ws.day(tl.now) + timedelta(days=1),
        )
        clock.set(ws.thursday(tl, 2))  # credit 1,800.00 of a paid 3,000.00
        staff = ws.owner(url, client)
        found = ws.preview_of(client, staff.cookie, **world.subject_query)
        assert found["totals"]["refundable_cash"] == "1800.00"
        today = ws.day(ws.thursday(tl, 2))
        response = ws.refund(client, staff.cookie, ws.body(found, refund_on=today))

    assert response.status_code == 201, response.text
    withdrawal = response.json()["withdrawal"]
    assert withdrawal["outcome"] == "refund"
    assert withdrawal["refund_amount"] == "1800.00"
    refunds = _refunds(url, withdrawal["id"])
    # The newest allocation (the second payment, 2,000.00) is released first:
    # 1,800.00 of it is freed, nothing of the first one.
    assert [(str(r.payment_id), f"{r.amount:.2f}") for r in refunds] == [
        (second, "1800.00")
    ]
    assert first != second
    assert sorted(withdrawal["refund_ids"]) == sorted(str(r.id) for r in refunds)
    for refund in refunds:
        assert refund.reason == ws.REASON
        assert refund.payment_method_code == "transfer"
        [event] = events(url, record_type="refunds", record_id=refund.id)
        assert event.actor_user_id == staff.user.id
    assert response.json()["account_credit_after"] == "0.00"
    cs.assert_invariants(url, ws.account_of(url, world.family))


def test_two_payments_both_freed_are_refunded_one_row_each(
    module_seeded_url: str, client: TestClient
) -> None:
    url = module_seeded_url
    tl = timeline()
    with frozen_clock(tl.now) as clock:
        world = ws.cohort_world(url, client, tl)
        [fee] = ec.fees(url, cohort_enrollment=world.enrollment_id)
        staff = ws.owner(url, client)
        on = ws.day(tl.now)
        a = ws.pay(
            client, staff.cookie, world.family, "1500.00", [(fee.id, "1500.00")], on
        )
        b = ws.pay(
            client, staff.cookie, world.family, "1500.00", [(fee.id, "1500.00")], on
        )
        clock.set(ws.thursday(tl, 2))
        staff = ws.owner(url, client)
        found = ws.preview_of(client, staff.cookie, **world.subject_query)
        response = ws.refund(
            client,
            staff.cookie,
            ws.body(
                found,
                refund_on=ws.day(ws.thursday(tl, 2)),
                overrides=[(fee.id, "2500.00")],
            ),
        )
    assert response.status_code == 201, response.text
    refunds = _refunds(url, response.json()["withdrawal"]["id"])
    by_payment = {str(r.payment_id): f"{r.amount:.2f}" for r in refunds}
    assert by_payment == {b: "1500.00", a: "1000.00"}
    assert response.json()["withdrawal"]["refund_amount"] == "2500.00"
    cs.assert_invariants(url, ws.account_of(url, world.family))


def test_a_partly_paid_fee_refunds_only_the_freed_part(
    module_seeded_url: str, client: TestClient
) -> None:
    url = module_seeded_url
    tl = timeline()
    with frozen_clock(tl.now) as clock:
        world = ws.cohort_world(url, client, tl)
        [fee] = ec.fees(url, cohort_enrollment=world.enrollment_id)
        staff = ws.owner(url, client)
        ws.pay(
            client,
            staff.cookie,
            world.family,
            "1500.00",
            [(fee.id, "1500.00")],
            ws.day(tl.now),
        )
        clock.set(ws.thursday(tl, 2))
        staff = ws.owner(url, client)
        found = ws.preview_of(client, staff.cookie, **world.subject_query)
        # new net 1,200.00 < 1,500.00 allocated: 300.00 freed.
        assert found["totals"]["refundable_cash"] == "300.00"
        assert found["totals"]["reduces_debt"] == "1500.00"
        response = ws.refund(
            client, staff.cookie, ws.body(found, refund_on=ws.day(ws.thursday(tl, 2)))
        )
    assert response.status_code == 201, response.text
    assert response.json()["withdrawal"]["refund_amount"] == "300.00"
    assert ec.net_of(url, fee.id) == "1200.00"


def test_a_groups_automatic_credits_free_money_that_is_refunded(
    module_seeded_url: str, client: TestClient
) -> None:
    url = module_seeded_url
    tl = timeline()
    with frozen_clock(tl.now) as clock:
        world = ws.group_world(url, client, tl, fee=None, session_price="150.00")
        sessions = ec.sessions_of(url, group_id=world.owner_id)
        ec.confirm_sessions(url, [s.id for s in sessions[:6]])
        ec.run_sessions(url)
        charges = ec.charges_of(url, world.student, "session")
        staff = ws.owner(url, client)
        ws.pay(
            client,
            staff.cookie,
            world.family,
            "900.00",
            [(c.id, "150.00") for c in charges],
            ws.day(tl.now),
        )
        clock.set(ws.thursday(tl, 2))  # sessions 5 and 6 are left
        staff = ws.owner(url, client)
        found = ws.preview_of(client, staff.cookie, **world.subject_query)
        assert found["totals"]["refundable_cash"] == "300.00"
        response = ws.refund(
            client, staff.cookie, ws.body(found, refund_on=ws.day(ws.thursday(tl, 2)))
        )
    assert response.status_code == 201, response.text
    withdrawal = response.json()["withdrawal"]
    assert (withdrawal["amount"], withdrawal["refund_amount"]) == ("0.00", "300.00")
    assert len(_refunds(url, withdrawal["id"])) == 1
    ws.assert_no_session_credit(url)
    cs.assert_invariants(url, ws.account_of(url, world.family))


def test_unrelated_older_credit_is_not_refunded(
    module_seeded_url: str, client: TestClient
) -> None:
    url = module_seeded_url
    tl = timeline()
    with frozen_clock(tl.now) as clock:
        world = ws.cohort_world(url, client, tl)
        [fee] = ec.fees(url, cohort_enrollment=world.enrollment_id)
        staff = ws.owner(url, client)
        ws.pay(client, staff.cookie, world.family, "500.00", None, ws.day(tl.now))
        ws.pay(
            client,
            staff.cookie,
            world.family,
            "3000.00",
            [(fee.id, "3000.00")],
            ws.day(tl.now),
        )
        clock.set(ws.thursday(tl, 2))
        staff = ws.owner(url, client)
        found = ws.preview_of(client, staff.cookie, **world.subject_query)
        assert found["totals"]["refundable_cash"] == "1800.00"
        assert found["totals"]["account_credit_after_credit"] == "2300.00"
        response = ws.refund(
            client, staff.cookie, ws.body(found, refund_on=ws.day(ws.thursday(tl, 2)))
        )
    assert response.status_code == 201, response.text
    assert response.json()["withdrawal"]["refund_amount"] == "1800.00"
    assert response.json()["account_credit_after"] == "500.00"


def test_nothing_freed_is_refused(module_seeded_url: str, client: TestClient) -> None:
    url = module_seeded_url
    tl = timeline()
    with frozen_clock(tl.now) as clock:
        world = ws.cohort_world(url, client, tl)
        clock.set(ws.thursday(tl, 2))
        staff = ws.owner(url, client)
        found = ws.preview_of(client, staff.cookie, **world.subject_query)
        response = ws.refund(
            client, staff.cookie, ws.body(found, refund_on=ws.day(ws.thursday(tl, 2)))
        )
    assert code_of(response) == (409, "withdrawal_nothing_to_refund")
    assert ws.withdrawal_rows(url, cohort_enrollment_id=world.enrollment_id) == []


def test_the_refund_date_and_method_are_checked(
    module_seeded_url: str, client: TestClient
) -> None:
    url = module_seeded_url
    tl = timeline()
    with frozen_clock(tl.now) as clock:
        world = ws.cohort_world(url, client, tl)
        [fee] = ec.fees(url, cohort_enrollment=world.enrollment_id)
        staff = ws.owner(url, client)
        paid_on = ws.day(tl.now) + timedelta(days=1)
        clock.advance(timedelta(days=1))
        staff = ws.owner(url, client)
        ws.pay(
            client,
            staff.cookie,
            world.family,
            "3000.00",
            [(fee.id, "3000.00")],
            paid_on,
        )
        clock.set(ws.thursday(tl, 2))
        staff = ws.owner(url, client)
        found = ws.preview_of(client, staff.cookie, **world.subject_query)
        today = ws.day(ws.thursday(tl, 2))
        future = ws.refund(
            client, staff.cookie, ws.body(found, refund_on=today + timedelta(days=1))
        )
        before = ws.refund(
            client, staff.cookie, ws.body(found, refund_on=paid_on - timedelta(days=1))
        )
        method = ws.refund(
            client, staff.cookie, ws.body(found, refund_on=today, method="nope")
        )
        missing = ws.refund(client, staff.cookie, ws.body(found))
    assert code_of(future) == (422, "refunded_on_in_future")
    assert code_of(before) == (422, "refunded_on_before_payment")
    assert code_of(method) == (422, "payment_method_unavailable")
    assert missing.status_code == 422
    assert ws.withdrawal_rows(url, cohort_enrollment_id=world.enrollment_id) == []


def test_a_reversed_payment_frees_nothing(
    module_seeded_url: str, client: TestClient
) -> None:
    url = module_seeded_url
    tl = timeline()
    with frozen_clock(tl.now) as clock:
        world = ws.cohort_world(url, client, tl)
        [fee] = ec.fees(url, cohort_enrollment=world.enrollment_id)
        staff = ws.owner(url, client)
        payment = ws.pay(
            client,
            staff.cookie,
            world.family,
            "3000.00",
            [(fee.id, "3000.00")],
            ws.day(tl.now),
        )
        reversed_ = cs.reverse(client, staff.cookie, world.family, payment)
        assert reversed_.status_code == 201, reversed_.text
        clock.set(ws.thursday(tl, 2))
        staff = ws.owner(url, client)
        found = ws.preview_of(client, staff.cookie, **world.subject_query)
        assert found["lines"][0]["allocated"] == "0.00"
        assert found["totals"]["refundable_cash"] == "0.00"
        response = ws.refund(
            client, staff.cookie, ws.body(found, refund_on=ws.day(ws.thursday(tl, 2)))
        )
    assert code_of(response) == (409, "withdrawal_nothing_to_refund")
