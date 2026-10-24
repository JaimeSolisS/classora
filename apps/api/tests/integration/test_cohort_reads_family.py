"""Reading cohorts with the student and guardian record policy (#66 J4; G1-G5,
F1, F3; E4) with real logins through the real app, plus the statement
budgets.

Cohort A (Monday/Wednesday 16:00, ten sessions) has the students S (own
login), M (a minor), AD (an adult without academic consent), K and, from
week 4 on, L (own login); W (own login) enrolled and withdrew. Cohort B has
T (own login). The guardians: GM of M, GA of AD, G2 of M and K, G3 of M and
N (N not enrolled), GR of M (the link is removed), GD of M (the profile is
deactivated). A tutor and a student account without a profile see nothing.

A's rows: 0 completed, 1 confirmed, 2 offered, 3 cancelled, 4 conflict, 5
skipped_closure, 6 unassigned, 7-9 confirmed.
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
from app.modules.scheduling.schemas import CohortSessionsQuery
from app.workflows import cohorts
from tests.conftest import frozen_clock
from tests.factories.base import persist
from tests.factories.families import (
    ADULT_BIRTHDATE,
    GuardianFactory,
    link_guardian,
    persist_student,
)
from tests.integration.auth_support import auth_client, create_user
from tests.integration.catalog_support import count_statements, get
from tests.integration.cohort_support import (
    COHORTS,
    cohort_body,
    cohort_rows,
    create_cohort,
    enroll,
    new_template,
    withdraw,
)
from tests.integration.conftest import run_sql
from tests.integration.consent_support import adult_profile_for
from tests.integration.group_support import set_state
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, in_uow
from tests.integration.series_support import iso_z, owner_cookie, timeline
from tests.integration.test_class_session_create import (
    CREATED,
    new_room,
    new_subject,
    new_tutor,
)

NOT_FOUND = (404, "cohort_not_found")
OK = (200, None)
FAMILY_STATES = {"confirmed", "completed"}
STAFF_ROLES = (OWNER, ADMINISTRATOR, ACADEMIC_COORDINATOR, FINANCE_STAFF)


@dataclass
class World:
    now: datetime
    a: dict[str, Any]
    b: dict[str, Any]
    rows: list[Any]
    students: dict[str, uuid.UUID]
    users: dict[str, Any]
    guardians: dict[str, uuid.UUID]
    late_at: datetime


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Any:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


def _student(url: str, *, adult: bool = False) -> uuid.UUID:
    async def _make(session: Any) -> uuid.UUID:
        values = {"birthdate": ADULT_BIRTHDATE} if adult else {}
        return (await persist_student(session, contacts=0, **values)).id

    return in_uow(url, _make)


def _guardian_of(
    url: str, student_ids: list[uuid.UUID], *, roles: tuple[str, ...] = (GUARDIAN,)
) -> tuple[Any, uuid.UUID]:
    user = create_user(url, roles=roles)

    async def _link(session: Any) -> uuid.UUID:
        guardian = await persist(session, GuardianFactory, user_id=user.id)
        for student_id in student_ids:
            await link_guardian(
                session, await session.get(Student, student_id), guardian
            )
        return guardian.id

    return user, in_uow(url, _link)


@pytest.fixture(scope="module")
def world(module_seeded_url: str, client: Any) -> World:
    url = module_seeded_url
    tl = timeline()
    subject = new_subject(url)
    tutor = new_tutor(url, qualified_for=[subject])
    room = new_room(url, capacity=20)
    users: dict[str, Any] = {}
    students: dict[str, uuid.UUID] = {}
    guardians: dict[str, uuid.UUID] = {}
    for name in ("S", "T", "W", "L"):
        users[name] = create_user(url, roles=(STUDENT,))
        students[name] = adult_profile_for(url, users[name].id)
    students["M"] = _student(url)
    students["AD"] = _student(url, adult=True)
    students["K"] = _student(url)
    students["N"] = _student(url)
    for name, linked in (
        ("GM", ["M"]),
        ("GA", ["AD"]),
        ("G2", ["M", "K"]),
        ("G3", ["M", "N"]),
        ("GR", ["M"]),
        ("GD", ["M"]),
    ):
        users[name], guardians[name] = _guardian_of(url, [students[s] for s in linked])
    # Both roles: an own profile (enrolled in B) and a linked child (M in A).
    users["both"], _ = _guardian_of(url, [students["M"]], roles=(STUDENT, GUARDIAN))
    students["both"] = adult_profile_for(url, users["both"].id)
    users["unlinked"] = create_user(url, roles=(STUDENT,))
    users["tutor"] = create_user(url, roles=(TUTOR,))
    for role in STAFF_ROLES:
        users[role] = create_user(url, roles=(role,))

    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        template = new_template(client, cookie, [subject])

        def make(start: str) -> dict[str, Any]:
            response = create_cohort(
                client,
                cookie,
                cohort_body(
                    template=template["id"],
                    subject=subject,
                    starts_on=tl.monday,
                    tutor=tutor,
                    room=room,
                    start_time=start,
                ),
            )
            assert code_of(response) == CREATED, response.text
            created: dict[str, Any] = response.json()
            return created

        a = make("16:00")
        b = make("18:00")
        for name in ("S", "W", "M", "AD", "K"):
            assert code_of(enroll(client, cookie, a["id"], students[name])) == CREATED
        for name in ("T", "both"):
            assert code_of(enroll(client, cookie, b["id"], students[name])) == CREATED
        roster = get(client, cookie, f"{COHORTS}/{a['id']}").json()["roster"]
        w = next(i for i in roster if i["student"]["id"] == str(students["W"]))
        assert code_of(withdraw(client, cookie, a["id"], w["id"])) == OK
    rows = cohort_rows(url, a["id"])
    for index, state in (
        (0, "completed"),
        (1, "confirmed"),
        (3, "cancelled"),
        (4, "conflict"),
        (5, "skipped_closure"),
        (7, "confirmed"),
        (8, "confirmed"),
        (9, "confirmed"),
    ):
        set_state(url, rows[index].id, state)
    run_sql(
        url,
        "UPDATE class_sessions SET state = 'unassigned', tutor_user_id = NULL"
        " WHERE id = :id",
        {"id": str(rows[6].id)},
    )
    for row in cohort_rows(url, b["id"]):
        set_state(url, row.id, "confirmed")
    # L joins after row 7 has started.
    late_at = rows[7].starts_at + timedelta(minutes=1)
    with frozen_clock(late_at):
        cookie = owner_cookie(url, client)
        assert code_of(enroll(client, cookie, a["id"], students["L"])) == CREATED
    return World(
        now=tl.now,
        a=a,
        b=b,
        rows=cohort_rows(url, a["id"]),
        students=students,
        users=users,
        guardians=guardians,
        late_at=late_at,
    )


def cookie_of(client: Any, world: World, name: str) -> Any:
    return sign_in(client, world.users[name].username)


def sessions(client: Any, cookie: Any, cohort_id: Any, start: datetime) -> Any:
    return get(
        client,
        cookie,
        f"{COHORTS}/{cohort_id}/sessions",
        **{"from": iso_z(start), "to": iso_z(start + timedelta(days=40))},
    )


def reads(client: Any, world: World, name: str, at: datetime | None = None) -> Any:
    """``(list, detail of A, sessions of A)`` for ``name``."""
    with frozen_clock(at or world.now):
        cookie = cookie_of(client, world, name)
        listed = get(client, cookie, COHORTS, status="all", limit=100)
        detail = get(client, cookie, f"{COHORTS}/{world.a['id']}")
        found = sessions(
            client, cookie, world.a["id"], world.rows[0].starts_at - timedelta(hours=1)
        )
    return listed, detail, found


def family_rows(world: World, *, after: datetime | None = None) -> list[str]:
    """The ids of A's rows a family caller sees, by start."""
    return [
        str(r.id)
        for r in world.rows
        if r.state in FAMILY_STATES and (after is None or r.starts_at > after)
    ]


def hidden_rows(world: World) -> set[str]:
    return {str(r.id) for r in world.rows if r.state not in FAMILY_STATES}


# --- Staff ----------------------------------------------------------------------------


@pytest.mark.parametrize("role", STAFF_ROLES)
def test_staff_see_every_cohort_with_its_roster(client, world, role):
    listed, detail, found = reads(client, world, role)

    item = next(i for i in listed.json()["items"] if i["id"] == world.a["id"])
    assert item["enrolled_count"] == 5  # S, M, AD, K, L
    assert world.b["id"] in [i["id"] for i in listed.json()["items"]]
    body = detail.json()
    assert body["enrolled_count"] == 5
    assert len(body["roster"]) == 6  # and W, withdrawn
    assert "assignment" in body["series"]
    states = {i["state"] for i in found.json()["items"]}
    assert {"completed", "offered", "cancelled", "conflict", "unassigned"} <= states
    assert all("students" not in i for i in found.json()["items"])


# --- Students and guardians -----------------------------------------------------------


def _assert_family_view(world: World, listed, detail, found, *, sees: list[str]):
    """``sees``: the caller's own students in A."""
    assert code_of(listed) == OK
    assert world.a["id"] in [i["id"] for i in listed.json()["items"]]
    item = next(i for i in listed.json()["items"] if i["id"] == world.a["id"])
    assert item["enrolled_count"] is None
    assert item["assignment_status"] is None
    assert code_of(detail) == OK, detail.text
    body = detail.json()
    assert "roster" not in body
    assert "enrolled_count" not in body
    assert "assignment" not in body["series"]
    assert {p["student_id"] for p in body["series"]["participants"]} == {
        str(world.students[s]) for s in sees
    }
    assert body["syllabus"]["modules"]
    assert {o["state"] for o in body["next_occurrences"]} <= {"confirmed"}
    items = found.json()["items"]
    assert code_of(found) == OK
    assert {i["state"] for i in items} <= FAMILY_STATES
    assert not {i["id"] for i in items} & hidden_rows(world)
    for i in items:
        assert {s["id"] for s in i["students"]} <= {
            str(world.students[s]) for s in sees
        }
    others = set(world.students) - set(sees)
    for text in (listed.text, detail.text, found.text):
        for other in others:
            assert str(world.students[other]) not in text, other


def test_an_enrolled_student_sees_their_cohort_and_sessions(client, world):
    listed, detail, found = reads(client, world, "S")

    assert [i["id"] for i in listed.json()["items"]] == [world.a["id"]]
    _assert_family_view(world, listed, detail, found, sees=["S"])
    items = found.json()["items"]
    assert [i["id"] for i in items] == family_rows(world)
    assert all(
        [s["id"] for s in i["students"]] == [str(world.students["S"])] for i in items
    )
    with frozen_clock(world.now):
        other = get(client, cookie_of(client, world, "S"), f"{COHORTS}/{world.b['id']}")
    assert code_of(other) == NOT_FOUND


def test_a_late_enrollee_sees_only_the_sessions_after_their_enrollment(client, world):
    listed, detail, found = reads(client, world, "L", at=world.late_at)

    _assert_family_view(world, listed, detail, found, sees=["L"])
    assert [i["id"] for i in found.json()["items"]] == family_rows(
        world, after=world.late_at
    )
    assert found.json()["items"]


@pytest.mark.parametrize(("name", "child"), [("GM", "M"), ("GA", "AD")])
def test_the_guardian_of_a_minor_and_of_an_adult_without_consent(
    client, world, name, child
):
    listed, detail, found = reads(client, world, name)

    assert [i["id"] for i in listed.json()["items"]] == [world.a["id"]]
    _assert_family_view(world, listed, detail, found, sees=[child])
    items = found.json()["items"]
    assert [i["id"] for i in items] == family_rows(world)
    assert all(
        [s["id"] for s in i["students"]] == [str(world.students[child])] for i in items
    )


def test_a_guardian_with_two_children_in_the_cohort_sees_both(client, world):
    listed, detail, found = reads(client, world, "G2")

    _assert_family_view(world, listed, detail, found, sees=["M", "K"])
    for item in found.json()["items"]:
        assert {s["id"] for s in item["students"]} == {
            str(world.students["M"]),
            str(world.students["K"]),
        }
        names = [s["display_name"] for s in item["students"]]
        assert names == sorted(names, key=str.casefold)


def test_a_guardian_with_one_child_enrolled_sees_only_that_one(client, world):
    listed, detail, found = reads(client, world, "G3")

    _assert_family_view(world, listed, detail, found, sees=["M"])
    assert str(world.students["N"]) not in found.text


def test_a_student_of_another_cohort_and_a_withdrawn_one(client, world):
    for name in ("T", "W"):
        listed, detail, found = reads(client, world, name)

        assert code_of(detail) == NOT_FOUND, name
        assert code_of(found) == NOT_FOUND, name
        assert world.a["id"] not in [i["id"] for i in listed.json()["items"]], name
    assert [i["id"] for i in reads(client, world, "T")[0].json()["items"]] == [
        world.b["id"]
    ]
    assert reads(client, world, "W")[0].json()["items"] == []


def test_a_tutor_and_an_unlinked_student_account_see_nothing(client, world):
    for name in ("tutor", "unlinked"):
        listed, detail, found = reads(client, world, name)

        assert code_of(listed) == OK, name
        assert listed.json() == {"items": [], "next_cursor": None}, name
        assert code_of(detail) == NOT_FOUND, name
        assert code_of(found) == NOT_FOUND, name


def test_both_roles_see_the_union(client, world):
    listed, detail, found = reads(client, world, "both")

    assert {i["id"] for i in listed.json()["items"]} == {world.a["id"], world.b["id"]}
    assert code_of(detail) == OK
    assert {s["id"] for i in found.json()["items"] for s in i["students"]} == {
        str(world.students["M"])
    }


def test_a_removed_link_and_a_deactivated_profile_lose_the_cohort(
    module_seeded_url, client, world
):
    url = module_seeded_url
    for name in ("GR", "GD"):
        assert code_of(reads(client, world, name)[1]) == OK, name
    run_sql(
        url,
        "DELETE FROM student_guardians WHERE guardian_id = :g",
        {"g": str(world.guardians["GR"])},
    )
    run_sql(
        url,
        "UPDATE guardians SET is_active = false WHERE id = :g",
        {"g": str(world.guardians["GD"])},
    )

    for name in ("GR", "GD"):
        listed, detail, found = reads(client, world, name)

        assert code_of(detail) == NOT_FOUND, name
        assert code_of(found) == NOT_FOUND, name
        assert listed.json()["items"] == [], name


# --- G4 and the hidden/unknown cases --------------------------------------------------


def test_the_range_rules_and_an_unknown_cohort(client, world):
    with frozen_clock(world.now):
        cookie = cookie_of(client, world, "S")
        unknown = get(client, cookie, f"{COHORTS}/{uuid.uuid4()}")
        unknown_sessions = sessions(client, cookie, uuid.uuid4(), world.now)
        too_long = get(
            client,
            cookie,
            f"{COHORTS}/{world.a['id']}/sessions",
            **{"from": iso_z(world.now), "to": iso_z(world.now + timedelta(days=46))},
        )

    assert code_of(unknown) == NOT_FOUND
    assert code_of(unknown_sessions) == NOT_FOUND
    assert [e["code"] for e in too_long.json()["errors"]] == ["range_too_long"]


# --- Statement budgets (F1 <= 9, F3 <= 5, G4 <= 7) ------------------------------------


def _principal(user: Any, roles: set[str], permissions: set[str]) -> Principal:
    return Principal(
        user_id=user.id, roles=frozenset(roles), permissions=frozenset(permissions)
    )


def test_the_statement_budgets(module_seeded_url, world):
    url = module_seeded_url
    staff = _principal(world.users[OWNER], {OWNER}, {"course.view"})
    student = _principal(world.users["S"], {STUDENT}, set())
    guardian = _principal(world.users["G2"], {GUARDIAN}, set())
    both = _principal(world.users["both"], {STUDENT, GUARDIAN}, set())
    cohort_id = uuid.UUID(world.a["id"])
    query = CohortSessionsQuery.model_validate(
        {
            "from": world.rows[0].starts_at,
            "to": world.rows[0].starts_at + timedelta(days=40),
        }
    )

    def detail(principal: Principal) -> Any:
        async def _read(session: Any) -> Any:
            viewer = await cohorts.cohort_viewer(session, principal)
            return await cohorts.get_cohort_detail(session, cohort_id, viewer)

        return _read

    def found(principal: Principal) -> Any:
        async def _read(session: Any) -> Any:
            viewer = await cohorts.cohort_viewer(session, principal)
            return await cohorts.list_cohort_sessions(session, cohort_id, query, viewer)

        return _read

    def listed(principal: Principal, limit: int) -> Any:
        async def _read(session: Any) -> Any:
            viewer = await cohorts.cohort_viewer(session, principal)
            return await cohorts.list_cohorts(
                session,
                q=None,
                template_id=None,
                subject_id=None,
                status="all",
                limit=limit,
                cursor=None,
                viewer=viewer,
            )

        return _read

    with frozen_clock(world.now):
        staff_count, staff_detail = count_statements(url, detail(staff))
        family_counts = [
            count_statements(url, detail(p))[0] for p in (student, guardian, both)
        ]
        session_counts = [
            count_statements(url, found(p))[0] for p in (staff, student, guardian, both)
        ]
        list_counts = [
            count_statements(url, listed(p, n))[0]
            for p in (staff, student, guardian, both)
            for n in (1, 25)
        ]

    assert staff_detail.roster is not None and len(staff_detail.roster) == 6
    assert staff_count <= 9
    assert all(count <= 9 for count in family_counts), family_counts
    assert all(count <= 7 for count in session_counts), session_counts
    assert all(count <= 5 for count in list_counts), list_counts
