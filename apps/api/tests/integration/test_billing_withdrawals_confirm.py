"""Withdrawal confirms with the account-credit outcome (#101 F2; C2, C3,
S1, S3, S4, S6, E1, E2).

Active cohort (withdrawn and credited in one transaction; a forced failure
rolls back everything); active group (#92 J4 credits posted once by the
hook, none by #101 on ``session`` charges); settle-only of an M4-withdrawn
enrollment (``at`` = ``withdrawn_at``); overrides; a paid fee (the release
becomes account credit, no allocation) and an unpaid one (only the debt);
S6 fees and tuition periods posted now, then credited; ``withdrawal_exists``;
the M4 ``version_conflict``; stale previews; the J5 re-enrollment and the
#92 restoration boundary."""

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.modules.billing import service as billing_service
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


def _fee(url: str, world: ws.World) -> Any:
    [charge] = ec.fees(url, **{world.kind: world.enrollment_id})
    return charge


def test_an_active_cohort_is_withdrawn_and_its_fee_credited_pro_rata(
    module_seeded_url: str, client: TestClient
) -> None:
    url = module_seeded_url
    tl = timeline()
    with frozen_clock(tl.now) as clock:
        world = ws.cohort_world(url, client, tl)
        fee = _fee(url, world)
        clock.set(ws.thursday(tl, 2))  # 4 of 10 held
        staff = ws.owner(url, client)
        found = ws.preview_of(client, staff.cookie, **world.subject_query)
        [line] = found["lines"]
        assert line["charge_id"] == str(fee.id)
        assert line["basis"] == {"consumed": 4, "unused": 6, "denominator": 10}
        assert (line["policy"], line["computed_credit"]) == ("pro_rata", "1800.00")
        assert found["sessions"]["consumed"] == 4
        assert found["sessions"]["unused"] == 6
        assert found["sessions"]["unused_minutes"] == 6 * 90
        assert found["totals"]["reduces_debt"] == "1800.00"
        assert found["totals"]["refundable_cash"] == "0.00"
        assert found["blocking"] == []
        response = ws.credit(client, staff.cookie, ws.body(found))

    assert response.status_code == 201, response.text
    confirmed = response.json()
    withdrawal = confirmed["withdrawal"]
    assert withdrawal["outcome"] == "account_credit"
    assert (withdrawal["amount"], withdrawal["refund_amount"]) == ("1800.00", "0.00")
    assert withdrawal["subject"] == {
        "kind": "cohort_enrollment",
        "id": world.enrollment_id,
    }
    assert ec.net_of(url, fee.id) == "1200.00"
    [adjustment] = ws.withdrawal_adjustments(url, withdrawal["id"])
    assert adjustment.source_key == f"withdrawal:{fee.id}:{withdrawal['id']}"
    assert adjustment.reason == ws.REASON
    assert withdrawal["adjustment_ids"] == [str(adjustment.id)]
    [period] = run_sql(
        url,
        "SELECT withdrawn_at, withdrawal_reason FROM cohort_enrollments WHERE id = :id",
        {"id": world.enrollment_id},
    )
    assert period.withdrawn_at is not None
    assert period.withdrawal_reason == ws.REASON
    [row] = ws.withdrawal_rows(url, cohort_enrollment_id=world.enrollment_id)
    assert row.withdrawn_at == period.withdrawn_at
    assert row.preview["outcome"] == "account_credit"
    for record_type, record_id in (
        ("withdrawals", row.id),
        ("charge_adjustments", adjustment.id),
    ):
        [event] = events(url, record_type=record_type, record_id=record_id)
        assert event.action == "create"
        assert event.actor_user_id == staff.user.id
    ws.assert_no_session_credit(url)
    account = ws.account_of(url, world.family)
    cs.assert_invariants(url, account)


def test_a_failure_after_the_m4_call_rolls_everything_back(
    module_seeded_url: str, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = module_seeded_url
    tl = timeline()
    with frozen_clock(tl.now) as clock:
        world = ws.cohort_world(url, client, tl)
        clock.set(ws.thursday(tl, 2))
        staff = ws.owner(url, client)
        found = ws.preview_of(client, staff.cookie, **world.subject_query)

        async def _boom(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("forced")

        monkeypatch.setattr(billing_service, "post_adjustment", _boom)
        key = f"key-{uuid.uuid4().hex}"
        failed = ws.credit(client, staff.cookie, ws.body(found), key=key)

    assert failed.status_code == 500
    [period] = run_sql(
        url,
        "SELECT withdrawn_at FROM cohort_enrollments WHERE id = :id",
        {"id": world.enrollment_id},
    )
    assert period.withdrawn_at is None
    participants = run_sql(
        url,
        "SELECT p.student_id FROM class_session_participants p"
        " JOIN class_sessions s ON s.id = p.class_session_id"
        " WHERE s.cohort_id = :c AND p.student_id = :st",
        {"c": world.owner_id, "st": world.student},
    )
    assert len(participants) == 10
    assert ws.withdrawal_rows(url, cohort_enrollment_id=world.enrollment_id) == []
    assert ws.key_rows(url, key) == 0


def test_settle_only_uses_the_m4_withdrawal_instant(
    module_seeded_url: str, client: TestClient
) -> None:
    url = module_seeded_url
    tl = timeline()
    with frozen_clock(tl.now) as clock:
        world = ws.cohort_world(url, client, tl)
        fee = _fee(url, world)
        clock.set(ws.thursday(tl, 1))  # 2 held, withdrawn through M4
        staff = ws.owner(url, client)
        ec.withdraw_cohort(
            url, client, staff.cookie, world.owner_id, world.enrollment_id
        )
        clock.set(ws.thursday(tl, 3))  # settled two weeks later
        staff = ws.owner(url, client)
        found = ws.preview_of(client, staff.cookie, **world.subject_query)
        assert found["enrollment_status"] == "withdrawn"
        assert found["automatic_credits"] == []
        [line] = found["lines"]
        assert line["basis"]["unused"] == 8
        assert line["computed_credit"] == "2400.00"
        response = ws.credit(client, staff.cookie, ws.body(found))

    assert response.status_code == 201, response.text
    [period] = run_sql(
        url,
        "SELECT withdrawn_at FROM cohort_enrollments WHERE id = :id",
        {"id": world.enrollment_id},
    )
    [row] = ws.withdrawal_rows(url, cohort_enrollment_id=world.enrollment_id)
    assert row.withdrawn_at == period.withdrawn_at
    assert ec.net_of(url, fee.id) == "600.00"

    # A second settlement of the same enrollment.
    with frozen_clock(ws.thursday(tl, 3)):
        staff = ws.owner(url, client)
        found = ws.preview_of(client, staff.cookie, **world.subject_query)
        assert [b["code"] for b in found["blocking"]] == ["withdrawal_exists"]
        again = ws.credit(client, staff.cookie, ws.body(found))
    assert code_of(again)[1] == "withdrawal_exists"
    assert again.json()["details"] == {"withdrawal_id": str(row.id)}


def test_overrides_zero_net_above_and_unknown(
    module_seeded_url: str, client: TestClient
) -> None:
    url = module_seeded_url
    tl = timeline()
    with frozen_clock(tl.now) as clock:
        world = ws.cohort_world(url, client, tl)
        fee = _fee(url, world)
        clock.set(ws.thursday(tl, 2))
        staff = ws.owner(url, client)
        found = ws.preview_of(client, staff.cookie, **world.subject_query)
        above = ws.credit(
            client, staff.cookie, ws.body(found, overrides=[(fee.id, "3000.01")])
        )
        unknown = ws.credit(
            client, staff.cookie, ws.body(found, overrides=[(uuid.uuid4(), "1.00")])
        )
        negative = ws.credit(
            client, staff.cookie, ws.body(found, overrides=[(fee.id, "-1.00")])
        )
        zero = ws.credit(
            client, staff.cookie, ws.body(found, overrides=[(fee.id, "0.00")])
        )

    assert code_of(above)[1] == "charge_net_negative"
    assert code_of(unknown)[1] == "credit_override_unknown_charge"
    assert unknown.status_code == 422
    assert negative.status_code == 422
    assert zero.status_code == 201, zero.text
    withdrawal = zero.json()["withdrawal"]
    assert withdrawal["amount"] == "0.00"
    assert withdrawal["adjustment_ids"] == []
    assert ws.withdrawal_adjustments(url, withdrawal["id"]) == []
    assert ec.net_of(url, fee.id) == "3000.00"
    [row] = ws.withdrawal_rows(url, id=withdrawal["id"])
    assert row.preview["overrides"] == [{"charge_id": str(fee.id), "amount": "0.00"}]


def test_an_override_of_the_whole_net(
    module_seeded_url: str, client: TestClient
) -> None:
    url = module_seeded_url
    tl = timeline()
    with frozen_clock(tl.now) as clock:
        world = ws.cohort_world(url, client, tl)
        fee = _fee(url, world)
        clock.set(ws.thursday(tl, 2))
        staff = ws.owner(url, client)
        found = ws.preview_of(client, staff.cookie, **world.subject_query)
        response = ws.credit(
            client, staff.cookie, ws.body(found, overrides=[(fee.id, "3000.00")])
        )
    assert response.status_code == 201, response.text
    assert response.json()["withdrawal"]["amount"] == "3000.00"
    assert ec.net_of(url, fee.id) == "0.00"


def test_a_paid_fee_frees_account_credit_without_an_allocation(
    module_seeded_url: str, client: TestClient
) -> None:
    url = module_seeded_url
    tl = timeline()
    with frozen_clock(tl.now) as clock:
        world = ws.cohort_world(url, client, tl)
        fee = _fee(url, world)
        staff = ws.owner(url, client)
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
        assert found["totals"]["reduces_debt"] == "0.00"
        assert found["totals"]["account_credit_after_credit"] == "1800.00"
        response = ws.credit(client, staff.cookie, ws.body(found))

    assert response.status_code == 201, response.text
    assert response.json()["account_credit_after"] == "1800.00"
    account = ws.account_of(url, world.family)
    allocations = run_sql(
        url,
        "SELECT count(*) AS n FROM payment_allocations WHERE billing_account_id = :a",
        {"a": account},
    )
    assert allocations[0].n == 2  # the original plus the re-inserted part
    assert (
        run_sql(
            url, "SELECT * FROM refunds WHERE billing_account_id = :a", {"a": account}
        )
        == []
    )
    cs.assert_invariants(url, account)


def test_an_unpaid_fee_only_lowers_the_debt(
    module_seeded_url: str, client: TestClient
) -> None:
    url = module_seeded_url
    tl = timeline()
    with frozen_clock(tl.now) as clock:
        world = ws.cohort_world(url, client, tl)
        clock.set(ws.thursday(tl, 2))
        staff = ws.owner(url, client)
        found = ws.preview_of(client, staff.cookie, **world.subject_query)
        response = ws.credit(client, staff.cookie, ws.body(found))
        nothing = ws.refund(
            client, staff.cookie, ws.body(found, refund_on=ws.day(ws.thursday(tl, 2)))
        )
    assert response.status_code == 201, response.text
    assert response.json()["account_credit_after"] == "0.00"
    assert response.json()["account"]["outstanding"] == "1200.00"
    assert code_of(nothing)[1] in {"withdrawal_exists", "withdrawal_nothing_to_refund"}


def test_a_billable_unposted_fee_is_posted_now_then_credited(
    module_seeded_url: str, client: TestClient
) -> None:
    url = module_seeded_url
    tl = timeline()
    with frozen_clock(tl.now) as clock:
        world = ws.cohort_world(url, client, tl, fee=None)
        assert ec.fees(url, cohort_enrollment=world.enrollment_id) == []
        ec.cohort_fee(
            url, world.subject, "1000.00", cohort_id=uuid.UUID(world.owner_id)
        )
        clock.set(ws.thursday(tl, 2))
        staff = ws.owner(url, client)
        found = ws.preview_of(client, staff.cookie, **world.subject_query)
        [line] = found["lines"]
        assert (line["posting"], line["charge_id"]) == ("now", None)
        assert line["net"] == "1000.00"
        assert line["computed_credit"] == "600.00"
        assert [w["code"] for w in found["warnings"]] == ["fee_posted_now"]
        response = ws.credit(client, staff.cookie, ws.body(found))

    assert response.status_code == 201, response.text
    [fee] = ec.fees(url, cohort_enrollment=world.enrollment_id)
    assert ec.net_of(url, fee.id) == "400.00"
    [event] = events(url, record_type="charges", record_id=fee.id)
    assert event.actor_user_id is not None


def test_a_price_added_after_a_settlement_charges_nothing(
    module_seeded_url: str, client: TestClient
) -> None:
    url = module_seeded_url
    tl = timeline()
    with frozen_clock(tl.now) as clock:
        world = ws.cohort_world(url, client, tl, fee=None)
        clock.set(ws.thursday(tl, 2))
        staff = ws.owner(url, client)
        found = ws.preview_of(client, staff.cookie, **world.subject_query)
        assert found["lines"] == []
        response = ws.credit(client, staff.cookie, ws.body(found))
        assert response.status_code == 201, response.text
        assert response.json()["withdrawal"]["amount"] == "0.00"
        ec.cohort_fee(
            url, world.subject, "1000.00", cohort_id=uuid.UUID(world.owner_id)
        )
        run = ec.run_enrollments(url)
    assert ec.fees(url, cohort_enrollment=world.enrollment_id) == []
    assert run.withdrawal_settled >= 1


def test_a_stale_preview_is_refused_with_a_fresh_one(
    module_seeded_url: str, client: TestClient
) -> None:
    url = module_seeded_url
    tl = timeline()
    with frozen_clock(tl.now) as clock:
        world = ws.cohort_world(url, client, tl)
        clock.set(ws.thursday(tl, 2))
        staff = ws.owner(url, client)
        found = ws.preview_of(client, staff.cookie, **world.subject_query)
        sessions = ec.sessions_of(url, cohort_id=world.owner_id)
        ec.cancel_session(url, sessions[-1].id)  # the center cancels one
        key = f"key-{uuid.uuid4().hex}"
        stale = ws.credit(client, staff.cookie, ws.body(found), key=key)

    assert code_of(stale)[1] == "withdrawal_preview_stale"
    fresh = stale.json()["details"]["preview"]
    assert fresh["fingerprint"] != found["fingerprint"]
    assert fresh["lines"][0]["basis"] == {"consumed": 4, "unused": 5, "denominator": 9}
    assert fresh["lines"][0]["computed_credit"] == "1666.67"
    assert ws.withdrawal_rows(url, cohort_enrollment_id=world.enrollment_id) == []
    assert ws.key_rows(url, key) == 0
    [period] = run_sql(
        url,
        "SELECT withdrawn_at FROM cohort_enrollments WHERE id = :id",
        {"id": world.enrollment_id},
    )
    assert period.withdrawn_at is None


def test_a_payment_allocated_since_the_preview_is_stale(
    module_seeded_url: str, client: TestClient
) -> None:
    url = module_seeded_url
    tl = timeline()
    with frozen_clock(tl.now) as clock:
        world = ws.cohort_world(url, client, tl)
        fee = _fee(url, world)
        clock.set(ws.thursday(tl, 2))
        staff = ws.owner(url, client)
        found = ws.preview_of(client, staff.cookie, **world.subject_query)
        ws.pay(
            client,
            staff.cookie,
            world.family,
            "500.00",
            [(fee.id, "500.00")],
            ws.day(ws.thursday(tl, 2)),
        )
        stale = ws.credit(client, staff.cookie, ws.body(found))
    assert code_of(stale)[1] == "withdrawal_preview_stale"
    assert stale.json()["details"]["preview"]["lines"][0]["allocated"] == "500.00"


def test_a_version_conflict_passes_through(
    module_seeded_url: str, client: TestClient
) -> None:
    url = module_seeded_url
    tl = timeline()
    with frozen_clock(tl.now) as clock:
        world = ws.cohort_world(url, client, tl)
        clock.set(ws.thursday(tl, 2))
        staff = ws.owner(url, client)
        found = ws.preview_of(client, staff.cookie, **world.subject_query)
        response = ws.credit(client, staff.cookie, ws.body(found, version=99))
    assert code_of(response)[1] == "version_conflict"


def test_an_active_group_gets_its_j4_credits_once_and_no_session_withdrawal(
    module_seeded_url: str, client: TestClient
) -> None:
    url = module_seeded_url
    tl = timeline()
    with frozen_clock(tl.now) as clock:
        world = ws.group_world(url, client, tl, fee=None, session_price="200.00")
        sessions = ec.sessions_of(url, group_id=world.owner_id)
        ec.confirm_sessions(url, [s.id for s in sessions[:6]])
        ec.run_sessions(url)
        charges = ec.charges_of(url, world.student, "session")
        assert len(charges) == 6
        clock.set(ws.thursday(tl, 2))
        staff = ws.owner(url, client)
        found = ws.preview_of(client, staff.cookie, **world.subject_query)
        assert found["lines"] == []
        planned = {c["charge_id"] for c in found["automatic_credits"]}
        assert len(planned) == 2  # the confirmed sessions after the instant
        assert found["totals"]["automatic_credits"] == "400.00"
        response = ws.credit(client, staff.cookie, ws.body(found))

    assert response.status_code == 201, response.text
    withdrawal = response.json()["withdrawal"]
    removals = run_sql(
        url,
        "SELECT charge_id, amount FROM charge_adjustments"
        " WHERE kind = 'enrollment_removal' AND student_id = :s",
        {"s": world.student},
    )
    assert {str(r.charge_id) for r in removals} == planned
    assert len(withdrawal["automatic_credit_adjustment_ids"]) == 2
    assert withdrawal["amount"] == "0.00"
    ws.assert_no_session_credit(url)


def test_a_re_enrollment_after_a_full_credit_charges_again(
    module_seeded_url: str, client: TestClient
) -> None:
    url = module_seeded_url
    tl = timeline()
    with frozen_clock(tl.now) as clock:
        world = ws.group_world(url, client, tl)
        fee = _fee(url, world)
        staff = ws.owner(url, client)
        found = ws.preview_of(client, staff.cookie, **world.subject_query)
        assert found["lines"][0]["computed_credit"] == "2000.00"  # nothing held
        response = ws.credit(client, staff.cookie, ws.body(found))
        assert response.status_code == 201, response.text
        assert ec.net_of(url, fee.id) == "0.00"
        clock.set(ws.thursday(tl, 1))
        cookie = ws.owner(url, client).cookie
        again = ec.enroll_group(client, cookie, world.owner_id, world.student)
    [new_fee] = ec.fees(url, group_enrollment=again["id"])
    assert ec.net_of(url, new_fee.id) != "0.00"
    # The #92 restoration never touches the withdrawal adjustment.
    assert len(ws.withdrawal_adjustments(url, response.json()["withdrawal"]["id"])) == 1
    assert ec.net_of(url, fee.id) == "0.00"
