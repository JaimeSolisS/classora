"""Reading tutoring groups and the student record policy (#56 J5, G, H; E4)
with real logins through the real app, plus the statement budgets.

Group G has students A and B (A and B with their own logins) and later D;
group H has student C. W was enrolled in G and withdrew. A guardian linked
to A, a tutor and a student account without a profile see nothing.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import pytest

from app.core.authz import Principal
from app.modules.families.models import Student
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    STUDENT,
    TUTOR,
)
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.schemas import GroupSessionsQuery
from tests.conftest import frozen_clock
from tests.factories.base import persist
from tests.factories.families import GuardianFactory, link_guardian
from tests.integration.auth_support import auth_client, create_user
from tests.integration.catalog_support import count_statements, get
from tests.integration.consent_support import adult_profile_for
from tests.integration.group_support import (
    GROUPS,
    create_group,
    enroll,
    enrollments,
    group_body,
    group_rows,
    set_state,
    withdraw,
)
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, in_uow
from tests.integration.series_support import iso_z, owner_cookie, timeline
from tests.integration.test_class_session_create import (
    CREATED,
    new_room,
    new_subject,
    new_tutor,
)

NOT_FOUND = (404, "group_not_found")


@dataclass
class World:
    now: datetime
    g: dict[str, Any]
    h: dict[str, Any]
    rows: list[Any]
    students: dict[str, uuid.UUID]
    users: dict[str, Any]
    d_enrolled_at: datetime


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Any:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


def _student_user(url: str) -> tuple[Any, uuid.UUID]:
    user = create_user(url, roles=(STUDENT,))
    return user, adult_profile_for(url, user.id)


def _guardian_of(url: str, student_id: uuid.UUID) -> Any:
    user = create_user(url, roles=(GUARDIAN,))

    async def _link(session: Any) -> None:
        guardian = await persist(session, GuardianFactory, user_id=user.id)
        await link_guardian(session, await session.get(Student, student_id), guardian)

    in_uow(url, _link)
    return user


@pytest.fixture(scope="module")
def world(module_seeded_url: str, client: Any) -> World:
    url = module_seeded_url
    tl = timeline()
    subject = new_subject(url)
    tutor = new_tutor(url, qualified_for=[subject])
    room = new_room(url, capacity=10)
    users: dict[str, Any] = {}
    students: dict[str, uuid.UUID] = {}
    for name in ("A", "B", "C", "D", "W"):
        users[name], students[name] = _student_user(url)
    users["unlinked"] = create_user(url, roles=(STUDENT,))
    users["guardian"] = _guardian_of(url, students["A"])
    users["tutor"] = create_user(url, roles=(TUTOR,))
    for role in (OWNER, ADMINISTRATOR, ACADEMIC_COORDINATOR, FINANCE_STAFF):
        users[role] = create_user(url, roles=(role,))

    def make(name: str, members: list[str], start: str) -> dict[str, Any]:
        response = create_group(
            client,
            cookie,
            group_body(
                subject=subject,
                starts_on=tl.monday,
                students=[students[m] for m in members],
                tutor=tutor,
                room=room,
                start_time=start,
                name=name,
            ),
        )
        assert code_of(response) == CREATED, response.text
        created: dict[str, Any] = response.json()
        return created

    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        g = make(f"Grupo G {uuid.uuid4().hex[:6]}", ["A", "B", "W"], "16:00")
        h = make(f"Grupo H {uuid.uuid4().hex[:6]}", ["C"], "18:00")
        [w] = [e for e in enrollments(url, g["id"]) if e.student_id == students["W"]]
        assert code_of(withdraw(client, cookie, g["id"], w.id)) == (200, None)
    rows = group_rows(url, g["id"])
    # Week 1 Monday: completed; Wednesday: confirmed; week 2 Monday: stays
    # offered; Wednesday: cancelled; week 3 Monday: conflict; the rest:
    # confirmed.
    set_state(url, rows[0].id, "completed")
    set_state(url, rows[3].id, "cancelled")
    set_state(url, rows[4].id, "conflict")
    for row in rows[1:2] + rows[5:]:
        set_state(url, row.id, "confirmed")
    # D joins after week 3.
    d_enrolled_at = rows[5].starts_at + timedelta(minutes=1)
    with frozen_clock(d_enrolled_at):
        cookie = owner_cookie(url, client)
        assert code_of(enroll(client, cookie, g["id"], students["D"])) == CREATED
    return World(
        now=tl.now,
        g=g,
        h=h,
        rows=group_rows(url, g["id"]),
        students=students,
        users=users,
        d_enrolled_at=d_enrolled_at,
    )


def cookie_of(client: Any, world: World, name: str) -> Any:
    return sign_in(client, world.users[name].username)


def sessions(client: Any, cookie: Any, group_id: Any, start: datetime, days: int):
    return get(
        client,
        cookie,
        f"{GROUPS}/{group_id}/sessions",
        **{"from": iso_z(start), "to": iso_z(start + timedelta(days=days))},
    )


# --- Staff ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "role", [OWNER, ADMINISTRATOR, ACADEMIC_COORDINATOR, FINANCE_STAFF]
)
def test_staff_see_every_group_with_its_roster(module_seeded_url, client, world, role):
    with frozen_clock(world.now):
        cookie = cookie_of(client, world, role)
        listed = get(client, cookie, GROUPS, q=world.g["name"][:9].lower())
        detail = get(client, cookie, f"{GROUPS}/{world.g['id']}")
        found = sessions(client, cookie, world.g["id"], world.now, 30)

    assert world.g["id"] in [i["id"] for i in listed.json()["items"]]
    item = next(i for i in listed.json()["items"] if i["id"] == world.g["id"])
    assert item["enrolled_count"] == 3
    assert item["tutor"] is not None
    body = detail.json()
    assert code_of(detail) == (200, None)
    assert body["enrolled_count"] == 3
    roster = body["roster"]
    assert len(roster) == 4
    assert [i["withdrawn_at"] is None for i in roster] == [True, True, True, False]
    assert {i["student"]["id"] for i in roster[:3]} == {
        str(world.students[n]) for n in "ABD"
    }
    states = [i["state"] for i in found.json()["items"]]
    assert {"completed", "offered", "cancelled", "conflict", "confirmed"} <= set(states)


# --- Students -------------------------------------------------------------------------


def test_a_student_sees_only_their_groups_without_the_roster(client, world):
    with frozen_clock(world.now):
        cookie = cookie_of(client, world, "A")
        listed = get(client, cookie, GROUPS)
        detail = get(client, cookie, f"{GROUPS}/{world.g['id']}")
        other = get(client, cookie, f"{GROUPS}/{world.h['id']}")
        other_sessions = sessions(client, cookie, world.h["id"], world.now, 30)

    assert [i["id"] for i in listed.json()["items"]] == [world.g["id"]]
    assert listed.json()["items"][0]["enrolled_count"] is None
    body = detail.json()
    assert body["roster"] is None
    assert body["enrolled_count"] is None
    # Only confirmed (or completed) ones: never offered, conflict, cancelled.
    assert {o["state"] for o in body["next_occurrences"]} <= {"confirmed", "completed"}
    assert str(world.rows[2].id) not in detail.text
    for student in ("B", "C", "D", "W"):
        assert str(world.students[student]) not in detail.text
    assert code_of(other) == NOT_FOUND
    assert code_of(other_sessions) == NOT_FOUND


def test_a_student_sees_only_their_confirmed_and_completed_sessions(client, world):
    first = world.rows[0].starts_at - timedelta(hours=1)
    with frozen_clock(world.now):
        a = sessions(client, cookie_of(client, world, "A"), world.g["id"], first, 40)
    with frozen_clock(world.d_enrolled_at):
        d = sessions(client, cookie_of(client, world, "D"), world.g["id"], first, 40)

    a_ids = [i["id"] for i in a.json()["items"]]
    expected = [
        str(r.id)
        for r in world.rows
        if r.state in ("confirmed", "completed") and r.starts_at < first + timedelta(40)
    ]
    assert a_ids == expected
    assert str(world.rows[2].id) not in a_ids  # offered
    assert str(world.rows[4].id) not in a_ids  # conflict
    d_ids = [i["id"] for i in d.json()["items"]]
    assert d_ids == [
        i for i in expected if i not in {str(r.id) for r in world.rows[:6]}
    ]
    assert d_ids  # D sees the sessions after their enrollment only


def test_a_withdrawn_student_and_other_roles_see_nothing(client, world):
    with frozen_clock(world.now):
        for name in ("W", "guardian", "tutor", "unlinked", "C"):
            cookie = cookie_of(client, world, name)
            detail = get(client, cookie, f"{GROUPS}/{world.g['id']}")
            found = sessions(client, cookie, world.g["id"], world.now, 30)
            listed = get(client, cookie, GROUPS).json()["items"]

            assert code_of(detail) == NOT_FOUND, name
            assert code_of(found) == NOT_FOUND, name
            assert world.g["id"] not in [i["id"] for i in listed], name
            if name != "C":
                assert listed == [], name


def test_status_filter_and_unknown_group(module_seeded_url, client, world):
    with frozen_clock(world.now):
        cookie = cookie_of(client, world, OWNER)
        ended = get(client, cookie, GROUPS, status="ended")
        everything = get(client, cookie, GROUPS, status="all", limit=100)
        unknown = get(client, cookie, f"{GROUPS}/{uuid.uuid4()}")
        bad_range = get(
            client,
            cookie,
            f"{GROUPS}/{world.g['id']}/sessions",
            **{"from": iso_z(world.now), "to": iso_z(world.now + timedelta(days=46))},
        )

    assert world.g["id"] not in [i["id"] for i in ended.json()["items"]]
    assert world.g["id"] in [i["id"] for i in everything.json()["items"]]
    assert code_of(unknown) == NOT_FOUND
    assert [e["code"] for e in bad_range.json()["errors"]] == ["range_too_long"]


def test_the_list_pages_by_name(module_seeded_url, client, world):
    with frozen_clock(world.now):
        cookie = cookie_of(client, world, OWNER)
        first = get(client, cookie, GROUPS, status="all", limit=1)
        second = get(
            client,
            cookie,
            GROUPS,
            status="all",
            limit=1,
            cursor=first.json()["next_cursor"],
        )
        broken = get(client, cookie, GROUPS, cursor="nope")

    assert len(first.json()["items"]) == 1
    assert first.json()["next_cursor"] is not None
    assert second.json()["items"][0]["name"].lower() >= (
        first.json()["items"][0]["name"].lower()
    )
    assert second.json()["items"][0]["id"] != first.json()["items"][0]["id"]
    assert code_of(broken) == (400, "invalid_cursor")


# --- Statement budgets (G1 <= 8, H3 <= 6) ---------------------------------------------


def _principal(user: Any, roles: set[str], permissions: set[str]) -> Principal:
    return Principal(
        user_id=user.id, roles=frozenset(roles), permissions=frozenset(permissions)
    )


def test_the_statement_budgets(module_seeded_url, world):
    url = module_seeded_url
    staff = _principal(world.users[OWNER], {OWNER}, {"schedule.view"})
    student = _principal(world.users["A"], {STUDENT}, set())
    group_id = uuid.UUID(world.g["id"])
    query = GroupSessionsQuery.model_validate(
        {"from": world.now, "to": world.now + timedelta(days=30)}
    )

    with frozen_clock(world.now):
        staff_count, staff_detail = count_statements(
            url, lambda s: scheduling.get_group(s, staff, group_id)
        )
        student_count, student_detail = count_statements(
            url, lambda s: scheduling.get_group(s, student, group_id)
        )
        staff_sessions, _ = count_statements(
            url, lambda s: scheduling.list_group_sessions(s, staff, group_id, query)
        )
        student_sessions, _ = count_statements(
            url, lambda s: scheduling.list_group_sessions(s, student, group_id, query)
        )

    assert staff_count <= 8
    assert student_count <= 8
    assert staff_sessions <= 6
    assert student_sessions <= 6
    assert staff_detail.roster is not None
    assert student_detail.roster is None
