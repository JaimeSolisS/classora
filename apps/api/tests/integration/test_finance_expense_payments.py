"""Expense payments and their reversals through the real routes, and their
races on real connections (#115 D, E, H1, H2, H4; ADR-0017 E12; data-model
§12.4, §12.5, §13 X1, §15).

A race (``overlap``, from ``test_payroll_workflow_concurrency``) pauses the
first writer **inside** its transaction, right after its row is written and
before its commit; the second must be seen waiting on a lock in
``pg_stat_activity`` (never timed), and then reads what the first committed.
"""

import json
import uuid
from collections.abc import Awaitable, Iterator
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.idempotency import REPLAYED_HEADER
from app.core.money import Money
from app.modules.finance import expense_payments
from app.modules.finance import service as finance
from app.modules.finance.schemas import (
    ExpensePaymentCreate,
    ExpensePaymentReversalCreate,
)
from app.modules.identity.permissions import ADMINISTRATOR, FINANCE_STAFF, OWNER
from tests.conftest import FrozenClock, frozen_clock
from tests.integration.auth_support import auth_client, signed_headers
from tests.integration.conftest import run_sql
from tests.integration.corrections_support import Staff, staff
from tests.integration.expense_support import (
    ALREADY_REVERSED,
    CREATED,
    DENIED,
    DUPLICATE,
    EXCEEDS,
    EXPENSE_PAYMENTS,
    GONE,
    INVALID,
    NOT_FOUND,
    NOW,
    REAUTH,
    RECORDED_KEYS,
    REVERSED_KEYS,
    TODAY,
    VOID,
    adjust,
    category_of,
    code,
    created,
    detail,
    expense_body,
    field_errors,
    figures,
    new_key,
    paid,
    pay,
    payment_body,
    reverse,
    rows,
    stored_keys,
)
from tests.integration.finance_support import reset
from tests.integration.test_payroll_workflow_concurrency import overlap

LATER = (TODAY + timedelta(days=10)).isoformat()


@pytest.fixture(scope="module")
def frozen() -> Iterator[FrozenClock]:
    with frozen_clock(NOW) as clock:
        yield clock


@pytest.fixture(autouse=True)
def at_now(frozen: FrozenClock) -> Iterator[None]:
    frozen.set(NOW)
    yield
    frozen.set(NOW)


@pytest.fixture(scope="module")
def client(module_seeded_url: str, frozen: FrozenClock) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def clean(module_seeded_url: str) -> Iterator[None]:
    yield
    reset(module_seeded_url)


@pytest.fixture(scope="module")
def owner(module_seeded_url: str, client: TestClient) -> Staff:
    return staff(module_seeded_url, client, (OWNER,))


@pytest.fixture(scope="module")
def finance_staff(module_seeded_url: str, client: TestClient) -> Staff:
    return staff(module_seeded_url, client, (FINANCE_STAFF,))


@pytest.fixture
def expense(module_seeded_url: str, client: TestClient, finance_staff: Staff) -> str:
    """An expense of 1000.00, due in ten days."""
    rent = category_of(module_seeded_url)
    return created(client, finance_staff, expense_body(rent, due_on=LATER))


# --- D: the happy path -----


def test_d3_partial_payments_up_to_exactly_the_open_amount(
    module_seeded_url, client, finance_staff, owner, expense
):
    url = module_seeded_url

    first = pay(client, finance_staff, expense, payment_body("300.00", "SPEI 1"))
    assert code(first) == CREATED, first.text
    assert set(first.json()) == RECORDED_KEYS
    assert first.json() == {
        "id": first.json()["id"],
        "expense_id": expense,
        "status": "partially_paid",
    }
    second = pay(client, owner, expense, payment_body("400.00", method="cash"))
    assert second.json()["status"] == "partially_paid"
    over = pay(client, finance_staff, expense, payment_body("300.01"))
    assert code(over) == EXCEEDS
    assert over.json()["details"] == {"open": "300.00"}
    exact = pay(client, finance_staff, expense, payment_body("300.00"))
    assert code(exact) == CREATED
    assert exact.json()["status"] == "paid"
    assert code(pay(client, finance_staff, expense, payment_body("0.01"))) == EXCEEDS

    stored = rows(url, "expense_payments", expense_id=expense)
    assert [(r.amount, r.method_code, r.reference) for r in stored] == [
        (Decimal("300.00"), "transfer", "SPEI 1"),
        (Decimal("400.00"), "cash", None),
        (Decimal("300.00"), "transfer", None),
    ]
    assert [r.recorded_by_user_id for r in stored] == [
        finance_staff.user.id,
        owner.user.id,
        finance_staff.user.id,
    ]
    assert figures(url, expense) == (Decimal("1000.00"), Decimal("1000.00"))
    assert detail(client, owner, expense)["status"] == "paid"


def test_d2_texts_are_trimmed_blank_is_null_and_any_past_date_passes(
    module_seeded_url, client, finance_staff, expense
):
    long_ago = (TODAY - timedelta(days=400)).isoformat()
    response = pay(
        client,
        finance_staff,
        expense,
        {
            **payment_body("1.00"),
            "reference": "  SPEI 9 ",
            "note": "  ",
            "paid_on": long_ago,
        },
    )

    assert code(response) == CREATED, response.text
    [row] = rows(module_seeded_url, "expense_payments")
    assert (row.reference, row.note, row.paid_on.isoformat()) == (
        "SPEI 9",
        None,
        long_ago,
    )
    today = pay(client, finance_staff, expense, payment_body("1.00", "x" * 100))
    assert code(today) == CREATED


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"amount": "0.00"}, (("body", "amount"), "amount_not_positive")),
        ({"amount": "-5.00"}, (("body", "amount"), "amount_not_positive")),
        ({"amount": "1.005"}, (("body", "amount"), "money_precision")),
        ({"reference": "x" * 101}, (("body", "reference"), "text_too_long")),
        ({"reference": "a\nb"}, (("body", "reference"), "text_invalid")),
        ({"note": "x" * 1001}, (("body", "note"), "text_too_long")),
        ({"method_code": ""}, (("body", "method_code"), "string_too_short")),
        ({"extra": 1}, (("body", "extra"), "extra_forbidden")),
    ],
)
def test_d2_the_body_is_checked(
    module_seeded_url, client, finance_staff, expense, change, error
):
    response = pay(client, finance_staff, expense, {**payment_body(), **change})

    assert code(response) == INVALID, response.text
    assert error in field_errors(response)
    assert rows(module_seeded_url, "expense_payments") == []


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"method_code": "cheque"}, ("method_code", "payment_method_unavailable")),
        (
            {"paid_on": (TODAY + timedelta(days=1)).isoformat()},
            ("paid_on", "paid_on_in_future"),
        ),
    ],
)
def test_d2_the_method_and_the_date(
    module_seeded_url, client, finance_staff, expense, change, error
):
    response = pay(client, finance_staff, expense, {**payment_body(), **change})

    assert code(response) == (422, error[1]), response.text
    assert field_errors(response) == [(("body", error[0]), error[1])]
    assert rows(module_seeded_url, "expense_payments") == []


def test_d1_the_order_after_the_lock(
    module_seeded_url, client, finance_staff, owner, expense
):
    url = module_seeded_url
    paid(client, finance_staff, expense, "999.00", reference="SPEI 1")
    in_future = (TODAY + timedelta(days=1)).isoformat()

    # The method before the date, the date before the duplicate, the
    # duplicate before the cap.
    both = payment_body("5.00", "SPEI 1", method="cheque", paid_on=TODAY)
    assert code(
        pay(client, finance_staff, expense, {**both, "paid_on": in_future})
    ) == (
        422,
        "payment_method_unavailable",
    )
    late = payment_body("5.00", "SPEI 1")
    assert code(
        pay(client, finance_staff, expense, {**late, "paid_on": in_future})
    ) == (422, "paid_on_in_future")
    assert code(pay(client, finance_staff, expense, late)) == DUPLICATE
    # A void expense refuses before everything else.
    rent = category_of(url)
    void = created(client, finance_staff, expense_body(rent))
    assert code(adjust(client, owner, void, "-1000.00")) == CREATED
    refused = pay(client, finance_staff, void, payment_body("5000.00", method="cheque"))
    assert code(refused) == VOID
    assert code(pay(client, finance_staff, GONE)) == NOT_FOUND


def test_d1_an_inactive_method_is_refused_and_its_past_payment_still_reverses(
    module_seeded_url, client, finance_staff, owner, expense
):
    url = module_seeded_url
    payment = paid(client, finance_staff, expense, "10.00", method="other")
    run_sql(url, "UPDATE payment_methods SET is_active = false WHERE code = 'other'")
    try:
        refused = pay(client, finance_staff, expense, payment_body(method="other"))
        assert code(refused) == (422, "payment_method_unavailable")
        assert code(reverse(client, owner, payment)) == CREATED
    finally:
        run_sql(url, "UPDATE payment_methods SET is_active = true WHERE code = 'other'")


# --- D4: duplicates and idempotency -----


def test_d4_the_duplicate_rule(
    module_seeded_url, client, finance_staff, owner, expense
):
    url = module_seeded_url
    first = paid(client, finance_staff, expense, "10.00", reference="SPEI 77")

    same = pay(client, finance_staff, expense, payment_body("20.00", " spei 77 "))
    assert code(same) == DUPLICATE
    assert same.json()["details"] == {"payment_id": first}
    other_method = pay(
        client, finance_staff, expense, payment_body("20.00", "SPEI 77", method="cash")
    )
    assert code(other_method) == CREATED
    # No reference, no duplicate rule.
    assert code(pay(client, finance_staff, expense, payment_body("1.00"))) == CREATED
    assert code(pay(client, finance_staff, expense, payment_body("1.00"))) == CREATED
    # The same reference on another expense passes.
    rent = category_of(url)
    another = created(client, finance_staff, expense_body(rent))
    assert code(
        pay(client, finance_staff, another, payment_body("1.00", "SPEI 77"))
    ) == (CREATED)
    # A reversal frees the pair.
    assert code(reverse(client, owner, first)) == CREATED
    again = pay(client, finance_staff, expense, payment_body("10.00", "SPEI 77"))
    assert code(again) == CREATED


def test_d5_replay_and_reuse(module_seeded_url, client, finance_staff, expense):
    url = module_seeded_url
    key = new_key()
    payload = payment_body("10.00")

    first = pay(client, finance_staff, expense, payload, key=key)
    again = pay(client, finance_staff, expense, payload, key=key)
    reused = pay(client, finance_staff, expense, payment_body("11.00"), key=key)

    assert code(first) == CREATED
    assert again.json() == first.json()
    assert again.headers.get(REPLAYED_HEADER) == "true"
    assert code(reused) == (422, "idempotency_key_reused")
    assert len(rows(url, "expense_payments")) == 1


# --- E: reversals -----


def test_e2_a_reversal_frees_the_cap_and_answers_ids_and_status_only(
    module_seeded_url, client, finance_staff, owner, expense
):
    url = module_seeded_url
    payment = paid(client, finance_staff, expense, "1000.00", reference="SPEI 1")
    assert code(pay(client, finance_staff, expense, payment_body("1.00"))) == EXCEEDS

    response = reverse(client, owner, payment, "  Monto equivocado ")

    assert code(response) == CREATED, response.text
    assert set(response.json()) == REVERSED_KEYS
    assert response.json() == {
        "id": response.json()["id"],
        "expense_payment_id": payment,
        "status": "unpaid",
    }
    [row] = rows(url, "expense_payment_reversals")
    assert (row.reason, row.reversed_by_user_id) == ("Monto equivocado", owner.user.id)
    assert figures(url, expense) == (Decimal("1000.00"), Decimal("0"))
    assert code(pay(client, finance_staff, expense, payment_body("1000.00"))) == CREATED
    item = detail(client, finance_staff, expense)
    assert (item["paid_amount"], item["status"]) == ("1000.00", "paid")
    assert code(reverse(client, owner, payment)) == ALREADY_REVERSED
    assert code(reverse(client, owner, GONE)) == NOT_FOUND


@pytest.mark.parametrize(
    ("reason", "error"),
    [("   ", "text_empty"), ("x" * 1001, "text_too_long"), ("a\x00", "text_invalid")],
)
def test_e2_the_reason_is_checked(
    module_seeded_url, client, finance_staff, owner, expense, reason, error
):
    payment = paid(client, finance_staff, expense, "1.00")

    response = reverse(client, owner, payment, reason)

    assert code(response) == INVALID
    assert field_errors(response) == [(("body", "reason"), error)]
    assert code(reverse(client, owner, payment, "Línea 1\nLínea 2")) == CREATED


def test_e1_e3_who_reverses_and_the_order(
    module_seeded_url, client, frozen, finance_staff, owner, expense
):
    url = module_seeded_url
    payment = paid(client, finance_staff, expense, "1.00")
    delegated = staff(
        url, client, (ADMINISTRATOR,), delegations=["finance.refund.approve"]
    )
    stale = staff(url, client, (OWNER,), reauth=False)

    # Permission before reauthentication: neither Finance Staff (no
    # finance.refund.approve) nor a delegated Administrator (no expense.view).
    assert code(reverse(client, finance_staff, payment)) == DENIED
    assert code(reverse(client, delegated, payment)) == DENIED
    assert code(reverse(client, stale, payment)) == REAUTH
    # Reauthentication before the key and the body.
    client.cookies.clear()
    no_key = client.post(
        f"{EXPENSE_PAYMENTS}/{payment}/reversal",
        json={},
        headers=signed_headers(stale.cookie),
    )
    assert code(no_key) == REAUTH
    client.cookies.clear()
    owner_no_key = client.post(
        f"{EXPENSE_PAYMENTS}/{payment}/reversal",
        json={},
        headers=signed_headers(owner.cookie),
    )
    assert code(owner_no_key) == (400, "idempotency_key_required")
    raw = json.dumps({"reason": "@@"}).replace("@@", "a\\ud800b")
    client.cookies.clear()
    surrogate = client.post(
        f"{EXPENSE_PAYMENTS}/{payment}/reversal",
        content=raw.encode(),
        headers={**signed_headers(owner.cookie), "Content-Type": "application/json"},
    )
    assert field_errors(surrogate) == [(("body",), "text_invalid")]
    # Five minutes and one second after the reauthentication: stale.
    frozen.set(NOW + timedelta(minutes=5, seconds=1))
    assert code(reverse(client, owner, payment)) == REAUTH
    frozen.set(NOW + timedelta(minutes=4, seconds=59))
    assert code(reverse(client, owner, payment)) == CREATED
    assert len(rows(url, "expense_payment_reversals")) == 1


def test_e1_the_unique_is_the_backstop_mapped_to_409(
    module_seeded_url, client, finance_staff, owner, expense, monkeypatch
):
    payment = paid(client, finance_staff, expense, "1.00")
    assert code(reverse(client, owner, payment)) == CREATED

    async def never(*_: Any) -> bool:
        return False

    monkeypatch.setattr(expense_payments, "is_reversed", never)

    assert code(reverse(client, owner, payment)) == ALREADY_REVERSED
    assert len(rows(module_seeded_url, "expense_payment_reversals")) == 1


def test_e_tables_are_append_only(
    module_seeded_url, client, finance_staff, owner, expense
):
    url = module_seeded_url
    payment = paid(client, finance_staff, expense, "1.00")
    reversal = reverse(client, owner, payment).json()["id"]

    for statement, row in (
        ("UPDATE expense_payments SET amount = 2 WHERE id = :id", payment),
        ("DELETE FROM expense_payments WHERE id = :id", payment),
        ("UPDATE expense_payment_reversals SET reason = 'x' WHERE id = :id", reversal),
        ("DELETE FROM expense_payment_reversals WHERE id = :id", reversal),
        ("UPDATE expenses SET amount = 1 WHERE id = :id", expense),
    ):
        with pytest.raises(DBAPIError) as caught:
            run_sql(url, statement, {"id": row})
        assert getattr(caught.value.orig, "sqlstate", None) == "42501", statement


# --- H: races on real connections -----


def paying(user_id: uuid.UUID, expense: str, amount: str, reference: str | None) -> Any:
    made = ExpensePaymentCreate(
        paid_on=TODAY,
        amount=Money(amount),
        method_code="transfer",
        reference=reference,
    )

    def call(session: AsyncSession) -> Awaitable[Any]:
        return finance.record_payment(session, user_id, uuid.UUID(expense), made)

    return call


def reversing(user_id: uuid.UUID, payment: str) -> Any:
    made = ExpensePaymentReversalCreate(reason="Monto equivocado")

    def call(session: AsyncSession) -> Awaitable[Any]:
        return finance.reverse_payment(session, user_id, uuid.UUID(payment), made)

    return call


def test_h1_two_payments_over_the_open_amount_one_is_refused(
    module_seeded_url, monkeypatch, finance_staff, expense
):
    url = module_seeded_url
    user = finance_staff.user.id

    first, second = overlap(
        url,
        monkeypatch,
        expense_payments,
        "insert_payment",
        paying(user, expense, "700.00", "A"),
        paying(user, expense, "400.00", "B"),
    )

    assert first.status == "partially_paid"
    assert isinstance(second, AppError)
    assert second.code == "expense_payment_exceeds_open"
    assert second.details == {"open": "300.00"}
    assert figures(url, expense) == (Decimal("1000.00"), Decimal("700.00"))


def test_h2_two_payments_with_one_method_and_reference_one_is_refused(
    module_seeded_url, monkeypatch, finance_staff, expense
):
    url = module_seeded_url
    user = finance_staff.user.id

    first, second = overlap(
        url,
        monkeypatch,
        expense_payments,
        "insert_payment",
        paying(user, expense, "10.00", "SPEI 5"),
        paying(user, expense, "20.00", "spei 5"),
    )

    assert first.status == "partially_paid"
    assert isinstance(second, AppError)
    assert second.code == "expense_payment_duplicate"
    assert second.details == {"payment_id": str(first.id)}
    assert len(rows(url, "expense_payments")) == 1


def test_h4_two_reversals_of_one_payment_one_is_refused(
    module_seeded_url, client, monkeypatch, finance_staff, owner, expense
):
    url = module_seeded_url
    payment = paid(client, finance_staff, expense, "10.00")

    first, second = overlap(
        url,
        monkeypatch,
        expense_payments,
        "insert_reversal",
        reversing(owner.user.id, payment),
        reversing(owner.user.id, payment),
    )

    assert first.status == "unpaid"
    assert isinstance(second, AppError)
    assert second.code == "expense_payment_already_reversed"
    assert len(rows(url, "expense_payment_reversals")) == 1


def test_h_a_payment_waits_for_a_reversal_that_frees_the_cap(
    module_seeded_url, client, monkeypatch, finance_staff, owner, expense
):
    url = module_seeded_url
    payment = paid(client, finance_staff, expense, "1000.00")

    first, second = overlap(
        url,
        monkeypatch,
        expense_payments,
        "insert_reversal",
        reversing(owner.user.id, payment),
        paying(finance_staff.user.id, expense, "1000.00", None),
    )

    assert first.status == "unpaid"
    assert second.status == "paid"
    assert figures(url, expense) == (Decimal("1000.00"), Decimal("1000.00"))


def test_stored_keys_hold_ids_and_status_only(module_seeded_url, client, expense):
    url = module_seeded_url
    who = staff(url, client, (OWNER,))
    payment = paid(client, who, expense, "12.34", reference="SPEI 4242", note="Nota")
    assert code(reverse(client, who, payment, "Motivo privado")) == CREATED

    keys = stored_keys(url, who.user.id)

    assert [set(k.response_body) for k in keys] == [RECORDED_KEYS, REVERSED_KEYS]
    text = json.dumps([k.response_body for k in keys])
    for secret in ("12.34", "SPEI 4242", "Nota", "Motivo privado"):
        assert secret not in text
