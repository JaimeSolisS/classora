"""Who may record and read payments (#96 F5; C5-C7, M6, M7, M8), through
real logins on the real app.

- ``POST …/payments`` and ``…/allocations``: ``payment.record`` (Owner,
  Administrator, Finance Staff, a delegated Administrator); Guardian,
  Student, Tutor and Academic Coordinator get ``403 permission_denied``
  (guardians can't record); anonymous ``401``; no reauthentication.
- ``GET …/payments`` and ``…/payments/{id}``: the #364 record policy.
  Staff see every family; a guardian their active profile's family with
  ``note`` and the actor ids ``null`` (``reference``/``payer_name``
  shown); anyone else, and a payment of another family, the same ``404``
  as a missing family.
- ``GET /api/v1/billing/payment-methods``: ``billing.view``.
- The reads' statement bounds.

The clock is frozen at noon on 2031-03-10 (center zone) for the module."""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal
from app.core.money import Money
from app.core.pagination import OffsetParams
from app.core.reauth import REAUTH_WINDOW
from app.modules.billing import service as billing
from app.modules.billing.schemas import AdjustmentDraft
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    DELEGABLE_PERMISSIONS,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    PAYMENT_RECORD,
    ROLE_PERMISSIONS,
    STUDENT,
    TUTOR,
)
from app.workflows import billing_payments
from tests.authz.assertions import assert_same_not_found
from tests.conftest import FrozenClock, frozen_clock
from tests.factories import billing as factories
from tests.factories.base import persist
from tests.factories.families import (
    FamilyFactory,
    GuardianFactory,
    add_to_family,
    persist_student,
)
from tests.integration import payment_support as ps
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.billing_ledger_support import count_statements
from tests.integration.catalog_support import get
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, in_uow

TODAY = ps.TODAY
METHODS = "/api/v1/billing/payment-methods"
RECORDERS = [OWNER, ADMINISTRATOR, FINANCE_STAFF, "delegated"]
NON_RECORDERS = [GUARDIAN, STUDENT, TUTOR, ACADEMIC_COORDINATOR]


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
        url, roles=(ADMINISTRATOR,), delegations=sorted(DELEGABLE_PERMISSIONS)
    )
    for name in ("in_family", "other_family", "inactive"):
        made[name] = create_user(url, roles=(GUARDIAN,))
    made[GUARDIAN] = made["in_family"]
    return made


@dataclass(frozen=True)
class World:
    """Family A: students SA1 and SA2, guardian ``in_family`` (active) and
    ``inactive`` (inactive profile); two charges; three payments recorded
    by Finance Staff (one with note, reference and payer name; one partly
    released by a credit adjustment). Family B: guardian ``other_family``
    and one payment. Family C: no account."""

    a: uuid.UUID
    b: uuid.UUID
    c: uuid.UUID
    a_payments: list[str]
    b_payment: str
    released_charge: uuid.UUID


@pytest.fixture(scope="module")
def world(module_seeded_url: str, client: TestClient, users: Any) -> World:
    url = module_seeded_url

    async def _families(session: AsyncSession) -> tuple[Any, ...]:
        family_a = await persist(session, FamilyFactory)
        family_b = await persist(session, FamilyFactory)
        family_c = await persist(session, FamilyFactory)
        sa1 = await persist_student(session, contacts=0)
        sa2 = await persist_student(session, contacts=0)
        sb1 = await persist_student(session, contacts=0)

        async def guardian(name: str, active: bool = True) -> Any:
            return await persist(
                session, GuardianFactory, user_id=users[name].id, is_active=active
            )

        await add_to_family(
            session,
            family_a,
            sa1,
            sa2,
            await guardian("in_family"),
            await guardian("inactive", active=False),
        )
        await add_to_family(session, family_b, sb1, await guardian("other_family"))
        return family_a.id, family_b.id, family_c.id, sa1.id, sa2.id, sb1.id

    a, b, c, sa1, sa2, sb1 = in_uow(url, _families)
    c1, c2 = ps.charges(url, (sa1, "100.00", ps.YESTERDAY), (sa2, "100.00", TODAY))
    [cb] = ps.charges(url, (sb1, "50.00", TODAY))
    cookie = sign_in(client, users[FINANCE_STAFF].username)

    def pay(fam: uuid.UUID, body: dict[str, Any]) -> str:
        response = ps.pay(client, cookie, fam, body)
        assert response.status_code == 201, response.text
        payment_id: str = response.json()["payment"]["id"]
        return payment_id

    first = pay(
        a,
        ps.payment_body(
            "100.00",
            "explicit",
            [(c1, "100.00")],
            received_on=(TODAY - timedelta(days=3)).isoformat(),
            note="Pagó la abuela",
            reference="SPEI-77",
            payer_name="Rosa Pérez",
        ),
    )
    second = pay(
        a, ps.payment_body("30.00", received_on=(TODAY - timedelta(days=1)).isoformat())
    )
    third = pay(a, ps.payment_body("20.00", "none"))
    b_payment = pay(b, ps.payment_body("50.00"))

    async def _credit(session: AsyncSession) -> None:
        await billing.post_adjustment(
            session,
            AdjustmentDraft(
                charge_id=c1,
                kind="cancellation_credit",
                amount=Money("-40.00"),
                source_key=f"test:{uuid.uuid4()}",
            ),
        )

    in_uow(url, _credit)
    del cb
    return World(
        a=a,
        b=b,
        c=c,
        a_payments=[third, second, first],
        b_payment=b_payment,
        released_charge=c1,
    )


def cookie_of(client: TestClient, users: Any, name: str) -> Any:
    return sign_in(client, users[name].username)


# --- Recording (C1, C4, C7; M6) ------------------------------------------------------


@pytest.mark.parametrize("role", RECORDERS)
def test_recorders_record_and_allocate(module_seeded_url, client, users, role):
    fam, [s1] = ps.family(module_seeded_url)
    ps.charges(module_seeded_url, (s1, "10.00", TODAY))
    cookie = cookie_of(client, users, role)

    paid = ps.pay(client, cookie, fam, ps.payment_body("30.00", "none"))
    allocated = ps.allocate(client, cookie, fam, ps.credit_body())

    assert paid.status_code == 201, paid.text
    assert allocated.status_code == 201, allocated.text
    assert paid.json()["payment"]["recorded_by_user_id"] == str(users[role].id)


@pytest.mark.parametrize("role", NON_RECORDERS)
def test_everyone_else_gets_403_and_guardians_cant_record(
    module_seeded_url, client, users, world, role
):
    cookie = cookie_of(client, users, role)
    family = world.a if role == GUARDIAN else world.b

    paid = ps.pay(client, cookie, family, ps.payment_body())
    allocated = ps.allocate(client, cookie, family, ps.credit_body())

    assert code_of(paid) == (403, "permission_denied")
    assert code_of(allocated) == (403, "permission_denied")


def test_payment_record_is_not_delegable():
    assert PAYMENT_RECORD not in DELEGABLE_PERMISSIONS


def test_no_reauthentication_is_needed(module_seeded_url, client, users, frozen):
    fam, _ = ps.family(module_seeded_url)
    cookie = cookie_of(client, users, FINANCE_STAFF)
    start = frozen.now()
    frozen.advance(REAUTH_WINDOW + timedelta(minutes=1))
    try:
        response = ps.pay(client, cookie, fam, ps.payment_body("5.00"))
    finally:
        frozen.set(start)

    assert response.status_code == 201, response.text


def test_anonymous_is_401_everywhere(client, world):
    client.cookies.clear()
    payment = world.a_payments[0]
    responses = [
        client.post(ps.account_path(world.a, "/payments"), json=ps.payment_body()),
        client.post(ps.account_path(world.a, "/allocations"), json=ps.credit_body()),
        client.get(ps.account_path(world.a, "/payments")),
        client.get(ps.account_path(world.a, f"/payments/{payment}")),
        client.get("/api/v1/billing/payment-methods"),
    ]

    assert [r.status_code for r in responses] == [401] * 5


# --- Payment methods (C5, M8) --------------------------------------------------------


@pytest.mark.parametrize("role", [OWNER, ADMINISTRATOR, FINANCE_STAFF])
def test_billing_view_holders_list_the_methods(client, users, role):
    response = get(client, cookie_of(client, users, role), METHODS)

    assert response.status_code == 200, response.text
    methods = response.json()
    assert [m["code"] for m in methods] == sorted(m["code"] for m in methods)
    assert {"cash": True, "other": True, "transfer": True}.items() <= {
        m["code"]: m["is_active"] for m in methods
    }.items()


@pytest.mark.parametrize("role", [GUARDIAN, STUDENT, TUTOR, ACADEMIC_COORDINATOR])
def test_others_cant_list_the_methods(client, users, role):
    response = get(client, cookie_of(client, users, role), METHODS)

    assert code_of(response) == (403, "permission_denied")


# --- Reads (C6, M7) ------------------------------------------------------------------


def test_staff_read_every_familys_payments_newest_first(client, users, world):
    cookie = cookie_of(client, users, OWNER)

    page = get(client, cookie, ps.account_path(world.a, "/payments")).json()
    other = get(client, cookie, ps.account_path(world.b, "/payments")).json()
    none = get(client, cookie, ps.account_path(world.c, "/payments")).json()

    assert [p["id"] for p in page["items"]] == world.a_payments
    assert page["total"] == 3
    assert [p["id"] for p in other["items"]] == [world.b_payment]
    assert (none["items"], none["total"]) == ([], 0)
    first = page["items"][2]
    assert (first["note"], first["reference"], first["payer_name"]) == (
        "Pagó la abuela",
        "SPEI-77",
        "Rosa Pérez",
    )
    assert first["recorded_by_user_id"] == str(users[FINANCE_STAFF].id)
    # 40.00 of its 100.00 allocation was released by the credit.
    assert (first["allocated_amount"], first["unallocated_amount"]) == (
        "60.00",
        "40.00",
    )


def test_the_list_paginates(client, users, world):
    cookie = cookie_of(client, users, OWNER)
    path = ps.account_path(world.a, "/payments")

    page = get(client, cookie, path, limit=2, offset=2).json()
    past = get(client, cookie, path, limit=2, offset=10).json()

    assert ([p["id"] for p in page["items"]], page["total"]) == (
        world.a_payments[2:],
        3,
    )
    assert (past["items"], past["total"]) == ([], 3)


def test_the_detail_lists_every_allocation_with_released_ones_invalid(
    client, users, world
):
    cookie = cookie_of(client, users, FINANCE_STAFF)
    first = world.a_payments[2]

    detail = get(client, cookie, ps.account_path(world.a, f"/payments/{first}")).json()

    assert detail["id"] == first
    assert [
        (a["charge_id"], a["amount"], a["is_valid"]) for a in detail["allocations"]
    ] == [
        (str(world.released_charge), "100.00", False),
        (str(world.released_charge), "60.00", True),
    ]
    assert detail["allocations"][0]["created_by_user_id"] == str(
        users[FINANCE_STAFF].id
    )
    assert detail["allocations"][1]["created_by_user_id"] is None  # re-inserted


def test_a_guardian_reads_their_family_without_note_and_actor_ids(client, users, world):
    staff = cookie_of(client, users, OWNER)
    guardian = cookie_of(client, users, "in_family")
    path = ps.account_path(world.a, "/payments")

    as_staff = get(client, staff, path).json()
    as_guardian = get(client, guardian, path).json()

    redacted = ("note", "recorded_by_user_id")
    for staff_item, guardian_item in zip(
        as_staff["items"], as_guardian["items"], strict=True
    ):
        assert all(guardian_item[k] is None for k in redacted)
        assert {k: v for k, v in staff_item.items() if k not in redacted} == {
            k: v for k, v in guardian_item.items() if k not in redacted
        }
    assert as_guardian["items"][2]["reference"] == "SPEI-77"
    assert as_guardian["items"][2]["payer_name"] == "Rosa Pérez"
    first = world.a_payments[2]
    detail = get(client, guardian, ps.account_path(world.a, f"/payments/{first}"))
    assert detail.status_code == 200, detail.text
    assert detail.json()["note"] is None
    assert all(a["created_by_user_id"] is None for a in detail.json()["allocations"])


HIDDEN = [
    ("in_family", "b"),
    ("other_family", "a"),
    ("inactive", "a"),
    (TUTOR, "a"),
    (ACADEMIC_COORDINATOR, "a"),
    (STUDENT, "a"),
]


@pytest.mark.parametrize(("user", "family"), HIDDEN)
def test_any_other_family_is_the_missing_404(client, users, world, user, family):
    cookie = cookie_of(client, users, user)
    target = getattr(world, family)
    payment = world.a_payments[0] if family == "a" else world.b_payment

    assert_same_not_found(
        get(client, cookie, ps.account_path(target, "/payments")),
        get(client, cookie, ps.account_path(ps.MISSING, "/payments")),
    )
    assert_same_not_found(
        get(client, cookie, ps.account_path(target, f"/payments/{payment}")),
        get(client, cookie, ps.account_path(ps.MISSING, f"/payments/{payment}")),
    )


@pytest.mark.parametrize("user", [OWNER, "in_family"])
def test_a_payment_of_another_family_is_404_under_this_family(
    client, users, world, user
):
    cookie = cookie_of(client, users, user)

    foreign = f"/payments/{world.b_payment}"
    hidden = get(client, cookie, ps.account_path(world.a, foreign))
    missing = get(client, cookie, ps.account_path(world.a, f"/payments/{uuid.uuid4()}"))

    assert_same_not_found(hidden, missing)


def test_a_family_without_an_account_has_no_payment_detail(client, users, world):
    cookie = cookie_of(client, users, OWNER)

    response = get(
        client, cookie, ps.account_path(world.c, f"/payments/{world.b_payment}")
    )

    assert code_of(response) == (404, "not_found")


# --- Statement bounds ----------------------------------------------------------------


def test_the_reads_cost_a_bounded_number_of_statements(module_seeded_url, users, world):
    url = module_seeded_url
    staff = Principal(
        user_id=uuid.uuid4(),
        roles=frozenset({OWNER}),
        permissions=ROLE_PERMISSIONS[OWNER],
    )
    guardian = Principal(
        user_id=users["in_family"].id,
        roles=frozenset({GUARDIAN}),
        permissions=ROLE_PERMISSIONS[GUARDIAN],
    )
    first = uuid.UUID(world.a_payments[2])

    for principal in (staff, guardian):
        count, page = count_statements(
            url,
            lambda s, p=principal: billing_payments.list_payments(
                s, p, world.a, page=OffsetParams(limit=50, offset=0)
            ),
        )
        assert count <= 4, principal
        assert len(page.items) == 3
        count, detail = count_statements(
            url,
            lambda s, p=principal: billing_payments.get_payment(s, p, world.a, first),
        )
        assert count <= 5, principal
        assert len(detail.allocations) == 2


def test_the_page_costs_the_same_with_many_payments(module_seeded_url):
    url = module_seeded_url

    async def _make(session: AsyncSession) -> uuid.UUID:
        recorder = await factories.staff_user_id(session)
        family_id, [student] = await factories.family_with_students(session, 1)
        posted = await factories.charge(session, student, "1000.00")
        for _ in range(30):
            paid = await factories.payment(
                session, posted.billing_account_id, "10.00", recorded_by=recorder
            )
            await factories.allocation(session, paid, posted.charge_id, "5.00")
        return family_id

    family_id = in_uow(url, _make)
    staff = Principal(
        user_id=uuid.uuid4(),
        roles=frozenset({OWNER}),
        permissions=ROLE_PERMISSIONS[OWNER],
    )

    count, page = count_statements(
        url,
        lambda s: billing_payments.list_payments(
            s, staff, family_id, page=OffsetParams(limit=50, offset=0)
        ),
    )

    assert count <= 4
    assert page.total == 30
    assert {item.unallocated_amount for item in page.items} == {Money("5.00")}
