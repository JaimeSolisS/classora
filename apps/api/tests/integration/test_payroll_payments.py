"""Payroll payments and their reversals through the real routes (#112 B, C, D,
E1, E2, E4-E7; ADR-0017 E12, E13; data-model §11.10, §11.11, §13, P7).

A period gets to ``finalized`` only by its routes here (Finance prepares, the
Owner finalizes), except in the statement count test, which needs many. Races
are in ``test_payroll_payments_concurrency.py``.
"""

import json
import logging
import uuid
from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError

from app.core.idempotency import REPLAYED_HEADER
from app.core.money import Money
from app.core.pagination import OffsetParams
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    OWNER,
    TUTOR,
)
from app.workflows import payroll_workflow
from tests.conftest import FrozenClock, frozen_clock
from tests.integration.auth_support import (
    CreatedUser,
    auth_client,
    create_user,
    signed_headers,
)
from tests.integration.catalog_support import count_statements
from tests.integration.conftest import run_sql
from tests.integration.corrections_support import Staff, staff
from tests.integration.payroll_calculation_support import (
    add_item,
    add_pay_type,
    add_period_row,
    clear,
    paid_tutor,
)
from tests.integration.payroll_manual_support import carry, manual
from tests.integration.payroll_payment_support import (
    ALREADY_REVERSED,
    CREATED,
    DENIED,
    DUPLICATE,
    EXCEEDS,
    NOT_FINALIZED,
    NOT_FOUND,
    NOW,
    OWN_PAY,
    PAID_ON,
    REAUTH,
    RECORDED_KEYS,
    REVERSED_KEYS,
    audit_rows,
    body,
    code,
    finalized_period,
    listed,
    paid_sum,
    pay,
    payments_of,
    reversals_of,
    reverse,
    stored_keys,
    worked_period,
)
from tests.integration.payroll_period_support import set_floor
from tests.integration.payroll_workflow_support import (
    OK,
    ROOT,
    locked,
    name_of,
    prepare,
    read,
    return_period,
)
from tests.integration.test_attendance import principal_of

GONE = "00000000-0000-7000-8000-000000000000"


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
    set_floor(module_seeded_url, "10-01")
    yield
    clear(module_seeded_url)
    set_floor(module_seeded_url, None)


@pytest.fixture
def owner(module_seeded_url: str, client: TestClient) -> Staff:
    """Per test: tests read the keys this user stored, and move the clock past
    its reauthentication."""
    return staff(module_seeded_url, client, (OWNER,))


@pytest.fixture(scope="module")
def finance(module_seeded_url: str, client: TestClient) -> Staff:
    return staff(module_seeded_url, client, (FINANCE_STAFF,))


@pytest.fixture
def tutor(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(TUTOR,), display_name="Tutora Pagada")


@pytest.fixture
def period(module_seeded_url, client, owner, finance, tutor) -> uuid.UUID:
    """A finalized period of 110.00."""
    return finalized_period(module_seeded_url, client, owner, finance, tutor)


def detail(client: TestClient, who: Staff, period: Any) -> dict[str, Any]:
    response = read(client, who, period)
    assert response.status_code == 200, response.text
    return dict(response.json()["period"])


def listed_row(client: TestClient, who: Staff, period: Any) -> dict[str, Any]:
    client.cookies.clear()
    items = client.get(ROOT, headers=signed_headers(who.cookie)).json()["items"]
    [row] = [item for item in items if item["id"] == str(period)]
    return dict(row)


# --- E1: the happy path -----


def test_e1_two_partial_payments_then_the_exact_remainder(
    module_seeded_url, client, owner, finance, tutor, period
):
    url = module_seeded_url
    assert (detail(client, finance, period)["paid_state"]) == "unpaid"

    first = pay(client, finance, period, body("30.00", "SPEI 1"))
    assert code(first) == CREATED, first.text
    assert set(first.json()) == RECORDED_KEYS
    assert first.json()["paid_state"] == "partially_paid"
    assert first.json()["payroll_period_id"] == str(period)
    second = pay(client, owner, period, body("50.00", "SPEI 2", note=" En dos "))
    assert code(second) == CREATED
    assert second.json()["paid_state"] == "partially_paid"
    row = listed_row(client, finance, period)
    assert (row["paid_total"], row["paid_state"]) == ("80.00", "partially_paid")

    third = pay(client, finance, period, body("30.01", "SPEI 3"))
    assert code(third) == EXCEEDS
    assert third.json()["details"] == {"remaining": "30.00"}
    remainder = pay(client, finance, period, body("30.00", "SPEI 3", method="cash"))
    assert code(remainder) == CREATED
    assert remainder.json()["paid_state"] == "paid"
    assert code(pay(client, finance, period, body("0.01", "SPEI 4"))) == EXCEEDS

    rows = payments_of(url, period)
    assert [(r.amount, r.reference, r.note) for r in rows] == [
        (Decimal("30.00"), "SPEI 1", None),
        (Decimal("50.00"), "SPEI 2", "En dos"),
        (Decimal("30.00"), "SPEI 3", None),
    ]
    assert [r.recorded_by_user_id for r in rows] == [
        finance.user.id,
        owner.user.id,
        finance.user.id,
    ]
    head = detail(client, owner, period)
    assert (head["paid_total"], head["paid_state"]) == ("110.00", "paid")
    assert head["total_amount"] == "110.00"


def test_d1_the_payments_read(module_seeded_url, client, owner, finance, period):
    url = module_seeded_url
    older = pay(client, finance, period, body("10.00", "A-1", paid_on="2026-11-10"))
    newer = pay(client, finance, period, body("20.00", "B-2", note="Efectivo"))
    reversed_ = reverse(client, owner, older.json()["id"], "Duplicado")
    assert code(reversed_) == CREATED

    response = listed(client, finance, period)

    assert response.status_code == 200
    items = response.json()
    assert [item["id"] for item in items] == [newer.json()["id"], older.json()["id"]]
    first, second = items
    assert set(first) == {
        "id",
        "paid_on",
        "amount",
        "method_code",
        "reference",
        "note",
        "recorded_by",
        "created_at",
        "reversal",
    }
    assert (first["paid_on"], first["amount"], first["method_code"]) == (
        PAID_ON,
        "20.00",
        "transfer",
    )
    assert (first["reference"], first["note"], first["reversal"]) == (
        "B-2",
        "Efectivo",
        None,
    )
    assert first["recorded_by"] == {
        "id": str(finance.user.id),
        "display_name": name_of(url, finance.user.id),
    }
    assert second["reversal"]["reason"] == "Duplicado"
    assert second["reversal"]["reversed_by"]["id"] == str(owner.user.id)
    assert second["reversal"]["created_at"]
    assert listed(client, finance, GONE).status_code == 404
    assert [r.id for r in reversals_of(url, older.json()["id"])] == [
        uuid.UUID(reversed_.json()["id"])
    ]


# --- B: the rules -----


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"amount": "0.00"}, "amount_not_positive"),
        ({"amount": "-5.00"}, "amount_not_positive"),
        ({"amount": "1.001"}, None),
        ({"reference": "   "}, "text_empty"),
        ({"reference": "x" * 101}, "text_too_long"),
        ({"reference": "a\x00b"}, "text_invalid"),
        ({"note": "x" * 1001}, "text_too_long"),
        ({"method_code": ""}, "string_too_short"),
        ({"paid_on": "2026-13-01"}, None),
        ({"extra": 1}, "extra_forbidden"),
    ],
)
def test_b2_the_body_is_checked_and_claims_no_key(
    module_seeded_url, client, owner, period, change, error
):
    response = pay(client, owner, period, {**body(), **change})

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "validation_error"
    if error is not None:
        assert error in {e["code"] for e in response.json()["errors"]}
    assert payments_of(module_seeded_url, period) == []
    assert stored_keys(module_seeded_url, owner.user.id) == []


def test_b2_the_reference_and_note_are_trimmed_and_a_blank_note_is_null(
    module_seeded_url, client, finance, period
):
    response = pay(client, finance, period, body(reference="  SPEI 9 ", note="  "))

    assert code(response) == CREATED
    [row] = payments_of(module_seeded_url, period)
    assert (row.reference, row.note) == ("SPEI 9", None)
    assert code(pay(client, finance, period, body(reference="x" * 100))) == CREATED


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"method_code": "cheque"}, ("method_code", "payment_method_unavailable")),
        ({"paid_on": "2026-12-08"}, ("paid_on", "paid_on_in_future")),
        ({"paid_on": "2026-11-01"}, ("paid_on", "paid_on_before_period")),
    ],
)
def test_b2_e4_the_method_and_the_date(
    module_seeded_url, client, finance, period, change, error
):
    response = pay(client, finance, period, {**body(), **change})

    assert response.status_code == 422, response.text
    assert response.json()["code"] == error[1]
    [field_error] = response.json()["errors"]
    assert (field_error["path"], field_error["code"]) == (["body", error[0]], error[1])
    assert payments_of(module_seeded_url, period) == []


def test_b2_e4_the_bounds_are_inclusive_and_an_inactive_method_is_refused(
    module_seeded_url, client, finance, period
):
    url = module_seeded_url
    first_day = body("1.00", "A", paid_on="2026-11-02")
    assert code(pay(client, finance, period, first_day)) == CREATED
    today = body("1.00", "B", paid_on="2026-12-07")
    assert code(pay(client, finance, period, today)) == CREATED
    run_sql(url, "UPDATE payment_methods SET is_active = false WHERE code = 'other'")
    try:
        refused = pay(client, finance, period, body("1.00", "C", method="other"))
        assert code(refused) == (422, "payment_method_unavailable")
    finally:
        run_sql(url, "UPDATE payment_methods SET is_active = true WHERE code = 'other'")
    assert len(payments_of(url, period)) == 2


def test_b1_a_missing_period_is_404(client, finance):
    assert code(pay(client, finance, GONE)) == NOT_FOUND


# --- E2: idempotency and duplicates -----


def test_e2_the_key_replays_and_the_duplicate_rule(
    module_seeded_url, client, owner, finance, period
):
    url = module_seeded_url
    key = f"key-{uuid.uuid4().hex}"
    first = pay(client, finance, period, body("10.00", "SPEI 77"), key=key)
    assert code(first) == CREATED

    again = pay(client, finance, period, body("10.00", "SPEI 77"), key=key)
    assert code(again) == CREATED
    assert again.headers.get(REPLAYED_HEADER) == "true"
    assert again.json() == first.json()
    reused = pay(client, finance, period, body("11.00", "SPEI 77"), key=key)
    assert code(reused) == (422, "idempotency_key_reused")
    assert len(payments_of(url, period)) == 1

    duplicate = pay(client, finance, period, body("10.00", "SPEI 77"))
    assert code(duplicate) == DUPLICATE
    assert duplicate.json()["details"] == {"payment_id": first.json()["id"]}
    # E3: trimmed and ignoring case.
    assert code(pay(client, finance, period, body("5.00", "  spei 77 "))) == DUPLICATE
    # Another method is another payment.
    cash = body("5.00", "SPEI 77", method="cash")
    assert code(pay(client, finance, period, cash)) == CREATED
    # E2: a reversed payment frees the pair.
    assert code(reverse(client, owner, first.json()["id"])) == CREATED
    again_after = pay(client, finance, period, body("12.00", "spei 77"))
    assert code(again_after) == CREATED
    assert paid_sum(url, period) == Decimal("17.00")


def test_e2_a_duplicate_on_another_period_is_another_payment(
    module_seeded_url, client, owner, finance, period
):
    url = module_seeded_url
    other_tutor = create_user(url, roles=(TUTOR,))
    other = finalized_period(url, client, owner, finance, other_tutor)

    assert code(pay(client, finance, period, body("10.00", "SPEI 1"))) == CREATED
    assert code(pay(client, finance, other, body("10.00", "SPEI 1"))) == CREATED


# --- E4: the period's status, a 0.00 period, adjustment lines -----


def test_e4_only_a_finalized_period_takes_a_payment(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    period = worked_period(url, client, owner, tutor)

    response = pay(client, finance, period)
    assert code(response) == NOT_FINALIZED
    assert response.json()["details"] == {"status": "open"}
    assert code(prepare(client, finance, period)) == OK
    response = pay(client, finance, period)
    assert (code(response), response.json()["details"]) == (
        NOT_FINALIZED,
        {"status": "prepared"},
    )
    assert code(return_period(client, owner, period)) == OK
    response = pay(client, finance, period)
    assert (code(response), response.json()["details"]) == (
        NOT_FINALIZED,
        {"status": "open"},
    )
    assert payments_of(url, period) == []
    assert detail(client, finance, period)["paid_state"] is None
    assert detail(client, finance, period)["paid_total"] is None


def test_e4_e8_a_carried_forward_period_is_paid_at_0_and_refuses_any_payment(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    period = worked_period(url, client, owner, tutor)
    add_item(url, tutor.id, add_pay_type(url, direction="deduction"), "200.00")
    assert code(carry(client, finance, period)) == CREATED
    locked(client, url, finance, owner, period)
    assert detail(client, owner, period)["total_amount"] == "0.00"

    assert (detail(client, owner, period)["paid_state"]) == "paid"
    assert listed_row(client, owner, period)["paid_total"] == "0.00"
    response = pay(client, finance, period, body("0.01"))
    assert code(response) == EXCEEDS
    assert response.json()["details"] == {"remaining": "0.00"}


def test_e4_adjustment_lines_are_in_the_total_the_cap_reads(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    period = worked_period(url, client, owner, tutor)
    assert code(manual(client, finance, tutor.id, "25.00")) == CREATED
    locked(client, url, finance, owner, period)

    assert code(pay(client, finance, period, body("135.01"))) == EXCEEDS
    paid = pay(client, finance, period, body("135.00"))
    assert code(paid) == CREATED
    assert paid.json()["paid_state"] == "paid"


def test_the_paid_state_filter(module_seeded_url, client, owner, finance, tutor):
    url = module_seeded_url
    unpaid = finalized_period(url, client, owner, finance, tutor)
    partly_tutor = create_user(url, roles=(TUTOR,))
    partly = finalized_period(url, client, owner, finance, partly_tutor)
    paid_tutor_ = create_user(url, roles=(TUTOR,))
    paid = finalized_period(url, client, owner, finance, paid_tutor_)
    open_period = paid_tutor(url, create_user(url, roles=(TUTOR,)).id)
    assert code(pay(client, finance, partly, body("10.00"))) == CREATED
    assert code(pay(client, finance, paid, body("110.00"))) == CREATED

    def ids(state: str) -> set[str]:
        client.cookies.clear()
        response = client.get(
            ROOT, params={"paid_state": state}, headers=signed_headers(owner.cookie)
        )
        assert response.status_code == 200, response.text
        return {item["id"] for item in response.json()["items"]}

    assert ids("unpaid") == {str(unpaid)}
    assert ids("partially_paid") == {str(partly)}
    assert ids("paid") == {str(paid)}
    assert listed_row(client, owner, open_period)["paid_state"] is None
    client.cookies.clear()
    refused = client.get(
        ROOT, params={"paid_state": "nope"}, headers=signed_headers(owner.cookie)
    )
    assert refused.status_code == 422


def test_d2_the_list_reads_the_paid_total_in_its_own_statement(
    module_seeded_url, owner
):
    url = module_seeded_url
    principal = principal_of(owner.user.id, OWNER)

    def page(session: Any) -> Any:
        return payroll_workflow.list_periods(
            session,
            principal,
            page=OffsetParams(limit=25, offset=0),
            status=None,
            tutor_user_id=None,
            paid_state="partially_paid",
        )

    def finalized_with_a_payment(count: int) -> None:
        for _ in range(count):
            tutor = create_user(url, roles=(TUTOR,))
            period = add_period_row(url, tutor.id, "11-02", "11-08")
            run_sql(
                url,
                "UPDATE payroll_periods SET status = 'prepared',"
                " prepared_at = now(), prepared_by_user_id = :u WHERE id = :p",
                {"p": period, "u": owner.user.id},
            )
            run_sql(
                url,
                "UPDATE payroll_periods SET status = 'finalized',"
                " finalized_at = now(), finalized_by_user_id = :u,"
                " total_amount = 100 WHERE id = :p",
                {"p": period, "u": owner.user.id},
            )
            run_sql(
                url,
                "INSERT INTO payroll_payments (payroll_period_id, paid_on, amount,"
                " method_code, reference, recorded_by_user_id) VALUES"
                " (:p, '2026-11-20', 40, 'cash', 'R', :u)",
                {"p": period, "u": owner.user.id},
            )

    finalized_with_a_payment(1)
    one, _ = count_statements(url, page)
    finalized_with_a_payment(4)
    many, result = count_statements(url, page)

    assert len(result.items) == 5
    assert {(i.paid_total, i.paid_state) for i in result.items} == {
        (Money("40.00"), "partially_paid")
    }
    assert one == many == 3


# --- C: reversal -----


def test_c_a_reversal_frees_the_cap_and_is_one_per_payment(
    module_seeded_url, client, owner, finance, period
):
    url = module_seeded_url
    paid = pay(client, finance, period, body("110.00"))
    assert paid.json()["paid_state"] == "paid"
    payment = paid.json()["id"]

    response = reverse(client, owner, payment, "  Monto equivocado ")

    assert code(response) == CREATED, response.text
    assert set(response.json()) == REVERSED_KEYS
    assert response.json()["paid_state"] == "unpaid"
    assert response.json()["payroll_payment_id"] == payment
    [row] = reversals_of(url, payment)
    assert (row.reason, row.reversed_by_user_id) == ("Monto equivocado", owner.user.id)
    assert code(reverse(client, owner, payment)) == ALREADY_REVERSED
    assert code(reverse(client, owner, GONE)) == NOT_FOUND
    assert code(pay(client, finance, period, body("110.00", "SPEI 2"))) == CREATED
    assert paid_sum(url, period) == Decimal("110.00")
    assert detail(client, finance, period)["paid_state"] == "paid"


@pytest.mark.parametrize(
    ("reason", "error"),
    [("   ", "text_empty"), ("x" * 1001, "text_too_long"), ("\x00", "text_invalid")],
)
def test_c3_the_reason_is_checked(
    module_seeded_url, client, owner, finance, period, reason, error
):
    payment = pay(client, finance, period).json()["id"]

    response = reverse(client, owner, payment, reason)

    assert response.status_code == 422
    assert error in {e["code"] for e in response.json()["errors"]}
    assert reversals_of(module_seeded_url, payment) == []


def test_c1_the_reversal_needs_a_recent_reauthentication(
    module_seeded_url, client, owner, finance, period, frozen
):
    url = module_seeded_url
    payment = pay(client, finance, period).json()["id"]
    stale = staff(url, client, (OWNER,), reauth=False)

    assert code(reverse(client, stale, payment)) == REAUTH
    frozen.set(NOW + timedelta(minutes=5, seconds=1))
    assert code(reverse(client, owner, payment)) == REAUTH
    frozen.set(NOW + timedelta(minutes=4, seconds=59))
    assert code(reverse(client, owner, payment)) == CREATED
    assert stored_keys(url, stale.user.id) == []


# --- E5: who -----


def test_e5_who_records_reverses_and_reads(
    module_seeded_url, client, owner, finance, period
):
    url = module_seeded_url
    payment = pay(client, finance, period).json()["id"]
    others = {
        role: staff(url, client, (role,))
        for role in (ADMINISTRATOR, ACADEMIC_COORDINATOR, TUTOR)
    }
    finalizer = staff(url, client, (ADMINISTRATOR,), delegations=["payroll.finalize"])
    refunder = staff(
        url, client, (ADMINISTRATOR,), delegations=["finance.refund.approve"]
    )

    for who in (*others.values(), finalizer, refunder):
        assert code(pay(client, who, period)) == DENIED
    for who in (*others.values(), finalizer, finance):
        assert code(reverse(client, who, payment)) == DENIED
    for who in (*others.values(), refunder):
        assert code(listed(client, who, period)) == DENIED
    for who in (owner, finance, finalizer):
        assert listed(client, who, period).status_code == 200
    assert len(payments_of(url, period)) == 1

    response = reverse(client, refunder, payment)
    assert code(response) == CREATED, response.text


def test_e7_a_delegated_administrator_reads_no_open_period(
    module_seeded_url, client, owner, tutor
):
    url = module_seeded_url
    period = worked_period(url, client, owner, tutor)
    finalizer = staff(url, client, (ADMINISTRATOR,), delegations=["payroll.finalize"])

    assert code(listed(client, finalizer, period)) == NOT_FOUND
    assert listed(client, owner, period).json() == []


def test_e5_own_pay_is_refused_but_to_an_owner(
    module_seeded_url, client, owner, finance
):
    url = module_seeded_url
    teller = staff(url, client, (FINANCE_STAFF, TUTOR))
    boss = staff(url, client, (OWNER, TUTOR))
    own = finalized_period(url, client, owner, finance, teller.user)
    boss_period = finalized_period(url, client, owner, finance, boss.user)

    assert code(pay(client, teller, own)) == OWN_PAY
    assert stored_keys(url, teller.user.id) == []
    paid = pay(client, boss, boss_period)
    assert code(paid) == CREATED, paid.text
    assert code(reverse(client, boss, paid.json()["id"])) == CREATED
    # A delegated reverser can't reverse a payment of their own pay.
    reverser = staff(
        url,
        client,
        (ADMINISTRATOR, TUTOR),
        delegations=["finance.refund.approve"],
    )
    theirs = finalized_period(url, client, owner, finance, reverser.user)
    payment = pay(client, finance, theirs).json()["id"]
    assert code(reverse(client, reverser, payment)) == OWN_PAY
    assert reversals_of(url, payment) == []


def test_an_archived_tutor_is_still_paid(
    module_seeded_url, client, owner, finance, tutor, period
):
    run_sql(
        module_seeded_url,
        "UPDATE users SET state = 'archived' WHERE id = :u",
        {"u": tutor.id},
    )

    assert code(pay(client, finance, period)) == CREATED


def test_a_deactivated_method_keeps_its_payment_and_its_reversal(
    module_seeded_url, client, owner, finance, period
):
    url = module_seeded_url
    payment = pay(client, finance, period, body(method="other")).json()["id"]
    run_sql(url, "UPDATE payment_methods SET is_active = false WHERE code = 'other'")
    try:
        assert code(reverse(client, owner, payment)) == CREATED
        refused = pay(client, finance, period, body(reference="X", method="other"))
        assert code(refused) == (422, "payment_method_unavailable")
    finally:
        run_sql(url, "UPDATE payment_methods SET is_active = true WHERE code = 'other'")


# --- E6, B7: no pay in side channels -----


def test_e6_the_audit_masks_and_no_response_key_or_log_holds_an_amount(
    module_seeded_url, client, owner, finance, period, caplog
):
    url = module_seeded_url
    reference, note, reason = "SPEI 4242-77", "Nota privada", "Motivo privado"

    with caplog.at_level(logging.DEBUG):
        paid = pay(client, owner, period, body("98.76", reference, note=note))
        reversed_ = reverse(client, owner, paid.json()["id"], reason)

    assert code(paid) == CREATED
    assert code(reversed_) == CREATED
    payment, reversal = paid.json()["id"], reversed_.json()["id"]
    [payment_audit] = audit_rows(url, [payment])
    [reversal_audit] = audit_rows(url, [reversal])
    assert payment_audit.action == reversal_audit.action == "create"
    assert payment_audit.actor_user_id == owner.user.id
    for masked in ("amount", "reference", "note"):
        assert payment_audit.changes[masked]["new"] == "[masked]"
    assert reversal_audit.changes["reason"]["new"] == "[masked]"
    stored = stored_keys(url, owner.user.id)
    assert {frozenset(key.response_body) for key in stored} == {
        frozenset(RECORDED_KEYS),
        frozenset(REVERSED_KEYS),
    }
    texts = [
        paid.text,
        reversed_.text,
        caplog.text,
        *(json.dumps(key.response_body) for key in stored),
        *(json.dumps(a.changes, default=str) for a in (payment_audit, reversal_audit)),
    ]
    for text in texts:
        for secret in ("98.76", reference, note, reason):
            assert secret not in text, (secret, text)
    events = run_sql(
        url,
        "SELECT action, details FROM audit_events WHERE record_id = :p"
        " AND action LIKE 'payroll.%'",
        {"p": period},
    )
    assert all("98.76" not in json.dumps(e.details) for e in events)


# --- E7: append-only -----


def test_e7_both_tables_are_append_only(
    module_seeded_url, client, owner, finance, period
):
    url = module_seeded_url
    payment = pay(client, finance, period).json()["id"]
    reversal = reverse(client, owner, payment).json()["id"]

    for statement, row in (
        ("UPDATE payroll_payments SET amount = 1 WHERE id = :id", payment),
        ("DELETE FROM payroll_payments WHERE id = :id", payment),
        ("UPDATE payroll_payment_reversals SET reason = 'x' WHERE id = :id", reversal),
        ("DELETE FROM payroll_payment_reversals WHERE id = :id", reversal),
    ):
        with pytest.raises(DBAPIError) as caught:
            run_sql(url, statement, {"id": row})
        assert getattr(caught.value.orig, "sqlstate", "") == "42501", statement
