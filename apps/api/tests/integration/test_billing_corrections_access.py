"""Who may correct, reverse and refund, and what the reads show (#100 F8;
C6-C8, D1-D3, R3, R8), through real logins on the real app.

- Manual charges, manual adjustments and package cancellations
  (``billing.manage``): Owner, Administrator, Finance Staff; everyone else
  ``403 permission_denied``.
- Reversals and refunds (``finance.refund.approve``): the Owner and an
  Administrator delegated it; a plain Administrator, Finance Staff and
  everyone else ``403 permission_denied`` (never ``reauth_required``).
- Anonymous ``401`` on every route.
- Reauthentication: every manual adjustment, reversal, refund and
  cancellation needs it (``403 reauth_required`` first); a manual charge
  doesn't.
- The charge detail and the extended payment detail: staff see the reasons
  and actor ids; a guardian of the family sees ``null`` instead (the
  refund's ``reference`` stays); another family or anyone else the same
  ``404`` as a missing family; the statement bounds.

The clock is frozen at noon on 2031-03-10 (center zone) for the module."""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal
from app.core.money import Money
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_REFUND_APPROVE,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    ROLE_PERMISSIONS,
    STUDENT,
    TUTOR,
)
from app.workflows import billing_corrections
from tests.authz.assertions import assert_same_not_found
from tests.conftest import FrozenClock, frozen_clock
from tests.factories.base import persist
from tests.factories.families import (
    FamilyFactory,
    GuardianFactory,
    add_to_family,
    persist_student,
)
from tests.integration import corrections_support as cs
from tests.integration import package_support as pks
from tests.integration import payment_support as ps
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.billing_ledger_support import count_statements
from tests.integration.invitation_support import reauthenticate, sign_in
from tests.integration.lifecycle_support import code_of, in_uow

TODAY = ps.TODAY
SOLD = date(2031, 3, 1)
MANAGERS = [OWNER, ADMINISTRATOR, FINANCE_STAFF]
NON_MANAGERS = [ACADEMIC_COORDINATOR, TUTOR, STUDENT, GUARDIAN]
APPROVERS = [OWNER, "delegated"]
NON_APPROVERS = [
    ADMINISTRATOR,
    FINANCE_STAFF,
    ACADEMIC_COORDINATOR,
    TUTOR,
    STUDENT,
    GUARDIAN,
]


@pytest.fixture(scope="module")
def frozen() -> Iterator[FrozenClock]:
    with frozen_clock(ps.NOW) as clock:
        yield clock


@pytest.fixture(scope="module")
def client(module_seeded_url: str, frozen: FrozenClock) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def users(module_seeded_url: str) -> dict[str, CreatedUser]:
    url = module_seeded_url
    made = {
        role: create_user(url, roles=(role,))
        for role in (OWNER, ADMINISTRATOR, FINANCE_STAFF, STUDENT, TUTOR)
    }
    made[ACADEMIC_COORDINATOR] = create_user(url, roles=(ACADEMIC_COORDINATOR,))
    made["delegated"] = create_user(
        url, roles=(ADMINISTRATOR,), delegations=[FINANCE_REFUND_APPROVE]
    )
    made[GUARDIAN] = create_user(url, roles=(GUARDIAN,))
    made["other_family"] = create_user(url, roles=(GUARDIAN,))
    return made


def cookie_of(client: TestClient, users: Any, name: str, *, reauth: bool = True) -> Any:
    cookie = sign_in(client, users[name].username)
    if reauth:
        reauthenticate(client, cookie)
    return cookie


@dataclass(frozen=True)
class World:
    """Family A (guardian ``GUARDIAN``): a charge with a manual credit, a
    refunded payment and a reversed one. Family B (guardian
    ``other_family``): one charge and one payment."""

    a: uuid.UUID
    b: uuid.UUID
    a_student: uuid.UUID
    a_charge: uuid.UUID
    refunded: uuid.UUID
    reversed_: uuid.UUID
    b_charge: uuid.UUID
    b_payment: uuid.UUID


@pytest.fixture(scope="module")
def world(module_seeded_url: str, client: TestClient, users: Any) -> World:
    url = module_seeded_url

    async def _families(session: AsyncSession) -> tuple[Any, ...]:
        family_a = await persist(session, FamilyFactory)
        family_b = await persist(session, FamilyFactory)
        sa = await persist_student(session, contacts=0)
        sb = await persist_student(session, contacts=0)
        ga = await persist(session, GuardianFactory, user_id=users[GUARDIAN].id)
        gb = await persist(session, GuardianFactory, user_id=users["other_family"].id)
        await add_to_family(session, family_a, sa, ga)
        await add_to_family(session, family_b, sb, gb)
        return family_a.id, family_b.id, sa.id, sb.id

    a, b, sa, sb = in_uow(url, _families)
    [a_charge] = ps.charges(url, (sa, "200.00", TODAY))
    [b_charge] = ps.charges(url, (sb, "50.00", TODAY))
    owner = cookie_of(client, users, OWNER)
    cs.created(cs.adjust(client, owner, a, a_charge, "-20.00", "Descuento acordado"))
    account_a, account_b = ps.account_of(url, a), ps.account_of(url, b)
    assert account_a is not None and account_b is not None
    refunded = cs.recorded_payment(url, users[OWNER].id, account_a, "100.00")
    cs.created(cs.refund(client, owner, a, refunded, cs.refund_body("30.00")))
    reversed_ = cs.recorded_payment(
        url, users[OWNER].id, account_a, "80.00", [(a_charge, "80.00")]
    )
    cs.created(cs.reverse(client, owner, a, reversed_, "Cheque sin fondos"))
    b_payment = cs.recorded_payment(url, users[OWNER].id, account_b, "50.00")
    return World(
        a=a,
        b=b,
        a_student=sa,
        a_charge=a_charge,
        refunded=refunded,
        reversed_=reversed_,
        b_charge=b_charge,
        b_payment=b_payment,
    )


# --- Writes (D1-D3) -------------------------------------------------------------------


@pytest.mark.parametrize("role", MANAGERS)
def test_managers_post_charges_adjustments_and_cancellations(
    module_seeded_url, client, users, role
):
    url = module_seeded_url
    fam, _, [student] = cs.new_account(url)
    cookie = cookie_of(client, users, role)

    charge = cs.created(cs.charge_post(client, cookie, fam, cs.charge_body(student)))
    adjusted = cs.adjust(client, cookie, fam, charge["charge"]["id"], "-10.00")
    w = pks.world(url)
    package = pks.sell(url, w.student, pks.product(url), SOLD)
    cancelled = cs.cancel(client, cookie, package)

    assert adjusted.status_code == 201, adjusted.text
    assert cancelled.status_code == 201, cancelled.text
    assert charge["charge"]["posted_by_user_id"] == str(users[role].id)


@pytest.mark.parametrize("role", NON_MANAGERS)
def test_everyone_else_gets_403_on_the_manage_routes(client, users, world, role):
    cookie = cookie_of(client, users, role)
    family = world.a if role == GUARDIAN else world.b
    charge = world.a_charge if role == GUARDIAN else world.b_charge

    responses = [
        cs.charge_post(client, cookie, family, cs.charge_body(world.a_student)),
        cs.adjust(client, cookie, family, charge, "-1.00"),
        cs.cancel(client, cookie, uuid.uuid4()),
    ]

    assert [code_of(r) for r in responses] == [(403, "permission_denied")] * 3


@pytest.mark.parametrize("role", APPROVERS)
def test_the_owner_and_a_delegated_administrator_reverse_and_refund(
    module_seeded_url, client, users, role
):
    url = module_seeded_url
    fam, account, _ = cs.new_account(url)
    cookie = cookie_of(client, users, role)
    to_refund = cs.recorded_payment(url, users[OWNER].id, account, "50.00")
    to_reverse = cs.recorded_payment(url, users[OWNER].id, account, "50.00")

    refunded = cs.created(
        cs.refund(client, cookie, fam, to_refund, cs.refund_body("5.00"))
    )
    reversed_ = cs.created(cs.reverse(client, cookie, fam, to_reverse))

    assert refunded["refund"]["refunded_by_user_id"] == str(users[role].id)
    assert reversed_["reversal"]["reversed_by_user_id"] == str(users[role].id)


@pytest.mark.parametrize("role", NON_APPROVERS)
@pytest.mark.parametrize("reauth", [True, False])
def test_no_one_else_reverses_or_refunds(client, users, world, role, reauth):
    cookie = cookie_of(client, users, role, reauth=reauth and role != GUARDIAN)
    family = world.a if role == GUARDIAN else world.b
    payment = world.refunded if role == GUARDIAN else world.b_payment

    responses = [
        cs.reverse(client, cookie, family, payment),
        cs.refund(client, cookie, family, payment, cs.refund_body("1.00")),
    ]

    # A missing permission is never a password prompt (#26 order).
    assert [code_of(r) for r in responses] == [(403, "permission_denied")] * 2


def test_anonymous_is_401_everywhere(client, world):
    client.cookies.clear()
    account = ps.account_path(world.a)
    responses = [
        client.post(f"{account}/charges", json=cs.charge_body(world.a_student)),
        client.post(
            f"{account}/charges/{world.a_charge}/adjustments",
            json={"amount": "1.00", "reason": "x"},
        ),
        client.post(
            f"{account}/payments/{world.refunded}/reversal", json={"reason": "x"}
        ),
        client.post(
            f"{account}/payments/{world.refunded}/refunds", json=cs.refund_body("1.00")
        ),
        client.post(f"{cs.PACKAGES}/{uuid.uuid4()}/cancellation", json={"reason": "x"}),
        client.get(f"{account}/charges/{world.a_charge}"),
    ]

    assert [r.status_code for r in responses] == [401] * 6


def test_reauthentication_is_needed_except_for_a_manual_charge(
    module_seeded_url, client, users
):
    url = module_seeded_url
    fam, account, [student] = cs.new_account(url)
    [charge] = ps.charges(url, (student, "100.00", TODAY))
    payment = cs.recorded_payment(url, users[OWNER].id, account, "50.00")
    w = pks.world(url)
    package = pks.sell(url, w.student, pks.product(url), SOLD)
    cookie = cookie_of(client, users, OWNER, reauth=False)
    before = cs.snapshot(url, account)

    responses = [
        cs.adjust(client, cookie, fam, charge, "-1.00"),
        cs.reverse(client, cookie, fam, payment),
        cs.refund(client, cookie, fam, payment, cs.refund_body("1.00")),
        cs.cancel(client, cookie, package),
    ]
    posted = cs.charge_post(client, cookie, fam, cs.charge_body(student))

    assert [code_of(r) for r in responses] == [(403, "reauth_required")] * 4
    assert posted.status_code == 201, posted.text
    after = cs.snapshot(url, account)
    assert after["charge_adjustments"] == before["charge_adjustments"]
    assert pks.remaining(url, package) == 10


# --- Reads (C6, R8) -------------------------------------------------------------------


def test_staff_read_the_charge_with_its_reasons(client, users, world):
    cookie = cookie_of(client, users, FINANCE_STAFF, reauth=False)

    body = cs.charge_get(client, cookie, world.a, world.a_charge).json()

    assert body["id"] == str(world.a_charge)
    assert (body["net"], body["allocated"], body["open"]) == (
        "180.00",
        "0.00",
        "180.00",
    )
    [adjustment] = body["adjustments"]
    assert (adjustment["kind"], adjustment["amount"], adjustment["reason"]) == (
        "manual",
        "-20.00",
        "Descuento acordado",
    )
    assert adjustment["posted_by_user_id"] == str(users[OWNER].id)
    [allocation] = body["allocations"]
    assert (allocation["payment_id"], allocation["amount"], allocation["is_valid"]) == (
        str(world.reversed_),
        "80.00",
        False,
    )


def test_staff_read_the_reversal_and_the_refunds(client, users, world):
    cookie = cookie_of(client, users, OWNER, reauth=False)

    reversed_ = cs.payment_get(client, cookie, world.a, world.reversed_).json()
    refunded = cs.payment_get(client, cookie, world.a, world.refunded).json()

    assert reversed_["is_reversed"] is True
    assert reversed_["reversal"]["reason"] == "Cheque sin fondos"
    assert reversed_["reversal"]["reversed_by_user_id"] == str(users[OWNER].id)
    assert reversed_["reversed_at"] == reversed_["reversal"]["reversed_at"]
    assert reversed_["refunds"] == []
    assert refunded["reversal"] is None
    assert refunded["reversed_at"] is None
    [refund] = refunded["refunds"]
    assert (refund["amount"], refund["reference"], refund["reason"]) == (
        "30.00",
        "SPEI-900",
        "Saldo a favor devuelto",
    )


def test_a_guardian_reads_without_reasons_and_actor_ids(client, users, world):
    staff = cookie_of(client, users, OWNER, reauth=False)
    guardian = cookie_of(client, users, GUARDIAN, reauth=False)

    charge = cs.charge_get(client, guardian, world.a, world.a_charge)
    as_staff = cs.charge_get(client, staff, world.a, world.a_charge).json()
    reversed_ = cs.payment_get(client, guardian, world.a, world.reversed_).json()
    refunded = cs.payment_get(client, guardian, world.a, world.refunded).json()

    assert charge.status_code == 200, charge.text
    body = charge.json()
    assert body["posted_by_user_id"] is None
    assert [(a["reason"], a["posted_by_user_id"]) for a in body["adjustments"]] == [
        (None, None)
    ]
    assert [a["amount"] for a in body["adjustments"]] == ["-20.00"]
    assert {
        k: v for k, v in body.items() if k not in ("adjustments", "posted_by_user_id")
    } == {
        k: v
        for k, v in as_staff.items()
        if k not in ("adjustments", "posted_by_user_id")
    }
    assert reversed_["reversal"]["reason"] is None
    assert reversed_["reversal"]["reversed_by_user_id"] is None
    assert reversed_["reversal"]["reversed_at"] is not None
    [refund] = refunded["refunds"]
    assert (refund["reason"], refund["refunded_by_user_id"]) == (None, None)
    assert refund["reference"] == "SPEI-900"
    assert "Cheque sin fondos" not in str(reversed_)
    assert "Descuento acordado" not in charge.text


@pytest.mark.parametrize(
    ("user", "family"),
    [(GUARDIAN, "b"), ("other_family", "a"), (TUTOR, "a"), (STUDENT, "a")],
)
def test_any_other_family_is_the_missing_404(client, users, world, user, family):
    cookie = cookie_of(client, users, user, reauth=False)
    target = getattr(world, family)
    charge = world.a_charge if family == "a" else world.b_charge

    assert_same_not_found(
        cs.charge_get(client, cookie, target, charge),
        cs.charge_get(client, cookie, ps.MISSING, charge),
    )


@pytest.mark.parametrize("user", [OWNER, GUARDIAN])
def test_a_charge_of_another_family_is_404_under_this_family(
    client, users, world, user
):
    cookie = cookie_of(client, users, user, reauth=False)

    assert_same_not_found(
        cs.charge_get(client, cookie, world.a, world.b_charge),
        cs.charge_get(client, cookie, world.a, uuid.uuid4()),
    )


def test_the_charge_detail_costs_a_bounded_number_of_statements(
    module_seeded_url, users, world
):
    url = module_seeded_url
    staff = Principal(
        user_id=uuid.uuid4(),
        roles=frozenset({OWNER}),
        permissions=ROLE_PERMISSIONS[OWNER],
    )
    guardian = Principal(
        user_id=users[GUARDIAN].id,
        roles=frozenset({GUARDIAN}),
        permissions=ROLE_PERMISSIONS[GUARDIAN],
    )

    [charge] = ps.charges(url, (world.a_student, "100.00", TODAY))

    async def _adjustments(session: AsyncSession, n: int) -> None:
        from app.modules.billing import service as billing

        for _ in range(n):
            await billing.post_manual_adjustment(
                session, charge, Money("1.00"), "Ajuste"
            )

    in_uow(url, lambda s: _adjustments(s, 1), actor_id=users[OWNER].id)
    for principal in (staff, guardian):
        count, detail = count_statements(
            url,
            lambda s, p=principal: billing_corrections.get_charge(
                s, p, world.a, charge
            ),
        )
        assert count <= 9, principal  # #102 C1: + the discount lines
        assert len(detail.adjustments) == 1
    in_uow(url, lambda s: _adjustments(s, 5), actor_id=users[OWNER].id)
    count, detail = count_statements(
        url,
        lambda s: billing_corrections.get_charge(s, guardian, world.a, charge),
    )
    assert count <= 9
    assert len(detail.adjustments) == 6
