"""Manual adjustments through the real app (#100 F2; C2, B2, R3, R6, E1-E3).

A debit; a partial credit; a full credit to ``void``; a credit below the
net (``409 charge_net_negative``, nothing written); a credit on a paid
charge releasing the newest allocation (E5), the money becoming account
credit; ``package_purchase`` refused; another family's charge ``404``; a
zero amount ``422``; the audit events and the unchanged original rows; a
later #99 cancellation crediting only the remaining net.

The clock is frozen at noon on 2031-03-10 (center zone) for the module."""

import json
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.money import Money
from app.modules.identity.permissions import FINANCE_STAFF
from tests.conftest import FrozenClock, frozen_clock
from tests.integration import corrections_support as cs
from tests.integration import package_support as pks
from tests.integration import payment_support as ps
from tests.integration import schedule_reconciliation_support as sr
from tests.integration.auth_support import auth_client
from tests.integration.conftest import run_sql
from tests.integration.lifecycle_support import code_of, events

TODAY = ps.TODAY


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


def adjustments_of(url: str, charge: object) -> list[object]:
    return run_sql(
        url,
        "SELECT * FROM charge_adjustments WHERE charge_id = :c ORDER BY posted_at, id",
        {"c": charge},
    )


def test_a_debit_raises_what_is_owed(module_seeded_url, client, cashier):
    url = module_seeded_url
    fam, account, [student] = cs.new_account(url)
    [charge] = ps.charges(url, (student, "200.00", TODAY))

    body = cs.created(cs.adjust(client, cashier.cookie, fam, charge, "35.50"))

    adjustment = body["adjustment"]
    assert (adjustment["kind"], adjustment["amount"], adjustment["reason"]) == (
        "manual",
        "35.50",
        "Error de captura",
    )
    assert adjustment["posted_by_user_id"] == str(cashier.user.id)
    assert (body["charge"]["net"], body["charge"]["open"]) == ("235.50", "235.50")
    assert body["charge"]["adjustments_total"] == "35.50"
    assert body["released_allocation_ids"] == []
    assert body["account_credit_after"] == "0.00"
    [row] = adjustments_of(url, charge)
    [event] = events(url, record_type="charge_adjustments", record_id=row.id)
    assert (event.action, event.actor_user_id) == ("create", cashier.user.id)
    assert "Error de captura" in json.dumps(event.changes, ensure_ascii=False)
    cs.assert_invariants(url, account)


def test_a_partial_credit_then_a_full_credit_voids_the_charge(
    module_seeded_url, client, cashier
):
    url = module_seeded_url
    fam, account, [student] = cs.new_account(url)
    [charge] = ps.charges(url, (student, "200.00", TODAY))

    partial = cs.created(cs.adjust(client, cashier.cookie, fam, charge, "-50.00"))
    full = cs.created(cs.adjust(client, cashier.cookie, fam, charge, "-150.00"))

    assert (partial["charge"]["net"], partial["charge"]["status"]) == (
        "150.00",
        "pending",
    )
    assert (full["charge"]["net"], full["charge"]["status"]) == ("0.00", "void")
    cs.assert_invariants(url, account)


def test_a_credit_below_zero_is_refused_and_writes_nothing(
    module_seeded_url, client, cashier
):
    url = module_seeded_url
    fam, account, [student] = cs.new_account(url)
    [charge] = ps.charges(url, (student, "200.00", TODAY))
    before = cs.snapshot(url, account)

    response = cs.adjust(client, cashier.cookie, fam, charge, "-200.01")

    assert code_of(response) == (409, "charge_net_negative")
    assert response.json()["details"] == {
        "charge_id": str(charge),
        "net": "200.00",
        "amount": "-200.01",
    }
    assert adjustments_of(url, charge) == []
    assert cs.snapshot(url, account) == before


def test_a_credit_on_a_paid_charge_releases_the_newest_allocation(
    module_seeded_url, client, cashier
):
    url = module_seeded_url
    fam, account, [student] = cs.new_account(url)
    [charge] = ps.charges(url, (student, "200.00", TODAY))
    first = cs.recorded_payment(
        url, cashier.user.id, account, "120.00", [(charge, "120.00")]
    )
    second = cs.recorded_payment(
        url, cashier.user.id, account, "80.00", [(charge, "80.00")]
    )
    before = cs.snapshot(url, account)
    allocations = ps.allocations_of(url, account)
    newest = [a for a in allocations if a.payment_id == second]

    body = cs.created(cs.adjust(client, cashier.cookie, fam, charge, "-50.00"))

    assert body["released_allocation_ids"] == [str(newest[0].id)]
    assert (
        body["charge"]["net"],
        body["charge"]["open"],
        body["charge"]["status"],
    ) == (
        "150.00",
        "0.00",
        "paid",
    )
    # 80.00 released, 30.00 re-inserted: 50.00 of the second payment is credit.
    assert body["account_credit_after"] == "50.00"
    assert ps.account_credit(url, account) == Money("50.00")
    valid = [a for a in ps.allocations_of(url, account) if a.valid]
    assert sorted((str(a.payment_id), str(a.amount)) for a in valid) == sorted(
        [(str(first), "120.00"), (str(second), "30.00")]
    )
    cs.unchanged(before, cs.snapshot(url, account))
    cs.assert_invariants(url, account)


def test_a_package_purchase_is_refused(module_seeded_url, client, cashier):
    url = module_seeded_url
    pks.reset(url)
    w = pks.world(url)
    package = pks.sell(url, w.student, pks.product(url), TODAY)
    [purchase] = run_sql(
        url, "SELECT id FROM charges WHERE package_id = :p", {"p": package}
    )

    response = cs.adjust(client, cashier.cookie, w.family, purchase.id, "-10.00")

    assert code_of(response) == (409, "package_purchase_not_adjustable")
    assert adjustments_of(url, purchase.id) == []


def test_another_familys_charge_or_an_unknown_one_is_404(
    module_seeded_url, client, cashier
):
    url = module_seeded_url
    fam, _, _ = cs.new_account(url)
    _, _, [other_student] = cs.new_account(url)
    [other] = ps.charges(url, (other_student, "200.00", TODAY))

    for family, charge in ((fam, other), (fam, uuid.uuid4()), (uuid.uuid4(), other)):
        response = cs.adjust(client, cashier.cookie, family, charge, "-10.00")
        assert code_of(response) == (404, "not_found")
    assert adjustments_of(url, other) == []


@pytest.mark.parametrize(
    ("amount", "reason", "path", "code"),
    [
        ("0.00", "x", "amount", "amount_zero"),
        ("1.001", "x", "amount", "money_precision"),
        ("5.00", " ", "reason", "string_too_short"),
        ("5.00", "x" * 501, "reason", "string_too_long"),
    ],
)
def test_every_body_rule(
    module_seeded_url, client, cashier, amount, reason, path, code
):
    url = module_seeded_url
    fam, _, [student] = cs.new_account(url)
    [charge] = ps.charges(url, (student, "200.00", TODAY))

    response = cs.adjust(client, cashier.cookie, fam, charge, amount, reason)

    assert code_of(response) == (422, "validation_error")
    assert [(e["path"], e["code"]) for e in response.json()["errors"]] == [
        (["body", path], code)
    ]
    assert adjustments_of(url, charge) == []


def test_a_later_cancellation_credits_only_the_remaining_net(
    module_seeded_url, client, cashier
):
    url = module_seeded_url
    sr.reset(url)
    c = sr.charged(url)  # 250.00
    assert c.item.family is not None

    cs.created(cs.adjust(client, cashier.cookie, c.item.family, c.charge, "-100.00"))
    sr.cancel(url, c.session)

    assert sr.one(url, c.session, c.student) == ["cancellation_credited"]
    [credit] = sr.adjustments(url, c.charge, "cancellation_credit")
    assert Money(credit.amount) == Money("-150.00")
    assert sr.net(url, c.charge) == Money.zero()
