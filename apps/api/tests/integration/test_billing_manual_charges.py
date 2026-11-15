"""Manual charges through the real app (#100 F1; C1, R7, E1).

Success (the due date as entered, no family offset even with
``due_offset_days > 0``; no ``charge_discounts``; billing off still posts),
``student_not_in_family`` (another family's student, a student without a
family), the account created on first use, every R7 validation, the 366-day
horizon, a moved-out student's old charge staying on the old account, and
the audit events.

The clock is frozen at noon on 2031-03-10 (center zone) for the module."""

import json
import uuid
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import Money
from app.modules.billing import service as billing
from app.modules.identity.permissions import FINANCE_STAFF
from tests.conftest import FrozenClock, frozen_clock
from tests.integration import corrections_support as cs
from tests.integration import package_support as pks
from tests.integration import payment_support as ps
from tests.integration.auth_support import auth_client
from tests.integration.conftest import run_sql
from tests.integration.lifecycle_support import code_of, events, in_uow

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
    return cs.staff(module_seeded_url, client, (FINANCE_STAFF,), reauth=False)


def post(client: TestClient, cashier: cs.Staff, family: object, body: Any) -> Any:
    return cs.charge_post(client, cashier.cookie, family, body)


def charges_of(url: str, account: object) -> list[Any]:
    return run_sql(
        url,
        "SELECT * FROM charges WHERE billing_account_id = :a ORDER BY posted_at, id",
        {"a": account},
    )


def test_a_manual_charge_is_posted_as_entered(module_seeded_url, client, cashier):
    url = module_seeded_url
    fam, account, [student] = cs.new_account(url)
    run_sql(
        url,
        "UPDATE billing_accounts SET due_offset_days = 7, version = version + 1"
        " WHERE id = :a",
        {"a": account},
    )
    due = TODAY + timedelta(days=20)

    body = cs.created(
        post(
            client,
            cashier,
            fam,
            cs.charge_body(
                student,
                "350.00",
                description="  Libro de lectura  ",
                service_date=(TODAY - timedelta(days=3)).isoformat(),
                due_date=due.isoformat(),
            ),
        )
    )

    charge = body["charge"]
    assert charge["kind"] == "manual"
    assert charge["description"] == "Libro de lectura"
    assert (charge["list_amount"], charge["discount_amount"], charge["amount"]) == (
        "350.00",
        "0.00",
        "350.00",
    )
    assert charge["due_date"] == charge["original_due_date"] == due.isoformat()
    assert charge["service_date"] == (TODAY - timedelta(days=3)).isoformat()
    assert (charge["net"], charge["open"], charge["status"]) == (
        "350.00",
        "350.00",
        "pending",
    )
    assert charge["student"]["id"] == str(student)
    assert charge["posted_by_user_id"] == str(cashier.user.id)
    [row] = charges_of(url, account)
    assert (row.price_version_id, row.custom_price_id, row.education_level_id) == (
        None,
        None,
        None,
    )
    assert (
        run_sql(
            url, "SELECT id FROM charge_discounts WHERE charge_id = :c", {"c": row.id}
        )
        == []
    )
    [event] = events(url, record_type="charges", record_id=row.id)
    assert (event.action, event.actor_user_id) == ("create", cashier.user.id)
    assert "Libro de lectura" in json.dumps(event.changes, ensure_ascii=False)
    cs.assert_invariants(url, account)


def test_billing_off_still_posts_and_the_due_date_may_precede_the_service(
    module_seeded_url, client, cashier
):
    url = module_seeded_url
    pks.set_floor(url, None)
    fam, account, [student] = cs.new_account(url)

    body = cs.created(
        post(
            client,
            cashier,
            fam,
            cs.charge_body(
                student,
                service_date=(TODAY + timedelta(days=30)).isoformat(),
                due_date=(TODAY - timedelta(days=5)).isoformat(),
            ),
        )
    )

    assert body["charge"]["status"] == "overdue"
    assert len(charges_of(url, account)) == 1


def test_the_account_is_created_with_the_first_charge(
    module_seeded_url, client, cashier
):
    url = module_seeded_url
    fam, [student] = ps.family(url)
    assert ps.account_of(url, fam) is None

    cs.created(post(client, cashier, fam, cs.charge_body(student)))

    account = ps.account_of(url, fam)
    assert account is not None
    [event] = events(url, record_type="billing_accounts", record_id=account)
    assert (event.action, event.actor_user_id) == ("create", cashier.user.id)


def test_an_inactive_student_may_still_be_charged(module_seeded_url, client, cashier):
    url = module_seeded_url
    fam, account, [student] = cs.new_account(url)
    run_sql(
        url,
        "UPDATE students SET is_active = false, version = version + 1 WHERE id = :s",
        {"s": student},
    )

    cs.created(post(client, cashier, fam, cs.charge_body(student)))

    assert len(charges_of(url, account)) == 1


def test_another_familys_student_or_one_without_a_family_is_refused(
    module_seeded_url, client, cashier
):
    url = module_seeded_url
    fam, account, _ = cs.new_account(url)
    _, [other] = ps.family(url)

    async def _loner(session: AsyncSession) -> uuid.UUID:
        from tests.factories.families import persist_student

        return (await persist_student(session, contacts=0)).id

    loner = in_uow(url, _loner)

    for student in (other, loner, uuid.uuid4()):
        response = post(client, cashier, fam, cs.charge_body(student))
        assert code_of(response) == (422, "student_not_in_family"), student
        assert response.json()["errors"] == [
            {
                "path": ["body", "student_id"],
                "code": "student_not_in_family",
                "message": "Choose a student of this family.",
            }
        ]
    assert charges_of(url, account) == []


def test_an_unknown_family_is_404(client, cashier):
    response = post(client, cashier, uuid.uuid4(), cs.charge_body(uuid.uuid4()))

    assert code_of(response) == (404, "not_found")


@pytest.mark.parametrize(
    ("override", "path", "code"),
    [
        ({"amount": "0.00"}, "amount", "amount_not_positive"),
        ({"amount": "-5.00"}, "amount", "amount_not_positive"),
        ({"amount": "5.005"}, "amount", "money_precision"),
        ({"amount": "1000000000000.00"}, "amount", "money_out_of_range"),
        ({"description": "  "}, "description", "string_too_short"),
        ({"description": "x" * 201}, "description", "string_too_long"),
        ({"service_date": "1999-12-31"}, "service_date", "date_out_of_range"),
        ({"due_date": "1999-12-31"}, "due_date", "date_out_of_range"),
        (
            {"price_version_id": str(uuid.uuid4())},
            "price_version_id",
            "extra_forbidden",
        ),
    ],
)
def test_every_body_rule(module_seeded_url, client, cashier, override, path, code):
    url = module_seeded_url
    fam, account, [student] = cs.new_account(url)

    response = post(client, cashier, fam, cs.charge_body(student, **override))

    assert code_of(response) == (422, "validation_error")
    assert [(e["path"], e["code"]) for e in response.json()["errors"]] == [
        (["body", path], code)
    ]
    assert charges_of(url, account) == []


def test_dates_more_than_366_days_ahead_are_refused(module_seeded_url, client, cashier):
    url = module_seeded_url
    fam, account, [student] = cs.new_account(url)
    limit = TODAY + timedelta(days=366)

    late = post(
        client,
        cashier,
        fam,
        cs.charge_body(
            student,
            service_date=(limit + timedelta(days=1)).isoformat(),
            due_date=(limit + timedelta(days=1)).isoformat(),
        ),
    )
    edge = post(
        client,
        cashier,
        fam,
        cs.charge_body(
            student, service_date=limit.isoformat(), due_date=limit.isoformat()
        ),
    )

    assert code_of(late) == (422, "validation_error")
    assert [(e["path"], e["code"]) for e in late.json()["errors"]] == [
        (["body", "service_date"], "date_out_of_range"),
        (["body", "due_date"], "date_out_of_range"),
    ]
    assert edge.status_code == 201, edge.text
    assert len(charges_of(url, account)) == 1


def test_a_moved_out_students_old_charge_stays_on_the_old_account(
    module_seeded_url, client, cashier
):
    url = module_seeded_url
    fam, account, [student] = cs.new_account(url)
    old = cs.created(post(client, cashier, fam, cs.charge_body(student)))
    new_family = pks.move_to_new_family(url, student)

    refused = post(client, cashier, fam, cs.charge_body(student))
    moved = cs.created(post(client, cashier, new_family, cs.charge_body(student)))

    assert code_of(refused) == (422, "student_not_in_family")
    [kept] = charges_of(url, account)
    assert str(kept.id) == old["charge"]["id"]
    new_account = ps.account_of(url, new_family)
    assert [str(c.id) for c in charges_of(url, new_account)] == [moved["charge"]["id"]]


def test_the_primitive_needs_an_actor(module_seeded_url):
    url = module_seeded_url
    fam, _, [student] = cs.new_account(url)

    with pytest.raises(ValueError, match="actor"):
        in_uow(
            url,
            lambda s: billing.post_manual_charge(
                s,
                billing.ManualChargeDraft(
                    family_id=fam,
                    student_id=student,
                    description="x",
                    amount=Money("1.00"),
                    service_date=TODAY,
                    due_date=TODAY,
                ),
            ),
        )
