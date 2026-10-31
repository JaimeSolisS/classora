"""The family billing portal reads (#102 A, B, C; T2-T4 (a)) through real
logins on the real app.

- ``GET /api/v1/families/own``: the family of the caller's **active**
  guardian profile (an inactive family too), ``null`` for every other case,
  a staff member who is also a guardian gets their own family; the
  statement bound.
- ``GET /api/v1/families/{family_id}/billing-account/packages`` and
  ``…/{package_id}``: #94's shapes through the #364 record policy, the
  filters, the order, a package of another family's account ``404``
  (identical to an unknown family), packages stay on the account they were
  bought on, the statement bound; #94's staff routes stay staff-only.
- The charge detail's ``discounts``: the posting-time snapshot (a later
  rename or deactivation changes nothing), the same for staff and
  guardians; the #100 statement bound plus one.

The clock is frozen at noon on 2031-03-10 (center zone) for the module.
The cross-family walk is ``test_billing_family_leak.py``."""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal
from app.core.pagination import OffsetParams
from app.modules.families import service as families
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    ROLE_PERMISSIONS,
    STUDENT,
    TUTOR,
)
from app.workflows import billing_corrections, packages
from tests.authz.assertions import assert_same_not_found
from tests.conftest import FrozenClock, frozen_clock
from tests.factories.base import persist
from tests.factories.billing import assign_discount, persist_discount
from tests.factories.families import (
    FamilyFactory,
    GuardianFactory,
    add_to_family,
    link_guardian,
    persist_student,
    student_login,
)
from tests.integration import package_support as pks
from tests.integration import payment_support as ps
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.billing_ledger_support import count_statements
from tests.integration.catalog_support import get
from tests.integration.conftest import run_sql
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, in_uow

OWN = "/api/v1/families/own"
SOLD = date(2031, 3, 1)
MISSING = "00000000-0000-4000-8000-000000000102"
ITEM_KEYS = {
    "id",
    "student",
    "family",
    "product",
    "unit",
    "units_granted",
    "remaining_units",
    "subject",
    "purchased_on",
    "expires_on",
    "status",
    "purchase_charge_id",
}


def packages_path(family: object, package: object | None = None) -> str:
    suffix = "/packages" if package is None else f"/packages/{package}"
    return ps.account_path(family, suffix)


@pytest.fixture(scope="module")
def frozen() -> Iterator[FrozenClock]:
    with frozen_clock(ps.NOW) as clock:
        yield clock


@pytest.fixture(scope="module")
def client(module_seeded_url: str, frozen: FrozenClock) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


def _get(client: TestClient, user: CreatedUser, path: str, **params: Any) -> Any:
    return get(client, sign_in(client, user.username), path, **params)


def _guardian_principal(user: CreatedUser, *roles: str) -> Principal:
    held = frozenset({GUARDIAN, *roles})
    permissions: frozenset[str] = frozenset().union(
        *(ROLE_PERMISSIONS[role] for role in held)
    )
    return Principal(user_id=user.id, roles=held, permissions=permissions)


# --- A. Own family ---------------------------------------------------------------


@dataclass(frozen=True)
class OwnWorld:
    users: dict[str, CreatedUser]
    family: uuid.UUID
    family_name: str
    inactive_family: uuid.UUID
    staff_family: uuid.UUID


@pytest.fixture(scope="module")
def own(module_seeded_url: str) -> OwnWorld:
    url = module_seeded_url
    users = {
        name: create_user(url, roles=(GUARDIAN,))
        for name in (
            "member",
            "inactive_profile",
            "no_family",
            "linked_only",
            "in_inactive",
        )
    }
    users["staff_parent"] = create_user(url, roles=(OWNER, GUARDIAN))
    for role in (OWNER, ADMINISTRATOR, FINANCE_STAFF, ACADEMIC_COORDINATOR, TUTOR):
        users[role] = create_user(url, roles=(role,))
    users[STUDENT] = create_user(url, roles=(STUDENT,))

    async def _make(session: AsyncSession) -> tuple[Any, ...]:
        family = await persist(session, FamilyFactory)
        inactive = await persist(session, FamilyFactory, is_active=False)
        staff_family = await persist(session, FamilyFactory)
        # Families created before and after the staff parent's: GET
        # /families would list them all to an Owner (T2).
        await persist(session, FamilyFactory)
        child = await persist_student(session, contacts=0)
        await student_login(session, child, users[STUDENT].id)

        async def guardian(name: str, *, active: bool = True):  # type: ignore[no-untyped-def]
            return await persist(
                session, GuardianFactory, user_id=users[name].id, is_active=active
            )

        member = await guardian("member")
        off = await guardian("inactive_profile", active=False)
        await guardian("no_family")
        linked = await guardian("linked_only")
        in_inactive = await guardian("in_inactive")
        parent = await guardian("staff_parent")
        await add_to_family(session, family, child, member, off)
        await add_to_family(session, inactive, in_inactive)
        await add_to_family(session, staff_family, parent)
        await link_guardian(session, child, linked)
        await persist(session, FamilyFactory)
        return family.id, family.name, inactive.id, staff_family.id

    family, name, inactive, staff_family = in_uow(url, _make)
    return OwnWorld(
        users=users,
        family=family,
        family_name=name,
        inactive_family=inactive,
        staff_family=staff_family,
    )


def test_a_guardian_gets_their_own_family(client, own):
    response = _get(client, own.users["member"], OWN)

    assert response.status_code == 200, response.text
    assert response.json() == {
        "family": {"id": str(own.family), "name": own.family_name, "is_active": True}
    }


def test_an_inactive_family_is_returned_inactive(client, own):
    body = _get(client, own.users["in_inactive"], OWN).json()

    assert body["family"]["id"] == str(own.inactive_family)
    assert body["family"]["is_active"] is False


@pytest.mark.parametrize(
    "name",
    [
        "inactive_profile",
        "no_family",
        "linked_only",
        OWNER,
        ADMINISTRATOR,
        FINANCE_STAFF,
        ACADEMIC_COORDINATOR,
        TUTOR,
        STUDENT,
    ],
)
def test_everyone_else_gets_null(client, own, name):
    response = _get(client, own.users[name], OWN)

    assert response.status_code == 200, response.text
    assert response.json() == {"family": None}


def test_a_staff_member_who_is_a_guardian_gets_their_own_family(client, own):
    body = _get(client, own.users["staff_parent"], OWN).json()

    assert body["family"]["id"] == str(own.staff_family)
    listed = _get(client, own.users["staff_parent"], "/api/v1/families", limit=100)
    assert listed.json()["total"] > 1  # why the portal can't use the list


def test_anonymous_is_401_and_the_privacy_notice_comes_first(module_seeded_url, client):
    client.cookies.clear()
    assert client.get(OWN).status_code == 401
    pending = create_user(
        module_seeded_url, roles=(GUARDIAN,), privacy_notice_accepted=False
    )
    assert code_of(_get(client, pending, OWN)) == (403, "privacy_notice_required")


def test_own_family_is_bounded_in_statements(module_seeded_url, own):
    url = module_seeded_url
    member = _guardian_principal(own.users["member"])
    staff = Principal(
        user_id=own.users[OWNER].id,
        roles=frozenset({OWNER}),
        permissions=ROLE_PERMISSIONS[OWNER],
    )

    count, found = count_statements(url, lambda s: families.own_family(s, member))
    assert count <= 2
    assert found.family is not None and found.family.id == own.family

    count, found = count_statements(url, lambda s: families.own_family(s, staff))
    assert count == 0
    assert found.family is None


# --- B. Family packages ----------------------------------------------------------


@dataclass(frozen=True)
class PackageWorld:
    family: uuid.UUID
    other_family: uuid.UUID
    student: uuid.UUID
    sibling: uuid.UUID
    guardian: CreatedUser
    other_guardian: CreatedUser
    owner: CreatedUser
    later: uuid.UUID
    sooner: uuid.UUID
    expired: uuid.UUID
    sibling_package: uuid.UUID
    other_package: uuid.UUID
    session: uuid.UUID


def _guardian_of(url: str, family: uuid.UUID) -> CreatedUser:
    user = create_user(url, roles=(GUARDIAN,))

    async def _add(session: AsyncSession) -> None:
        from app.modules.families.models import Family

        found = await session.get(Family, family)
        assert found is not None
        guardian = await persist(session, GuardianFactory, user_id=user.id)
        await add_to_family(session, found, guardian)

    in_uow(url, _add)
    return user


@pytest.fixture(scope="module")
def pw(module_seeded_url: str) -> PackageWorld:
    url = module_seeded_url
    w = pks.world(url)
    brother = pks.sibling(url, w)
    other = pks.world(url)
    later = pks.sell(url, w.student, pks.product(url, validity_days=60), SOLD)
    sooner = pks.sell(
        url, w.student, pks.product(url, validity_days=30), date(2031, 3, 5)
    )
    expired = pks.sell(url, w.student, pks.product(url, validity_days=5), SOLD)
    sibling_package = pks.sell(
        url, brother, pks.product(url, quantity=1, validity_days=20), SOLD
    )
    session = pks.session_for(url, w, date(2031, 3, 8), student=brother)
    assert pks.consume(url, session, brother).outcome == "consumed"
    other_package = pks.sell(url, other.student, pks.product(url), SOLD)
    return PackageWorld(
        family=w.family,
        other_family=other.family,
        student=w.student,
        sibling=brother,
        guardian=_guardian_of(url, w.family),
        other_guardian=_guardian_of(url, other.family),
        owner=create_user(url, roles=(OWNER,)),
        later=later,
        sooner=sooner,
        expired=expired,
        sibling_package=sibling_package,
        other_package=other_package,
        session=session,
    )


def _ids(response: Any) -> list[str]:
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


def test_a_guardian_lists_the_familys_packages(client, pw):
    path = packages_path(pw.family)
    cookie = sign_in(client, pw.guardian.username)

    everything = get(client, cookie, path, limit=100)
    assert _ids(everything) == [
        str(pw.expired),
        str(pw.sibling_package),
        str(pw.sooner),
        str(pw.later),
    ]
    assert everything.json()["total"] == 4
    item = everything.json()["items"][1]
    assert set(item) == ITEM_KEYS
    assert item["family"]["id"] == str(pw.family)
    assert (item["status"], item["remaining_units"]) == ("exhausted", 0)
    assert _ids(get(client, cookie, path, student_id=str(pw.student))) == [
        str(pw.expired),
        str(pw.sooner),
        str(pw.later),
    ]
    assert _ids(get(client, cookie, path, status="active")) == [
        str(pw.sooner),
        str(pw.later),
    ]
    assert _ids(get(client, cookie, path, status=["expired", "exhausted"])) == [
        str(pw.expired),
        str(pw.sibling_package),
    ]
    page = get(client, cookie, path, limit=2, offset=1).json()
    assert (page["total"], [i["id"] for i in page["items"]]) == (
        4,
        [str(pw.sibling_package), str(pw.sooner)],
    )


def test_a_student_with_no_package_here_gives_an_empty_page(client, pw):
    """An unknown student, and another family's student (staff can name
    one): an empty page, never a 404 or another account's packages."""
    for user, student in ((pw.guardian, uuid.uuid4()), (pw.owner, pw.sibling)):
        family = pw.family if user is pw.guardian else pw.other_family
        response = _get(client, user, packages_path(family), student_id=str(student))
        assert response.status_code == 200, response.text
        assert (response.json()["items"], response.json()["total"]) == ([], 0)


def test_staff_list_any_familys_packages(client, pw):
    response = _get(client, pw.owner, packages_path(pw.other_family))

    assert _ids(response) == [str(pw.other_package)]


def test_a_family_without_an_account_has_no_packages(module_seeded_url, client):
    url = module_seeded_url
    family, _ = ps.family(url)
    guardian = _guardian_of(url, family)

    response = _get(client, guardian, packages_path(family))

    assert response.status_code == 200, response.text
    assert response.json()["items"] == []


def test_bad_queries_are_422_before_the_404(client, pw):
    cookie = sign_in(client, pw.other_guardian.username)
    for params in ({"status": "unknown"}, {"student_id": "x"}, {"limit": 0}):
        assert code_of(get(client, cookie, packages_path(pw.family), **params)) == (
            422,
            "validation_error",
        )
    assert code_of(get(client, cookie, f"{ps.account_path('x')}/packages")) == (
        422,
        "validation_error",
    )


def test_the_detail_shows_the_unit_movements(client, pw):
    response = _get(client, pw.guardian, packages_path(pw.family, pw.sibling_package))

    assert response.status_code == 200, response.text
    detail = response.json()
    assert set(detail) == ITEM_KEYS | {"transactions"}
    assert [(t["kind"], t["units"]) for t in detail["transactions"]] == [
        ("grant", 1),
        ("consume", -1),
    ]
    assert detail["transactions"][1]["class_session_id"] == str(pw.session)
    staff = _get(client, pw.owner, packages_path(pw.family, pw.sibling_package))
    assert staff.json() == detail


def test_another_familys_package_is_the_unknown_family_404(client, pw):
    cookie = sign_in(client, pw.guardian.username)
    missing = get(client, cookie, packages_path(MISSING, uuid.uuid4()))

    for path in (
        # Another family's package under the guardian's own family.
        packages_path(pw.family, pw.other_package),
        packages_path(pw.family, uuid.uuid4()),
        # The other family itself.
        packages_path(pw.other_family, pw.other_package),
        packages_path(pw.other_family),
    ):
        assert_same_not_found(get(client, cookie, path), missing)
    # Staff too: a package is read under its own account only.
    owner = sign_in(client, pw.owner.username)
    assert_same_not_found(
        get(client, owner, packages_path(pw.family, pw.other_package)),
        get(client, owner, packages_path(MISSING, uuid.uuid4())),
    )


@pytest.mark.parametrize("role", [ACADEMIC_COORDINATOR, TUTOR, STUDENT])
def test_other_roles_get_the_unknown_family_404(module_seeded_url, client, pw, role):
    cookie = sign_in(client, create_user(module_seeded_url, roles=(role,)).username)
    for path, missing in (
        (packages_path(pw.family), packages_path(MISSING)),
        (
            packages_path(pw.family, pw.later),
            packages_path(MISSING, pw.later),
        ),
    ):
        assert_same_not_found(get(client, cookie, path), get(client, cookie, missing))


def test_a_package_stays_on_the_account_it_was_bought_on(module_seeded_url, client):
    url = module_seeded_url
    w = pks.world(url)
    old = pks.sell(url, w.student, pks.product(url), SOLD)
    new_family = pks.move_to_new_family(url, w.student)
    new = pks.sell(url, w.student, pks.product(url), date(2031, 3, 5))
    owner = create_user(url, roles=(OWNER,))

    assert _ids(_get(client, owner, packages_path(w.family))) == [str(old)]
    assert _ids(_get(client, owner, packages_path(new_family))) == [str(new)]
    assert code_of(_get(client, owner, packages_path(new_family, old))) == (
        404,
        "not_found",
    )


def test_the_staff_package_routes_stay_staff_only(client, pw):
    cookie = sign_in(client, pw.guardian.username)
    for path in (pks.PACKAGES, f"{pks.PACKAGES}/{pw.later}"):
        assert code_of(get(client, cookie, path)) == (403, "permission_denied")


def test_the_package_reads_are_bounded_in_statements(module_seeded_url):
    url = module_seeded_url
    w = pks.world(url)
    guardian = _guardian_principal(_guardian_of(url, w.family))
    product = pks.product(url)

    def _count() -> tuple[int, Any]:
        with frozen_clock(ps.NOW):
            return count_statements(
                url,
                lambda s: packages.list_family_packages(
                    s,
                    guardian,
                    w.family,
                    student_id=None,
                    statuses=(),
                    page=OffsetParams(limit=50, offset=0),
                ),
            )

    first = pks.sell(url, w.student, product, SOLD)
    count, page = _count()
    assert page.total == 1
    assert count <= 5, count
    for _ in range(29):
        pks.sell(url, w.student, product, SOLD)
    count, page = _count()
    assert page.total == 30
    assert count <= 5, count

    with frozen_clock(ps.NOW):
        count, detail = count_statements(
            url,
            lambda s: packages.get_family_package(s, guardian, w.family, first),
        )
    assert detail.id == first
    assert count <= 6, count


# --- C. Discount lines on the charge detail --------------------------------------


@dataclass(frozen=True)
class DiscountWorld:
    family: uuid.UUID
    guardian: CreatedUser
    owner: CreatedUser
    charge: uuid.UUID
    plain_charge: uuid.UUID
    discounts: tuple[uuid.UUID, uuid.UUID]
    names: tuple[str, str]


@pytest.fixture(scope="module")
def dw(module_seeded_url: str) -> DiscountWorld:
    url = module_seeded_url
    w = pks.world(url)
    suffix = uuid.uuid4().hex[:6]
    names = (f"Beca Fuga {suffix}", f"Descuento Fuga {suffix}")

    async def _discounts(session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
        made = []
        for kind, name, values in (
            ("scholarship", names[0], {"amount": "50.00"}),
            ("discount", names[1], {"percent": "10"}),
        ):
            discount = await persist_discount(
                session,
                kind=kind,
                audience="assigned",
                name=name,
                eligibility={"charge_kinds": ["package_purchase"]},
                **values,
            )
            await assign_discount(session, discount.id, student_id=w.student)
            made.append(discount.id)
        return made[0], made[1]

    discounts = in_uow(url, _discounts)
    package = pks.sell(url, w.student, pks.product(url, amount="1000.00"), SOLD)
    [row] = run_sql(
        url,
        "SELECT id FROM charges WHERE package_id = :p AND kind = 'package_purchase'",
        {"p": package},
    )
    [plain] = ps.charges(url, (w.student, "120.00", ps.TODAY))
    return DiscountWorld(
        family=w.family,
        guardian=_guardian_of(url, w.family),
        owner=create_user(url, roles=(FINANCE_STAFF,)),
        charge=row.id,
        plain_charge=plain,
        discounts=discounts,
        names=names,
    )


def _detail(client: TestClient, user: CreatedUser, family: object, charge: object):  # type: ignore[no-untyped-def]
    response = _get(client, user, ps.account_path(family, f"/charges/{charge}"))
    assert response.status_code == 200, response.text
    return response.json()


EXPECTED_LINES = [
    # The scholarship applies first (H4), then 10 % of the remainder.
    ("scholarship", 0, None, "50.00", "50.00"),
    ("discount", 1, "10.0000", None, "95.00"),
]


def _lines(body: dict[str, Any], names: tuple[str, str]) -> list[Any]:
    return [
        (
            line["kind"],
            names.index(line["name"]),
            line["percent"],
            line["fixed_amount"],
            line["amount"],
        )
        for line in body["discounts"]
    ]


def test_the_charge_detail_lists_its_discount_lines(client, dw):
    body = _detail(client, dw.guardian, dw.family, dw.charge)

    assert [line["position"] for line in body["discounts"]] == [1, 2]
    assert set(body["discounts"][0]) == {
        "position",
        "kind",
        "name",
        "percent",
        "fixed_amount",
        "amount",
    }
    assert _lines(body, dw.names) == EXPECTED_LINES
    assert (body["list_amount"], body["discount_amount"], body["amount"]) == (
        "1000.00",
        "145.00",
        "855.00",
    )
    staff = _detail(client, dw.owner, dw.family, dw.charge)
    assert staff["discounts"] == body["discounts"]


def test_a_charge_without_discounts_has_an_empty_list(client, dw):
    assert _detail(client, dw.guardian, dw.family, dw.plain_charge)["discounts"] == []


def test_renamed_and_deactivated_discounts_keep_the_posting_names(
    module_seeded_url, client, dw
):
    before = _detail(client, dw.guardian, dw.family, dw.charge)
    run_sql(
        module_seeded_url,
        "UPDATE discounts SET name = name || ' (renombrado)', is_active = false,"
        " version = version + 1 WHERE id IN (:a, :b)",
        {"a": dw.discounts[0], "b": dw.discounts[1]},
    )

    after = _detail(client, dw.guardian, dw.family, dw.charge)

    assert after["discounts"] == before["discounts"]
    assert _lines(after, dw.names) == EXPECTED_LINES


def test_the_discount_lines_cost_one_statement(module_seeded_url, dw):
    guardian = _guardian_principal(dw.guardian)
    with frozen_clock(ps.NOW):
        count, detail = count_statements(
            module_seeded_url,
            lambda s: billing_corrections.get_charge(s, guardian, dw.family, dw.charge),
        )
    assert len(detail.discounts) == 2
    assert count <= 9, count
