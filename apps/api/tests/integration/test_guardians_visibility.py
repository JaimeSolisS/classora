"""Guardian record policies and IDOR (#34 F2, F3, F5; #33 F6 harness).

Real logins through the #21/#23 harness. A hidden student or guardian must be
indistinguishable from a missing one (``assert_same_not_found``). Facts load
on every request, so unlinking, deactivating a profile, moving its account
and removing the ``guardian`` role apply on the guardian's next request.
"""

import asyncio
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from app.core.authz import Principal
from app.core.db import create_sessionmaker
from app.core.pagination import OffsetParams
from app.core.uow import UnitOfWork
from app.modules.families import service
from app.modules.families.assignments import NoTutorAssignments
from app.modules.identity import service as identity_service
from app.modules.identity.permissions import (
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    ROLE_PERMISSIONS,
    STUDENT,
    TUTOR,
)
from tests.authz.assertions import assert_same_not_found
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import count_statements, get, patch, post
from tests.integration.conftest import short_lived_engine
from tests.integration.guardian_support import (
    GUARDIANS,
    create_guardian,
    guardian_body,
    guardian_path,
    linked,
    set_active,
    unlink,
)
from tests.integration.guardian_support import link as link_request
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, in_uow
from tests.integration.student_support import (
    STUDENTS,
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
    """Minors S1, S2, S3 and adult SA; guardian users U1, U2 (and a spare U3)
    with profiles G1 (U1) and G2 (U2); links G1–S1, G1–S2, G1–SA (mother),
    G2–S1, G2–S3 (father)."""

    word: str
    s: dict[str, dict[str, Any]]
    u: dict[str, CreatedUser]
    g: dict[str, dict[str, Any]]


def build_world(url: str, client: TestClient, staff_cookie) -> World:
    word = suffix()
    students = {
        name: create_student(
            client,
            staff_cookie,
            given_names=name,
            family_names=f"Familia {word}",
            **({"birthdate": "2000-01-01"} if name == "SA" else {}),
        )
        for name in ("S1", "S2", "S3", "SA")
    }
    users = {name: create_user(url, roles=(GUARDIAN,)) for name in ("U1", "U2", "U3")}
    guardians = {
        "G1": create_guardian(client, staff_cookie, user_id=str(users["U1"].id)),
        "G2": create_guardian(client, staff_cookie, user_id=str(users["U2"].id)),
    }
    for student in ("S1", "S2", "SA"):
        linked(client, staff_cookie, students[student]["id"], guardians["G1"]["id"])
    for student in ("S1", "S3"):
        linked(
            client,
            staff_cookie,
            students[student]["id"],
            guardians["G2"]["id"],
            "father",
        )
    return World(word=word, s=students, u=users, g=guardians)


@pytest.fixture(scope="module")
def world(module_seeded_url, client, owner) -> World:
    """A shared world for the read-only tests."""
    return build_world(module_seeded_url, client, sign_in(client, owner.username))


@pytest.fixture
def fresh(module_seeded_url, client, staff) -> World:
    """A world of its own for a test that changes links, profiles or roles."""
    return build_world(module_seeded_url, client, staff)


def listed(client, cookie, **params: Any) -> set[str]:
    response = get(client, cookie, STUDENTS, status="all", limit=100, **params)
    assert response.status_code == 200, response.text
    return {item["id"] for item in response.json()["items"]}


def ids_of(world: World, *names: str) -> set[str]:
    return {world.s[name]["id"] for name in names}


def detail(client, cookie, student: dict[str, Any]):
    return get(client, cookie, student_path(student["id"]))


def missing_student(client, cookie):
    return get(client, cookie, student_path(MISSING))


def missing_guardian(client, cookie):
    return get(client, cookie, guardian_path(MISSING))


def guardian_ids(client, cookie, **params: Any) -> list[str]:
    response = get(client, cookie, GUARDIANS, **params)
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


# --- Read-only (shared world) -----------------------------------------------------


def test_u1_sees_exactly_their_linked_students(client, world):
    u1 = sign_in(client, world.u["U1"].username)

    assert listed(client, u1) == ids_of(world, "S1", "S2", "SA")
    assert listed(client, u1, q=f"S3 {world.word}") <= ids_of(world, "S1", "S2", "SA")
    assert world.s["S3"]["id"] not in listed(client, u1, q="S3")
    assert_same_not_found(
        detail(client, u1, world.s["S3"]), missing_student(client, u1)
    )


def test_u1_gets_full_access_to_a_minor_and_limited_to_an_adult(client, world):
    u1 = sign_in(client, world.u["U1"].username)

    minor = detail(client, u1, world.s["S1"])
    adult = detail(client, u1, world.s["SA"])

    assert minor.status_code == 200
    assert minor.json()["personal"]["birthdate"] == "2012-05-04"
    assert adult.status_code == 200
    assert adult.json()["is_minor"] is False
    assert adult.json()["personal"] is None
    # A guardian never sees the co-guardians.
    assert (minor.json()["guardians"], adult.json()["guardians"]) == (None, None)


def test_u2_sees_the_shared_child_and_their_other_child(client, world):
    u2 = sign_in(client, world.u["U2"].username)

    assert listed(client, u2) == ids_of(world, "S1", "S3")
    assert detail(client, u2, world.s["S1"]).json()["personal"] is not None
    assert_same_not_found(
        detail(client, u2, world.s["S2"]), missing_student(client, u2)
    )


def test_u1_sees_only_their_own_guardian_profile(client, world):
    u1 = sign_in(client, world.u["U1"].username)

    assert_same_not_found(
        get(client, u1, guardian_path(world.g["G2"]["id"])),
        missing_guardian(client, u1),
    )
    assert guardian_ids(client, u1) == [world.g["G1"]["id"]]
    assert guardian_ids(client, u1, status="all", q="Laura") == [world.g["G1"]["id"]]
    assert guardian_ids(client, u1, status="inactive") == []
    own = get(client, u1, guardian_path(world.g["G1"]["id"]))
    assert own.status_code == 200
    body = own.json()
    assert body["account"] is None
    assert body["has_account"] is True
    assert {s["student_id"] for s in body["students"]} == ids_of(
        world, "S1", "S2", "SA"
    )


def test_u1_may_not_change_profiles_or_links(client, world):
    u1 = sign_in(client, world.u["U1"].username)
    g1, s1, s3 = world.g["G1"], world.s["S1"], world.s["S3"]

    responses = [
        patch(client, u1, guardian_path(g1["id"]), {"version": 1, "phone": None}),
        post(client, u1, GUARDIANS, guardian_body()),
        link_request(client, u1, s3["id"], g1["id"]),
        unlink(client, u1, s1["id"], g1["id"]),
    ]

    assert [code_of(r) for r in responses] == [(403, "permission_denied")] * 4


def test_a_guardian_without_a_profile_sees_nothing(module_seeded_url, client, world):
    person = sign_in(client, create_user(module_seeded_url, roles=(GUARDIAN,)).username)

    assert listed(client, person) == set()
    assert guardian_ids(client, person, status="all") == []
    for student in world.s.values():
        assert_same_not_found(
            detail(client, person, student), missing_student(client, person)
        )
    assert_same_not_found(
        get(client, person, guardian_path(world.g["G1"]["id"])),
        missing_guardian(client, person),
    )


def test_a_profile_without_an_account_grants_nothing(
    module_seeded_url, client, staff, world
):
    orphan = create_guardian(client, staff)
    linked(client, staff, world.s["S2"]["id"], orphan["id"])
    person = sign_in(client, create_user(module_seeded_url, roles=(GUARDIAN,)).username)

    assert listed(client, person) == set()
    assert_same_not_found(
        detail(client, person, world.s["S2"]), missing_student(client, person)
    )


@pytest.mark.parametrize("role", [TUTOR, STUDENT])
def test_tutors_and_students_may_not_read_guardians(
    module_seeded_url, client, world, role
):
    person = sign_in(client, create_user(module_seeded_url, roles=(role,)).username)

    assert code_of(get(client, person, GUARDIANS)) == (403, "permission_denied")
    assert code_of(get(client, person, guardian_path(world.g["G1"]["id"]))) == (
        403,
        "permission_denied",
    )


def test_a_guardian_who_is_a_tutor_sees_links_not_other_students(
    module_seeded_url, client, staff, world
):
    user = create_user(module_seeded_url, roles=(TUTOR, GUARDIAN))
    profile = create_guardian(client, staff, user_id=str(user.id))
    linked(client, staff, world.s["S3"]["id"], profile["id"], "grandparent")
    person = sign_in(client, user.username)

    assert listed(client, person) == ids_of(world, "S3")


# --- F2: fact loading -----------------------------------------------------------------


def principal_for(user_id: uuid.UUID, *roles: str) -> Principal:
    permissions: set[str] = set()
    for role in roles:
        permissions |= ROLE_PERMISSIONS[role]
    return Principal(
        user_id=user_id, roles=frozenset(roles), permissions=frozenset(permissions)
    )


def list_call(caller: Principal):
    return lambda session: service.list_students(
        session,
        principal=caller,
        source=NoTutorAssignments(),
        q=None,
        status="all",
        education_level_id=None,
        page=OffsetParams(limit=25, offset=0),
    )


def test_a_guardian_list_makes_one_statement_more_than_a_staff_list(
    module_seeded_url, owner, world
):
    staff_count, _ = count_statements(
        module_seeded_url, list_call(principal_for(owner.id, OWNER))
    )
    guardian_count, page = count_statements(
        module_seeded_url, list_call(principal_for(world.u["U1"].id, GUARDIAN))
    )

    assert guardian_count == staff_count + 1
    assert {str(item.id) for item in page.items} == ids_of(world, "S1", "S2", "SA")


@pytest.mark.parametrize("roles", [(OWNER,), (FINANCE_STAFF,), (FINANCE_STAFF, TUTOR)])
def test_staff_requests_never_query_links(module_seeded_url, owner, roles):
    caller = principal_for(owner.id, *roles)
    statements, facts = count_statements(
        module_seeded_url,
        lambda session: service.load_access_facts(
            session, caller, NoTutorAssignments()
        ),
    )
    assert statements == 0
    assert facts.linked_student_ids == frozenset()

    _, sent = statements_of(module_seeded_url, list_call(caller))
    assert sent
    assert not [sql for sql in sent if "student_guardians" in sql]


def statements_of(url: str, call: Any) -> tuple[Any, list[str]]:
    """``call(session)``'s result and the SQL it sent."""
    seen: list[str] = []

    async def _run() -> Any:
        engine = short_lived_engine(url)

        def record(conn, cursor, statement, parameters, context, executemany) -> None:  # type: ignore[no-untyped-def]
            seen.append(statement)

        event.listen(engine.sync_engine, "before_cursor_execute", record)
        try:
            async with UnitOfWork(create_sessionmaker(engine)) as uow:
                seen.clear()
                return await call(uow.session)
        finally:
            await engine.dispose()

    result = asyncio.run(_run())
    return result, seen


# --- F5: access follows the facts on the next request (same session) --------------


def test_unlinking_revokes_access_on_the_next_request(client, staff, fresh):
    u1 = sign_in(client, fresh.u["U1"].username)
    assert detail(client, u1, fresh.s["S2"]).status_code == 200

    assert (
        unlink(client, staff, fresh.s["S2"]["id"], fresh.g["G1"]["id"]).status_code
        == 204
    )

    assert_same_not_found(
        detail(client, u1, fresh.s["S2"]), missing_student(client, u1)
    )
    assert listed(client, u1) == ids_of(fresh, "S1", "SA")


def test_deactivating_the_profile_revokes_access_and_reactivating_restores_it(
    client, staff, fresh
):
    u1 = sign_in(client, fresh.u["U1"].username)
    g1 = set_active(client, staff, fresh.g["G1"], False)

    assert_same_not_found(
        detail(client, u1, fresh.s["S1"]), missing_student(client, u1)
    )
    assert listed(client, u1) == set()
    assert_same_not_found(
        get(client, u1, guardian_path(g1["id"])), missing_guardian(client, u1)
    )
    assert guardian_ids(client, u1, status="all") == []

    set_active(client, staff, g1, True)

    assert detail(client, u1, fresh.s["S1"]).status_code == 200
    assert listed(client, u1) == ids_of(fresh, "S1", "S2", "SA")


def test_moving_the_account_moves_the_access(client, staff, fresh):
    u2 = sign_in(client, fresh.u["U2"].username)
    assert listed(client, u2) == ids_of(fresh, "S1", "S3")

    moved = patch(
        client,
        staff,
        guardian_path(fresh.g["G2"]["id"]),
        {"version": fresh.g["G2"]["version"], "user_id": str(fresh.u["U3"].id)},
    )
    assert moved.status_code == 200, moved.text

    assert listed(client, u2) == set()
    assert_same_not_found(
        detail(client, u2, fresh.s["S3"]), missing_student(client, u2)
    )
    assert guardian_ids(client, u2, status="all") == []
    u3 = sign_in(client, fresh.u["U3"].username)
    assert listed(client, u3) == ids_of(fresh, "S1", "S3")


def test_removing_the_guardian_role_revokes_access(
    module_seeded_url, client, staff, fresh
):
    user = create_user(module_seeded_url, roles=(TUTOR, GUARDIAN))
    profile = create_guardian(client, staff, user_id=str(user.id))
    linked(client, staff, fresh.s["S1"]["id"], profile["id"])
    person = sign_in(client, user.username)
    assert detail(client, person, fresh.s["S1"]).status_code == 200

    in_uow(
        module_seeded_url,
        lambda session: identity_service.remove_user_role(
            session, user_id=user.id, role=GUARDIAN
        ),
    )

    # #25: removing a role revokes the sessions; the next login is a tutor's.
    assert code_of(detail(client, person, fresh.s["S1"]))[0] == 401
    tutor = sign_in(client, user.username)
    assert_same_not_found(
        detail(client, tutor, fresh.s["S1"]), missing_student(client, tutor)
    )
    assert listed(client, tutor) == set()
