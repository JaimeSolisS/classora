"""The family schedule and its child selector (#67 C1-C5, G1) with real
logins through the real app: ``GET /api/v1/students/{student_id}/schedule``
and ``GET /api/v1/schedule/family-students``.

IDOR (an unknown id, another family's child, a sibling, a student a tutor
only teaches, an inactive guardian profile: the same ``404``), the adult
consent rule (``403 student_consent_required``), the D2 state rule with
``first_confirmed_at``, the closed allow-list and the group leak test, the
#57 range rules and the statement budgets.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

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
from app.modules.scheduling.models import ClassSession, ClassSessionParticipant
from app.modules.scheduling.schedule_schemas import ScheduleRangeQuery
from tests.authz.assertions import assert_same_not_found
from tests.conftest import frozen_clock
from tests.factories.base import persist
from tests.factories.families import (
    ADULT_BIRTHDATE,
    GuardianFactory,
    link_guardian,
    persist_student,
)
from tests.factories.scheduling import persist_subject
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import count_statements, get, unique_year
from tests.integration.consent_support import adult_profile_for
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, in_uow
from tests.integration.schedule_support import (
    ALL_STATES,
    FAMILY_STUDENTS,
    ids_of,
    principal_of,
    read,
    stored,
    student_path,
    tutor_path,
    week,
    z,
)
from tests.integration.session_request_support import NOW as REQUESTS_NOW
from tests.integration.session_request_support import (
    Family,
    deactivate_guardian,
    guardian_with_child,
    link_child,
    revoke_academic_consent,
    student_account,
    unlink,
)
from tests.integration.test_class_session_create import new_room, new_student


def _monday(year: int) -> date:
    day = date(year, 1, 1)
    return day + timedelta(days=(7 - day.weekday()) % 7)


YEAR = unique_year()
MONDAY = _monday(YEAR)
NOW = REQUESTS_NOW
"""The frozen clock of the #59 tests (2030-12-30): their minors (born 2016)
are minors then. The test weeks are in a ``unique_year()``."""
FORBIDDEN_CONSENT = (403, "student_consent_required")
NOT_FOUND = (404, "not_found")
INVALID = (422, "validation_error")
OK = (200, None)


def slot(day: int, hour: int) -> datetime:
    """``hour`` UTC on the ``day``-th day of the test week (0 = Monday)."""
    return week(MONDAY)[0] + timedelta(days=day, hours=hour)


@pytest.fixture(scope="module", autouse=True)
def frozen() -> Any:
    with frozen_clock(NOW):
        yield


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Any:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@dataclass
class World:
    family: Family
    """The guardian GM and their minor child M."""
    tutor: CreatedUser
    other_tutor: CreatedUser
    visible: dict[str, str]
    """M's sessions a family sees, by label."""
    hidden: dict[str, str]
    """M's sessions a family never sees, by label."""
    group_session: str
    classmates: tuple[uuid.UUID, uuid.UUID]
    room: uuid.UUID


@pytest.fixture(scope="module")
def world(module_seeded_url: str) -> World:
    url = module_seeded_url
    family = guardian_with_child(url)
    m = family.student_id
    tutor = create_user(url, roles=(TUTOR,), display_name="Tutora Visible")
    other_tutor = create_user(url, roles=(TUTOR,), display_name="Tutor Oculto")
    room = new_room(url)
    t = tutor.id
    visible = {
        "confirmed": stored(
            url, slot(0, 14), students=(m,), tutor_user_id=t, classroom_id=room
        ),
        "completed": stored(url, slot(0, 16), state="completed", students=(m,)),
        "cancelled_seen": stored(
            url,
            slot(0, 18),
            state="cancelled",
            seen=True,
            tutor_user_id=t,
            students=(m,),
        ),
        "skipped_seen": stored(
            url,
            slot(0, 20),
            state="skipped_closure",
            seen=True,
            tutor_user_id=t,
            students=(m,),
        ),
    }
    hidden = {
        "cancelled_never": stored(
            url, slot(1, 14), state="cancelled", seen=False, students=(m,)
        ),
        "skipped_never": stored(
            url, slot(1, 15), state="skipped_closure", seen=False, students=(m,)
        ),
        "offered": stored(
            url, slot(1, 16), state="offered", tutor_user_id=t, students=(m,)
        ),
        "unassigned": stored(url, slot(1, 17), state="unassigned", students=(m,)),
        "conflict": stored(url, slot(1, 18), state="conflict", students=(m,)),
        # #67 G1: confirmed → skipped_closure → restored to offered.
        "restored_offered": stored(
            url,
            slot(1, 19),
            state="offered",
            seen=True,
            tutor_user_id=t,
            students=(m,),
        ),
    }
    classmates = (new_student(url), new_student(url))
    group_session = stored(
        url,
        slot(2, 15),
        kind="group",
        tutor_user_id=t,
        students=(m, *classmates),
    )
    visible["group"] = group_session
    return World(
        family=family,
        tutor=tutor,
        other_tutor=other_tutor,
        visible=visible,
        hidden=hidden,
        group_session=group_session,
        classmates=classmates,
        room=room,
    )


def link_profile(
    url: str, user_id: uuid.UUID, students: list[uuid.UUID], *, adult: bool = False
) -> uuid.UUID:
    """An active guardian profile of ``user_id`` linked to ``students`` (with
    ``adult``, also a new adult student without consent)."""

    async def _link(session: AsyncSession) -> uuid.UUID:
        guardian = await persist(session, GuardianFactory, user_id=user_id)
        wanted = list(students)
        if adult:
            wanted.append(
                (
                    await persist_student(
                        session, contacts=0, birthdate=ADULT_BIRTHDATE
                    )
                ).id
            )
        for student_id in wanted:
            student = await session.get(Student, student_id)
            assert student is not None
            await link_guardian(session, student, guardian)
        return guardian.id

    return in_uow(url, _link)


def read_week(client: TestClient, cookie: Any, student_id: Any) -> Any:
    return read(client, cookie, student_path(student_id), *week(MONDAY))


# --- C2, A3: what a family sees -------------------------------------------------------


def test_a_guardian_of_a_minor_sees_only_the_family_visible_sessions(client, world):
    cookie = sign_in(client, world.family.user.username)

    response = read_week(client, cookie, world.family.student_id)

    assert code_of(response) == OK
    body = response.json()
    assert body["student_id"] == str(world.family.student_id)
    assert body["truncated"] is False
    assert sorted(ids_of(response)) == sorted(world.visible.values())
    assert not set(ids_of(response)) & set(world.hidden.values())
    starts = [item["starts_at"] for item in body["items"]]
    assert starts == sorted(starts)


def test_the_family_item_is_a_closed_allow_list(client, world):
    cookie = sign_in(client, world.family.user.username)

    items = {
        item["id"]: item
        for item in read_week(client, cookie, world.family.student_id).json()["items"]
    }
    item = items[world.visible["confirmed"]]

    assert set(item) == {
        "id",
        "kind",
        "state",
        "starts_at",
        "ends_at",
        "subject",
        "tutor",
        "classroom",
    }
    assert set(item["subject"]) == {"id", "name", "education_level_name"}
    assert item["tutor"] == {"display_name": "Tutora Visible"}
    assert set(item["classroom"]) == {"name"}
    assert item["state"] == "confirmed"
    assert items[world.visible["cancelled_seen"]]["state"] == "cancelled"
    assert items[world.visible["skipped_seen"]]["state"] == "skipped_closure"


def test_a_group_session_never_leaks_other_students_or_the_tutor_id(
    module_seeded_url, client, world
):
    cookie = sign_in(client, world.family.user.username)
    response = read_week(client, cookie, world.family.student_id)
    raw = response.text
    [group] = [
        item for item in response.json()["items"] if item["id"] == world.group_session
    ]

    assert group["kind"] == "group"
    assert "participants" not in group
    for classmate in world.classmates:
        assert str(classmate) not in raw
    assert str(world.tutor.id) not in raw
    assert str(world.room) not in raw
    for name in in_uow(module_seeded_url, _names(world)):
        assert name not in raw

    # The tutor sees all three students of their group session.
    tutor_cookie = sign_in(client, world.tutor.username)
    tutor_items = read(
        client, tutor_cookie, tutor_path(world.tutor.id), *week(MONDAY)
    ).json()["items"]
    [tutor_group] = [i for i in tutor_items if i["id"] == world.group_session]
    assert {p["student_id"] for p in tutor_group["participants"]} == {
        str(world.family.student_id),
        *(str(c) for c in world.classmates),
    }


def _names(world: World) -> Any:
    async def _read(session: AsyncSession) -> list[str]:
        found = []
        for student_id in world.classmates:
            student = await session.get(Student, student_id)
            assert student is not None
            found.append(f"{student.given_names} {student.family_names}")
        return found

    return _read


STATE_CASES = [
    (state, seen)
    for state in ALL_STATES
    for seen in (True, False)
    # The CHECK requires first_confirmed_at for these states.
    if seen or state not in {"confirmed", "completed"}
]


@pytest.mark.parametrize(("state", "seen"), STATE_CASES)
def test_every_state_with_and_without_first_confirmed_at(
    module_seeded_url, client, state, seen
):
    """A3 × G1: the family and the tutor results follow the pure rules."""
    url = module_seeded_url
    family = guardian_with_child(url)
    tutor = create_user(url, roles=(TUTOR,))
    with_tutor = state not in {"unassigned"}
    session_id = stored(
        url,
        slot(3, 15),
        state=state,
        seen=seen,
        students=(family.student_id,),
        **({"tutor_user_id": tutor.id} if with_tutor else {"tutor": False}),
    )

    family_ids = ids_of(
        read_week(client, sign_in(client, family.user.username), family.student_id)
    )
    tutor_ids = ids_of(
        read(
            client,
            sign_in(client, tutor.username),
            tutor_path(tutor.id),
            *week(MONDAY),
        )
    )

    assert (session_id in family_ids) is scheduling.is_family_visible(
        state, was_confirmed=seen
    )
    assert (session_id in tutor_ids) is (
        with_tutor and scheduling.is_tutor_visible(state, was_confirmed=seen)
    )


# --- C1, G1: who may read -------------------------------------------------------------


def test_another_familys_child_is_the_same_404_as_an_unknown_id(
    module_seeded_url, client, world
):
    other = guardian_with_child(module_seeded_url)
    stored(module_seeded_url, slot(4, 15), students=(other.student_id,))
    cookie = sign_in(client, world.family.user.username)

    assert_same_not_found(
        read_week(client, cookie, other.student_id),
        read_week(client, cookie, uuid.uuid4()),
    )


def test_a_student_sees_their_own_schedule_but_not_another_students(
    module_seeded_url, client
):
    me, mine = student_account(module_seeded_url)
    _, theirs = student_account(module_seeded_url)
    own = stored(module_seeded_url, slot(4, 16), students=(mine,))
    stored(module_seeded_url, slot(4, 17), students=(theirs,))
    cookie = sign_in(client, me.username)

    assert ids_of(read_week(client, cookie, mine)) == [own]
    assert_same_not_found(
        read_week(client, cookie, theirs), read_week(client, cookie, uuid.uuid4())
    )


def test_a_student_cannot_read_a_siblings_schedule(module_seeded_url, client):
    """Two students of one guardian: each sees only their own profile."""
    me, mine = student_account(module_seeded_url)
    sibling = new_student(module_seeded_url)
    guardian = create_user(module_seeded_url, roles=(GUARDIAN,))
    link_profile(module_seeded_url, guardian.id, [mine, sibling])
    stored(module_seeded_url, slot(4, 21), students=(sibling,))
    cookie = sign_in(client, me.username)

    assert_same_not_found(
        read_week(client, cookie, sibling), read_week(client, cookie, uuid.uuid4())
    )
    assert code_of(read_week(client, cookie, mine)) == OK


def test_the_adult_rule_and_its_changes_apply_on_the_next_request(
    module_seeded_url, client
):
    url = module_seeded_url
    without = guardian_with_child(url, adult=True)
    with_consent = guardian_with_child(url, adult=True, consent=True)
    shown = stored(url, slot(4, 18), students=(with_consent.student_id,))
    stored(url, slot(4, 19), students=(without.student_id,))

    refused = read_week(
        client, sign_in(client, without.user.username), without.student_id
    )
    assert code_of(refused) == FORBIDDEN_CONSENT

    cookie = sign_in(client, with_consent.user.username)
    assert ids_of(read_week(client, cookie, with_consent.student_id)) == [shown]
    revoke_academic_consent(url, with_consent)
    assert code_of(read_week(client, cookie, with_consent.student_id)) == (
        FORBIDDEN_CONSENT
    )
    unlink(url, with_consent)
    assert_same_not_found(
        read_week(client, cookie, with_consent.student_id),
        read_week(client, cookie, uuid.uuid4()),
    )


def test_a_minor_needs_no_consent_and_an_unlink_applies_at_once(
    module_seeded_url, client
):
    family = guardian_with_child(module_seeded_url)
    cookie = sign_in(client, family.user.username)

    assert code_of(read_week(client, cookie, family.student_id)) == OK
    unlink(module_seeded_url, family)
    assert_same_not_found(
        read_week(client, cookie, family.student_id),
        read_week(client, cookie, uuid.uuid4()),
    )


def test_an_inactive_guardian_profile_sees_nothing(module_seeded_url, client):
    family = guardian_with_child(module_seeded_url)
    deactivate_guardian(module_seeded_url, family.guardian_id)
    cookie = sign_in(client, family.user.username)

    assert_same_not_found(
        read_week(client, cookie, family.student_id),
        read_week(client, cookie, uuid.uuid4()),
    )


def test_a_tutor_of_the_student_gets_404_on_the_family_route(client, world):
    """M is a participant of the tutor's sessions (a D1 assignment)."""
    cookie = sign_in(client, world.tutor.username)

    assert_same_not_found(
        read_week(client, cookie, world.family.student_id),
        read_week(client, cookie, uuid.uuid4()),
    )


@pytest.mark.parametrize(
    "role", [OWNER, ADMINISTRATOR, ACADEMIC_COORDINATOR, FINANCE_STAFF]
)
def test_staff_with_schedule_view_read_the_family_view(
    module_seeded_url, client, world, role
):
    staff = create_user(module_seeded_url, roles=(role,))
    cookie = sign_in(client, staff.username)

    response = read_week(client, cookie, world.family.student_id)

    assert sorted(ids_of(response)) == sorted(world.visible.values())
    assert_same_not_found(
        read_week(client, cookie, uuid.uuid4()),
        read_week(client, cookie, uuid.uuid4()),
    )


# --- C2, G1: the range rules ----------------------------------------------------------


def test_the_overlap_edges(module_seeded_url, client):
    family = guardian_with_child(module_seeded_url)
    student = family.student_id
    start, end = slot(5, 12), slot(5, 14)
    before = stored(module_seeded_url, start - timedelta(hours=1), students=(student,))
    across = stored(
        module_seeded_url, start - timedelta(minutes=30), students=(student,)
    )
    inside = stored(
        module_seeded_url, start + timedelta(minutes=30), students=(student,)
    )
    at_end = stored(module_seeded_url, end, students=(student,))
    cookie = sign_in(client, family.user.username)

    found = ids_of(read(client, cookie, student_path(student), start, end))

    assert found == [across, inside]
    assert before not in found and at_end not in found


DAY_2 = "2031-01-07T00:00:00Z"
DAY_47 = "2031-02-16T00:00:00Z"


@pytest.mark.parametrize(
    ("params", "error"),
    [
        (
            {"from": "2031-01-06T00:00:00", "to": "2031-01-07T00:00:00Z"},
            "naive_datetime",
        ),
        (
            {"from": "2031-01-07T00:00:00Z", "to": "2031-01-07T00:00:00Z"},
            "range_invalid",
        ),
        (
            {"from": "2031-01-01T00:00:00Z", "to": "2031-02-16T00:00:00Z"},
            "range_too_long",
        ),
        ({"from": "2031-01-06T00:00:00Z"}, None),
        (
            {
                "from": "2031-01-06T00:00:00Z",
                "to": "2031-01-07T00:00:00Z",
                "include_cancelled": "true",
            },
            None,
        ),
    ],
)
def test_the_range_422s(client, world, params, error):
    cookie = sign_in(client, world.family.user.username)

    response = get(client, cookie, student_path(world.family.student_id), **params)

    assert code_of(response) == INVALID, response.text
    if error is not None:
        assert error in {e["code"] for e in response.json()["errors"]}


def test_45_days_is_allowed(client, world):
    cookie = sign_in(client, world.family.user.username)
    start = week(MONDAY)[0]

    response = read(
        client,
        cookie,
        student_path(world.family.student_id),
        start,
        start + timedelta(days=45),
    )

    assert code_of(response) == OK


def test_more_than_1000_matches_are_truncated(module_seeded_url, client):
    family = guardian_with_child(module_seeded_url)
    start = slot(6, 8)

    async def _bulk(session: AsyncSession) -> None:
        subject = await persist_subject(session)
        rows = [
            ClassSession(
                kind="individual",
                state="cancelled",
                subject_id=subject.id,
                starts_at=start,
                ends_at=start + timedelta(minutes=60),
                scheduled_minutes=60,
                cancelled_at=start - timedelta(days=1),
                cancellation_reason="Prueba",
                first_confirmed_at=start - timedelta(days=2),
            )
            for _ in range(1001)
        ]
        session.add_all(rows)
        await session.flush()
        session.add_all(
            ClassSessionParticipant(
                class_session_id=row.id, student_id=family.student_id
            )
            for row in rows
        )
        await session.flush()

    in_uow(module_seeded_url, _bulk)
    cookie = sign_in(client, family.user.username)

    body = read_week(client, cookie, family.student_id).json()

    assert len(body["items"]) == 1000
    assert body["truncated"] is True
    ids = [item["id"] for item in body["items"]]
    assert ids == sorted(ids)


# --- C4: the child selector -----------------------------------------------------------


def test_the_selector_lists_own_and_linked_students(module_seeded_url, client):
    url = module_seeded_url
    family = guardian_with_child(url, adult=True)
    minor = link_child(url, family)
    other = guardian_with_child(url)
    cookie = sign_in(client, family.user.username)

    body = get(client, cookie, FAMILY_STUDENTS).json()

    by_id = {item["student_id"]: item for item in body["items"]}
    assert set(by_id) == {str(family.student_id), str(minor)}
    assert str(other.student_id) not in by_id
    adult = by_id[str(family.student_id)]
    assert set(adult) == {
        "student_id",
        "display_name",
        "relation",
        "is_minor",
        "schedule_access",
    }
    assert (adult["relation"], adult["is_minor"], adult["schedule_access"]) == (
        "guardian",
        False,
        "consent_required",
    )
    child = by_id[str(minor)]
    assert (child["relation"], child["is_minor"], child["schedule_access"]) == (
        "guardian",
        True,
        "allowed",
    )
    names = [item["display_name"].casefold() for item in body["items"]]
    assert names == sorted(names)


def test_the_selector_of_a_student_is_their_own_profile(module_seeded_url, client):
    me, mine = student_account(module_seeded_url)
    cookie = sign_in(client, me.username)

    [item] = get(client, cookie, FAMILY_STUDENTS).json()["items"]

    assert (item["student_id"], item["relation"], item["schedule_access"]) == (
        str(mine),
        "self",
        "allowed",
    )


def test_a_tutor_guardian_gets_only_family_students(module_seeded_url, client, world):
    """Mutation 8: never the tutor's assigned students."""
    url = module_seeded_url
    user = create_user(url, roles=(GUARDIAN, TUTOR))
    child = new_student(url)
    link_profile(url, user.id, [child])
    taught = new_student(url)
    stored(url, slot(4, 20), tutor_user_id=user.id, students=(taught,))
    cookie = sign_in(client, user.username)

    items = get(client, cookie, FAMILY_STUDENTS).json()["items"]

    assert [item["student_id"] for item in items] == [str(child)]
    # The tutor does see the taught student at the limited level (D3).
    listed = get(client, cookie, "/api/v1/students", status="all", limit=100).json()
    assert str(taught) in {item["id"] for item in listed["items"]}


@pytest.mark.parametrize("role", [OWNER, FINANCE_STAFF, TUTOR])
def test_callers_without_a_family_role_get_an_empty_selector(
    module_seeded_url, client, role
):
    user = create_user(module_seeded_url, roles=(role,))
    cookie = sign_in(client, user.username)

    assert get(client, cookie, FAMILY_STUDENTS).json() == {"items": []}


# --- C4, C5: statement budgets --------------------------------------------------------


@dataclass
class BudgetFamily:
    user: CreatedUser
    student_id: uuid.UUID


def _budget_world(url: str, count: int, *, adult: bool = False) -> BudgetFamily:
    """A guardian (also a student with their own profile) whose minor child
    has ``count`` confirmed sessions in the budget week."""
    user = create_user(url, roles=(GUARDIAN, STUDENT))
    adult_profile_for(url, user.id)
    child = new_student(url)
    link_profile(url, user.id, [child], adult=adult)
    family = BudgetFamily(user=user, student_id=child)
    tutor = create_user(url, roles=(TUTOR,))
    room = new_room(url)
    budget = MONDAY + timedelta(days=14)
    base = week(budget)[0]
    for index in range(count):
        stored(
            url,
            base + timedelta(days=index // 8, hours=10 + index % 8),
            students=(family.student_id,),
            tutor_user_id=tutor.id,
            classroom_id=room,
        )
    return family


@pytest.mark.parametrize("count", [0, 1, 30])
def test_the_schedule_takes_at_most_8_statements(module_seeded_url, count):
    url = module_seeded_url
    family = _budget_world(url, count)
    budget = MONDAY + timedelta(days=14)
    start, end = week(budget)
    query = ScheduleRangeQuery.model_validate({"from": start, "to": end})
    callers = {
        "guardian": principal_of(family.user, GUARDIAN),
        "guardian+student": principal_of(family.user, GUARDIAN, STUDENT),
        "staff": principal_of(create_user(url, roles=(OWNER,)), OWNER),
    }

    def read_as(principal: Any) -> Any:
        async def _read(session: AsyncSession) -> Any:
            return await scheduling.family_schedule(
                session, principal, family.student_id, query
            )

        return _read

    counts = {}
    for name, principal in callers.items():
        counts[name], result = count_statements(url, read_as(principal))
        assert len(result.items) == count
    assert all(value <= 8 for value in counts.values()), counts


def test_the_selector_takes_at_most_4_statements(module_seeded_url):
    url = module_seeded_url
    family = _budget_world(url, 0, adult=True)

    def listed(principal: Any) -> Any:
        async def _read(session: AsyncSession) -> Any:
            return await scheduling.family_students(session, principal)

        return _read

    for roles in ((GUARDIAN,), (STUDENT,), (GUARDIAN, STUDENT), (GUARDIAN, TUTOR)):
        count, result = count_statements(url, listed(principal_of(family.user, *roles)))
        assert count <= 4, (roles, count)
        assert result.items
    count, result = count_statements(url, listed(principal_of(family.user, OWNER)))
    assert (count, result.items) == (0, [])


def test_class_sessions_range_read_stays_staff_only(client, world):
    """#57 is unchanged: tutors, students and guardians get 403."""
    start, end = week(MONDAY)
    for user in (world.family.user, world.tutor):
        cookie = sign_in(client, user.username)
        response = get(
            client,
            cookie,
            "/api/v1/class-sessions",
            **{"from": z(start), "to": z(end)},
        )
        assert code_of(response) == (403, "permission_denied")
