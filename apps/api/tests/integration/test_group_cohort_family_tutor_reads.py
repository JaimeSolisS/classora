"""Groups and cohorts for tutors, students and guardians (#246 C1-C3, A1-A7)
with real logins through the real app, plus the statement budgets.

"Now" is the Sunday 12:00 (Mexico City) of the series timeline; the
students born ten years before it are minors then. Everything is created by
the Owner through the API:

- **trio** group ``G3`` (Tuesday/Thursday 16:00) and cohort ``C3``
  (Tuesday/Thursday 18:00): S1, S2, S3 (minors of three families), taught by
  T1, who accepted; their rows get the #67 A3 states;
- **E1** group ``GE`` (Monday/Wednesday 16:00) and cohort ``CE``
  (Monday/Wednesday 18:00): AD (an adult whose guardian has no consent), AC
  (an adult who gave their guardian the consent), AR (the same, revoked in a
  test), W1 (withdrawn after the start) and W2 (minors, siblings), taught by
  SL (a student with their own login), taught by TG (a tutor who is also
  the guardian of B1), who accepted;
- **family B** group ``HB`` and cohort ``CB`` (Friday 16:00 / 18:00): B1,
  taught by T2, accepted;
- **pending** group ``GP`` and cohort ``CP`` (Saturday 10:00 / 12:00): S1,
  offered to T1 (pending), who declines in a test.
"""

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal
from app.modules.families.models import Guardian, Student
from app.modules.identity.permissions import GUARDIAN, OWNER, STUDENT, TUTOR
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.schemas import CohortSessionsQuery, GroupSessionsQuery
from app.modules.staff.models import TutorAvailabilityWindow, TutorQualification
from app.workflows import cohorts
from tests.conftest import frozen_clock
from tests.factories.base import persist
from tests.factories.families import (
    ADULT_BIRTHDATE,
    GuardianFactory,
    link_guardian,
    login_consent,
    persist_student,
    student_login,
)
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import count_statements, get, post
from tests.integration.cohort_support import (
    COHORTS,
    cohort_body,
    cohort_rows,
    new_template,
)
from tests.integration.cohort_support import create_cohort as post_cohort
from tests.integration.cohort_support import enrollment_rows as cohort_enrollments
from tests.integration.cohort_support import withdraw as withdraw_cohort
from tests.integration.conftest import run_sql
from tests.integration.group_support import GROUPS, group_body, group_rows
from tests.integration.group_support import create_group as post_group
from tests.integration.group_support import enrollments as group_enrollments
from tests.integration.group_support import withdraw as withdraw_group
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, in_uow
from tests.integration.series_support import iso_z, timeline
from tests.integration.test_class_session_create import ALL_DAY, MX, new_subject

OK = (200, None)
CREATED = (201, None)
GROUP_NOT_FOUND = (404, "group_not_found")
COHORT_NOT_FOUND = (404, "cohort_not_found")
OFFERS = "/api/v1/assignment-offers"
SERIES = "/api/v1/class-series"

GROUP_FAMILY_KEYS = {
    "id",
    "name",
    "subject",
    "max_students",
    "enrolled_count",
    "effective_capacity",
    "current_series",
    "next_occurrences",
    "version",
    "created_at",
    "updated_at",
    "roster",
}
GROUP_TUTOR_KEYS = GROUP_FAMILY_KEYS | {"tutor_roster"}
GROUP_SERIES_KEYS = {
    "id",
    "tutor",
    "classroom",
    "starts_on",
    "start_time",
    "duration_minutes",
    "time_zone",
    "recurrence",
    "last_occurrence_on",
    "version",
}
GROUP_ITEM_KEYS = {"id", "state", "starts_at", "ends_at", "tutor", "classroom"}
COHORT_DETAIL_KEYS = {
    "id",
    "name",
    "version",
    "created_at",
    "updated_at",
    "template",
    "template_snapshot_id",
    "subject",
    "max_students",
    "effective_capacity",
    "status",
    "planned_session_count",
    "session_minutes",
    "first_session_on",
    "last_session_on",
    "series",
    "next_occurrences",
    "syllabus",
}
COHORT_SERIES_KEYS = {
    "id",
    "kind",
    "subject",
    "tutor",
    "classroom",
    "participants",
    "starts_on",
    "start_time",
    "duration_minutes",
    "time_zone",
    "recurrence",
    "rrule",
    "last_occurrence_on",
    "ended",
    "split_from_series_id",
    "split_into_series_ids",
    "group_id",
    "cohort_id",
    "version",
    "created_at",
    "updated_at",
}
COHORT_ITEM_KEYS = GROUP_ITEM_KEYS | {"original_start", "is_exception"}
ROSTER_ITEM_KEYS = {"student"}
ROSTER_STUDENT_KEYS = {"id", "display_name"}


# --- Building the world ---------------------------------------------------------------


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Any:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


def tutor_user(
    url: str, subject: uuid.UUID, *, roles: tuple[str, ...] = (TUTOR,), **user: Any
) -> CreatedUser:
    """A tutor who can sign in, available all day, qualified for ``subject``
    (``roles`` may add a family role)."""
    tutor = create_user(url, roles=roles, **user)

    async def _make(session: AsyncSession) -> None:
        session.add_all(
            TutorAvailabilityWindow(
                tutor_user_id=tutor.id, weekday=day, starts_at=start, ends_at=end
            )
            for day, start, end in ALL_DAY
        )
        session.add(
            TutorQualification(
                tutor_user_id=tutor.id,
                subject_id=subject,
                status="approved",
                decided_at=datetime(2026, 1, 5, tzinfo=MX),
                decided_by_user_id=tutor.id,
            )
        )
        await session.flush()

    in_uow(url, _make)
    return tutor


def new_child(url: str, *, minor_on: date, adult: bool = False) -> uuid.UUID:
    """A student without a login: a minor on ``minor_on`` unless ``adult``."""

    async def _make(session: AsyncSession) -> uuid.UUID:
        birthdate = ADULT_BIRTHDATE if adult else date(minor_on.year - 10, 1, 1)
        student: Student = await persist_student(
            session, contacts=0, birthdate=birthdate
        )
        return student.id

    return in_uow(url, _make)


def own_profile(url: str, user_id: uuid.UUID, *, minor_on: date) -> uuid.UUID:
    """A minor profile with ``user_id``'s login."""

    async def _make(session: AsyncSession) -> uuid.UUID:
        student: Student = await persist_student(
            session, contacts=0, birthdate=date(minor_on.year - 10, 1, 1)
        )
        await student_login(session, student, user_id)
        return student.id

    return in_uow(url, _make)


def guardian_profile(
    url: str,
    user: CreatedUser,
    children: list[uuid.UUID],
    *,
    consent_for: tuple[uuid.UUID, ...] = (),
) -> uuid.UUID:
    """An active guardian profile of ``user`` linked to ``children``, with
    a ``guardian_academic_access`` consent from ``consent_for``."""

    async def _make(session: AsyncSession) -> uuid.UUID:
        guardian: Guardian = await persist(session, GuardianFactory, user_id=user.id)
        for student_id in children:
            student = await session.get(Student, student_id)
            assert student is not None
            await link_guardian(session, student, guardian)
            if student_id in consent_for:
                await login_consent(
                    session,
                    student,
                    guardian,
                    recorded_by=user.id,
                    scope="guardian_academic_access",
                )
        return guardian.id

    return in_uow(url, _make)


def guardian(
    url: str,
    children: list[uuid.UUID],
    *,
    consent_for: tuple[uuid.UUID, ...] = (),
    roles: tuple[str, ...] = (GUARDIAN,),
) -> tuple[CreatedUser, uuid.UUID]:
    user = create_user(url, roles=roles)
    return user, guardian_profile(url, user, children, consent_for=consent_for)


def answer_offer(
    client: TestClient,
    tutor: CreatedUser,
    at: datetime,
    series_id: Any,
    action: str,
    url: str,
) -> None:
    [offer] = run_sql(
        url,
        "SELECT id FROM assignment_offers WHERE class_series_id = :id"
        " AND status = 'pending'",
        {"id": str(series_id)},
    )
    with frozen_clock(at):
        cookie = sign_in(client, tutor.username)
        body = {"note": "No puedo"} if action == "decline" else None
        response = post(client, cookie, f"{OFFERS}/{offer.id}/{action}", body)
    assert code_of(response) == OK, response.text


def series_id_of(url: str, column: str, record_id: Any) -> Any:
    [row] = run_sql(
        url,
        f"SELECT id FROM class_series WHERE {column} = :id",  # noqa: S608 - test
        {"id": str(record_id)},
    )
    return row.id


def set_rows(url: str, rows: list[Any], states: dict[int, str]) -> None:
    """Force rows into #67 A3 states: ``cancelled_seen`` / ``skipped_seen``
    keep ``first_confirmed_at``; ``cancelled_never``, ``offered``,
    ``unassigned`` and ``conflict`` clear it; ``substituted`` gives the row
    to ``SUBSTITUTE``; the others (``confirmed``, ``completed``) set it."""
    for index, state in states.items():
        row = rows[index]
        params = {"id": str(row.id)}
        if state in {"cancelled_seen", "cancelled_never"}:
            run_sql(
                url,
                "UPDATE class_sessions SET state = 'cancelled', cancelled_at = now(),"
                " cancellation_reason = 'Prueba', first_confirmed_at = CASE WHEN"
                " :keep THEN first_confirmed_at END WHERE id = :id",
                {**params, "keep": state == "cancelled_seen"},
            )
        elif state == "skipped_seen":
            run_sql(
                url,
                "UPDATE class_sessions SET state = 'skipped_closure' WHERE id = :id",
                params,
            )
        elif state in {"offered", "conflict"}:
            run_sql(
                url,
                "UPDATE class_sessions SET state = :state, first_confirmed_at = NULL"
                " WHERE id = :id",
                {**params, "state": state},
            )
        elif state == "unassigned":
            run_sql(
                url,
                "UPDATE class_sessions SET state = 'unassigned', tutor_user_id = NULL,"
                " first_confirmed_at = NULL WHERE id = :id",
                params,
            )
        elif state.startswith("substituted:"):
            run_sql(
                url,
                "UPDATE class_sessions SET tutor_user_id = :tutor WHERE id = :id",
                {**params, "tutor": state.split(":", 1)[1]},
            )
        else:
            run_sql(
                url,
                "UPDATE class_sessions SET state = :state, first_confirmed_at ="
                " coalesce(first_confirmed_at, now()) WHERE id = :id",
                {**params, "state": state},
            )


TRIO_STATES = {
    0: "completed",
    1: "confirmed",
    2: "cancelled_seen",
    3: "cancelled_never",
    4: "skipped_seen",
    5: "unassigned",
    6: "offered",
    7: "conflict",
}
FAMILY_SEES = {0, 1, 2, 4}
"""The trio rows a family sees (#67 A3); 8 (the substitute's) and the
confirmed rest too."""
TUTOR_SEES = {0, 1, 2, 4, 6}
"""The trio rows T1 sees; never 8 (another tutor's) nor 3, 5, 7."""


@dataclass
class World:
    now: datetime
    monday: date
    owner: CreatedUser
    t1: CreatedUser
    t2: CreatedUser
    tg: CreatedUser
    students: dict[str, uuid.UUID]
    users: dict[str, CreatedUser]
    guardians: dict[str, uuid.UUID]
    groups: dict[str, dict[str, Any]]
    cohorts: dict[str, dict[str, Any]]
    trio_group_rows: list[Any] = field(default_factory=list)
    trio_cohort_rows: list[Any] = field(default_factory=list)


@pytest.fixture(scope="module")
def world(module_seeded_url: str, client: Any) -> World:  # noqa: PLR0915 - one world
    url = module_seeded_url
    tl = timeline()
    minor_on = tl.monday
    subject = new_subject(url)
    owner = create_user(url, roles=(OWNER,))
    t1 = tutor_user(url, subject, display_name="Tutora Uno")
    t2 = tutor_user(url, subject, display_name="Tutor Dos")
    tg = tutor_user(url, subject, roles=(TUTOR, GUARDIAN), display_name="Tutor G")
    students: dict[str, uuid.UUID] = {}
    users: dict[str, CreatedUser] = {}
    guardians: dict[str, uuid.UUID] = {}
    for name in ("S1", "S2", "S3", "W1", "W2", "B1"):
        students[name] = new_child(url, minor_on=minor_on)
    for name in ("AD", "AC", "AR"):
        students[name] = new_child(url, minor_on=minor_on, adult=True)
    users["SL"] = create_user(url, roles=(STUDENT,))
    students["SL"] = own_profile(url, users["SL"].id, minor_on=minor_on)
    for name, children, consent in (
        ("GA", ["S1"], ()),
        ("G2", ["S2"], ()),
        ("GB", ["B1"], ()),
        ("GAD", ["AD"], ()),
        ("GAC", ["AC"], ("AC",)),
        ("GAR", ["AR"], ("AR",)),
        ("GW", ["W1", "W2"], ()),
        ("GW1", ["W1"], ()),
        ("GI", ["S1"], ()),
        ("GU", ["S1"], ()),
    ):
        users[name], guardians[name] = guardian(
            url,
            [students[c] for c in children],
            consent_for=tuple(students[c] for c in consent),
        )
    guardians["TG"] = guardian_profile(url, tg, [students["B1"]])
    users["nobody"] = create_user(url, roles=(GUARDIAN,))

    groups: dict[str, dict[str, Any]] = {}
    found: dict[str, dict[str, Any]] = {}
    with frozen_clock(tl.now):
        cookie = sign_in(client, owner.username)
        template = new_template(client, cookie, [subject])

        def group(name: str, members: list[str], tutor: Any, days: list[int], at: str):
            response = post_group(
                client,
                cookie,
                group_body(
                    subject=subject,
                    starts_on=tl.monday,
                    students=[students[m] for m in members],
                    tutor=tutor.id,
                    start_time=at,
                    weekdays=days,
                    max_students=12,
                    name=f"Grupo {name} {uuid.uuid4().hex[:6]}",
                ),
            )
            assert code_of(response) == CREATED, response.text
            groups[name] = response.json()

        def cohort(name: str, members: list[str], tutor: Any, days: list[int], at: str):
            response = post_cohort(
                client,
                cookie,
                cohort_body(
                    template=template["id"],
                    subject=subject,
                    starts_on=tl.monday,
                    tutor=tutor.id,
                    start_time=at,
                    weekdays=days,
                    name=f"Cohorte {name} {uuid.uuid4().hex[:6]}",
                ),
            )
            assert code_of(response) == CREATED, response.text
            found[name] = response.json()
            for member in members:
                enrolled = post(
                    client,
                    cookie,
                    f"{COHORTS}/{found[name]['id']}/enrollments",
                    {"student_id": str(students[member])},
                )
                assert code_of(enrolled) == CREATED, enrolled.text

        trio = ["S1", "S2", "S3"]
        e1 = ["AD", "AC", "AR", "W1", "W2", "SL"]
        group("G3", trio, t1, [2, 4], "16:00")
        cohort("C3", trio, t1, [2, 4], "18:00")
        group("GE", e1, tg, [1, 3], "16:00")
        cohort("CE", e1, tg, [1, 3], "18:00")
        group("HB", ["B1"], t2, [5], "16:00")
        cohort("CB", ["B1"], t2, [5], "18:00")
        group("GP", ["S1"], t1, [6], "10:00")
        cohort("CP", ["S1"], t1, [6], "12:00")

    accept_at = tl.now + timedelta(hours=1)
    for tutor, column, record in (
        (t1, "group_id", groups["G3"]),
        (t1, "cohort_id", found["C3"]),
        (t2, "group_id", groups["HB"]),
        (t2, "cohort_id", found["CB"]),
        (tg, "group_id", groups["GE"]),
        (tg, "cohort_id", found["CE"]),
    ):
        answer_offer(
            client,
            tutor,
            accept_at,
            series_id_of(url, column, record["id"]),
            "accept",
            url,
        )

    # W1 leaves the E1 group and cohort; W2 (their sibling) stays.
    with frozen_clock(accept_at):
        cookie = sign_in(client, owner.username)
        [w] = [
            e
            for e in group_enrollments(url, groups["GE"]["id"])
            if e.student_id == students["W1"]
        ]
        assert code_of(withdraw_group(client, cookie, groups["GE"]["id"], w.id)) == OK
        [w] = [
            e
            for e in cohort_enrollments(url, found["CE"]["id"])
            if e.student_id == students["W1"]
        ]
        assert code_of(withdraw_cohort(client, cookie, found["CE"]["id"], w.id)) == OK

    trio_group = group_rows(url, groups["G3"]["id"])
    trio_cohort = cohort_rows(url, found["C3"]["id"])
    substitute = {8: f"substituted:{t2.id}"}
    set_rows(url, trio_group, TRIO_STATES | substitute)
    set_rows(url, trio_cohort, TRIO_STATES | substitute)
    return World(
        now=tl.now,
        monday=tl.monday,
        owner=owner,
        t1=t1,
        t2=t2,
        tg=tg,
        students=students,
        users=users,
        guardians=guardians,
        groups=groups,
        cohorts=found,
        trio_group_rows=group_rows(url, groups["G3"]["id"]),
        trio_cohort_rows=cohort_rows(url, found["C3"]["id"]),
    )


# --- Reading ----------------------------------------------------------------------


@dataclass
class Reads:
    listed: Any
    detail: Any
    found: Any

    @property
    def texts(self) -> list[str]:
        return [self.listed.text, self.detail.text, self.found.text]


def user_of(world: World, name: str) -> CreatedUser:
    return {"T1": world.t1, "T2": world.t2, "TG": world.tg, "owner": world.owner}.get(
        name
    ) or world.users[name]


def reads(client: TestClient, world: World, name: str, kind: str, record: str) -> Reads:
    """``(list, detail, sessions)`` of ``record`` (a group or cohort key) for
    ``name``, at "now", over the first 40 days."""
    base = GROUPS if kind == "group" else COHORTS
    records = world.groups if kind == "group" else world.cohorts
    record_id = records[record]["id"]
    start = datetime.combine(world.monday, datetime.min.time(), tzinfo=MX)
    with frozen_clock(world.now):
        cookie = sign_in(client, user_of(world, name).username)
        listed = get(client, cookie, base, status="all", limit=100)
        detail = get(client, cookie, f"{base}/{record_id}")
        found = get(
            client,
            cookie,
            f"{base}/{record_id}/sessions",
            **{"from": iso_z(start), "to": iso_z(start + timedelta(days=40))},
        )
    return Reads(listed=listed, detail=detail, found=found)


def listed_ids(response: Any) -> set[str]:
    return {item["id"] for item in response.json()["items"]}


def assert_hidden(world: World, got: Reads, kind: str, record: str) -> None:
    records = world.groups if kind == "group" else world.cohorts
    not_found = GROUP_NOT_FOUND if kind == "group" else COHORT_NOT_FOUND
    assert code_of(got.listed) == OK
    assert records[record]["id"] not in listed_ids(got.listed)
    assert code_of(got.detail) == not_found, got.detail.text
    assert code_of(got.found) == not_found, got.found.text


def assert_like_unknown(
    client: TestClient, world: World, name: str, kind: str, got: Reads
) -> None:
    """A hidden record answers exactly like an unknown id."""
    base = GROUPS if kind == "group" else COHORTS
    unknown = uuid.uuid4()
    start = datetime.combine(world.monday, datetime.min.time(), tzinfo=MX)
    with frozen_clock(world.now):
        cookie = sign_in(client, user_of(world, name).username)
        detail = get(client, cookie, f"{base}/{unknown}")
        found = get(
            client,
            cookie,
            f"{base}/{unknown}/sessions",
            **{"from": iso_z(start), "to": iso_z(start + timedelta(days=40))},
        )
    for hidden, missing in ((got.detail, detail), (got.found, found)):
        assert hidden.status_code == missing.status_code == 404
        assert {k: v for k, v in hidden.json().items() if k != "request_id"} == {
            k: v for k, v in missing.json().items() if k != "request_id"
        }
        assert {h.lower() for h in hidden.headers} == {
            h.lower() for h in missing.headers
        }


KINDS = [("group", "G3"), ("cohort", "C3")]


# --- C1: IDOR between families --------------------------------------------------------


@pytest.mark.parametrize(("kind", "record"), [("group", "HB"), ("cohort", "CB")])
def test_a_guardian_never_sees_another_familys_group_or_cohort(
    client, world, kind, record
):
    got = reads(client, world, "GA", kind, record)

    assert_hidden(world, got, kind, record)
    assert_like_unknown(client, world, "GA", kind, got)
    mine = reads(client, world, "GB", kind, record)
    assert code_of(mine.detail) == OK
    assert code_of(mine.found) == OK


@pytest.mark.parametrize(("kind", "record"), KINDS)
def test_the_guardian_of_a_minor_sees_the_group_and_cohort(client, world, kind, record):
    got = reads(client, world, "GA", kind, record)

    assert code_of(got.detail) == OK, got.detail.text
    assert code_of(got.found) == OK
    assert (world.groups if kind == "group" else world.cohorts)[record][
        "id"
    ] in listed_ids(got.listed)


@pytest.mark.parametrize("kind", ["group", "cohort"])
def test_the_guardian_of_an_adult_needs_the_consent(client, world, kind):
    record = "GE" if kind == "group" else "CE"
    without = reads(client, world, "GAD", kind, record)
    with_consent = reads(client, world, "GAC", kind, record)

    assert_hidden(world, without, kind, record)
    assert_like_unknown(client, world, "GAD", kind, without)
    assert without.listed.json()["items"] == []
    assert code_of(with_consent.detail) == OK
    assert code_of(with_consent.found) == OK
    assert (world.groups if kind == "group" else world.cohorts)[record][
        "id"
    ] in listed_ids(with_consent.listed)


@pytest.mark.parametrize("kind", ["group", "cohort"])
def test_a_revoked_consent_hides_on_the_next_request(
    module_seeded_url, client, world, kind
):
    record = "GE" if kind == "group" else "CE"
    name = f"GAR-{kind}"
    world.users[name], guardian_id = guardian(
        module_seeded_url,
        [world.students["AR"]],
        consent_for=(world.students["AR"],),
    )
    assert code_of(reads(client, world, name, kind, record).detail) == OK
    run_sql(
        module_seeded_url,
        "UPDATE consent_records SET revoked_at = now(), revoked_by_user_id = :u,"
        " revocation_cause = 'revoked' WHERE guardian_id = :g"
        " AND scope = 'guardian_academic_access' AND revoked_at IS NULL",
        {"u": str(world.users[name].id), "g": str(guardian_id)},
    )

    assert_hidden(world, reads(client, world, name, kind, record), kind, record)


def test_an_unlinked_guardian_and_an_inactive_profile_lose_access(
    module_seeded_url, client, world
):
    for name in ("GI", "GU"):
        for kind, record in KINDS:
            assert code_of(reads(client, world, name, kind, record).detail) == OK
    run_sql(
        module_seeded_url,
        "UPDATE guardians SET is_active = false WHERE id = :g",
        {"g": str(world.guardians["GI"])},
    )
    run_sql(
        module_seeded_url,
        "DELETE FROM student_guardians WHERE guardian_id = :g",
        {"g": str(world.guardians["GU"])},
    )

    for name in ("GI", "GU", "nobody"):
        for kind, record in KINDS:
            got = reads(client, world, name, kind, record)
            assert_hidden(world, got, kind, record)
            assert got.listed.json()["items"] == [], name


@pytest.mark.parametrize("kind", ["group", "cohort"])
def test_a_withdrawn_child_keeps_the_record_only_through_a_sibling(client, world, kind):
    record = "GE" if kind == "group" else "CE"
    only_w1 = reads(client, world, "GW1", kind, record)
    siblings = reads(client, world, "GW", kind, record)

    assert_hidden(world, only_w1, kind, record)
    assert code_of(siblings.detail) == OK
    items = siblings.found.json()["items"]
    assert items
    assert {s["id"] for i in items for s in i["students"]} == {
        str(world.students["W2"])
    }


@pytest.mark.parametrize("kind", ["group", "cohort"])
def test_an_enrolled_student_sees_their_own(client, world, kind):
    record = "GE" if kind == "group" else "CE"
    got = reads(client, world, "SL", kind, record)

    assert code_of(got.detail) == OK
    assert {s["id"] for i in got.found.json()["items"] for s in i["students"]} <= {
        str(world.students["SL"])
    }
    for other in ("AD", "AC", "AR", "W1", "W2"):
        for text in got.texts:
            assert str(world.students[other]) not in text, other


# --- C1: tutors -----------------------------------------------------------------------


@pytest.mark.parametrize(("kind", "record"), KINDS)
def test_a_tutor_never_sees_another_tutors_group_or_cohort(client, world, kind, record):
    other = "HB" if kind == "group" else "CB"
    got = reads(client, world, "T1", kind, other)

    assert_hidden(world, got, kind, other)
    assert_like_unknown(client, world, "T1", kind, got)
    mine = reads(client, world, "T1", kind, record)
    assert code_of(mine.detail) == OK
    assert mine.listed.json()["items"]
    # T2 substitutes on one trio occurrence: the session, never the record.
    substitute = reads(client, world, "T2", kind, record)
    assert_hidden(world, substitute, kind, record)


def test_a_pending_tutor_is_not_shown_to_families(module_seeded_url, client, world):
    for kind, record in (("group", "GP"), ("cohort", "CP")):
        column = "group_id" if kind == "group" else "cohort_id"
        records = world.groups if kind == "group" else world.cohorts
        [series] = run_sql(
            module_seeded_url,
            f"SELECT assignment_status FROM class_series WHERE {column} = :id",  # noqa: S608
            {"id": records[record]["id"]},
        )
        assert series.assignment_status == "pending"
        got = reads(client, world, "GA", kind, record)
        detail = got.detail.json()
        series_block = detail["current_series"] if kind == "group" else detail["series"]

        assert code_of(got.detail) == OK
        assert series_block["tutor"] is None, kind
        [item] = [
            i for i in got.listed.json()["items"] if i["id"] == records[record]["id"]
        ]
        assert item["tutor"] is None, kind


@pytest.mark.parametrize("kind", ["group", "cohort"])
def test_a_pending_offer_shows_it_and_a_decline_hides_it(
    module_seeded_url, client, world, kind
):
    record = "GP" if kind == "group" else "CP"
    column = "group_id" if kind == "group" else "cohort_id"
    records = world.groups if kind == "group" else world.cohorts
    pending = reads(client, world, "T1", kind, record)

    assert code_of(pending.detail) == OK, pending.detail.text
    assert records[record]["id"] in listed_ids(pending.listed)
    assert pending.found.json()["items"]  # the offered occurrences

    answer_offer(
        client,
        world.t1,
        world.now + timedelta(hours=2),
        series_id_of(module_seeded_url, column, records[record]["id"]),
        "decline",
        module_seeded_url,
    )

    assert_hidden(world, reads(client, world, "T1", kind, record), kind, record)


@pytest.mark.parametrize("kind", ["group", "cohort"])
def test_a_tutor_who_is_also_a_guardian_sees_the_union(client, world, kind):
    taught = "GE" if kind == "group" else "CE"
    family = "HB" if kind == "group" else "CB"
    records = world.groups if kind == "group" else world.cohorts
    as_tutor = reads(client, world, "TG", kind, taught)
    as_family = reads(client, world, "TG", kind, family)

    assert {records[taught]["id"], records[family]["id"]} <= listed_ids(as_tutor.listed)
    assert "tutor_roster" in as_tutor.detail.json()
    assert "tutor_roster" not in as_family.detail.json()
    assert all("participants" in i for i in as_tutor.found.json()["items"])
    family_items = as_family.found.json()["items"]
    assert family_items
    assert all("students" in i and "participants" not in i for i in family_items)


# --- C1: the leak test ----------------------------------------------------------------


@pytest.mark.parametrize(("kind", "record"), KINDS)
def test_a_guardian_learns_nothing_about_the_other_students(
    client, world, kind, record
):
    got = reads(client, world, "GA", kind, record)

    for text in got.texts:
        for other in ("S2", "S3"):
            assert str(world.students[other]) not in text, other
        for tutor in (world.t1, world.t2):
            assert str(tutor.id) not in text
    detail = got.detail.json()
    assert detail.get("roster") is None
    assert "tutor_roster" not in detail
    assert detail.get("enrolled_count") is None
    for item in got.found.json()["items"]:
        assert [s["id"] for s in item["students"]] == [str(world.students["S1"])]
        assert "participants" not in item


@pytest.mark.parametrize(("kind", "record"), KINDS)
def test_the_tutor_gets_the_active_students_by_name_only(client, world, kind, record):
    got = reads(client, world, "T1", kind, record)
    detail = got.detail.json()

    assert detail.get("roster") is None
    assert detail.get("enrolled_count") is None
    assert {r["student"]["id"] for r in detail["tutor_roster"]} == {
        str(world.students[s]) for s in ("S1", "S2", "S3")
    }
    for row in detail["tutor_roster"]:
        assert set(row) == ROSTER_ITEM_KEYS
        assert set(row["student"]) == ROSTER_STUDENT_KEYS
    for text in got.texts:
        assert "withdrawal_reason" not in text
        assert "enrolled_by" not in text
    for item in got.found.json()["items"]:
        assert {p["student_id"] for p in item["participants"]} <= {
            str(world.students[s]) for s in ("S1", "S2", "S3")
        }


def test_tutor_roster_lists_only_active_enrollments(client, world):
    for kind, record in (("group", "GE"), ("cohort", "CE")):
        detail = reads(client, world, "TG", kind, record).detail.json()

        ids = {r["student"]["id"] for r in detail["tutor_roster"]}
        assert str(world.students["W1"]) not in ids
        assert str(world.students["W2"]) in ids


# --- C1: the #67 A3 states ------------------------------------------------------------


def _row_ids(rows: list[Any], indexes: set[int]) -> set[str]:
    return {str(rows[i].id) for i in indexes}


@pytest.mark.parametrize(("kind", "record"), KINDS)
def test_family_sessions_follow_is_family_visible(client, world, kind, record):
    rows = world.trio_group_rows if kind == "group" else world.trio_cohort_rows
    got = reads(client, world, "GA", kind, record)
    seen = {item["id"] for item in got.found.json()["items"]}

    assert _row_ids(rows, FAMILY_SEES | {8}) <= seen
    assert not seen & _row_ids(rows, {3, 5, 6, 7})
    states = {item["id"]: item["state"] for item in got.found.json()["items"]}
    assert states[str(rows[2].id)] == "cancelled"  # once confirmed
    assert states[str(rows[4].id)] == "skipped_closure"
    upcoming = {o["id"] for o in got.detail.json()["next_occurrences"]}
    assert not upcoming & _row_ids(rows, {0, 3, 5, 6, 7})


@pytest.mark.parametrize(("kind", "record"), KINDS)
def test_tutor_sessions_follow_is_tutor_visible(client, world, kind, record):
    rows = world.trio_group_rows if kind == "group" else world.trio_cohort_rows
    got = reads(client, world, "T1", kind, record)
    seen = {item["id"] for item in got.found.json()["items"]}

    assert _row_ids(rows, TUTOR_SEES) <= seen
    assert not seen & _row_ids(rows, {3, 5, 7, 8})
    upcoming = {o["id"] for o in got.detail.json()["next_occurrences"]}
    assert str(rows[6].id) in upcoming  # offered: they can judge it
    assert not upcoming & _row_ids(rows, {0, 3, 5, 7, 8})


# --- C2: the allow-lists --------------------------------------------------------------


def test_the_group_shapes_are_closed(client, world):
    family = reads(client, world, "GA", "group", "G3")
    tutor = reads(client, world, "T1", "group", "G3")

    assert set(family.detail.json()) == GROUP_FAMILY_KEYS
    assert set(tutor.detail.json()) == GROUP_TUTOR_KEYS
    assert set(family.detail.json()["current_series"]) == GROUP_SERIES_KEYS
    assert family.detail.json()["current_series"]["tutor"] == {
        "display_name": "Tutora Uno"
    }
    assert set(tutor.detail.json()["current_series"]["tutor"]) == {
        "id",
        "display_name",
    }
    for item in family.found.json()["items"]:
        assert set(item) == GROUP_ITEM_KEYS | {"students"}
        assert item["tutor"] is None or set(item["tutor"]) == {"display_name"}
        assert all(set(s) == {"id", "display_name"} for s in item["students"])
    for item in tutor.found.json()["items"]:
        assert set(item) == GROUP_ITEM_KEYS | {"participants"}
        assert all(
            set(p) == {"student_id", "display_name"} for p in item["participants"]
        )
    [listed] = [
        i for i in family.listed.json()["items"] if i["id"] == world.groups["G3"]["id"]
    ]
    assert listed["enrolled_count"] is None
    assert listed["tutor"] == {"display_name": "Tutora Uno"}
    [taught] = [
        i for i in tutor.listed.json()["items"] if i["id"] == world.groups["G3"]["id"]
    ]
    assert taught["enrolled_count"] is None
    assert set(taught["tutor"]) == {"id", "display_name"}


def test_the_cohort_shapes_are_closed(client, world):
    family = reads(client, world, "GA", "cohort", "C3")
    tutor = reads(client, world, "T1", "cohort", "C3")

    assert set(family.detail.json()) == COHORT_DETAIL_KEYS
    assert set(tutor.detail.json()) == COHORT_DETAIL_KEYS | {"tutor_roster"}
    assert set(family.detail.json()["series"]) == COHORT_SERIES_KEYS
    assert set(tutor.detail.json()["series"]) == COHORT_SERIES_KEYS
    assert family.detail.json()["series"]["tutor"] == {"display_name": "Tutora Uno"}
    for item in family.found.json()["items"]:
        assert set(item) == COHORT_ITEM_KEYS | {"students"}
        assert item["tutor"] is None or set(item["tutor"]) == {"display_name"}
    for item in tutor.found.json()["items"]:
        assert set(item) == COHORT_ITEM_KEYS | {"participants"}
    for listed in (family.listed, tutor.listed):
        [item] = [
            i for i in listed.json()["items"] if i["id"] == world.cohorts["C3"]["id"]
        ]
        assert item["enrolled_count"] is None
        assert item["assignment_status"] is None


# --- A6, C3: the statement budgets ----------------------------------------------------


@dataclass
class Bulk:
    now: datetime
    subject: uuid.UUID
    """The bulk records' subject: the lists filter by it (the world's
    records, in another year, are ended by then)."""
    first: datetime
    principals: dict[str, Principal]
    small_groups: list[uuid.UUID]
    small_cohorts: list[uuid.UUID]
    big_group: uuid.UUID
    big_cohort: uuid.UUID


def _principal(user_id: uuid.UUID, *roles: str) -> Principal:
    return Principal(user_id=user_id, roles=frozenset(roles), permissions=frozenset())


@pytest.fixture(scope="module")
def bulk(module_seeded_url: str, client: Any) -> Bulk:
    """25 one-session groups and cohorts on distinct weekdays (16:00 and
    18:00) and one 25-session group and cohort (weekdays 10:00 / 12:00 for
    five weeks, 25 other students), all with the child K and taught by D
    (a tutor who is also K's guardian, without K's consent: K is a minor, so
    the zone is read). K also has a login. "Now" is the Friday before the
    first Monday (beyond the 24 h booking notice)."""
    url = module_seeded_url
    tl = timeline()
    now = tl.now - timedelta(days=2)  # Friday: beyond the 24 h notice
    subject = new_subject(url)
    owner = create_user(url, roles=(OWNER,))
    dual = tutor_user(url, subject, roles=(TUTOR, GUARDIAN))
    k_user = create_user(url, roles=(STUDENT,))
    k = own_profile(url, k_user.id, minor_on=tl.monday)
    guardian_profile(url, dual, [k])
    others = [new_child(url, minor_on=tl.monday) for _ in range(25)]
    small_groups: list[uuid.UUID] = []
    small_cohorts: list[uuid.UUID] = []
    days = [tl.monday + timedelta(days=w * 7 + d) for w in range(5) for d in range(5)]
    with frozen_clock(now):
        cookie = sign_in(client, owner.username)
        single = new_template(client, cookie, [subject], session_count=1)
        many = new_template(client, cookie, [subject], session_count=25)
        for day in days:
            response = post_group(
                client,
                cookie,
                group_body(
                    subject=subject,
                    starts_on=day,
                    students=[k],
                    tutor=dual.id,
                    weekdays=[day.isoweekday()],
                    count=1,
                    start_time="16:00",
                ),
            )
            assert code_of(response) == CREATED, response.text
            small_groups.append(uuid.UUID(response.json()["id"]))
            response = post_cohort(
                client,
                cookie,
                cohort_body(
                    template=single["id"],
                    subject=subject,
                    starts_on=day,
                    tutor=dual.id,
                    weekdays=[day.isoweekday()],
                    start_time="18:00",
                ),
            )
            assert code_of(response) == CREATED, response.text
            small_cohorts.append(uuid.UUID(response.json()["id"]))
            enrolled = post(
                client,
                cookie,
                f"{COHORTS}/{small_cohorts[-1]}/enrollments",
                {"student_id": str(k)},
            )
            assert code_of(enrolled) == CREATED, enrolled.text
        response = post_group(
            client,
            cookie,
            group_body(
                subject=subject,
                starts_on=tl.monday,
                students=[k, *others],
                tutor=dual.id,
                weekdays=[1, 2, 3, 4, 5],
                count=25,
                start_time="10:00",
                max_students=30,
            ),
        )
        assert code_of(response) == CREATED, response.text
        big_group = uuid.UUID(response.json()["id"])
        response = post_cohort(
            client,
            cookie,
            cohort_body(
                template=many["id"],
                subject=subject,
                starts_on=tl.monday,
                tutor=dual.id,
                weekdays=[1, 2, 3, 4, 5],
                start_time="12:00",
                max_students=30,
            ),
        )
        assert code_of(response) == CREATED, response.text
        big_cohort = uuid.UUID(response.json()["id"])
        for student in [k, *others]:
            enrolled = post(
                client,
                cookie,
                f"{COHORTS}/{big_cohort}/enrollments",
                {"student_id": str(student)},
            )
            assert code_of(enrolled) == CREATED, enrolled.text
    accept_at = now + timedelta(hours=1)
    for column, record in (("group_id", big_group), ("cohort_id", big_cohort)):
        answer_offer(
            client, dual, accept_at, series_id_of(url, column, record), "accept", url
        )
    return Bulk(
        now=now,
        subject=subject,
        first=datetime.combine(tl.monday, datetime.min.time(), tzinfo=MX),
        principals={
            "student": _principal(k_user.id, STUDENT),
            "guardian": _principal(dual.id, GUARDIAN),
            "tutor": _principal(dual.id, TUTOR),
            "guardian+tutor": _principal(dual.id, GUARDIAN, TUTOR),
            "student+guardian": _principal(k_user.id, STUDENT, GUARDIAN),
            "staff": Principal(
                user_id=owner.id,
                roles=frozenset({OWNER}),
                permissions=frozenset({"schedule.view", "course.view"}),
            ),
        },
        small_groups=small_groups,
        small_cohorts=small_cohorts,
        big_group=big_group,
        big_cohort=big_cohort,
    )


GROUP_BUDGETS = {"detail": 9, "sessions": 7, "list": 7}
"""#56 G1 8 and H3 6, plus one (A6); the list: the staff read's 6 plus one."""
COHORT_BUDGETS = {"detail": 10, "sessions": 8, "list": 6}
"""#66 F1 9, G4 7 and #65 F2 5, plus one (A6)."""


def _range(bulk: Bulk, items: int) -> GroupSessionsQuery:
    """A range holding 0, 1 or 25 of the big record's sessions (weekdays of
    five weeks from the first Monday)."""
    start = bulk.first - timedelta(days=7) if items == 0 else bulk.first
    days = {0: 3, 1: 1, 25: 35}[items]
    return GroupSessionsQuery.model_validate(
        {"from": start, "to": start + timedelta(days=days)}
    )


@pytest.mark.parametrize(
    "who",
    ["student", "guardian", "tutor", "guardian+tutor", "student+guardian", "staff"],
)
def test_the_group_statement_budgets(module_seeded_url, bulk, who):
    url = module_seeded_url
    principal = bulk.principals[who]
    counts: dict[str, list[int]] = {"detail": [], "sessions": [], "list": []}
    with frozen_clock(bulk.now):
        for group_id in (bulk.small_groups[0], bulk.big_group):
            count, _ = count_statements(
                url, lambda s, g=group_id: scheduling.get_group(s, principal, g)
            )
            counts["detail"].append(count)
        for items in (0, 1, 25):
            query = _range(bulk, items)
            count, result = count_statements(
                url,
                lambda s, q=query: scheduling.list_group_sessions(
                    s, principal, bulk.big_group, q
                ),
            )
            counts["sessions"].append(count)
            assert len(result.items) == items, (who, items)
        for status, limit, expected in (
            ("ended", 25, 0),
            ("all", 1, 1),
            ("all", 25, 25),
        ):
            count, page = count_statements(
                url,
                lambda s, st=status, n=limit: scheduling.list_groups(
                    s,
                    principal,
                    q=None,
                    subject_id=bulk.subject,
                    status=st,
                    limit=n,
                    cursor=None,
                ),
            )
            counts["list"].append(count)
            assert len(page.items) == expected, (who, status, limit)

    for read, budget in GROUP_BUDGETS.items():
        assert all(c <= budget for c in counts[read]), (who, read, counts[read])


@pytest.mark.parametrize(
    "who",
    ["student", "guardian", "tutor", "guardian+tutor", "student+guardian", "staff"],
)
def test_the_cohort_statement_budgets(module_seeded_url, bulk, who):
    url = module_seeded_url
    principal = bulk.principals[who]
    counts: dict[str, list[int]] = {"detail": [], "sessions": [], "list": []}

    def viewer_then(call: Any) -> Any:
        async def _read(session: Any) -> Any:
            viewer = await cohorts.cohort_viewer(session, principal)
            return await call(session, viewer)

        return _read

    with frozen_clock(bulk.now):
        for cohort_id in (bulk.small_cohorts[0], bulk.big_cohort):
            count, _ = count_statements(
                url,
                viewer_then(
                    lambda s, v, c=cohort_id: cohorts.get_cohort_detail(s, c, v)
                ),
            )
            counts["detail"].append(count)
        for items in (0, 1, 25):
            window = _range(bulk, items)
            query = CohortSessionsQuery.model_validate(
                {"from": window.from_, "to": window.to}
            )
            count, result = count_statements(
                url,
                viewer_then(
                    lambda s, v, q=query: cohorts.list_cohort_sessions(
                        s, bulk.big_cohort, q, v
                    )
                ),
            )
            counts["sessions"].append(count)
            assert len(result.items) == items, (who, items)
        for status, limit, expected in (
            ("ended", 25, 0),
            ("all", 1, 1),
            ("all", 25, 25),
        ):
            count, page = count_statements(
                url,
                viewer_then(
                    lambda s, v, st=status, n=limit: cohorts.list_cohorts(
                        s,
                        q=None,
                        template_id=None,
                        subject_id=bulk.subject,
                        status=st,
                        limit=n,
                        cursor=None,
                        viewer=v,
                    )
                ),
            )
            counts["list"].append(count)
            assert len(page.items) == expected, (who, status, limit)

    for read, budget in COHORT_BUDGETS.items():
        assert all(c <= budget for c in counts[read]), (who, read, counts[read])
