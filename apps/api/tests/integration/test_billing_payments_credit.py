"""Allocating account credit later through the real app (#96 F2; C4, E2;
M4): the oldest payment's remainder first (one line → several rows), the
default and partial ``amount``, ``insufficient_account_credit`` and
``nothing_to_allocate`` with nothing written, money released by a credit
adjustment allocated again, reversed payments and refunds excluded from
the credit, and the audit events.

The clock is frozen at noon on 2031-03-10 (center zone) for the module."""

import uuid
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import Money
from app.modules.billing import service as billing
from app.modules.billing.schemas import AdjustmentDraft
from app.modules.identity.permissions import ADMINISTRATOR
from tests.conftest import FrozenClock, frozen_clock
from tests.factories import billing as factories
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
def admin(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(ADMINISTRATOR,))


@pytest.fixture(scope="module")
def staff(client: TestClient, admin: CreatedUser) -> Any:
    return sign_in(client, admin.username)


def created(response: Any) -> dict[str, Any]:
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def credit_payment(
    client: TestClient, staff: Any, fam: uuid.UUID, amount: str, days_ago: int
) -> str:
    """A payment kept whole as account credit, received ``days_ago``."""
    body = ps.payment_body(
        amount, "none", received_on=(TODAY - timedelta(days=days_ago)).isoformat()
    )
    payment_id: str = created(ps.pay(client, staff, fam, body))["payment"]["id"]
    return payment_id


def rows(body: dict[str, Any]) -> list[tuple[str, str, str]]:
    return [(a["payment_id"], a["charge_id"], a["amount"]) for a in body["allocations"]]


def test_credit_comes_from_the_oldest_payment_first(module_seeded_url, client, staff):
    url = module_seeded_url
    fam, [s1, s2] = ps.family(url, 2)
    # Recorded newest-received first, so FIFO follows received_on, not entry.
    newer = credit_payment(client, staff, fam, "200.00", days_ago=1)
    older = credit_payment(client, staff, fam, "100.00", days_ago=5)
    c1, c2 = ps.charges(url, (s1, "250.00", YESTERDAY), (s2, "30.00", TODAY))

    body = created(ps.allocate(client, staff, fam, ps.credit_body()))

    assert rows(body) == [
        (older, str(c1), "100.00"),
        (newer, str(c1), "150.00"),
        (newer, str(c2), "30.00"),
    ]
    assert [a["student_id"] for a in body["allocations"]] == [str(s1), str(s1), str(s2)]
    assert (body["allocated_total"], body["account_credit_after"]) == (
        "280.00",
        "20.00",
    )
    account = ps.account_of(url, fam)
    assert ps.account_credit(url, account) == Money("20.00")
    ps.assert_invariants(url, account)


def test_an_amount_caps_an_oldest_first_allocation(module_seeded_url, client, staff):
    url = module_seeded_url
    fam, [s1] = ps.family(url)
    credit_payment(client, staff, fam, "100.00", days_ago=0)
    c1, c2 = ps.charges(url, (s1, "60.00", YESTERDAY), (s1, "60.00", TODAY))

    body = created(ps.allocate(client, staff, fam, ps.credit_body(amount="70.00")))

    assert [(a["charge_id"], a["amount"]) for a in body["allocations"]] == [
        (str(c1), "60.00"),
        (str(c2), "10.00"),
    ]
    assert (body["allocated_total"], body["account_credit_after"]) == (
        "70.00",
        "30.00",
    )


def test_explicit_credit_lines(module_seeded_url, client, staff):
    url = module_seeded_url
    fam, [s1] = ps.family(url)
    credit_payment(client, staff, fam, "100.00", days_ago=0)
    c1, c2 = ps.charges(url, (s1, "60.00", YESTERDAY), (s1, "60.00", TODAY))

    body = created(
        ps.allocate(client, staff, fam, ps.credit_body("explicit", [(c2, "55.00")]))
    )

    assert [(a["charge_id"], a["amount"]) for a in body["allocations"]] == [
        (str(c2), "55.00")
    ]
    assert ps.valid_allocated(url, c1) == Money.zero()
    assert body["account_credit_after"] == "45.00"


@pytest.mark.parametrize("mode", ["oldest_first", "explicit"])
def test_more_than_the_credit_is_refused(module_seeded_url, client, staff, mode):
    url = module_seeded_url
    fam, [s1] = ps.family(url)
    credit_payment(client, staff, fam, "50.00", days_ago=0)
    [c1] = ps.charges(url, (s1, "200.00", TODAY))
    body = (
        ps.credit_body(amount="50.01")
        if mode == "oldest_first"
        else ps.credit_body("explicit", [(c1, "50.01")])
    )

    response = ps.allocate(client, staff, fam, body)

    assert code_of(response) == (409, "insufficient_account_credit")
    assert response.json()["details"] == {"account_credit": "50.00"}
    assert ps.valid_allocated(url, c1) == Money.zero()


def test_a_family_without_an_account_has_nothing_to_allocate(
    module_seeded_url, client, staff
):
    url = module_seeded_url
    fam, _ = ps.family(url)

    response = ps.allocate(client, staff, fam, ps.credit_body())

    assert code_of(response) == (409, "nothing_to_allocate")
    assert ps.account_of(url, fam) is None


def test_no_credit_or_no_open_charge_is_nothing_to_allocate(
    module_seeded_url, client, staff
):
    url = module_seeded_url
    fam, [s1] = ps.family(url)
    [c1] = ps.charges(url, (s1, "80.00", TODAY))

    no_credit = ps.allocate(client, staff, fam, ps.credit_body())
    no_credit_explicit = ps.allocate(
        client, staff, fam, ps.credit_body("explicit", [(c1, "1.00")])
    )
    created(ps.pay(client, staff, fam, ps.payment_body("100.00")))  # pays c1, 20 left
    no_open = ps.allocate(client, staff, fam, ps.credit_body())

    assert code_of(no_credit) == (409, "nothing_to_allocate")
    assert code_of(no_credit_explicit) == (409, "nothing_to_allocate")
    assert code_of(no_open) == (409, "nothing_to_allocate")
    assert len(ps.allocations_of(url, ps.account_of(url, fam))) == 1


def test_unknown_family_and_foreign_charge(module_seeded_url, client, staff):
    url = module_seeded_url
    fam, _ = ps.family(url)
    other, [o1] = ps.family(url)
    credit_payment(client, staff, fam, "50.00", days_ago=0)
    [theirs] = ps.charges(url, (o1, "50.00", TODAY))

    missing = ps.allocate(client, staff, ps.MISSING, ps.credit_body())
    foreign = ps.allocate(
        client, staff, fam, ps.credit_body("explicit", [(theirs, "10.00")])
    )

    assert code_of(missing) == (404, "not_found")
    assert code_of(foreign) == (422, "allocation_charge_not_found")
    assert [e["path"] for e in foreign.json()["errors"]] == [
        ["body", "allocation", "lines", 0, "charge_id"]
    ]


def test_a_line_above_the_open_amount_is_409(module_seeded_url, client, staff):
    url = module_seeded_url
    fam, [s1] = ps.family(url)
    credit_payment(client, staff, fam, "500.00", days_ago=0)
    [c1] = ps.charges(url, (s1, "40.00", TODAY))

    response = ps.allocate(
        client, staff, fam, ps.credit_body("explicit", [(c1, "40.01")])
    )

    assert code_of(response) == (409, "allocation_exceeds_open")
    assert response.json()["details"] == {"charge_id": str(c1), "open": "40.00"}


def test_money_released_by_a_credit_adjustment_is_allocated_again(
    module_seeded_url, client, staff
):
    """E2: a negative adjustment on a charge #96 paid releases the newest
    allocation (#90 E5); the freed money is account credit the credit route
    can use."""
    url = module_seeded_url
    fam, [s1] = ps.family(url)
    c1, c2 = ps.charges(url, (s1, "100.00", YESTERDAY), (s1, "70.00", TODAY))
    body = ps.payment_body("100.00", "explicit", [(c1, "100.00")])
    paid = created(ps.pay(client, staff, fam, body))

    async def _credit(session: AsyncSession) -> None:
        await billing.post_adjustment(
            session,
            AdjustmentDraft(
                charge_id=c1,
                kind="cancellation_credit",
                amount=Money("-60.00"),
                source_key=f"test:{uuid.uuid4()}",
            ),
        )

    in_uow(url, _credit)
    assert get(client, staff, ps.account_path(fam)).json()["account_credit"] == "60.00"

    body = created(ps.allocate(client, staff, fam, ps.credit_body()))

    assert rows(body) == [(paid["payment"]["id"], str(c2), "60.00")]
    assert body["account_credit_after"] == "0.00"
    assert ps.valid_allocated(url, c1) == Money("40.00")
    ps.assert_invariants(url, ps.account_of(url, fam))


def test_a_reversed_payment_and_refunds_are_never_credit(
    module_seeded_url, client, staff, admin
):
    url = module_seeded_url
    fam, [s1] = ps.family(url)
    [c1] = ps.charges(url, (s1, "500.00", TODAY))
    account = in_uow(url, lambda s: billing.ensure_billing_account(s, fam))

    async def _payments(session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
        reversed_ = await factories.payment(
            session,
            account,
            "300.00",
            recorded_by=admin.id,
            received_on=TODAY - timedelta(days=10),
        )
        await factories.reversal(session, reversed_)
        refunded = await factories.payment(
            session,
            account,
            "100.00",
            recorded_by=admin.id,
            received_on=TODAY - timedelta(days=5),
        )
        await factories.refund(session, refunded, "30.00")
        return reversed_.id, refunded.id

    reversed_id, refunded_id = in_uow(url, _payments)

    body = created(ps.allocate(client, staff, fam, ps.credit_body()))

    assert rows(body) == [(str(refunded_id), str(c1), "70.00")]
    assert body["account_credit_after"] == "0.00"
    over = ps.allocate(client, staff, fam, ps.credit_body("explicit", [(c1, "0.01")]))
    assert code_of(over) == (409, "nothing_to_allocate")
    assert not any(
        r.payment_id == reversed_id and r.valid for r in ps.allocations_of(url, account)
    )
    ps.assert_invariants(url, account)


def test_each_credit_allocation_row_is_audited_by_the_caller(
    module_seeded_url, client, staff, admin
):
    url = module_seeded_url
    fam, [s1] = ps.family(url)
    credit_payment(client, staff, fam, "40.00", days_ago=2)
    credit_payment(client, staff, fam, "40.00", days_ago=1)
    ps.charges(url, (s1, "60.00", TODAY))

    body = created(ps.allocate(client, staff, fam, ps.credit_body()))

    assert len(body["allocations"]) == 2
    for allocation in body["allocations"]:
        found = events(
            url,
            record_type="payment_allocations",
            record_id=uuid.UUID(allocation["id"]),
        )
        assert [(e.action, e.actor_user_id) for e in found] == [("create", admin.id)]
    [count] = run_sql(
        url,
        "SELECT count(*) AS n FROM payment_allocations WHERE billing_account_id = :a"
        " AND created_by_user_id = :u",
        {"a": ps.account_of(url, fam), "u": admin.id},
    )
    assert count.n == 2
