"""Expense adjustments through the real routes, the void, and the race of a
void with a payment (#115 F, H3; ADR-0017 E12, E16; data-model §12.6, §13
X1, §15). Races use ``overlap`` as ``test_finance_expense_payments.py``."""

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
from app.modules.finance import expense_adjustments, expense_payments
from app.modules.finance import service as finance
from app.modules.finance.schemas import ExpenseAdjustmentCreate, ExpensePaymentCreate
from app.modules.identity.permissions import ADMINISTRATOR, FINANCE_STAFF, OWNER
from tests.conftest import FrozenClock, frozen_clock
from tests.integration.auth_support import auth_client, signed_headers
from tests.integration.conftest import run_sql
from tests.integration.corrections_support import Staff, staff
from tests.integration.expense_support import (
    ADJUSTMENT_EXCEEDS,
    CREATED,
    DENIED,
    EXPENSES,
    GONE,
    INVALID,
    NOT_FOUND,
    NOW,
    REAUTH,
    RECORDED_KEYS,
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


# --- F: adjustments -----


def test_f4_up_and_down_with_ids_and_status_only(
    module_seeded_url, client, finance_staff, owner, expense
):
    url = module_seeded_url

    up = adjust(client, finance_staff, expense, "250.50", "  Factura corregida ")
    down = adjust(client, owner, expense, "-50.50", "Descuento\npor pronto pago")

    assert code(up) == CREATED, up.text
    assert set(up.json()) == RECORDED_KEYS
    assert up.json() == {
        "id": up.json()["id"],
        "expense_id": expense,
        "status": "unpaid",
    }
    assert code(down) == CREATED
    stored = rows(url, "expense_adjustments")
    assert [(r.amount, r.reason, r.created_by_user_id) for r in stored] == [
        (Decimal("250.50"), "Factura corregida", finance_staff.user.id),
        (Decimal("-50.50"), "Descuento\npor pronto pago", owner.user.id),
    ]
    assert figures(url, expense) == (Decimal("1200.00"), Decimal("0"))
    item = detail(client, owner, expense)
    assert (item["amount"], item["adjusted_amount"], item["open_amount"]) == (
        "1000.00",
        "1200.00",
        "1200.00",
    )


def test_f1_a_partly_paid_expense_goes_down_to_paid_and_no_further(
    module_seeded_url, client, finance_staff, owner, expense
):
    url = module_seeded_url
    payment = paid(client, finance_staff, expense, "400.00")

    beyond = adjust(client, owner, expense, "-600.01")
    assert code(beyond) == ADJUSTMENT_EXCEEDS
    assert beyond.json()["details"] == {"open": "600.00"}
    to_paid = adjust(client, owner, expense, "-600.00")
    assert code(to_paid) == CREATED
    assert to_paid.json()["status"] == "paid"
    # F3: a partly paid expense can't be voided until its payments are reversed.
    assert code(adjust(client, owner, expense, "-400.00")) == ADJUSTMENT_EXCEEDS
    assert code(reverse(client, owner, payment)) == CREATED
    void = adjust(client, owner, expense, "-400.00")
    assert code(void) == CREATED
    assert void.json()["status"] == "void"
    assert figures(url, expense) == (Decimal("0.00"), Decimal("0"))


def test_f3_a_void_is_final(module_seeded_url, client, finance_staff, owner, expense):
    url = module_seeded_url
    void = adjust(client, finance_staff, expense, "-1000.00", "Capturado dos veces")
    assert void.json()["status"] == "void"

    assert code(adjust(client, owner, expense, "10.00")) == VOID
    assert code(adjust(client, owner, expense, "-10.00")) == VOID
    assert code(pay(client, finance_staff, expense, payment_body("1.00"))) == VOID
    item = detail(client, owner, expense)
    assert (item["status"], item["adjusted_amount"], item["open_amount"]) == (
        "void",
        "0.00",
        "0.00",
    )
    assert len(rows(url, "expense_adjustments")) == 1


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        ({"amount": "0.00", "reason": "x"}, (("body", "amount"), "amount_zero")),
        ({"amount": "1.001", "reason": "x"}, (("body", "amount"), "money_precision")),
        (
            {"amount": "-1000000000000.00", "reason": "x"},
            (("body", "amount"), "money_out_of_range"),
        ),
        ({"amount": "1.00", "reason": " "}, (("body", "reason"), "text_empty")),
        (
            {"amount": "1.00", "reason": "x" * 1001},
            (("body", "reason"), "text_too_long"),
        ),
        ({"amount": "1.00"}, (("body", "reason"), "missing")),
        (
            {"amount": "1.00", "reason": "x", "kind": "manual"},
            (("body", "kind"), "extra_forbidden"),
        ),
    ],
)
def test_f2_the_body_is_checked(
    module_seeded_url, client, owner, expense, payload, error
):
    response = adjust_raw(client, owner, expense, payload)

    assert code(response) == INVALID, response.text
    assert error in field_errors(response)
    assert rows(module_seeded_url, "expense_adjustments") == []


def adjust_raw(
    client: TestClient, who: Staff, expense: Any, payload: Any, key: str | None = None
) -> Any:
    client.cookies.clear()
    headers = {**signed_headers(who.cookie), "Idempotency-Key": key or new_key()}
    return client.post(
        f"{EXPENSES}/{expense}/adjustments", json=payload, headers=headers
    )


def test_f1_the_new_net_stays_in_the_money_range(
    module_seeded_url, client, finance_staff, owner
):
    rent = category_of(module_seeded_url)
    big = created(client, finance_staff, expense_body(rent, amount="999999999999.99"))

    response = adjust(client, owner, big, "0.01")

    assert code(response) == INVALID
    assert field_errors(response) == [(("body", "amount"), "money_out_of_range")]
    assert code(adjust(client, owner, big, "-0.99")) == CREATED


def test_f1_the_order(module_seeded_url, client, frozen, finance_staff, owner, expense):
    url = module_seeded_url
    stale = staff(url, client, (FINANCE_STAFF,), reauth=False)
    administrator = staff(url, client, (ADMINISTRATOR,))

    assert code(adjust(client, administrator, expense, "1.00")) == DENIED
    assert code(adjust(client, stale, expense, "1.00")) == REAUTH
    assert code(adjust_raw(client, stale, expense, {})) == REAUTH
    client.cookies.clear()
    no_key = client.post(
        f"{EXPENSES}/{expense}/adjustments",
        json={},
        headers=signed_headers(owner.cookie),
    )
    assert code(no_key) == (400, "idempotency_key_required")
    raw = json.dumps({"amount": "1.00", "reason": "@@"}).replace("@@", "a\\ud800b")
    client.cookies.clear()
    surrogate = client.post(
        f"{EXPENSES}/{expense}/adjustments",
        content=raw.encode(),
        headers={**signed_headers(owner.cookie), "Content-Type": "application/json"},
    )
    assert field_errors(surrogate) == [(("body",), "text_invalid")]
    assert code(adjust(client, owner, GONE, "1.00")) == NOT_FOUND
    # The body before the 404 and the void.
    assert code(adjust_raw(client, owner, GONE, {"amount": "0"})) == INVALID
    frozen.set(NOW + timedelta(minutes=5, seconds=1))
    assert code(adjust(client, finance_staff, expense, "1.00")) == REAUTH
    frozen.set(NOW)
    assert rows(url, "expense_adjustments") == []


def test_f_replay_and_reuse(module_seeded_url, client, owner, expense):
    key = new_key()

    first = adjust(client, owner, expense, "10.00", key=key)
    again = adjust(client, owner, expense, "10.00", key=key)
    reused = adjust(client, owner, expense, "11.00", key=key)

    assert code(first) == CREATED
    assert again.json() == first.json()
    assert again.headers.get(REPLAYED_HEADER) == "true"
    assert code(reused) == (422, "idempotency_key_reused")
    assert len(rows(module_seeded_url, "expense_adjustments")) == 1


def test_f_the_table_is_append_only(module_seeded_url, client, owner, expense):
    url = module_seeded_url
    adjustment = adjust(client, owner, expense, "10.00").json()["id"]

    for statement in (
        "UPDATE expense_adjustments SET amount = 2 WHERE id = :id",
        "DELETE FROM expense_adjustments WHERE id = :id",
    ):
        with pytest.raises(DBAPIError) as caught:
            run_sql(url, statement, {"id": adjustment})
        assert getattr(caught.value.orig, "sqlstate", None) == "42501", statement


# --- H3: a void racing a payment -----


def voiding(user_id: uuid.UUID, expense: str) -> Any:
    made = ExpenseAdjustmentCreate(amount=Money("-1000.00"), reason="Duplicado")

    def call(session: AsyncSession) -> Awaitable[Any]:
        return finance.post_adjustment(session, user_id, uuid.UUID(expense), made)

    return call


def paying(user_id: uuid.UUID, expense: str) -> Any:
    made = ExpensePaymentCreate(paid_on=TODAY, amount=Money("1.00"), method_code="cash")

    def call(session: AsyncSession) -> Awaitable[Any]:
        return finance.record_payment(session, user_id, uuid.UUID(expense), made)

    return call


def test_h3_a_payment_first_makes_the_void_exceed_the_open_amount(
    module_seeded_url, monkeypatch, finance_staff, owner, expense
):
    url = module_seeded_url

    first, second = overlap(
        url,
        monkeypatch,
        expense_payments,
        "insert_payment",
        paying(finance_staff.user.id, expense),
        voiding(owner.user.id, expense),
    )

    assert first.status == "partially_paid"
    assert isinstance(second, AppError)
    assert second.code == "expense_adjustment_exceeds_open"
    assert second.details == {"open": "999.00"}
    assert figures(url, expense) == (Decimal("1000.00"), Decimal("1.00"))


def test_h3_a_void_first_makes_the_payment_refused_as_void(
    module_seeded_url, monkeypatch, finance_staff, owner, expense
):
    url = module_seeded_url

    first, second = overlap(
        url,
        monkeypatch,
        expense_adjustments,
        "insert_adjustment",
        voiding(owner.user.id, expense),
        paying(finance_staff.user.id, expense),
    )

    assert first.status == "void"
    assert isinstance(second, AppError)
    assert second.code == "expense_void"
    net, paid_total = figures(url, expense)
    assert (net, paid_total) == (Decimal("0.00"), Decimal("0"))
    assert net - paid_total >= 0
