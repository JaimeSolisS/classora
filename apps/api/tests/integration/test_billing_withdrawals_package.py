"""Package withdrawals (#101 F4; S2, S7, D4, A4, E1, E2).

An unused and a partly used package (sessions and minutes): one ``refund``
units row of ``−remaining``, status ``withdrawn``, coverage gone and the
next session sweep charging a future covered session (warned in the
preview); expired, cancelled, exhausted and withdrawn packages refused; a
pending closeout refused; #100's cancellation after a withdrawal refused; a
second ``refund`` row refused by the database; a refund of a paid package.

The clock is frozen at noon on 2031-03-10 (center zone) for the module."""

import uuid
from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.money import Money
from app.modules.billing import service as billing
from app.modules.identity.permissions import FINANCE_STAFF, OWNER
from tests.conftest import FrozenClock, frozen_clock
from tests.integration import corrections_support as cs
from tests.integration import package_support as pks
from tests.integration import payment_support as ps
from tests.integration import withdrawal_support as ws
from tests.integration.auth_support import auth_client
from tests.integration.billing_ledger_support import fails_with
from tests.integration.catalog_support import get
from tests.integration.conftest import run_sql
from tests.integration.lifecycle_support import code_of, events, in_uow

TODAY = ps.TODAY
SOLD = date(2031, 3, 1)


@pytest.fixture(scope="module")
def frozen() -> Iterator[FrozenClock]:
    with frozen_clock(ps.NOW) as clock:
        yield clock


@pytest.fixture(scope="module")
def client(module_seeded_url: str, frozen: FrozenClock) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def cashier(module_seeded_url: str, client: TestClient) -> cs.Staff:
    return cs.staff(module_seeded_url, client, (FINANCE_STAFF,))


@pytest.fixture(scope="module")
def owner(module_seeded_url: str, client: TestClient) -> cs.Staff:
    return cs.staff(module_seeded_url, client, (OWNER,))


@pytest.fixture(autouse=True)
def _reset(module_seeded_url: str) -> None:
    pks.reset(module_seeded_url)


def sold(
    url: str,
    *,
    quantity: int = 10,
    amount: str = "1000.00",
    validity_days: int = 90,
    unit: str = "sessions",
) -> tuple[pks.World, uuid.UUID, Any]:
    w = pks.world(url)
    product = pks.product(
        url, quantity=quantity, amount=amount, validity_days=validity_days, unit=unit
    )
    package = pks.sell(url, w.student, product, SOLD)
    [purchase] = run_sql(
        url, "SELECT * FROM charges WHERE package_id = :p", {"p": package}
    )
    return w, package, purchase


def consume_n(url: str, w: pks.World, n: int, minutes: int = 60) -> None:
    for day in range(n):
        session = pks.session_for(url, w, date(2031, 3, 2 + day), minutes=minutes)
        assert pks.consume(url, session, w.student).outcome == "consumed"


def withdraw(
    client: TestClient, staff: cs.Staff, package: uuid.UUID, **extra: Any
) -> Any:
    found = ws.preview_of(client, staff.cookie, package_id=package)
    return found, ws.credit(client, staff.cookie, ws.body(found, **extra))


def test_an_unused_package_is_withdrawn_as_account_credit(
    module_seeded_url: str, client: TestClient, cashier: cs.Staff
) -> None:
    url = module_seeded_url
    w, package, purchase = sold(url)
    account = purchase.billing_account_id
    before = cs.snapshot(url, account)

    found, response = withdraw(client, cashier, package)

    assert found["units"] == {
        "unit": "sessions",
        "granted": 10,
        "consumed": 0,
        "remaining": 10,
    }
    assert found["sessions"] is None and found["enrollment_status"] is None
    [line] = found["lines"]
    assert (line["kind"], line["computed_credit"]) == ("package_purchase", "1000.00")
    body = cs.created(response)
    withdrawal = body["withdrawal"]
    assert withdrawal["subject"] == {"kind": "package", "id": str(package)}
    assert withdrawal["amount"] == "1000.00"
    [refund_row] = [t for t in pks.transactions(url, package) if t.kind == "refund"]
    assert (refund_row.units, str(refund_row.withdrawal_id)) == (-10, withdrawal["id"])
    assert str(refund_row.id) == body["package_refund_transaction_id"]
    [made] = events(
        url, record_type="package_credit_transactions", record_id=refund_row.id
    )
    assert (made.action, made.actor_user_id) == ("create", cashier.user.id)
    assert pks.remaining(url, package) == 0
    detail = get(client, cashier.cookie, f"{cs.PACKAGES}/{package}").json()
    assert detail["status"] == "withdrawn"
    listed = get(
        client,
        cashier.cookie,
        cs.PACKAGES,
        student_id=str(w.student),
        status="withdrawn",
    ).json()
    assert [p["id"] for p in listed["items"]] == [str(package)]
    [adjustment] = ws.withdrawal_adjustments(url, withdrawal["id"])
    assert (adjustment.charge_kind, Money(adjustment.amount)) == (
        "package_purchase",
        Money("-1000.00"),
    )
    cs.unchanged(before, cs.snapshot(url, account))
    cs.assert_invariants(url, account)


def test_a_partly_used_sessions_package_is_credited_pro_rata(
    module_seeded_url: str, client: TestClient, cashier: cs.Staff
) -> None:
    url = module_seeded_url
    w, package, _ = sold(url, amount="2500.00")
    consume_n(url, w, 4)

    found, response = withdraw(client, cashier, package)

    assert found["lines"][0]["basis"] == {"consumed": 4, "unused": 6, "denominator": 10}
    assert found["lines"][0]["computed_credit"] == "1500.00"
    assert cs.created(response)["withdrawal"]["amount"] == "1500.00"
    [refund_row] = [t for t in pks.transactions(url, package) if t.kind == "refund"]
    assert refund_row.units == -6


def test_a_minutes_package_reports_and_credits_minutes(
    module_seeded_url: str, client: TestClient, cashier: cs.Staff
) -> None:
    url = module_seeded_url
    w, package, _ = sold(url, quantity=600, unit="minutes")
    consume_n(url, w, 3, minutes=110)  # 330 used, 270 left

    found, response = withdraw(client, cashier, package)

    assert found["units"] == {
        "unit": "minutes",
        "granted": 600,
        "consumed": 330,
        "remaining": 270,
    }
    assert found["lines"][0]["computed_credit"] == "450.00"
    assert cs.created(response)["withdrawal"]["amount"] == "450.00"


def test_a_future_covered_session_is_warned_then_charged(
    module_seeded_url: str, client: TestClient, cashier: cs.Staff
) -> None:
    url = module_seeded_url
    w, package, _ = sold(url)
    future = pks.session_for(url, w, date(2031, 3, 12))
    pks.set_floor(url, date(2031, 1, 1))

    found, response = withdraw(client, cashier, package)

    [warning] = found["warnings"]
    assert warning["code"] == "future_covered_sessions"
    assert warning["class_session_ids"] == [str(future)]
    cs.created(response)

    async def _covered(s: Any) -> set[tuple[uuid.UUID, uuid.UUID]]:
        return await billing.package_covered_keys(
            s,
            [
                billing.CoverageQuery(
                    class_session_id=future,
                    student_id=w.student,
                    subject_id=w.subject,
                    service_date=date(2031, 3, 12),
                    scheduled_minutes=60,
                )
            ],
        )

    assert in_uow(url, _covered) == set()
    assert pks.sweep(url).posted == 1
    assert len(pks.session_charges(url, future)) == 1
    pks.set_floor(url, None)


def test_a_pending_closeout_is_refused(
    module_seeded_url: str, client: TestClient, cashier: cs.Staff
) -> None:
    url = module_seeded_url
    w, package, _ = sold(url)
    held = pks.session_for(url, w, date(2031, 3, 9))  # started, not approved

    found, response = withdraw(client, cashier, package)

    assert [b["code"] for b in found["blocking"]] == ["withdrawal_closeout_pending"]
    assert code_of(response) == (409, "withdrawal_closeout_pending")
    assert response.json()["details"] == {"class_session_ids": [str(held)]}
    assert pks.remaining(url, package) == 10
    pks.approved(url, held)  # consumed at approval: now it can be withdrawn
    found, response = withdraw(client, cashier, package)
    assert found["blocking"] == []
    assert cs.created(response)["withdrawal"]["amount"] == "900.00"


def test_closed_packages_are_not_withdrawable(
    module_seeded_url: str, client: TestClient, cashier: cs.Staff
) -> None:
    url = module_seeded_url
    w, exhausted, _ = sold(url, quantity=1)
    consume_n(url, w, 1)
    _, expired, _ = sold(url, validity_days=5)  # expires 2031-03-06 < today
    _, cancelled, _ = sold(url)
    cs.created(cs.cancel(client, cashier.cookie, cancelled))

    for package, reason in (
        (exhausted, "exhausted"),
        (expired, "expired"),
        (cancelled, "cancelled"),
    ):
        found, response = withdraw(client, cashier, package)
        assert found["blocking"] == [
            {"code": "package_not_withdrawable", "details": {"reason": reason}}
        ]
        assert code_of(response) == (409, "package_not_withdrawable"), package
        assert response.json()["details"] == {"reason": reason}


def test_a_withdrawn_package_is_not_withdrawn_or_cancelled_again(
    module_seeded_url: str, client: TestClient, cashier: cs.Staff
) -> None:
    url = module_seeded_url
    _, package, _ = sold(url)
    _, first = withdraw(client, cashier, package)
    withdrawal_id = cs.created(first)["withdrawal"]["id"]

    found, again = withdraw(client, cashier, package)
    cancelled = cs.cancel(client, cashier.cookie, package)

    assert [b["code"] for b in found["blocking"]] == ["withdrawal_exists"]
    assert code_of(again) == (409, "withdrawal_exists")
    assert code_of(cancelled) == (409, "package_not_cancellable")
    assert cancelled.json()["details"] == {"reason": "withdrawn"}
    fails_with(
        url,
        "INSERT INTO package_credit_transactions (package_id, kind, units,"
        " withdrawal_id) VALUES (:p, 'refund', -1, :w)",
        "23505",
        p=package,
        w=withdrawal_id,
    )


def test_a_paid_package_is_refunded(
    module_seeded_url: str, client: TestClient, owner: cs.Staff
) -> None:
    url = module_seeded_url
    w, package, purchase = sold(url)
    account = purchase.billing_account_id
    payment = cs.recorded_payment(
        url, owner.user.id, account, "1000.00", [(purchase.id, "1000.00")]
    )
    consume_n(url, w, 5)

    found = ws.preview_of(client, owner.cookie, package_id=package)
    assert found["totals"]["refundable_cash"] == "500.00"
    response = ws.refund(client, owner.cookie, ws.body(found, refund_on=TODAY))

    withdrawal = cs.created(response)["withdrawal"]
    assert withdrawal["refund_amount"] == "500.00"
    [refund] = run_sql(
        url, "SELECT * FROM refunds WHERE withdrawal_id = :w", {"w": withdrawal["id"]}
    )
    assert (refund.payment_id, f"{refund.amount:.2f}") == (payment, "500.00")
    detail = cs.payment_get(client, owner.cookie, w.family, payment).json()
    assert [r["withdrawal_id"] for r in detail["refunds"]] == [withdrawal["id"]]
    cs.assert_invariants(url, account)
