"""Changes to a posted charge's due date (#97 D1-D3, B1, B2; I2; N5 (a)).

``POST`` and ``GET /api/v1/families/{family_id}/billing-account/charges/
{charge_id}/due-date-changes``: the check order with every 404/409/422,
successive changes (the latest wins, ``previous_due_date`` chained),
append-only rows, the audit event, the statuses at the center-local
boundary, the #364 charges route (``due_date``, ``original_due_date``,
status filter) and the #96 oldest-first reorder. The clock is frozen at
noon on :data:`TODAY` in the center's zone unless a test moves it.
"""

import uuid
from collections.abc import Iterator
from datetime import date, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError

from app.core.money import Money
from app.modules.billing import service as billing
from app.modules.billing.schemas import AllocationRequest, PaymentDraft
from app.modules.identity.permissions import (
    ADMINISTRATOR,
    FINANCE_STAFF,
    OWNER,
)
from tests.conftest import frozen_clock
from tests.integration import due_date_support as dd
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.billing_ledger_support import count_statements
from tests.integration.catalog_support import get, post
from tests.integration.conftest import run_sql
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import events, in_uow

TODAY = dd.TODAY
NOON = dd.local(TODAY, 12)


def path(family: object, charge: object) -> str:
    return (
        f"/api/v1/families/{family}/billing-account/charges/{charge}/due-date-changes"
    )


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def staff(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(FINANCE_STAFF,))


def send(
    client: TestClient,
    user: CreatedUser,
    family: object,
    charge: object,
    body: Any,
    when: datetime = NOON,
) -> Any:
    with frozen_clock(when):
        return post(client, sign_in(client, user.username), path(family, charge), body)


def history(
    client: TestClient, user: CreatedUser, family: object, charge: object
) -> Any:
    with frozen_clock(NOON):
        return get(client, sign_in(client, user.username), path(family, charge))


def charges_page(
    client: TestClient,
    user: CreatedUser,
    family: object,
    when: datetime = NOON,
    **q: Any,
) -> Any:
    with frozen_clock(when):
        response = get(
            client,
            sign_in(client, user.username),
            f"/api/v1/families/{family}/billing-account/charges",
            **q,
        )
    assert response.status_code == 200, response.text
    return response.json()


def body(due: date, reason: str = "Acuerdo con la familia") -> dict[str, str]:
    return {"due_date": due.isoformat(), "reason": reason}


# --- D1: the change ------------------------------------------------------------------


def test_a_change_is_recorded_with_the_status_after_it(
    module_seeded_url, client, staff
):
    url = module_seeded_url
    w = dd.world(url)
    charge = dd.charge(url, w.student, due=TODAY + dd.days(5))

    response = send(
        client, staff, w.family, charge, body(TODAY - dd.days(1), "  Error  ")
    )

    assert response.status_code == 201, response.text
    created = response.json()
    assert created["charge_id"] == str(charge)
    assert created["previous_due_date"] == (TODAY + dd.days(5)).isoformat()
    assert created["due_date"] == (TODAY - dd.days(1)).isoformat()
    assert created["reason"] == "Error"
    assert created["changed_by_user_id"] == str(staff.id)
    assert created["status"] == "overdue"
    [event] = events(
        url, record_type="charge_due_date_changes", record_id=created["id"]
    )
    assert event.action == "create"
    assert event.actor_user_id == staff.id
    assert event.changes["previous_due_date"]["new"] == (TODAY + dd.days(5)).isoformat()
    assert event.changes["due_date"]["new"] == (TODAY - dd.days(1)).isoformat()


def test_successive_changes_chain_and_the_latest_wins(module_seeded_url, client, staff):
    url = module_seeded_url
    w = dd.world(url)
    charge = dd.charge(url, w.student, due=TODAY)

    first = send(client, staff, w.family, charge, body(TODAY + dd.days(10)))
    second = send(client, staff, w.family, charge, body(TODAY + dd.days(3)))

    assert (first.status_code, second.status_code) == (201, 201)
    listed = history(client, staff, w.family, charge)
    assert listed.status_code == 200
    rows = listed.json()
    assert [(r["previous_due_date"], r["due_date"]) for r in rows] == [
        (TODAY.isoformat(), (TODAY + dd.days(10)).isoformat()),
        ((TODAY + dd.days(10)).isoformat(), (TODAY + dd.days(3)).isoformat()),
    ]
    assert "status" not in rows[0]
    [item] = charges_page(client, staff, w.family)["items"]
    assert item["due_date"] == (TODAY + dd.days(3)).isoformat()
    assert item["original_due_date"] == TODAY.isoformat()


def test_the_same_date_is_due_date_unchanged(module_seeded_url, client, staff):
    url = module_seeded_url
    w = dd.world(url)
    charge = dd.charge(url, w.student, due=TODAY)
    assert (
        send(client, staff, w.family, charge, body(TODAY + dd.days(1))).status_code
        == 201
    )

    response = send(client, staff, w.family, charge, body(TODAY + dd.days(1)))

    assert response.status_code == 409
    problem = response.json()
    assert problem["code"] == "due_date_unchanged"
    assert problem["details"] == {"due_date": (TODAY + dd.days(1)).isoformat()}
    # Back to the posted date is a real change.
    assert send(client, staff, w.family, charge, body(TODAY)).status_code == 201


def test_paid_and_void_charges_are_settled(module_seeded_url, client, staff):
    url = module_seeded_url
    w = dd.world(url)
    paid = dd.charge(url, w.student)
    dd.pay(url, w, paid, "100.00")
    voided = dd.charge(url, w.student)
    dd.credit(url, voided, "-100.00")
    partial = dd.charge(url, w.student)
    dd.pay(url, w, partial, "40.00")

    for charge in (paid, voided):
        response = send(client, staff, w.family, charge, body(TODAY + dd.days(9)))
        assert response.status_code == 409, charge
        assert response.json()["code"] == "charge_settled"
    response = send(client, staff, w.family, partial, body(TODAY + dd.days(9)))
    assert response.status_code == 201
    assert response.json()["status"] == "partially_paid"
    count = run_sql(
        url,
        "SELECT count(*) AS n FROM charge_due_date_changes WHERE charge_id = ANY(:ids)",
        {"ids": [paid, voided]},
    )
    assert count[0].n == 0


def test_unknown_family_and_another_familys_charge_are_404(
    module_seeded_url, client, staff
):
    url = module_seeded_url
    w = dd.world(url)
    other = dd.world(url)
    charge = dd.charge(url, w.student)
    no_account = dd.world(url, students=1)

    cases = [
        (uuid.uuid4(), charge),
        (other.family, charge),
        (w.family, uuid.uuid4()),
        (no_account.family, charge),
    ]
    for family, target in cases:
        response = send(client, staff, family, target, body(TODAY + dd.days(1)))
        assert response.status_code == 404, (family, target)
        assert response.json()["code"] == "not_found"
        listed = history(client, staff, family, target)
        assert listed.status_code == 404


@pytest.mark.parametrize(
    ("payload", "location"),
    [
        ({"due_date": "1999-12-31", "reason": "x"}, ["body", "due_date"]),
        ({"due_date": "2100-01-01", "reason": "x"}, ["body", "due_date"]),
        ({"due_date": "2031-03-10", "reason": "   "}, ["body", "reason"]),
        ({"due_date": "2031-03-10", "reason": "x" * 501}, ["body", "reason"]),
        ({"due_date": "2031-03-10"}, ["body", "reason"]),
        ({"reason": "x"}, ["body", "due_date"]),
        ({"due_date": "2031-03-10", "reason": "x", "extra": 1}, ["body", "extra"]),
    ],
)
def test_bad_bodies_are_422_before_the_404(client, staff, payload, location):
    # An unknown family: the body is checked first.
    response = send(client, staff, uuid.uuid4(), uuid.uuid4(), payload)

    assert response.status_code == 422
    problem = response.json()
    assert problem["code"] == "validation_error"
    assert [e["path"] for e in problem["errors"]] == [location]


def test_a_past_date_is_allowed(module_seeded_url, client, staff):
    url = module_seeded_url
    w = dd.world(url)
    charge = dd.charge(url, w.student, due=TODAY)

    response = send(client, staff, w.family, charge, body(date(2000, 1, 1)))

    assert response.status_code == 201
    assert response.json()["status"] == "overdue"


# --- Access (G1) ---------------------------------------------------------------------


@pytest.mark.parametrize("role", [OWNER, ADMINISTRATOR, FINANCE_STAFF])
def test_billing_manage_roles_may_change_and_read(module_seeded_url, client, role):
    url = module_seeded_url
    user = create_user(url, roles=(role,))
    w = dd.world(url)
    charge = dd.charge(url, w.student)

    assert (
        send(client, user, w.family, charge, body(TODAY + dd.days(2))).status_code
        == 201
    )
    assert history(client, user, w.family, charge).status_code == 200


def test_the_familys_guardian_gets_403(module_seeded_url, client, staff):
    url = module_seeded_url
    w = dd.world(url)
    charge = dd.charge(url, w.student)
    send(client, staff, w.family, charge, body(TODAY + dd.days(2), "Nota interna"))
    [username] = run_sql(
        url, "SELECT username FROM users WHERE id = :id", {"id": w.guardians[0]}
    )
    guardian = CreatedUser(id=w.guardians[0], username=username.username)

    response = history(client, guardian, w.family, charge)

    assert response.status_code == 403
    assert "Nota interna" not in response.text
    assert send(client, guardian, w.family, charge, body(TODAY)).status_code == 403


# --- Append-only (A1) ----------------------------------------------------------------


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE charge_due_date_changes SET reason = 'Otra' WHERE id = :id",
        "DELETE FROM charge_due_date_changes WHERE id = :id",
    ],
)
def test_changes_are_append_only(module_seeded_url, statement):
    url = module_seeded_url
    w = dd.world(url)
    charge = dd.charge(url, w.student)
    change = dd.change_due(url, w, charge, TODAY + dd.days(4)).change

    with pytest.raises(DBAPIError) as caught:
        run_sql(url, statement, {"id": change.id})

    assert getattr(caught.value.orig, "sqlstate", "") == "42501"


# --- D3: effects ---------------------------------------------------------------------


def test_statuses_flip_at_the_center_local_boundary(module_seeded_url, client, staff):
    url = module_seeded_url
    w = dd.world(url)
    charge = dd.charge(url, w.student, due=TODAY + dd.days(20))
    dd.change_due(url, w, charge, TODAY)

    # 23:30 local on the due date is 05:30 UTC the next day: not overdue.
    late = dd.local(TODAY, 23, 30)
    [item] = charges_page(client, staff, w.family, late)["items"]
    assert item["status"] == "pending"
    # 00:05 local the next day: overdue, and the status filter follows.
    early = dd.local(TODAY + dd.days(1), 0, 5)
    [item] = charges_page(client, staff, w.family, early)["items"]
    assert item["status"] == "overdue"
    filtered = charges_page(client, staff, w.family, early, status="overdue")
    assert [i["id"] for i in filtered["items"]] == [str(charge)]


def test_moving_an_overdue_charge_forward_reopens_it(module_seeded_url, client, staff):
    url = module_seeded_url
    w = dd.world(url)
    open_charge = dd.charge(url, w.student, due=TODAY - dd.days(3))
    partial = dd.charge(url, w.student, due=TODAY - dd.days(3))
    dd.pay(url, w, partial, "25.00")

    statuses = [
        send(client, staff, w.family, c, body(TODAY)).json()["status"]
        for c in (open_charge, partial)
    ]

    assert statuses == ["pending", "partially_paid"]

    async def _balance(session: Any) -> Any:
        return await billing.account_balance(session, w.account, today=TODAY)

    balance = in_uow(url, _balance)
    assert (balance.pending, balance.partially_paid, balance.overdue) == (
        Money("100.00"),
        Money("75.00"),
        Money("0.00"),
    )


def test_the_oldest_first_order_follows_the_effective_date(module_seeded_url):
    url = module_seeded_url
    w = dd.world(url)
    first = dd.charge(url, w.student, due=TODAY - dd.days(10))
    second = dd.charge(url, w.student, due=TODAY - dd.days(5))
    dd.change_due(url, w, first, TODAY + dd.days(10))

    async def _pay(session: Any) -> Any:
        return await billing.record_payment(
            session,
            PaymentDraft(
                billing_account_id=w.account,
                amount=Money("100.00"),
                received_on=TODAY,
                payment_method_code="cash",
                allocation=AllocationRequest(mode="oldest_first"),
            ),
        )

    with frozen_clock(NOON):
        recorded = in_uow(url, _pay, actor_id=w.actor)

    assert [a.charge_id for a in recorded.allocations] == [second]


def test_the_history_read_is_bounded(module_seeded_url):
    url = module_seeded_url
    w = dd.world(url)
    charge = dd.charge(url, w.student)
    for n in range(1, 6):
        dd.change_due(url, w, charge, TODAY + dd.days(n))

    from app.workflows import billing_accounts

    statements, rows = count_statements(
        url, lambda s: billing_accounts.list_due_date_changes(s, w.family, charge)
    )

    assert statements <= 3
    assert len(rows) == 5
