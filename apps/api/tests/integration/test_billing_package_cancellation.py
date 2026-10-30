"""Package-purchase cancellations through the real app (#100 F5; C5, B5,
B7, R6, E1-E3).

Unused (credit = the full net); partly consumed (pro rata,
``Money.prorate``); an override of ``0.00`` (no adjustment) and of the net;
an override above the net ``409``; exhausted / expired / cancelled ``409
package_not_cancellable``; the ``cancelled`` status (and the list filter); a
covered, unconsumed session no longer covered and charged by the next
sweep; a credit on a paid purchase releasing its allocation; the audit
events and the unchanged original rows.

The clock is frozen at noon on 2031-03-10 (center zone) for the module."""

import json
import uuid
from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.money import Money
from app.modules.billing import service as billing
from app.modules.identity.permissions import FINANCE_STAFF
from tests.conftest import FrozenClock, frozen_clock
from tests.integration import corrections_support as cs
from tests.integration import package_support as pks
from tests.integration import payment_support as ps
from tests.integration.auth_support import auth_client
from tests.integration.catalog_support import get
from tests.integration.conftest import run_sql
from tests.integration.lifecycle_support import code_of, events, in_uow

TODAY = ps.TODAY
SOLD = date(2031, 3, 1)
FLOOR = date(2031, 1, 1)


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


@pytest.fixture(autouse=True)
def _reset(module_seeded_url: str) -> None:
    pks.reset(module_seeded_url)


def sold(
    url: str, *, quantity: int = 10, amount: str = "1000.00", validity_days: int = 90
) -> tuple[pks.World, uuid.UUID, Any]:
    w = pks.world(url)
    product = pks.product(
        url, quantity=quantity, amount=amount, validity_days=validity_days
    )
    package = pks.sell(url, w.student, product, SOLD)
    [purchase] = run_sql(
        url, "SELECT * FROM charges WHERE package_id = :p", {"p": package}
    )
    return w, package, purchase


def adjustments_of(url: str, charge: object) -> list[Any]:
    return run_sql(
        url,
        "SELECT * FROM charge_adjustments WHERE charge_id = :c ORDER BY posted_at, id",
        {"c": charge},
    )


def consume_n(url: str, w: pks.World, n: int) -> None:
    for day in range(n):
        session = pks.session_for(url, w, date(2031, 3, 2 + day))
        assert pks.consume(url, session, w.student).outcome == "consumed"


def test_an_unused_package_is_credited_in_full(module_seeded_url, client, cashier):
    url = module_seeded_url
    w, package, purchase = sold(url)
    account = purchase.billing_account_id
    before = cs.snapshot(url, account)

    body = cs.created(cs.cancel(client, cashier.cookie, package))

    assert (body["units_reversed"], body["credit"]) == (10, "1000.00")
    assert body["package"]["status"] == "cancelled"
    assert body["package"]["remaining_units"] == 0
    assert body["account_credit_after"] == "0.00"
    transactions = pks.transactions(url, package)
    grant = next(t for t in transactions if t.kind == "grant")
    [reversal] = [t for t in transactions if t.kind == "reversal"]
    assert (reversal.units, reversal.reverses_transaction_id) == (-10, grant.id)
    assert [t["kind"] for t in body["package"]["transactions"]] == ["grant", "reversal"]
    [adjustment] = adjustments_of(url, purchase.id)
    assert str(adjustment.id) == body["adjustment_id"]
    assert (adjustment.kind, Money(adjustment.amount), adjustment.reason) == (
        "package_cancellation",
        Money("-1000.00"),
        "La familia se mudó",
    )
    assert adjustment.source_key == f"package_cancellation:{package}"
    assert adjustment.posted_by_user_id == cashier.user.id
    [event] = events(url, record_type="charge_adjustments", record_id=adjustment.id)
    assert (event.action, event.actor_user_id) == ("create", cashier.user.id)
    assert "La familia se mudó" in json.dumps(event.changes, ensure_ascii=False)
    [made] = events(
        url, record_type="package_credit_transactions", record_id=reversal.id
    )
    assert (made.action, made.actor_user_id) == ("create", cashier.user.id)
    cs.unchanged(before, cs.snapshot(url, account))
    cs.assert_invariants(url, account)
    charge = cs.charge_get(client, cashier.cookie, w.family, purchase.id).json()
    assert (charge["net"], charge["status"]) == ("0.00", "void")


def test_a_partly_used_package_is_credited_pro_rata(module_seeded_url, client, cashier):
    url = module_seeded_url
    w, package, purchase = sold(url, amount="999.99")
    consume_n(url, w, 4)

    body = cs.created(cs.cancel(client, cashier.cookie, package))

    # 999.99 × 6 / 10 = 599.994 → 599.99
    assert (body["units_reversed"], body["credit"]) == (6, "599.99")
    assert pks.remaining(url, package) == 0
    [adjustment] = adjustments_of(url, purchase.id)
    assert Money(adjustment.amount) == Money("-599.99")
    cs.assert_invariants(url, purchase.billing_account_id)


def test_an_override_of_zero_writes_no_adjustment(module_seeded_url, client, cashier):
    url = module_seeded_url
    _, package, purchase = sold(url)

    body = cs.created(
        cs.cancel(
            client,
            cashier.cookie,
            package,
            {"reason": "Cortesía", "credit_amount": "0.00"},
        )
    )

    assert (body["credit"], body["adjustment_id"], body["units_reversed"]) == (
        "0.00",
        None,
        10,
    )
    assert adjustments_of(url, purchase.id) == []
    assert body["package"]["status"] == "cancelled"


def test_an_override_of_the_net_and_above_it(module_seeded_url, client, cashier):
    url = module_seeded_url
    w, package, purchase = sold(url)
    consume_n(url, w, 9)
    _, other, other_purchase = sold(url)

    above = cs.cancel(
        client, cashier.cookie, package, {"reason": "x", "credit_amount": "1000.01"}
    )
    exact = cs.cancel(
        client, cashier.cookie, other, {"reason": "x", "credit_amount": "1000.00"}
    )

    assert code_of(above) == (409, "charge_net_negative")
    assert above.json()["details"] == {
        "charge_id": str(purchase.id),
        "net": "1000.00",
        "amount": "-1000.01",
    }
    assert pks.remaining(url, package) == 1
    assert [t.kind for t in pks.transactions(url, package)].count("reversal") == 0
    assert exact.json()["credit"] == "1000.00"
    [adjustment] = adjustments_of(url, other_purchase.id)
    assert Money(adjustment.amount) == Money("-1000.00")


def test_an_exhausted_an_expired_and_a_cancelled_package_are_refused(
    module_seeded_url, client, cashier
):
    url = module_seeded_url
    w, exhausted, _ = sold(url, quantity=1)
    consume_n(url, w, 1)
    _, expired, _ = sold(url, validity_days=5)  # expires 2031-03-06 < today
    _, expire_row, _ = sold(url)
    run_sql(
        url,
        "INSERT INTO package_credit_transactions (package_id, kind, units)"
        " VALUES (:p, 'expire', -10)",
        {"p": expire_row},
    )
    _, cancelled, _ = sold(url)
    cs.created(cs.cancel(client, cashier.cookie, cancelled))

    for package, reason in (
        (exhausted, "exhausted"),
        (expired, "expired"),
        (expire_row, "expired"),
        (cancelled, "cancelled"),
    ):
        response = cs.cancel(client, cashier.cookie, package)
        assert code_of(response) == (409, "package_not_cancellable"), package
        assert response.json()["details"] == {"reason": reason}
    assert code_of(cs.cancel(client, cashier.cookie, uuid.uuid4())) == (
        404,
        "not_found",
    )


def test_the_status_filter_and_the_reads(module_seeded_url, client, cashier):
    url = module_seeded_url
    w, package, _ = sold(url)
    cs.created(cs.cancel(client, cashier.cookie, package))

    page = get(
        client,
        cashier.cookie,
        cs.PACKAGES,
        student_id=str(w.student),
        status="cancelled",
    ).json()
    active = get(
        client, cashier.cookie, cs.PACKAGES, student_id=str(w.student), status="active"
    ).json()

    assert [p["id"] for p in page["items"]] == [str(package)]
    assert page["items"][0]["status"] == "cancelled"
    assert active["items"] == []


def test_a_covered_session_is_charged_by_the_next_sweep(
    module_seeded_url, client, cashier
):
    url = module_seeded_url
    w, package, _ = sold(url)
    session = pks.session_for(url, w, TODAY)
    pks.set_floor(url, FLOOR)
    first = pks.sweep(url)
    assert (first.package_covered, first.posted) == (1, 0)

    cs.created(cs.cancel(client, cashier.cookie, package))

    async def _covered(s: Any) -> set[tuple[uuid.UUID, uuid.UUID]]:
        return await billing.package_covered_keys(
            s,
            [
                billing.CoverageQuery(
                    class_session_id=session,
                    student_id=w.student,
                    subject_id=w.subject,
                    service_date=TODAY,
                    scheduled_minutes=60,
                    class_type="individual",
                    closeout_approved=False,
                )
            ],
        )

    assert in_uow(url, _covered) == set()
    later = pks.sweep(url)
    assert later.posted == 1
    assert len(pks.session_charges(url, session)) == 1
    pks.set_floor(url, None)


def test_a_credit_on_a_paid_purchase_releases_its_allocation(
    module_seeded_url, client, cashier
):
    url = module_seeded_url
    w, package, purchase = sold(url)
    account = purchase.billing_account_id
    payment = cs.recorded_payment(
        url, cashier.user.id, account, "1000.00", [(purchase.id, "1000.00")]
    )
    consume_n(url, w, 5)

    body = cs.created(cs.cancel(client, cashier.cookie, package))

    assert body["credit"] == "500.00"
    assert body["account_credit_after"] == "500.00"
    valid = [a for a in ps.allocations_of(url, account) if a.valid]
    assert [(a.payment_id, str(a.amount)) for a in valid] == [(payment, "500.00")]
    charge = cs.charge_get(client, cashier.cookie, w.family, purchase.id).json()
    assert (charge["net"], charge["open"], charge["status"]) == (
        "500.00",
        "0.00",
        "paid",
    )
    cs.assert_invariants(url, account)
