"""Family membership: add and remove students and guardian profiles (#172 C).

Owner decision #172 E2 (a): one family per student and per guardian. ``PUT``
is idempotent; a move is remove, then add.
"""

import asyncio
import threading
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.authz import Principal
from app.core.db import create_sessionmaker
from app.modules.families import groupings, service
from app.modules.families.errors import FamilyMemberConflictError
from app.modules.families.models import (
    FAMILY_STUDENT_UNIQUE,
    FamilyStudent,
    Guardian,
)
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    ROLE_PERMISSIONS,
    TUTOR,
)
from tests.factories.base import persist
from tests.factories.families import FamilyFactory, GuardianFactory, persist_student
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import get, patch
from tests.integration.conftest import run_sql, short_lived_engine
from tests.integration.family_support import (
    add,
    added,
    create_family,
    family_event_count,
    family_path,
    family_row,
    guardian_ids,
    members,
    remove,
    set_family_active,
    student_ids,
)
from tests.integration.guardian_support import create_guardian
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of
from tests.integration.student_support import create_student

MISSING = "00000000-0000-4000-8000-000000000999"
KINDS = ["students", "guardians"]


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def owner(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(OWNER,))


@pytest.fixture
def cookie(client, owner):
    return sign_in(client, owner.username)


def new_member(client, cookie, kind: str) -> dict[str, Any]:
    if kind == "students":
        return create_student(client, cookie)
    return create_guardian(client, cookie)


def member_count(url: str, kind: str, member_id: Any) -> int:
    table = "family_students" if kind == "students" else "family_guardians"
    column = "student_id" if kind == "students" else "guardian_id"
    [row] = run_sql(
        url,
        f"SELECT count(*) AS n FROM {table} WHERE {column} = :id",  # noqa: S608
        {"id": member_id},
    )
    return int(row.n)


def principal_for(user_id: uuid.UUID, *roles: str) -> Principal:
    permissions: set[str] = set()
    for role in roles:
        permissions |= ROLE_PERMISSIONS[role]
    return Principal(
        user_id=user_id, roles=frozenset(roles), permissions=frozenset(permissions)
    )


# --- C1/C2: add -----------------------------------------------------------------------


def test_a_family_with_two_students_and_two_guardians(
    module_seeded_url, client, cookie
):
    family = create_family(client, cookie)
    students = [
        create_student(client, cookie, given_names=n, family_names="Ñúñez Familia")
        for n in ("Beto", "Ana")
    ]
    guardians = [
        create_guardian(client, cookie, given_names=n, family_names="Ñúñez Familia")
        for n in ("Carla", "Abel")
    ]
    for student in students:
        added(client, cookie, family["id"], "students", student["id"])
    for guardian in guardians:
        added(client, cookie, family["id"], "guardians", guardian["id"])

    detail = get(client, cookie, family_path(family["id"])).json()

    # By family names, then given names.
    assert student_ids(detail) == [students[1]["id"], students[0]["id"]]
    assert guardian_ids(detail) == [guardians[1]["id"], guardians[0]["id"]]
    assert detail["students"][0] == {
        "student_id": students[1]["id"],
        "given_names": "Ana",
        "family_names": "Ñúñez Familia",
        "is_minor": True,
        "is_active": True,
    }
    assert detail["guardians"][0] == {
        "guardian_id": guardians[1]["id"],
        "given_names": "Abel",
        "family_names": "Ñúñez Familia",
        "phone": None,
        "has_account": False,
        "is_active": True,
    }
    # Membership never changes the family's version (C4).
    assert detail["version"] == 1
    assert family_row(module_seeded_url, family["id"]).version == 1


@pytest.mark.parametrize("kind", KINDS)
def test_adding_the_same_member_again_is_204_without_an_event(
    module_seeded_url, client, cookie, kind
):
    family = create_family(client, cookie)
    member = new_member(client, cookie, kind)
    added(client, cookie, family["id"], kind, member["id"])
    events = family_event_count(module_seeded_url)

    response = add(client, cookie, family["id"], kind, member["id"])

    assert response.status_code == 204
    assert response.content == b""
    assert family_event_count(module_seeded_url) == events
    assert member_count(module_seeded_url, kind, member["id"]) == 1


@pytest.mark.parametrize("kind", KINDS)
def test_a_member_of_another_family_is_409_and_nothing_is_written(
    module_seeded_url, client, cookie, kind
):
    first = create_family(client, cookie)
    second = create_family(client, cookie)
    member = new_member(client, cookie, kind)
    added(client, cookie, first["id"], kind, member["id"])
    events = family_event_count(module_seeded_url)

    response = add(client, cookie, second["id"], kind, member["id"])

    assert code_of(response) == (409, "family_member_conflict")
    assert family_event_count(module_seeded_url) == events
    assert members(module_seeded_url, second["id"]) == (set(), set())
    assert member_count(module_seeded_url, kind, member["id"]) == 1


@pytest.mark.parametrize("kind", KINDS)
def test_remove_then_add_to_another_family_moves_the_member(
    module_seeded_url, client, cookie, kind
):
    first = create_family(client, cookie)
    second = create_family(client, cookie)
    member = new_member(client, cookie, kind)
    added(client, cookie, first["id"], kind, member["id"])

    removed = remove(client, cookie, first["id"], kind, member["id"])
    moved = add(client, cookie, second["id"], kind, member["id"])

    assert removed.status_code == 204
    assert moved.status_code == 204
    index = 0 if kind == "students" else 1
    assert members(module_seeded_url, first["id"])[index] == set()
    assert members(module_seeded_url, second["id"])[index] == {member["id"]}


@pytest.mark.parametrize("kind", KINDS)
def test_an_inactive_family_is_409_family_inactive(
    module_seeded_url, client, cookie, kind
):
    family = set_family_active(client, cookie, create_family(client, cookie), False)
    member = new_member(client, cookie, kind)

    response = add(client, cookie, family["id"], kind, member["id"])

    assert code_of(response) == (409, "family_inactive")
    assert member_count(module_seeded_url, kind, member["id"]) == 0


def test_an_inactive_family_is_checked_before_the_conflict(client, cookie):
    elsewhere = create_family(client, cookie)
    student = create_student(client, cookie)
    added(client, cookie, elsewhere["id"], "students", student["id"])
    inactive = set_family_active(client, cookie, create_family(client, cookie), False)

    response = add(client, cookie, inactive["id"], "students", student["id"])

    assert code_of(response) == (409, "family_inactive")


@pytest.mark.parametrize("kind", KINDS)
def test_inactive_members_may_be_added(client, cookie, kind):
    family = create_family(client, cookie)
    member = new_member(client, cookie, kind)
    path = f"/api/v1/{kind}/{member['id']}"
    off = patch(client, cookie, path, {"version": 1, "is_active": False})
    assert off.status_code == 200, off.text

    assert add(client, cookie, family["id"], kind, member["id"]).status_code == 204


@pytest.mark.parametrize("kind", KINDS)
def test_unknown_ids_are_the_same_404(client, cookie, kind):
    family = create_family(client, cookie)
    member = new_member(client, cookie, kind)

    unknown_member = add(client, cookie, family["id"], kind, MISSING)
    unknown_family = add(client, cookie, MISSING, kind, member["id"])
    # A student id is no guardian, and the other way round.
    other = "guardians" if kind == "students" else "students"
    wrong_kind = add(client, cookie, family["id"], other, member["id"])

    for response in (unknown_member, unknown_family, wrong_kind):
        assert code_of(response) == (404, "not_found")
    bodies = [
        {k: v for k, v in r.json().items() if k != "request_id"}
        for r in (unknown_member, unknown_family, wrong_kind)
    ]
    assert bodies[0] == bodies[1] == bodies[2]


def test_an_unknown_family_is_404_before_inactive_or_conflict(client, cookie):
    student = create_student(client, cookie)
    added(
        client, cookie, create_family(client, cookie)["id"], "students", student["id"]
    )

    assert code_of(add(client, cookie, MISSING, "students", student["id"])) == (
        404,
        "not_found",
    )


# --- C3: remove -----------------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_delete_of_a_non_member_is_404(module_seeded_url, client, cookie, kind):
    family = create_family(client, cookie)
    other = create_family(client, cookie)
    member = new_member(client, cookie, kind)
    added(client, cookie, other["id"], kind, member["id"])

    for response in (
        remove(client, cookie, family["id"], kind, member["id"]),
        remove(client, cookie, family["id"], kind, MISSING),
        remove(client, cookie, MISSING, kind, member["id"]),
    ):
        assert code_of(response) == (404, "not_found")
    assert member_count(module_seeded_url, kind, member["id"]) == 1


@pytest.mark.parametrize("kind", KINDS)
def test_delete_removes_only_the_membership(module_seeded_url, client, cookie, kind):
    family = create_family(client, cookie)
    member = new_member(client, cookie, kind)
    added(client, cookie, family["id"], kind, member["id"])

    response = remove(client, cookie, family["id"], kind, member["id"])

    assert response.status_code == 204
    assert member_count(module_seeded_url, kind, member["id"]) == 0
    assert get(client, cookie, f"/api/v1/{kind}/{member['id']}").status_code == 200
    assert family_row(module_seeded_url, family["id"]).version == 1
    again = remove(client, cookie, family["id"], kind, member["id"])
    assert code_of(again) == (404, "not_found")


def test_membership_does_not_bump_the_member_versions(
    module_seeded_url, client, cookie
):
    family = create_family(client, cookie)
    student = create_student(client, cookie)
    guardian = create_guardian(client, cookie)
    added(client, cookie, family["id"], "students", student["id"])
    added(client, cookie, family["id"], "guardians", guardian["id"])

    assert (
        get(client, cookie, f"/api/v1/students/{student['id']}").json()["version"] == 1
    )
    assert (
        get(client, cookie, f"/api/v1/guardians/{guardian['id']}").json()["version"]
        == 1
    )


def test_a_rename_while_members_are_added_does_not_conflict(client, cookie):
    family = create_family(client, cookie)
    added(
        client, cookie, family["id"], "students", create_student(client, cookie)["id"]
    )

    renamed = patch(
        client, cookie, family_path(family["id"]), {"version": 1, "name": "Otro"}
    )

    assert renamed.status_code == 200, renamed.text


# --- Only family.manage ---------------------------------------------------------------


@pytest.mark.parametrize("role", [ACADEMIC_COORDINATOR, FINANCE_STAFF, TUTOR, GUARDIAN])
def test_others_get_403_on_all_four_routes(module_seeded_url, client, cookie, role):
    family = create_family(client, cookie)
    student = create_student(client, cookie)
    guardian = create_guardian(client, cookie)
    added(client, cookie, family["id"], "students", student["id"])
    person = create_user(module_seeded_url, roles=(role,))
    other = sign_in(client, person.username)
    events = family_event_count(module_seeded_url)
    fresh_student = create_student(client, cookie)

    responses = [
        add(client, other, family["id"], "students", fresh_student["id"]),
        remove(client, other, family["id"], "students", student["id"]),
        add(client, other, family["id"], "guardians", guardian["id"]),
        remove(client, other, family["id"], "guardians", guardian["id"]),
    ]

    assert [code_of(r) for r in responses] == [(403, "permission_denied")] * 4
    assert family_event_count(module_seeded_url) == events
    assert members(module_seeded_url, family["id"]) == ({student["id"]}, set())


def test_an_administrator_may_add_and_remove(module_seeded_url, client, cookie):
    admin = create_user(module_seeded_url, roles=(ADMINISTRATOR,))
    admin_cookie = sign_in(client, admin.username)
    family = create_family(client, cookie)
    student = create_student(client, cookie)

    assert (
        add(client, admin_cookie, family["id"], "students", student["id"]).status_code
        == 204
    )
    assert (
        remove(
            client, admin_cookie, family["id"], "students", student["id"]
        ).status_code
        == 204
    )


# --- Concurrency (E2 (a)) ------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_concurrent_adds_to_two_families_give_one_204_and_one_409(
    module_seeded_url, client, cookie, owner, kind
):
    families = [create_family(client, cookie) for _ in range(2)]
    member = new_member(client, cookie, kind)
    results: list[tuple[int, str | None]] = []
    start = threading.Barrier(2)

    def send(family_id: str) -> None:
        with auth_client(module_seeded_url) as own_client:
            own_cookie = sign_in(own_client, owner.username)
            start.wait(timeout=10)
            results.append(
                code_of(add(own_client, own_cookie, family_id, kind, member["id"]))
            )

    threads = [threading.Thread(target=send, args=(f["id"],)) for f in families]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert sorted(results, key=str) == [(204, None), (409, "family_member_conflict")]
    assert member_count(module_seeded_url, kind, member["id"]) == 1


async def _race(seeded_url: str, *, same_family: bool) -> tuple[Any, int]:
    """The first add holds its uncommitted row; the second waits on
    ``uq_family_students_student_id``, then gets the unique violation (its
    pre-check couldn't see the row)."""
    setup = short_lived_engine(seeded_url)
    first_engine = short_lived_engine(seeded_url)
    second_engine = short_lived_engine(seeded_url)
    try:
        async with create_sessionmaker(setup)() as db, db.begin():
            one = await persist(db, FamilyFactory)
            two = await persist(db, FamilyFactory)
            student = await persist_student(db)
            ids = (one.id, two.id if not same_family else one.id, student.id)
        caller = principal_for(uuid.uuid4(), OWNER)
        async with (
            create_sessionmaker(first_engine)() as first,
            create_sessionmaker(second_engine)() as second,
        ):
            await service.add_family_student(
                first, principal=caller, family_id=ids[0], student_id=ids[2]
            )
            waiting = asyncio.create_task(
                service.add_family_student(
                    second, principal=caller, family_id=ids[1], student_id=ids[2]
                )
            )
            await asyncio.sleep(0.5)
            assert not waiting.done()
            await first.commit()
            outcome: Any
            try:
                outcome = await asyncio.wait_for(waiting, timeout=10)
            except FamilyMemberConflictError as exc:
                outcome = exc
            await second.commit()
        async with create_sessionmaker(setup)() as db:
            count = await db.scalar(
                select(func.count(FamilyStudent.id)).where(
                    FamilyStudent.student_id == ids[2]
                )
            )
        return outcome, int(count or 0)
    finally:
        for engine in (setup, first_engine, second_engine):
            await engine.dispose()


@pytest.mark.anyio
async def test_the_unique_violation_of_a_racing_add_is_409(seeded_url):
    outcome, count = await _race(seeded_url, same_family=False)

    assert isinstance(outcome, FamilyMemberConflictError)
    assert count == 1


@pytest.mark.anyio
async def test_a_racing_add_to_the_same_family_is_the_idempotent_no_op(seeded_url):
    outcome, count = await _race(seeded_url, same_family=True)

    assert outcome is None
    assert count == 1


@pytest.mark.anyio
async def test_another_integrity_error_is_not_swallowed(seeded_url):
    # A guardian's id inserted as a student member: the student foreign key
    # fails, which must not pass for family_member_conflict.
    engine = short_lived_engine(seeded_url)
    try:
        async with create_sessionmaker(engine)() as session:
            family = await persist(session, FamilyFactory)
            guardian = await persist(session, GuardianFactory)
            with pytest.raises(IntegrityError, match="fk_family_students_student_id"):
                await groupings._add_member(  # noqa: SLF001 - the only seam
                    session,
                    family_id=family.id,
                    member_model=Guardian,
                    member_id=guardian.id,
                    membership=FamilyStudent,
                    member_column="student_id",
                    unique=FAMILY_STUDENT_UNIQUE,
                )
            await session.rollback()
    finally:
        await engine.dispose()
