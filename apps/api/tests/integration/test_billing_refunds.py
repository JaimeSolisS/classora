"""Refunds through the real app (#100 F4; C4, B4, R4, R5, R9, E1-E3).

A partial refund then a second one up to the remainder;
``refund_exceeds_remainder`` (the remainder after allocations and earlier
refunds); a reversed payment ``409``; every C4 validation; the account
credit drops and the outstanding amount doesn't; the receipt row unchanged;
the reference and the method stored; the audit events; the ledger row.

The clock is frozen at noon on 2031-03-10 (center zone) for the module."""

import json
import uuid
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.money import Money
from app.modules.identity.permissions import OWNER
from tests.conftest import FrozenClock, frozen_clock
from tests.integration import corrections_support as cs
from tests.integration import payment_support as ps
from tests.integration import receipt_support as rs
from tests.integration.auth_support import auth_client
from tests.integration.catalog_support import get
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
def owner(module_seeded_url: str, client: TestClient) -> cs.Staff:
    return cs.staff(module_seeded_url, client, (OWNER,))


def refunds_of(url: str, payment: object) -> list[Any]:
    return run_sql(
        url,
        "SELECT * FROM refunds WHERE payment_id = :p ORDER BY created_at, id",
        {"p": payment},
    )


def test_two_partial_refunds_up_to_the_remainder(module_seeded_url, client, owner):
    url = module_seeded_url
    fam, account, [student] = cs.new_account(url)
    [charge] = ps.charges(url, (student, "60.00", TODAY))
    payment = cs.recorded_payment(
        url, owner.user.id, account, "100.00", [(charge, "60.00")]
    )
    receipt = rs.receipt_of_payment(url, payment)
    before = cs.snapshot(url, account)

    first = cs.created(
        cs.refund(client, owner.cookie, fam, payment, cs.refund_body("15.00"))
    )
    summary = get(client, owner.cookie, ps.account_path(fam)).json()
    second = cs.created(
        cs.refund(
            client,
            owner.cookie,
            fam,
            payment,
            cs.refund_body(
                "25.00",
                payment_method_code="cash",
                reference="   ",
                refunded_on=TODAY.isoformat(),
            ),
        )
    )

    refund = first["refund"]
    assert (refund["amount"], refund["payment_method_code"], refund["reference"]) == (
        "15.00",
        "transfer",
        "SPEI-900",
    )
    assert refund["reason"] == "Saldo a favor devuelto"
    assert refund["refunded_by_user_id"] == str(owner.user.id)
    assert refund["refunded_on"] == TODAY.isoformat()
    assert first["account_credit_after"] == "25.00"
    assert (summary["outstanding"], summary["account_credit"]) == ("0.00", "25.00")
    assert second["refund"]["reference"] is None
    assert second["account_credit_after"] == "0.00"
    payment_item = second["payment"]
    assert (
        payment_item["refunded_amount"],
        payment_item["unallocated_amount"],
        payment_item["is_reversed"],
    ) == ("40.00", "0.00", False)
    rows = refunds_of(url, payment)
    assert [(str(r.amount), r.payment_method_code) for r in rows] == [
        ("15.00", "transfer"),
        ("25.00", "cash"),
    ]
    [event] = events(url, record_type="refunds", record_id=rows[0].id)
    assert (event.action, event.actor_user_id) == ("create", owner.user.id)
    assert "Saldo a favor devuelto" in json.dumps(event.changes, ensure_ascii=False)
    assert rs.receipt_of_payment(url, payment) == receipt
    cs.unchanged(before, cs.snapshot(url, account))
    cs.assert_invariants(url, account)
    ledger = get(client, owner.cookie, ps.account_path(fam, "/ledger"), limit=100)
    refund_rows = [e for e in ledger.json()["items"] if e["entry_type"] == "refund"]
    assert sorted((e["amount"], e["balance_effect"]) for e in refund_rows) == [
        ("15.00", "15.00"),
        ("25.00", "25.00"),
    ]


def test_a_refund_above_the_remainder_is_409(module_seeded_url, client, owner):
    url = module_seeded_url
    fam, account, [student] = cs.new_account(url)
    [charge] = ps.charges(url, (student, "60.00", TODAY))
    payment = cs.recorded_payment(
        url, owner.user.id, account, "100.00", [(charge, "60.00")]
    )
    cs.created(cs.refund(client, owner.cookie, fam, payment, cs.refund_body("10.00")))

    response = cs.refund(client, owner.cookie, fam, payment, cs.refund_body("30.01"))
    exact = cs.refund(client, owner.cookie, fam, payment, cs.refund_body("30.00"))

    assert code_of(response) == (409, "refund_exceeds_remainder")
    assert response.json()["details"] == {"remainder": "30.00"}
    assert exact.status_code == 201, exact.text
    assert [str(r.amount) for r in refunds_of(url, payment)] == ["10.00", "30.00"]
    cs.assert_invariants(url, account)


def test_a_refund_draws_only_on_its_own_payment(module_seeded_url, client, owner):
    url = module_seeded_url
    fam, account, _ = cs.new_account(url)
    small = cs.recorded_payment(url, owner.user.id, account, "20.00")
    cs.recorded_payment(url, owner.user.id, account, "200.00")

    response = cs.refund(client, owner.cookie, fam, small, cs.refund_body("20.01"))

    assert code_of(response) == (409, "refund_exceeds_remainder")
    assert response.json()["details"] == {"remainder": "20.00"}


def test_a_reversed_payment_cant_be_refunded(module_seeded_url, client, owner):
    url = module_seeded_url
    fam, account, _ = cs.new_account(url)
    payment = cs.recorded_payment(url, owner.user.id, account, "100.00")
    cs.created(cs.reverse(client, owner.cookie, fam, payment))

    response = cs.refund(client, owner.cookie, fam, payment, cs.refund_body("10.00"))

    assert code_of(response) == (409, "payment_reversed")
    assert refunds_of(url, payment) == []


def test_another_familys_payment_is_404(module_seeded_url, client, owner):
    url = module_seeded_url
    fam, _, _ = cs.new_account(url)
    _, other_account, _ = cs.new_account(url)
    other = cs.recorded_payment(url, owner.user.id, other_account, "100.00")

    for family, payment in ((fam, other), (fam, uuid.uuid4()), (uuid.uuid4(), other)):
        response = cs.refund(
            client, owner.cookie, family, payment, cs.refund_body("1.00")
        )
        assert code_of(response) == (404, "not_found")
    assert refunds_of(url, other) == []


@pytest.mark.parametrize(
    ("override", "path", "code"),
    [
        ({"amount": "0.00"}, "amount", "amount_not_positive"),
        ({"amount": "-1.00"}, "amount", "amount_not_positive"),
        ({"amount": "1.001"}, "amount", "money_precision"),
        ({"refunded_on": "1999-12-31"}, "refunded_on", "date_out_of_range"),
        ({"reference": "x" * 101}, "reference", "string_too_long"),
        ({"reason": " "}, "reason", "string_too_short"),
        ({"reason": "x" * 501}, "reason", "string_too_long"),
        ({"payment_method_code": ""}, "payment_method_code", "string_too_short"),
    ],
)
def test_every_body_rule(module_seeded_url, client, owner, override, path, code):
    url = module_seeded_url
    fam, account, _ = cs.new_account(url)
    payment = cs.recorded_payment(url, owner.user.id, account, "100.00")

    response = cs.refund(
        client, owner.cookie, fam, payment, {**cs.refund_body("10.00"), **override}
    )

    assert code_of(response) == (422, "validation_error")
    assert [(e["path"], e["code"]) for e in response.json()["errors"]] == [
        (["body", path], code)
    ]
    assert refunds_of(url, payment) == []


@pytest.mark.parametrize(
    ("override", "code"),
    [
        ({"payment_method_code": "bitcoin"}, "payment_method_unavailable"),
        (
            {"refunded_on": (TODAY + timedelta(days=1)).isoformat()},
            "refunded_on_in_future",
        ),
        (
            {"refunded_on": (TODAY - timedelta(days=4)).isoformat()},
            "refunded_on_before_payment",
        ),
    ],
)
def test_the_method_and_the_dates(module_seeded_url, client, owner, override, code):
    url = module_seeded_url
    fam, account, _ = cs.new_account(url)
    payment = cs.recorded_payment(url, owner.user.id, account, "100.00")
    # The payment was received TODAY (rs.draft): 4 days before is too early.

    response = cs.refund(
        client, owner.cookie, fam, payment, cs.refund_body("10.00", **override)
    )

    assert code_of(response) == (422, code)
    [error] = response.json()["errors"]
    assert error["code"] == code
    assert refunds_of(url, payment) == []


def test_a_refund_on_the_payment_day_and_the_detail_shows_it(
    module_seeded_url, client, owner
):
    url = module_seeded_url
    fam, account, _ = cs.new_account(url)
    payment = cs.recorded_payment(url, owner.user.id, account, "100.00")
    made = cs.created(
        cs.refund(client, owner.cookie, fam, payment, cs.refund_body("5.00"))
    )

    detail = cs.payment_get(client, owner.cookie, fam, payment).json()

    assert detail["reversal"] is None
    assert detail["refunds"] == [made["refund"]]
    assert Money(detail["unallocated_amount"]) == Money("95.00")
