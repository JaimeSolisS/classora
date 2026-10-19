"""Family record policy and IDOR (#172 D, E; #33 F6 harness).

Real logins. A hidden family must be indistinguishable from a missing one
(``assert_same_not_found``). **Membership is not access:** a guardian sees a
student's record only through a #34 link, never through a shared family.
Facts load on every request, so removing a guardian from a family or
deactivating their profile applies on their next request.
"""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.authz import Principal
from app.core.pagination import OffsetParams
from app.modules.families import service
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    ROLE_PERMISSIONS,
    TUTOR,
)
from tests.authz.assertions import assert_same_not_found
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import count_statements, get, patch
from tests.integration.family_support import (
    FAMILIES,
    add,
    added,
    create_family,
    family_path,
    guardian_ids,
    remove,
    set_family_active,
    student_ids,
)
from tests.integration.guardian_support import (
    create_guardian,
    guardian_path,
    linked,
    set_active,
)
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of
from tests.integration.student_support import (
    assigned,
    create_student,
    student_path,
    suffix,
)

MISSING = "00000000-0000-4000-8000-000000000999"


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def owner(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(OWNER,))


@pytest.fixture
def staff(client, owner):
    return sign_in(client, owner.username)


@dataclass
class World:
    """Family A: students SA1, SA2 and guardian GA (user UA); family B:
    student SB1 and guardian GB (user UB); links GA–SA1 and GB–SB1. **SA2 is
    in family A but not linked to GA.**"""

    f: dict[str, dict[str, Any]]
    s: dict[str, dict[str, Any]]
    u: dict[str, CreatedUser]
    g: dict[str, dict[str, Any]]


def build_world(url: str, client: TestClient, staff_cookie) -> World:
    word = suffix()
    families = {
        "A": create_family(client, staff_cookie, name=f"Familia A {word}"),
        "B": create_family(client, staff_cookie, name=f"Familia B {word}"),
    }
    students = {
        name: create_student(
            client, staff_cookie, given_names=name, family_names=f"Hijo {word}"
        )
        for name in ("SA1", "SA2", "SB1")
    }
    users = {name: create_user(url, roles=(GUARDIAN,)) for name in ("UA", "UB")}
    guardians = {
        "GA": create_guardian(client, staff_cookie, user_id=str(users["UA"].id)),
        "GB": create_guardian(client, staff_cookie, user_id=str(users["UB"].id)),
    }
    for family, kind, member in (
        ("A", "students", students["SA1"]),
        ("A", "students", students["SA2"]),
        ("A", "guardians", guardians["GA"]),
        ("B", "students", students["SB1"]),
        ("B", "guardians", guardians["GB"]),
    ):
        added(client, staff_cookie, families[family]["id"], kind, member["id"])
    linked(client, staff_cookie, students["SA1"]["id"], guardians["GA"]["id"])
    linked(client, staff_cookie, students["SB1"]["id"], guardians["GB"]["id"])
    return World(f=families, s=students, u=users, g=guardians)


@pytest.fixture(scope="module")
def world(module_seeded_url, client, owner) -> World:
    """A shared world for the read-only tests."""
    return build_world(module_seeded_url, client, sign_in(client, owner.username))


@pytest.fixture
def fresh(module_seeded_url, client, staff) -> World:
    """A world of its own for a test that changes memberships or profiles."""
    return build_world(module_seeded_url, client, staff)


def family_ids(client, cookie, **params: Any) -> list[str]:
    response = get(client, cookie, FAMILIES, status="all", limit=100, **params)
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


def missing_family(client, cookie):
    return get(client, cookie, family_path(MISSING))


def missing_student(client, cookie):
    return get(client, cookie, student_path(MISSING))


def as_user(client, world: World, name: str):
    return sign_in(client, world.u[name].username)


# --- D4: IDOR -------------------------------------------------------------------------


def test_ua_sees_only_family_a(client, world):
    ua = as_user(client, world, "UA")

    assert family_ids(client, ua) == [world.f["A"]["id"]]
    assert_same_not_found(
        get(client, ua, family_path(world.f["B"]["id"])), missing_family(client, ua)
    )


def test_ua_sees_both_students_of_a_and_only_their_own_guardian_profile(client, world):
    ua = as_user(client, world, "UA")

    response = get(client, ua, family_path(world.f["A"]["id"]))

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(student_ids(body)) == {world.s["SA1"]["id"], world.s["SA2"]["id"]}
    assert guardian_ids(body) == [world.g["GA"]["id"]]


def test_ub_sees_only_family_b(client, world):
    ub = as_user(client, world, "UB")

    assert family_ids(client, ub) == [world.f["B"]["id"]]
    assert_same_not_found(
        get(client, ub, family_path(world.f["A"]["id"])), missing_family(client, ub)
    )


def test_a_guardian_of_family_a_requesting_a_student_of_family_b_gets_404(
    client, world
):
    # The original issue's IDOR test.
    ua = as_user(client, world, "UA")

    assert_same_not_found(
        get(client, ua, student_path(world.s["SB1"]["id"])),
        missing_student(client, ua),
    )


def test_membership_is_not_access(client, world):
    # SA2 is in UA's family, but GA isn't linked to SA2.
    ua = as_user(client, world, "UA")

    assert_same_not_found(
        get(client, ua, student_path(world.s["SA2"]["id"])),
        missing_student(client, ua),
    )
    assert get(client, ua, student_path(world.s["SA1"]["id"])).status_code == 200
    students = get(client, ua, "/api/v1/students", status="all", limit=100).json()
    assert [item["id"] for item in students["items"]] == [world.s["SA1"]["id"]]


def test_ua_may_not_change_families_or_memberships(client, staff, world):
    ua = as_user(client, world, "UA")
    a = world.f["A"]["id"]
    spare = create_student(client, staff)

    responses = [
        patch(client, ua, family_path(a), {"version": 1, "name": "Mía"}),
        add(client, ua, a, "students", spare["id"]),
        remove(client, ua, a, "students", world.s["SA2"]["id"]),
        add(client, ua, a, "guardians", world.g["GB"]["id"]),
        remove(client, ua, a, "guardians", world.g["GA"]["id"]),
    ]

    assert [code_of(r) for r in responses] == [(403, "permission_denied")] * 5


def test_removing_the_guardian_from_the_family_hides_it_on_the_next_request(
    client, staff, fresh
):
    ua = as_user(client, fresh, "UA")
    a = fresh.f["A"]["id"]
    assert get(client, ua, family_path(a)).status_code == 200

    assert remove(client, staff, a, "guardians", fresh.g["GA"]["id"]).status_code == 204

    assert_same_not_found(get(client, ua, family_path(a)), missing_family(client, ua))
    assert family_ids(client, ua) == []
    # The link remains: UA still sees SA1.
    assert get(client, ua, student_path(fresh.s["SA1"]["id"])).status_code == 200


def test_a_deactivated_profile_sees_no_family(client, staff, fresh):
    ua = as_user(client, fresh, "UA")
    a = fresh.f["A"]["id"]

    set_active(client, staff, fresh.g["GA"], False)

    assert family_ids(client, ua) == []
    assert_same_not_found(get(client, ua, family_path(a)), missing_family(client, ua))


def test_an_inactive_family_stays_visible_to_its_guardians(client, staff, fresh):
    ua = as_user(client, fresh, "UA")
    set_family_active(client, staff, fresh.f["A"], False)

    assert get(client, ua, family_path(fresh.f["A"]["id"])).status_code == 200
    assert family_ids(client, ua) == [fresh.f["A"]["id"]]
    # The default filter (active) hides it from the list.
    default = get(client, ua, FAMILIES).json()
    assert (default["items"], default["total"]) == ([], 0)


def test_a_guardian_without_a_profile_or_family_sees_nothing(
    module_seeded_url, client, staff, world
):
    no_profile = create_user(module_seeded_url, roles=(GUARDIAN,))
    no_family_user = create_user(module_seeded_url, roles=(GUARDIAN,))
    create_guardian(client, staff, user_id=str(no_family_user.id))

    for user in (no_profile, no_family_user):
        cookie = sign_in(client, user.username)
        assert family_ids(client, cookie) == []
        assert_same_not_found(
            get(client, cookie, family_path(world.f["A"]["id"])),
            missing_family(client, cookie),
        )


def test_a_guardian_sees_the_family_of_their_own_profile(
    module_seeded_url, client, staff, world
):
    user = create_user(module_seeded_url, roles=(GUARDIAN,))
    own = create_guardian(client, staff, user_id=str(user.id))
    other = create_family(client, staff)
    added(client, staff, other["id"], "guardians", own["id"])
    cookie = sign_in(client, user.username)

    assert family_ids(client, cookie) == [other["id"]]


def test_a_tutor_who_is_a_guardian_sees_only_their_family(
    module_seeded_url, client, staff, world
):
    user = create_user(module_seeded_url, roles=(TUTOR, GUARDIAN))
    profile = create_guardian(client, staff, user_id=str(user.id))
    family = create_family(client, staff)
    added(client, staff, family["id"], "guardians", profile["id"])
    cookie = sign_in(client, user.username)

    assert family_ids(client, cookie) == [family["id"]]
    assert_same_not_found(
        get(client, cookie, family_path(world.f["A"]["id"])),
        missing_family(client, cookie),
    )
    detail = get(client, cookie, family_path(family["id"])).json()
    assert guardian_ids(detail) == [profile["id"]]


@pytest.mark.parametrize("role", [OWNER, ACADEMIC_COORDINATOR, FINANCE_STAFF])
def test_staff_see_every_guardian_of_a_family(module_seeded_url, client, world, role):
    person = sign_in(client, create_user(module_seeded_url, roles=(role,)).username)

    body = get(client, person, family_path(world.f["A"]["id"])).json()

    assert guardian_ids(body) == [world.g["GA"]["id"]]
    assert world.f["B"]["id"] in family_ids(client, person)


# --- Fact loading -------------------------------------------------------------------


def principal_for(user_id: uuid.UUID, *roles: str) -> Principal:
    permissions: set[str] = set()
    for role in roles:
        permissions |= ROLE_PERMISSIONS[role]
    return Principal(
        user_id=user_id, roles=frozenset(roles), permissions=frozenset(permissions)
    )


def list_call(caller: Principal):
    return lambda session: service.list_families(
        session,
        principal=caller,
        q=None,
        status="all",
        page=OffsetParams(limit=10, offset=0),
    )


def test_a_guardian_list_makes_one_statement_more_than_a_staff_list(
    module_seeded_url, owner, world
):
    staff_count, _ = count_statements(
        module_seeded_url, list_call(principal_for(owner.id, OWNER))
    )
    guardian_count, page = count_statements(
        module_seeded_url, list_call(principal_for(world.u["UA"].id, GUARDIAN))
    )
    none_count, empty = count_statements(
        module_seeded_url, list_call(principal_for(uuid.uuid4(), GUARDIAN))
    )

    assert staff_count == 2
    assert guardian_count == 3
    assert [item.id for item in page.items] == [uuid.UUID(world.f["A"]["id"])]
    # No family: only the fact statement.
    assert (none_count, empty.items) == (1, [])


def test_staff_requests_never_load_family_facts(module_seeded_url, owner, world):
    statements, detail = count_statements(
        module_seeded_url,
        lambda session: service.get_family(
            session,
            principal=principal_for(owner.id, OWNER),
            family_id=uuid.UUID(world.f["A"]["id"]),
        ),
    )

    # The family, its students and its guardians.
    assert statements == 3
    assert len(detail.students) == 2


# --- E: the family on the student and guardian details -----------------------------


@pytest.mark.parametrize("role", [OWNER, FINANCE_STAFF])
def test_owner_and_finance_see_the_family_on_both_details(
    module_seeded_url, client, world, role
):
    person = sign_in(client, create_user(module_seeded_url, roles=(role,)).username)
    expected = {"id": world.f["A"]["id"], "name": world.f["A"]["name"]}

    student = get(client, person, student_path(world.s["SA1"]["id"])).json()
    guardian = get(client, person, guardian_path(world.g["GA"]["id"])).json()

    assert student["family"] == expected
    assert guardian["family"] == expected


def test_a_detail_without_a_family_has_null(client, staff):
    student = create_student(client, staff)
    guardian = create_guardian(client, staff)

    assert get(client, staff, student_path(student["id"])).json()["family"] is None
    assert get(client, staff, guardian_path(guardian["id"])).json()["family"] is None
    assert student["family"] is None
    assert guardian["family"] is None


def test_a_tutor_gets_null(module_seeded_url, client, world):
    tutor = create_user(module_seeded_url, roles=(TUTOR,))
    cookie = sign_in(client, tutor.username)

    with assigned(client, {tutor.id: {world.s["SA1"]["id"]}}):
        response = get(client, cookie, student_path(world.s["SA1"]["id"]))

    assert response.status_code == 200, response.text
    assert response.json()["family"] is None


def test_a_guardian_sees_the_family_on_their_own_profile_only(client, world):
    ua = as_user(client, world, "UA")

    own = get(client, ua, guardian_path(world.g["GA"]["id"])).json()
    child = get(client, ua, student_path(world.s["SA1"]["id"])).json()

    assert own["family"] == {"id": world.f["A"]["id"], "name": world.f["A"]["name"]}
    assert child["family"] is None


def test_the_edit_responses_carry_the_family(client, staff, world):
    response = patch(
        client,
        staff,
        guardian_path(world.g["GB"]["id"]),
        {"version": world.g["GB"]["version"]},
    )

    assert response.json()["family"]["id"] == world.f["B"]["id"]
