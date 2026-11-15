"""Recording a payment through the real app (#96 F1, F7; C1-C3, D1, E1;
M2, M3, M5): each allocation mode, partial and multi-child payments,
overpayment as account credit, the oldest-first order, inactive and
account-less families, every C2/M5/B4 refusal with nothing written, the
response shape, the audit events and the #364 balances afterwards.

The clock is frozen at noon on 2031-03-10 (center zone) for the module, so
the center's today is 2031-03-10."""

import uuid
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import Money
from app.modules.billing import service as billing
from app.modules.billing.schemas import AdjustmentDraft
from app.modules.families.models import FamilyStudent
from app.modules.identity.permissions import FINANCE_STAFF
from tests.conftest import FrozenClock, frozen_clock
from tests.integration import payment_support as ps
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import get
from tests.integration.conftest import run_sql
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, events, in_uow

TODAY, YESTERDAY, TOMORROW = ps.TODAY, ps.YESTERDAY, ps.TOMORROW


@pytest.fixture(scope="module")
def frozen() -> Iterator[FrozenClock]:
    with frozen_clock(ps.NOW) as clock:
        yield clock


@pytest.fixture(scope="module")
def client(module_seeded_url: str, frozen: FrozenClock) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def cashier(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(FINANCE_STAFF,))


@pytest.fixture(scope="module")
def staff(client: TestClient, cashier: CreatedUser) -> Any:
    return sign_in(client, cashier.username)


def created(response: Any) -> dict[str, Any]:
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def allocated(body: dict[str, Any]) -> list[tuple[str, str]]:
    return [(a["charge_id"], a["amount"]) for a in body["allocations"]]


def key_rows(url: str, key: str) -> list[Any]:
    return run_sql(url, "SELECT id FROM idempotency_keys WHERE key = :k", {"k": key})


# --- Modes and amounts -----------------------------------------------------------


def test_oldest_first_across_two_children_partially_pays_the_last_charge(
    module_seeded_url, client, staff, cashier
):
    url = module_seeded_url
    fam, [s1, s2] = ps.family(url, 2)
    c_late, c_due, c_next = ps.charges(
        url,
        (s2, "100.00", YESTERDAY),
        (s1, "200.00", TODAY),
        (s1, "50.00", TOMORROW),
    )

    body = created(ps.pay(client, staff, fam, ps.payment_body("250.00")))

    assert allocated(body) == [(str(c_late), "100.00"), (str(c_due), "150.00")]
    assert [a["student_id"] for a in body["allocations"]] == [str(s2), str(s1)]
    assert (body["unallocated_amount"], body["account_credit_after"]) == (
        "0.00",
        "0.00",
    )
    payment = body["payment"]
    assert payment["amount"] == "250.00"
    assert payment["received_on"] == TODAY.isoformat()
    assert payment["payment_method_code"] == "cash"
    assert payment["recorded_by_user_id"] == str(cashier.id)
    assert (
        payment["allocated_amount"],
        payment["refunded_amount"],
        payment["unallocated_amount"],
        payment["is_reversed"],
    ) == ("250.00", "0.00", "0.00", False)
    assert ps.valid_allocated(url, c_next) == Money.zero()
    statuses = {
        c["id"]: c["status"]
        for c in get(client, staff, ps.account_path(fam, "/charges")).json()["items"]
    }
    assert statuses == {
        str(c_late): "paid",
        str(c_due): "partially_paid",
        str(c_next): "pending",
    }
    ps.assert_invariants(url, ps.account_of(url, fam))


def test_explicit_lines_across_two_children(module_seeded_url, client, staff):
    url = module_seeded_url
    fam, [s1, s2] = ps.family(url, 2)
    c1, c2, c3 = ps.charges(
        url, (s1, "100.00", YESTERDAY), (s2, "80.00", TODAY), (s2, "60.00", TODAY)
    )

    body = created(
        ps.pay(
            client,
            staff,
            fam,
            ps.payment_body("120.00", "explicit", [(c3, "60.00"), (c1, "40.00")]),
        )
    )

    assert allocated(body) == [(str(c3), "60.00"), (str(c1), "40.00")]
    assert (body["unallocated_amount"], body["account_credit_after"]) == (
        "20.00",
        "20.00",
    )
    assert ps.valid_allocated(url, c2) == Money.zero()
    ps.assert_invariants(url, ps.account_of(url, fam))


def test_an_overpayment_leaves_the_rest_as_account_credit(
    module_seeded_url, client, staff
):
    url = module_seeded_url
    fam, [s1] = ps.family(url)
    [c1] = ps.charges(url, (s1, "300.00", TODAY))

    body = created(ps.pay(client, staff, fam, ps.payment_body("500.00")))

    assert allocated(body) == [(str(c1), "300.00")]
    assert (body["unallocated_amount"], body["account_credit_after"]) == (
        "200.00",
        "200.00",
    )
    summary = get(client, staff, ps.account_path(fam)).json()
    assert (summary["outstanding"], summary["account_credit"]) == ("0.00", "200.00")
    assert summary["net_balance"] == "-200.00"


def test_mode_none_keeps_everything_as_credit(module_seeded_url, client, staff):
    url = module_seeded_url
    fam, [s1] = ps.family(url)
    [c1] = ps.charges(url, (s1, "300.00", YESTERDAY))

    body = created(ps.pay(client, staff, fam, ps.payment_body("120.00", "none")))

    assert body["allocations"] == []
    assert (body["unallocated_amount"], body["account_credit_after"]) == (
        "120.00",
        "120.00",
    )
    assert ps.valid_allocated(url, c1) == Money.zero()


def test_oldest_first_with_nothing_open_is_all_credit(module_seeded_url, client, staff):
    url = module_seeded_url
    fam, _ = ps.family(url)

    body = created(ps.pay(client, staff, fam, ps.payment_body("75.00")))

    assert body["allocations"] == []
    assert body["account_credit_after"] == "75.00"


def test_a_moved_out_students_charge_is_still_allocatable(
    module_seeded_url, client, staff
):
    url = module_seeded_url
    fam, [s1, s2] = ps.family(url, 2)
    c_moved, c_stays = ps.charges(url, (s2, "90.00", YESTERDAY), (s1, "50.00", TODAY))

    async def _move_out(session: AsyncSession) -> None:
        row = await session.scalar(
            select(FamilyStudent).where(FamilyStudent.student_id == s2)
        )
        await session.delete(row)

    in_uow(url, _move_out)

    body = created(ps.pay(client, staff, fam, ps.payment_body("140.00")))

    assert allocated(body) == [(str(c_moved), "90.00"), (str(c_stays), "50.00")]


def test_zero_void_and_paid_charges_are_skipped(module_seeded_url, client, staff):
    url = module_seeded_url
    fam, [s1] = ps.family(url)
    c_zero, c_void, c_paid, c_open = ps.charges(
        url,
        (s1, "0.00", YESTERDAY - timedelta(days=3)),
        (s1, "40.00", YESTERDAY - timedelta(days=2)),
        (s1, "30.00", YESTERDAY - timedelta(days=1)),
        (s1, "70.00", TODAY),
    )

    async def _void(session: AsyncSession) -> None:
        await billing.post_adjustment(
            session,
            AdjustmentDraft(
                charge_id=c_void,
                kind="cancellation_credit",
                amount=Money("-40.00"),
                source_key=f"test:{uuid.uuid4()}",
            ),
        )

    in_uow(url, _void)
    pays_c_paid = ps.payment_body("30.00", "explicit", [(c_paid, "30.00")])
    created(ps.pay(client, staff, fam, pays_c_paid))

    body = created(ps.pay(client, staff, fam, ps.payment_body("100.00")))

    assert allocated(body) == [(str(c_open), "70.00")]
    assert body["unallocated_amount"] == "30.00"
    assert ps.valid_allocated(url, c_zero) == Money.zero()
    assert ps.valid_allocated(url, c_void) == Money.zero()


def test_the_oldest_first_order_is_due_date_then_service_date_then_posting(
    module_seeded_url, client, staff
):
    """M2: an overdue charge with a later service date goes before a
    pending one; equal due dates fall back to the service date, then the
    posting order."""
    url = module_seeded_url
    fam, [s1, s2] = ps.family(url, 2)
    [pending_early_service] = ps.charges(
        url, (s1, "10.00", TOMORROW), service=TODAY - timedelta(days=20)
    )
    [overdue_late_service] = ps.charges(
        url, (s2, "10.00", YESTERDAY), service=TODAY + timedelta(days=5)
    )
    [later_service] = ps.charges(
        url, (s1, "10.00", TODAY), service=TODAY - timedelta(days=1)
    )
    [earlier_service] = ps.charges(
        url, (s2, "10.00", TODAY), service=TODAY - timedelta(days=2)
    )
    first_posted, second_posted = ps.charges(
        url, (s1, "10.00", TODAY), (s2, "10.00", TODAY), service=TODAY
    )

    body = created(ps.pay(client, staff, fam, ps.payment_body("60.00")))

    assert [a["charge_id"] for a in body["allocations"]] == [
        str(overdue_late_service),
        str(earlier_service),
        str(later_service),
        str(first_posted),
        str(second_posted),
        str(pending_early_service),
    ]


def test_an_inactive_family_may_pay(module_seeded_url, client, staff):
    url = module_seeded_url
    fam, [s1] = ps.family(url)
    [c1] = ps.charges(url, (s1, "60.00", TODAY))
    run_sql(url, "UPDATE families SET is_active = false WHERE id = :f", {"f": fam})

    body = created(ps.pay(client, staff, fam, ps.payment_body("60.00")))

    assert allocated(body) == [(str(c1), "60.00")]


def test_a_family_without_an_account_gets_one_with_its_first_payment(
    module_seeded_url, client, staff, cashier
):
    url = module_seeded_url
    fam, _ = ps.family(url)
    assert ps.account_of(url, fam) is None

    body = created(ps.pay(client, staff, fam, ps.payment_body("40.00")))

    account = ps.account_of(url, fam)
    assert account is not None
    [payment] = ps.payments_of(url, account)
    assert str(payment.id) == body["payment"]["id"]
    [event] = events(url, record_type="billing_accounts", record_id=account)
    assert (event.action, event.actor_user_id) == ("create", cashier.id)


def test_backdating_is_allowed_and_text_is_trimmed(module_seeded_url, client, staff):
    url = module_seeded_url
    fam, _ = ps.family(url)

    body = created(
        ps.pay(
            client,
            staff,
            fam,
            ps.payment_body(
                "10.00",
                received_on=(TODAY - timedelta(days=40)).isoformat(),
                payment_method_code="transfer",
                reference="  SPEI-123 ",
                payer_name="   ",
                note=" Pagó en ventanilla ",
            ),
        )
    )

    payment = body["payment"]
    assert payment["received_on"] == (TODAY - timedelta(days=40)).isoformat()
    assert (payment["reference"], payment["payer_name"], payment["note"]) == (
        "SPEI-123",
        None,
        "Pagó en ventanilla",
    )
    assert payment["payment_method_code"] == "transfer"


# --- Refusals: nothing written -----------------------------------------------------


def _nothing_written(url: str, fam: uuid.UUID, key: str) -> None:
    account = ps.account_of(url, fam)
    if account is not None:
        assert ps.payments_of(url, account) == []
        assert ps.allocations_of(url, account) == []
    assert key_rows(url, key) == []


def test_an_unknown_family_is_404(module_seeded_url, client, staff):
    key = ps.new_key()

    response = ps.pay(client, staff, ps.MISSING, ps.payment_body(), key)

    assert code_of(response) == (404, "not_found")
    assert key_rows(module_seeded_url, key) == []


@pytest.mark.parametrize("method", ["inactive", "unknown"])
def test_an_unavailable_method_is_refused(module_seeded_url, client, staff, method):
    url = module_seeded_url
    fam, _ = ps.family(url)
    code = "nope"
    if method == "inactive":
        code = f"t_{uuid.uuid4().hex[:12]}"
        run_sql(
            url,
            "INSERT INTO payment_methods (code, is_active) VALUES (:c, false)",
            {"c": code},
        )
    key = ps.new_key()

    response = ps.pay(
        client, staff, fam, ps.payment_body(payment_method_code=code), key
    )

    assert code_of(response) == (422, "payment_method_unavailable")
    assert [e["path"] for e in response.json()["errors"]] == [
        ["body", "payment_method_code"]
    ]
    _nothing_written(url, fam, key)
    assert ps.account_of(url, fam) is None


def test_a_payment_dated_tomorrow_is_refused_and_today_is_fine(
    module_seeded_url, client, staff
):
    url = module_seeded_url
    fam, _ = ps.family(url)
    key = ps.new_key()

    response = ps.pay(
        client, staff, fam, ps.payment_body(received_on=TOMORROW.isoformat()), key
    )

    assert code_of(response) == (422, "received_on_in_future")
    assert [e["path"] for e in response.json()["errors"]] == [["body", "received_on"]]
    _nothing_written(url, fam, key)
    created(ps.pay(client, staff, fam, ps.payment_body(received_on=TODAY.isoformat())))


def test_a_charge_of_another_family_is_not_found_on_its_line(
    module_seeded_url, client, staff
):
    url = module_seeded_url
    fam, [s1] = ps.family(url)
    other, [o1] = ps.family(url)
    [mine] = ps.charges(url, (s1, "50.00", TODAY))
    [theirs] = ps.charges(url, (o1, "50.00", TODAY))
    key = ps.new_key()

    response = ps.pay(
        client,
        staff,
        fam,
        ps.payment_body("100.00", "explicit", [(mine, "50.00"), (theirs, "50.00")]),
        key,
    )

    assert code_of(response) == (422, "allocation_charge_not_found")
    assert [e["path"] for e in response.json()["errors"]] == [
        ["body", "allocation", "lines", 1, "charge_id"]
    ]
    _nothing_written(url, fam, key)
    assert ps.valid_allocated(url, theirs) == Money.zero()


def test_a_line_above_the_open_amount_is_409_and_writes_nothing(
    module_seeded_url, client, staff
):
    url = module_seeded_url
    fam, [s1] = ps.family(url)
    c1, c2 = ps.charges(url, (s1, "50.00", TODAY), (s1, "80.00", TODAY))
    partial = ps.payment_body("30.00", "explicit", [(c2, "30.00")])
    created(ps.pay(client, staff, fam, partial))
    key = ps.new_key()

    response = ps.pay(
        client,
        staff,
        fam,
        ps.payment_body("100.00", "explicit", [(c1, "40.00"), (c2, "50.01")]),
        key,
    )

    assert code_of(response) == (409, "allocation_exceeds_open")
    assert response.json()["details"] == {"charge_id": str(c2), "open": "50.00"}
    assert len(ps.payments_of(url, ps.account_of(url, fam))) == 1
    assert key_rows(url, key) == []
    assert ps.valid_allocated(url, c1) == Money.zero()


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"amount": "0.00"}, "amount_not_positive"),
        ({"amount": "1000000000000.00"}, "money_out_of_range"),
        ({"received_on": "1999-12-31"}, "date_out_of_range"),
        ({"note": "x" * 501}, "string_too_long"),
        ({"allocation": {"mode": "explicit"}}, "allocation_lines_required"),
    ],
)
def test_body_errors_are_422_validation_error(
    module_seeded_url, client, staff, overrides, code
):
    url = module_seeded_url
    fam, _ = ps.family(url)
    key = ps.new_key()

    response = ps.pay(client, staff, fam, {**ps.payment_body(), **overrides}, key)

    assert code_of(response) == (422, "validation_error")
    assert [e["code"] for e in response.json()["errors"]] == [code]
    _nothing_written(url, fam, key)


def test_explicit_lines_above_the_payment_are_422(module_seeded_url, client, staff):
    url = module_seeded_url
    fam, [s1] = ps.family(url)
    c1, c2 = ps.charges(url, (s1, "50.00", TODAY), (s1, "80.00", TODAY))

    response = ps.pay(
        client,
        staff,
        fam,
        ps.payment_body("60.00", "explicit", [(c1, "50.00"), (c2, "10.01")]),
    )

    assert code_of(response) == (422, "validation_error")
    [error] = response.json()["errors"]
    assert (error["path"], error["code"]) == (
        ["body", "allocation"],
        "allocation_exceeds_payment",
    )


# --- Audit (D1) and the #364 reads (E1) --------------------------------------------


def test_each_row_has_one_create_event_by_the_caller(
    module_seeded_url, client, staff, cashier
):
    url = module_seeded_url
    fam, [s1] = ps.family(url)
    c1, c2 = ps.charges(url, (s1, "50.00", YESTERDAY), (s1, "80.00", TODAY))

    body = created(ps.pay(client, staff, fam, ps.payment_body("100.00")))

    payment_events = events(
        url, record_type="payments", record_id=uuid.UUID(body["payment"]["id"])
    )
    assert [(e.action, e.actor_user_id) for e in payment_events] == [
        ("create", cashier.id)
    ]
    for allocation in body["allocations"]:
        allocation_events = events(
            url,
            record_type="payment_allocations",
            record_id=uuid.UUID(allocation["id"]),
        )
        assert [(e.action, e.actor_user_id) for e in allocation_events] == [
            ("create", cashier.id)
        ]
    rows = ps.allocations_of(url, ps.account_of(url, fam))
    assert {r.created_by_user_id for r in rows} == {cashier.id}
    assert {str(r.charge_id) for r in rows} == {str(c1), str(c2)}


def test_the_account_reads_show_the_payment(module_seeded_url, client, staff):
    url = module_seeded_url
    fam, [s1, s2] = ps.family(url, 2)
    c1, c2 = ps.charges(url, (s1, "100.00", YESTERDAY), (s2, "100.00", TODAY))
    before = get(client, staff, ps.account_path(fam)).json()
    assert (before["outstanding"], before["overdue"]) == ("200.00", "100.00")

    body = created(ps.pay(client, staff, fam, ps.payment_body("150.00")))

    summary = get(client, staff, ps.account_path(fam)).json()
    assert (summary["outstanding"], summary["account_credit"]) == ("50.00", "0.00")
    assert (summary["overdue"], summary["partially_paid"]) == ("0.00", "50.00")
    assert summary["net_balance"] == "50.00"
    per_student = {s["student"]["id"]: s["outstanding"] for s in summary["students"]}
    assert per_student == {str(s1): "0.00", str(s2): "50.00"}
    ledger = get(client, staff, ps.account_path(fam, "/ledger")).json()["items"]
    payment_rows = [e for e in ledger if e["entry_type"] == "payment"]
    allocation_rows = [e for e in ledger if e["entry_type"] == "allocation"]
    assert [(e["payment_id"], e["balance_effect"]) for e in payment_rows] == [
        (body["payment"]["id"], "-150.00")
    ]
    effects = sorted((e["charge_id"], e["balance_effect"]) for e in allocation_rows)
    assert effects == sorted([(str(c1), "0.00"), (str(c2), "0.00")])
    total = Money.total(Money(e["balance_effect"]) for e in ledger)
    assert total == Money(summary["net_balance"])
