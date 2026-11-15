"""Course completion of a cohort (#85 C, E1-E2): the record policy, the live
computation from the snapshot, and the statement budget, through the real
app and real logins.

**Cohort A** (Monday/Wednesday 16:00, ten sessions, tutor T): its template
asks for 80 % attendance, two required modules ("Introducción", evidenced
by the required assessment "Parcial", minimum 70; "Desarrollo", without
assessments) and an optional assessment "Opcional" (minimum 60). After the
cohort was created the live template dropped "Parcial" and lowered the
minimum to 50: completion still follows the snapshot.

A's rows: 0-5 completed, 6 cancelled, 7 skipped by a closure, 8 completed,
9 confirmed (the last class). ``MID`` is after row 8, ``END`` after row 9
(the cohort has ended, row 9 waits for its closeout), ``AFTER`` the next
day (T's series stopped producing).

Students (``P`` present, ``L`` late, ``A`` absent; default present):

- S (own login), AD (adult, guardian GA without consent), AC (adult,
  guardian GAC with consent; absent in rows 0 and 1), M (minor, guardian GM;
  absent in row 5), K (minor, guardian GK; late in row 3, absent in 4, 5);
- L (enrolled after row 3 started: rows 4-9; late in row 4);
- R (in row 0, withdrew, re-enrolled after row 3: rows 0 and 4-9);
- W (own login, withdrew after row 0 started: only row 0, not on the
  roster).

Evaluations linked to A (``Parcial`` unless noted): S 85.00; M 90.00 then
65.00 (latest counts) and "Opcional" 50.00; K a draft and a voided one only;
AD 50.00 then 70.00 on the same day (finalized later counts); AC 69.99; R
75.00; L 80.00 **in cohort B** (the same assessment id: ignored).

Cohort B (18:00, tutor T2, student B1). The budget cohorts C (two
students X, Y) and D (X, Y and 28 more, with attendance and evaluations)
are taught by T.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.authz import PermissionDeniedError
from app.modules.courses.service import CohortNotFoundError
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    STUDENT,
    TUTOR,
)
from app.workflows import course_completion
from tests.conftest import frozen_clock
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import count_statements, get
from tests.integration.cohort_support import (
    COHORTS,
    cohort_body,
    cohort_rows,
    create_cohort,
    enroll,
    put_template,
    withdraw,
)
from tests.integration.conftest import run_sql
from tests.integration.consent_support import adult_profile_for
from tests.integration.courses_support import (
    assessment,
    create_template,
    module,
    write_body,
)
from tests.integration.group_support import set_state
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of
from tests.integration.schedule_support import principal_of
from tests.integration.series_support import owner_cookie, timeline
from tests.integration.test_class_session_create import CREATED, new_room, new_subject
from tests.integration.test_group_cohort_family_tutor_reads import (
    guardian,
    guardian_profile,
    new_child,
    own_profile,
    tutor_user,
)

NOT_FOUND = (404, "cohort_not_found")
DENIED = (403, "permission_denied")
OK = (200, None)

STUDENT_KEYS = {
    "student",
    "enrolled_at",
    "status",
    "final",
    "attendance",
    "assessments",
    "modules",
}
KEYS = {
    "body": {
        "cohort_id",
        "cohort_name",
        "cohort_status",
        "template",
        "criteria",
        "students",
    },
    "template": {"name", "version"},
    "criteria": {"min_attendance_percent", "required_modules", "assessments"},
    "required_module": {"id", "title"},
    "criteria_assessment": {
        "id",
        "title",
        "module_id",
        "is_required",
        "min_score_percent",
    },
    "student": {"id", "display_name"},
    "attendance": {
        "status",
        "min_percent",
        "held",
        "attended",
        "absent",
        "remaining",
        "awaiting_closeout",
        "attendance_percent",
    },
    "criterion": {"status", "items"},
    "assessment_item": {
        "assessment_id",
        "title",
        "is_required",
        "min_score_percent",
        "status",
        "score_percent",
        "evaluated_on",
        "attempts",
    },
    "module_item": {"module_id", "title", "status", "basis"},
}


@dataclass
class World:
    url: str
    now: datetime
    mid: datetime
    end: datetime
    after: datetime
    a: dict[str, Any]
    b: dict[str, Any]
    c: dict[str, Any]
    d: dict[str, Any]
    rows: list[Any]
    template: dict[str, Any]
    parcial: str
    opcional: str
    introduccion: str
    desarrollo: str
    tutor: CreatedUser
    tutor2: CreatedUser
    eval_template: uuid.UUID
    subject: uuid.UUID
    students: dict[str, uuid.UUID]
    users: dict[str, CreatedUser]
    hidden: dict[str, str] = field(default_factory=dict)
    """Strings no completion body may carry (draft/void evaluation ids,
    feedback, reasons)."""
    counted_ids: set[str] = field(default_factory=set)


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Any:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


def _eval_template(url: str, subject: uuid.UUID) -> uuid.UUID:
    [row] = run_sql(
        url,
        "INSERT INTO evaluation_templates (name, kind, subject_id)"
        " VALUES (:n, 'ongoing', :s) RETURNING id",
        {"n": f"Plantilla {uuid.uuid4().hex[:8]}", "s": subject},
    )
    return uuid.UUID(str(row.id))


def evaluation(
    world: World,
    student: uuid.UUID,
    percent: str,
    *,
    cohort: str | None = None,
    assessment_id: str | None = None,
    evaluated_on: date = date(2000, 1, 1),
    finalized_at: datetime | None = None,
    status: str = "final",
    voided: bool = False,
    feedback: str | None = None,
    change_reason: str | None = None,
) -> str:
    """A course evaluation of ``student`` linked to cohort A's ``Parcial``
    (unless given), on row 0."""
    final = status == "final"
    cohort_id = cohort or world.a["id"]
    session_id = cohort_rows(world.url, cohort_id)[0].id
    [row] = run_sql(
        world.url,
        "INSERT INTO evaluations (student_id, subject_id, author_user_id,"
        " template_id, template_version, template_name, kind, evaluated_on,"
        " class_session_id, cohort_id, course_assessment_id,"
        " course_assessment_title, status, feedback, score_total, max_total,"
        " score_percent, finalized_at, last_change_reason, voided_at,"
        " voided_by_user_id, void_reason)"
        " VALUES (:st, :sub, :a, :t, 1, 'Examen', 'ongoing', :on, :session,"
        " :cohort, :assessment, 'Parcial', :status, :f, :score, :max, :pct, :fin,"
        " :reason, :void, :void_by, :void_reason) RETURNING id",
        {
            "st": student,
            "sub": world.subject,
            "a": world.tutor.id,
            "t": world.eval_template,
            "on": evaluated_on,
            "session": session_id,
            "cohort": cohort_id,
            "assessment": assessment_id or world.parcial,
            "status": status,
            "f": feedback,
            "score": Decimal(percent) / 10 if final else None,
            "max": Decimal(10) if final else None,
            "pct": Decimal(percent) if final else None,
            "fin": (finalized_at or world.now) if final else None,
            "reason": change_reason,
            "void": world.now if voided else None,
            "void_by": world.tutor.id if voided else None,
            "void_reason": "CANARY-void-reason" if voided else None,
        },
    )
    return str(row.id)


def mark_all_present(url: str, session_ids: list[Any], marker: uuid.UUID) -> None:
    run_sql(
        url,
        "INSERT INTO class_session_attendance (class_session_id, student_id,"
        " status, marked_by_user_id, marked_at)"
        " SELECT class_session_id, student_id, 'present', :u, now()"
        " FROM class_session_participants WHERE class_session_id = ANY(:ids)"
        " ON CONFLICT (class_session_id, student_id) DO NOTHING",
        {"u": marker, "ids": [uuid.UUID(str(i)) for i in session_ids]},
    )


def set_mark(url: str, session_id: Any, student: uuid.UUID, status: str) -> None:
    run_sql(
        url,
        "UPDATE class_session_attendance SET status = :s"
        " WHERE class_session_id = :id AND student_id = :st",
        {"s": status, "id": str(session_id), "st": student},
    )


def _enrollment_id(response: Any) -> str:
    assert code_of(response) == CREATED, response.text
    return str(response.json()["id"])


@pytest.fixture(scope="module")
def world(module_seeded_url: str, client: Any) -> World:  # noqa: PLR0915 - one world
    url = module_seeded_url
    tl = timeline()
    subject = new_subject(url)
    tutor = tutor_user(url, subject)
    users: dict[str, CreatedUser] = {}
    students: dict[str, uuid.UUID] = {}
    for name in ("S", "W", "B1"):
        users[name] = create_user(url, roles=(STUDENT,))
        students[name] = adult_profile_for(url, users[name].id)
    for name in ("M", "K", "L", "R"):
        students[name] = new_child(url, minor_on=tl.monday)
    for name in ("AD", "AC"):
        students[name] = new_child(url, minor_on=tl.monday, adult=True)
    users["X"] = create_user(url, roles=(STUDENT,))
    students["X"] = own_profile(url, users["X"].id, minor_on=tl.monday)
    students["Y"] = new_child(url, minor_on=tl.monday, adult=True)
    s = students
    # T2 teaches B and is M's guardian (a tutor + guardian union).
    tutor2 = tutor_user(url, subject, roles=(TUTOR, GUARDIAN))
    guardian_profile(url, tutor2, [s["M"], s["X"]])
    # T (who teaches A, C and D) also has a guardian profile of X: measured
    # with both roles in the budget test.
    guardian_profile(url, tutor, [s["X"]])
    users["T2"] = tutor2
    for name, children, consent, roles in (
        ("GM", ["M"], (), (GUARDIAN,)),
        ("GK", ["K"], (), (GUARDIAN,)),
        ("GA", ["AD"], (), (GUARDIAN,)),
        ("GAC", ["AC"], ("AC",), (GUARDIAN,)),
        ("GMAD", ["M", "AD"], (), (GUARDIAN,)),
        ("GU", [], (), (GUARDIAN,)),
        ("FG", ["K"], (), (FINANCE_STAFF, GUARDIAN)),
        ("FGX", ["X"], (), (FINANCE_STAFF, GUARDIAN)),
        ("CG", ["M"], (), (ACADEMIC_COORDINATOR, GUARDIAN)),
        ("GX", ["X"], (), (GUARDIAN,)),
        ("GY", ["Y"], ("Y",), (GUARDIAN,)),
        ("GXN", ["X", "AD"], (), (GUARDIAN,)),
    ):
        users[name], _ = guardian(
            url,
            [s[c] for c in children],
            consent_for=tuple(s[c] for c in consent),
            roles=roles,
        )
    users["TG"] = tutor_user(url, subject, roles=(TUTOR, GUARDIAN))
    guardian_profile(url, users["TG"], [s["X"]])
    users["T"] = tutor
    for role in (OWNER, ADMINISTRATOR, ACADEMIC_COORDINATOR, FINANCE_STAFF):
        users[role] = create_user(url, roles=(role,))
    room = new_room(url, capacity=20)

    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        template = create_template(
            client,
            cookie,
            [str(subject)],
            session_count=10,
            session_minutes=60,
            min_attendance_percent=80,
            modules=[
                module("Introducción"),
                module("Desarrollo"),
                module("Cierre", is_required=False),
            ],
            assessments=[
                assessment("Parcial", module_index=0, min_score_percent=70),
                assessment(
                    "Opcional",
                    is_required=False,
                    module_index=None,
                    min_score_percent=60,
                ),
            ],
        )

        def make(
            start: str, teacher: CreatedUser, weekdays: list[int], **extra: Any
        ) -> dict[str, Any]:
            response = create_cohort(
                client,
                cookie,
                cohort_body(
                    template=template["id"],
                    subject=subject,
                    starts_on=tl.monday,
                    tutor=teacher.id,
                    start_time=start,
                    weekdays=weekdays,
                    max_students=40,
                    **extra,
                ),
            )
            assert code_of(response) == CREATED, response.text
            created: dict[str, Any] = response.json()
            return created

        a = make("16:00", tutor, [1, 3], room=room)
        b = make("18:00", tutor2, [1, 3])
        c = make("16:00", tutor, [2, 4])
        d = make("18:00", tutor, [2, 4])
        early: dict[str, str] = {}
        for name in ("S", "M", "K", "AD", "AC", "W", "R"):
            early[name] = _enrollment_id(enroll(client, cookie, a["id"], s[name]))
        _enrollment_id(enroll(client, cookie, b["id"], s["B1"]))
        for name in ("X", "Y"):
            _enrollment_id(enroll(client, cookie, c["id"], s[name]))
            _enrollment_id(enroll(client, cookie, d["id"], s[name]))
        for _ in range(28):
            _enrollment_id(
                enroll(client, cookie, d["id"], new_child(url, minor_on=tl.monday))
            )
        # The live template changes after the cohorts were created: the
        # cohorts keep their snapshot.
        body = write_body(template)
        body["min_attendance_percent"] = 50
        body["assessments"] = [
            item for item in body["assessments"] if item["title"] != "Parcial"
        ]
        assert code_of(put_template(client, cookie, template["id"], body)) == OK

    rows = cohort_rows(url, a["id"])
    with frozen_clock(rows[0].starts_at + timedelta(minutes=1)):
        cookie = owner_cookie(url, client)
        for name in ("W", "R"):
            assert code_of(withdraw(client, cookie, a["id"], early[name])) == OK
    with frozen_clock(rows[3].starts_at + timedelta(minutes=1)):
        cookie = owner_cookie(url, client)
        for name in ("L", "R"):
            _enrollment_id(enroll(client, cookie, a["id"], s[name]))

    completed = [0, 1, 2, 3, 4, 5, 8]
    for index in completed:
        set_state(url, rows[index].id, "completed")
    set_state(url, rows[6].id, "cancelled")
    set_state(url, rows[7].id, "skipped_closure")
    set_state(url, rows[9].id, "confirmed")
    mark_all_present(url, [rows[i].id for i in completed], tutor.id)
    for index, name, status in (
        (0, "AC", "absent"),
        (1, "AC", "absent"),
        (5, "M", "absent"),
        (3, "K", "late"),
        (4, "K", "absent"),
        (5, "K", "absent"),
        (4, "L", "late"),
    ):
        set_mark(url, rows[index].id, s[name], status)

    # C and D: the first four rows completed, everyone present; D also has
    # two evaluations per student.
    for cohort in (c, d):
        found = cohort_rows(url, cohort["id"])
        for row in found[:4]:
            set_state(url, row.id, "completed")
        for row in found[4:]:
            set_state(url, row.id, "confirmed")
        mark_all_present(url, [row.id for row in found[:4]], tutor.id)
    for row in cohort_rows(url, b["id"]):
        set_state(url, row.id, "confirmed")

    assessments = {item["title"]: item["id"] for item in template["assessments"]}
    modules = {item["title"]: item["id"] for item in template["modules"]}
    world = World(
        url=url,
        now=tl.now,
        mid=rows[8].ends_at + timedelta(hours=1),
        end=rows[9].ends_at + timedelta(hours=1),
        after=rows[9].ends_at + timedelta(days=1),
        a=a,
        b=b,
        c=c,
        d=d,
        rows=rows,
        template=template,
        parcial=assessments["Parcial"],
        opcional=assessments["Opcional"],
        introduccion=modules["Introducción"],
        desarrollo=modules["Desarrollo"],
        tutor=tutor,
        tutor2=tutor2,
        eval_template=_eval_template(url, subject),
        subject=subject,
        students=students,
        users=users,
    )
    day = rows[0].starts_at.date()
    counted = world.counted_ids
    counted.add(evaluation(world, s["S"], "85.00", evaluated_on=day))
    evaluation(
        world,
        s["M"],
        "90.00",
        evaluated_on=day,
        change_reason="CANARY-change-reason",
    )
    counted.add(evaluation(world, s["M"], "65.00", evaluated_on=day + timedelta(1)))
    counted.add(
        evaluation(
            world, s["M"], "50.00", evaluated_on=day, assessment_id=world.opcional
        )
    )
    world.hidden["draft"] = evaluation(
        world, s["K"], "99.00", status="draft", feedback="CANARY-draft-feedback"
    )
    world.hidden["voided"] = evaluation(
        world, s["K"], "95.00", voided=True, evaluated_on=day + timedelta(2)
    )
    world.hidden["draft_feedback"] = "CANARY-draft-feedback"
    world.hidden["void_reason"] = "CANARY-void-reason"
    world.hidden["change_reason"] = "CANARY-change-reason"
    evaluation(world, s["AD"], "50.00", evaluated_on=day, finalized_at=tl.now)
    counted.add(
        evaluation(
            world,
            s["AD"],
            "70.00",
            evaluated_on=day,
            finalized_at=tl.now + timedelta(hours=1),
        )
    )
    counted.add(evaluation(world, s["AC"], "69.99", evaluated_on=day))
    counted.add(evaluation(world, s["R"], "75.00", evaluated_on=day))
    evaluation(world, s["L"], "80.00", cohort=b["id"], evaluated_on=day)
    # D grows: two counted evaluations per student (C has none).
    run_sql(
        url,
        "INSERT INTO evaluations (student_id, subject_id, author_user_id,"
        " template_id, template_version, template_name, kind, evaluated_on,"
        " class_session_id, cohort_id, course_assessment_id,"
        " course_assessment_title, status, score_total, max_total,"
        " score_percent, finalized_at)"
        " SELECT e.student_id, :sub, :a, :t, 1, 'Examen', 'ongoing', :on, :session,"
        " e.cohort_id, :assessment, 'Parcial', 'final', 8, 10, 80, now()"
        " FROM cohort_enrollments e CROSS JOIN generate_series(1, 2)"
        " WHERE e.cohort_id = :cohort",
        {
            "sub": subject,
            "a": tutor.id,
            "t": world.eval_template,
            "on": day,
            "session": cohort_rows(url, d["id"])[0].id,
            "cohort": d["id"],
            "assessment": world.parcial,
        },
    )
    return world


def read(
    client: TestClient,
    world: World,
    name: str,
    *,
    at: datetime | None = None,
    cohort: str | None = None,
) -> Any:
    with frozen_clock(at or world.mid):
        cookie = sign_in(client, world.users[name].username)
        return get(client, cookie, f"{COHORTS}/{cohort or world.a['id']}/completion")


def rows_by_student(response: Any) -> dict[uuid.UUID, dict[str, Any]]:
    assert code_of(response) == OK, response.text
    return {uuid.UUID(row["student"]["id"]): row for row in response.json()["students"]}


def names(world: World, response: Any) -> set[str]:
    by_id = {v: k for k, v in world.students.items()}
    return {by_id[i] for i in rows_by_student(response)}


def assert_like_unknown(
    client: TestClient, world: World, name: str, hidden: Any, at: datetime | None = None
) -> None:
    """A hidden cohort answers exactly like an unknown id."""
    missing = read(client, world, name, at=at, cohort=str(uuid.uuid4()))
    for response in (hidden, missing):
        assert code_of(response) == NOT_FOUND, response.text
    assert {k: v for k, v in hidden.json().items() if k != "request_id"} == {
        k: v for k, v in missing.json().items() if k != "request_id"
    }
    assert {h.lower() for h in hidden.headers} == {h.lower() for h in missing.headers}


@contextmanager
def closed_out(world: World) -> Iterator[None]:
    """Row 9's closeout submitted (completed, everyone present), undone
    afterwards."""
    row = world.rows[9]
    set_state(world.url, row.id, "completed")
    mark_all_present(world.url, [row.id], world.tutor.id)
    try:
        yield
    finally:
        run_sql(
            world.url,
            "DELETE FROM class_session_attendance WHERE class_session_id = :id",
            {"id": str(row.id)},
        )
        set_state(world.url, row.id, "confirmed")


@contextmanager
def changed(
    world: World, apply: str, undo: str, params: dict[str, Any]
) -> Iterator[None]:
    run_sql(world.url, apply, params)
    try:
        yield
    finally:
        run_sql(world.url, undo, params)


# --- C1: who reads which rows ---------------------------------------------------------

ROSTER = {"S", "M", "K", "AD", "AC", "L", "R"}


@pytest.mark.parametrize("role", [OWNER, ADMINISTRATOR, ACADEMIC_COORDINATOR])
def test_evaluation_staff_read_every_active_enrollment(client, world, role):
    response = read(client, world, role)
    assert names(world, response) == ROSTER
    found = [row["student"] for row in response.json()["students"]]
    expected = sorted(found, key=lambda st: (st["display_name"].casefold(), st["id"]))
    assert found == expected
    assert str(world.students["W"]) not in response.text


def test_the_teaching_tutor_reads_the_roster_until_the_series_stops(client, world):
    for at in (world.mid, world.end):
        assert names(world, read(client, world, "T", at=at)) == ROSTER
    stopped = read(client, world, "T", at=world.after)
    assert_like_unknown(client, world, "T", stopped, at=world.after)
    # Staff still read it.
    assert names(world, read(client, world, OWNER, at=world.after)) == ROSTER


def test_a_tutor_of_another_cohort_gets_the_unknown_id_404(client, world):
    assert_like_unknown(
        client, world, "T", read(client, world, "T", cohort=world.b["id"])
    )


def test_finance_is_denied(client, world):
    response = read(client, world, FINANCE_STAFF)
    assert code_of(response) == DENIED
    assert response.headers["content-type"] == "application/problem+json"
    # An unknown id is the 404 for them too (the matrix placeholder).
    unknown = read(client, world, FINANCE_STAFF, cohort=str(uuid.uuid4()))
    assert code_of(unknown) == NOT_FOUND


@pytest.mark.parametrize(
    ("name", "sees"),
    [
        ("S", {"S"}),
        ("GM", {"M"}),
        ("GK", {"K"}),
        ("GAC", {"AC"}),
        ("GMAD", {"M"}),  # the adult without consent is left out
        ("T2", {"M"}),  # tutor of B, guardian of M
        ("FG", {"K"}),  # Finance and K's guardian
    ],
)
def test_families_read_only_their_own_rows(client, world, name, sees):
    response = read(client, world, name)
    assert names(world, response) == sees
    others = ROSTER - sees
    for other in others:
        assert str(world.students[other]) not in response.text, other


def test_role_unions_with_the_roster_read_everything(client, world):
    assert names(world, read(client, world, "CG")) == ROSTER
    # T2 reads B's whole roster as its tutor.
    assert names(world, read(client, world, "T2", cohort=world.b["id"])) == {"B1"}


@pytest.mark.parametrize("name", ["GA", "GU", "W", "B1", "X", "GX", "TG"])
def test_everyone_else_gets_the_unknown_id_404(client, world, name):
    assert_like_unknown(client, world, name, read(client, world, name))


def test_finance_with_a_child_elsewhere_is_denied(client, world):
    assert code_of(read(client, world, "FGX")) == DENIED


def test_a_revoked_consent_applies_on_the_next_request(client, world):
    user, guardian_id = guardian(
        world.url, [world.students["AC"]], consent_for=(world.students["AC"],)
    )
    world.users["GAC2"] = user
    assert names(world, read(client, world, "GAC2")) == {"AC"}
    run_sql(
        world.url,
        "UPDATE consent_records SET revoked_at = now(), revoked_by_user_id = :u,"
        " revocation_cause = 'revoked' WHERE guardian_id = :g"
        " AND scope = 'guardian_academic_access' AND revoked_at IS NULL",
        {"u": str(user.id), "g": str(guardian_id)},
    )
    assert_like_unknown(client, world, "GAC2", read(client, world, "GAC2"))


def test_a_malformed_id_and_no_session(client, world):
    with frozen_clock(world.mid):
        cookie = sign_in(client, world.users[OWNER].username)
        assert code_of(get(client, cookie, f"{COHORTS}/nope/completion"))[0] == 422
        client.cookies.clear()
        anonymous = client.get(f"{COHORTS}/{world.a['id']}/completion")
    assert code_of(anonymous) == (401, "authentication_required")


# --- C2: the closed body --------------------------------------------------------------


def test_the_body_is_a_closed_allow_list(client, world):
    response = read(client, world, OWNER)
    body = response.json()
    assert set(body) == KEYS["body"]
    assert set(body["template"]) == KEYS["template"]
    assert set(body["criteria"]) == KEYS["criteria"]
    for item in body["criteria"]["required_modules"]:
        assert set(item) == KEYS["required_module"]
    for item in body["criteria"]["assessments"]:
        assert set(item) == KEYS["criteria_assessment"]
    for row in body["students"]:
        assert set(row) == STUDENT_KEYS
        assert set(row["student"]) == KEYS["student"]
        assert set(row["attendance"]) == KEYS["attendance"]
        assert set(row["assessments"]) == set(row["modules"]) == KEYS["criterion"]
        for item in row["assessments"]["items"]:
            assert set(item) == KEYS["assessment_item"]
        for item in row["modules"]["items"]:
            assert set(item) == KEYS["module_item"]
    for text in (*world.hidden.values(), *world.counted_ids):
        assert text not in response.text
    # Enrollment ids never appear.
    for row in run_sql(
        world.url,
        "SELECT id FROM cohort_enrollments WHERE cohort_id = :id",
        {"id": world.a["id"]},
    ):
        assert str(row.id) not in response.text


def test_the_criteria_follow_the_snapshot_not_the_live_template(client, world):
    body = read(client, world, OWNER).json()
    assert body["cohort_id"] == world.a["id"]
    assert body["cohort_name"] == world.a["name"]
    assert body["cohort_status"] == "in_progress"
    assert body["template"] == {
        "name": world.template["name"],
        "version": world.template["version"],
    }
    criteria = body["criteria"]
    assert criteria["min_attendance_percent"] == 80
    assert criteria["required_modules"] == [
        {"id": world.introduccion, "title": "Introducción"},
        {"id": world.desarrollo, "title": "Desarrollo"},
    ]
    assert criteria["assessments"] == [
        {
            "id": world.parcial,
            "title": "Parcial",
            "module_id": world.introduccion,
            "is_required": True,
            "min_score_percent": 70,
        },
        {
            "id": world.opcional,
            "title": "Opcional",
            "module_id": None,
            "is_required": False,
            "min_score_percent": 60,
        },
    ]
    live = run_sql(
        world.url,
        "SELECT min_attendance_percent FROM course_templates WHERE id = :id",
        {"id": world.template["id"]},
    )
    assert live[0].min_attendance_percent == 50


# --- E3/E4/E5: the computation --------------------------------------------------------


def _attendance(row: dict[str, Any]) -> tuple[Any, ...]:
    a = row["attendance"]
    return (
        a["status"],
        a["held"],
        a["attended"],
        a["absent"],
        a["remaining"],
        a["awaiting_closeout"],
        a["attendance_percent"],
    )


def _parcial(row: dict[str, Any]) -> tuple[Any, ...]:
    [item] = [i for i in row["assessments"]["items"] if i["title"] == "Parcial"]
    return (item["status"], item["score_percent"], item["attempts"])


def test_in_progress_before_the_end(client, world):
    rows = rows_by_student(read(client, world, OWNER))
    s = world.students
    expected = {
        # status, held, attended, absent, remaining, awaiting, percent
        "S": ("pending", 7, 7, 0, 1, 0, "100.00"),
        "M": ("pending", 7, 6, 1, 1, 0, "85.71"),
        "K": ("pending", 7, 5, 2, 1, 0, "71.43"),
        "AC": ("pending", 7, 5, 2, 1, 0, "71.43"),
        "L": ("pending", 3, 3, 0, 1, 0, "100.00"),  # late enrollment, late = in
        "R": ("pending", 4, 4, 0, 1, 0, "100.00"),  # the earlier period counts
    }
    for name, attendance in expected.items():
        row = rows[s[name]]
        assert (row["status"], row["final"]) == ("in_progress", False), name
        assert _attendance(row) == attendance, name
    assert _parcial(rows[s["S"]]) == ("passed", "85.00", 1)
    assert _parcial(rows[s["M"]]) == ("failed", "65.00", 2)  # the latest counts
    assert _parcial(rows[s["K"]]) == ("not_evaluated", None, 0)  # draft, void
    assert _parcial(rows[s["AD"]]) == ("passed", "70.00", 2)  # finalized later
    assert _parcial(rows[s["AC"]]) == ("failed", "69.99", 1)
    assert _parcial(rows[s["L"]]) == ("not_evaluated", None, 0)  # cohort B's
    # A failed required assessment before the end is pending (a retake).
    assert rows[s["M"]]["assessments"]["status"] == "pending"
    assert rows[s["S"]]["assessments"]["status"] == "met"
    assert rows[s["S"]]["modules"] == {
        "status": "pending",
        "items": [
            {
                "module_id": world.introduccion,
                "title": "Introducción",
                "status": "met",
                "basis": "assessments",
            },
            {
                "module_id": world.desarrollo,
                "title": "Desarrollo",
                "status": "pending",
                "basis": "course_covered",
            },
        ],
    }
    [opcional] = [
        i for i in rows[s["M"]]["assessments"]["items"] if i["title"] == "Opcional"
    ]
    assert opcional == {
        "assessment_id": world.opcional,
        "title": "Opcional",
        "is_required": False,
        "min_score_percent": 60,
        "status": "failed",
        "score_percent": "50.00",
        "evaluated_on": world.rows[0].starts_at.date().isoformat(),
        "attempts": 1,
    }
    enrolled = {
        r.student_id: r.enrolled_at
        for r in run_sql(
            world.url,
            "SELECT student_id, enrolled_at FROM cohort_enrollments"
            " WHERE cohort_id = :id AND withdrawn_at IS NULL",
            {"id": world.a["id"]},
        )
    }
    for student_id, row in rows.items():
        assert datetime.fromisoformat(row["enrolled_at"]) == enrolled[student_id]


def test_an_ended_cohort_waits_for_the_last_closeout(client, world):
    body = read(client, world, OWNER, at=world.end).json()
    assert body["cohort_status"] == "ended"
    for row in body["students"]:
        assert (row["status"], row["final"]) == ("in_progress", False)
        assert row["attendance"]["awaiting_closeout"] == 1
        assert row["attendance"]["remaining"] == 0


def test_the_closeout_makes_the_course_final(client, world):
    with closed_out(world):
        rows = rows_by_student(read(client, world, OWNER, at=world.end))
    s = world.students
    expected = {
        "S": ("completed", "met", "met", "met"),
        "M": ("not_completed", "met", "not_met", "not_met"),
        "K": ("not_completed", "not_met", "not_met", "not_met"),
        "AD": ("completed", "met", "met", "met"),
        "AC": ("not_completed", "not_met", "not_met", "not_met"),
        "L": ("not_completed", "met", "not_met", "not_met"),
        "R": ("completed", "met", "met", "met"),
    }
    for name, (status, attendance, assessments, modules) in expected.items():
        row = rows[s[name]]
        assert row["final"] is True, name
        assert (
            row["status"],
            row["attendance"]["status"],
            row["assessments"]["status"],
            row["modules"]["status"],
        ) == (status, attendance, assessments, modules), name
        # The module without assessments is covered by the course.
        assert row["modules"]["items"][1]["status"] == "met"
    assert _attendance(rows[s["M"]]) == ("met", 8, 7, 1, 0, 0, "87.50")
    assert _attendance(rows[s["K"]]) == ("not_met", 8, 6, 2, 0, 0, "75.00")


# --- C4: live -------------------------------------------------------------------------


def _status(client: TestClient, world: World, name: str) -> dict[str, Any]:
    rows = rows_by_student(read(client, world, OWNER, at=world.end))
    return rows[world.students[name]]


def test_an_attendance_correction_shows_on_the_next_read(client, world):
    params = {"id": str(world.rows[4].id), "st": world.students["K"]}
    with closed_out(world):
        assert _status(client, world, "K")["attendance"]["status"] == "not_met"
        with changed(
            world,
            "UPDATE class_session_attendance SET status = 'present'"
            " WHERE class_session_id = :id AND student_id = :st",
            "UPDATE class_session_attendance SET status = 'absent'"
            " WHERE class_session_id = :id AND student_id = :st",
            params,
        ):
            row = _status(client, world, "K")
    assert _attendance(row) == ("met", 8, 7, 1, 0, 0, "87.50")


def test_an_amendment_below_the_minimum_shows_on_the_next_read(client, world):
    params = {"st": world.students["S"], "cohort": world.a["id"]}
    with closed_out(world):
        assert _status(client, world, "S")["status"] == "completed"
        with changed(
            world,
            "UPDATE evaluations SET score_percent = 60, score_total = 6"
            " WHERE student_id = :st AND cohort_id = :cohort",
            "UPDATE evaluations SET score_percent = 85, score_total = 8.5"
            " WHERE student_id = :st AND cohort_id = :cohort",
            params,
        ):
            row = _status(client, world, "S")
    assert row["status"] == "not_completed"
    assert _parcial(row) == ("failed", "60.00", 1)


def test_a_void_shows_on_the_next_read(client, world):
    params = {
        "st": world.students["M"],
        "a": world.parcial,
        "u": world.tutor.id,
        "on": world.rows[0].starts_at.date() + timedelta(1),
    }
    with closed_out(world):
        with changed(
            world,
            "UPDATE evaluations SET voided_at = now(), voided_by_user_id = :u,"
            " void_reason = 'Error' WHERE student_id = :st"
            " AND course_assessment_id = :a AND evaluated_on = :on",
            "UPDATE evaluations SET voided_at = NULL, voided_by_user_id = NULL,"
            " void_reason = NULL WHERE student_id = :st"
            " AND course_assessment_id = :a AND evaluated_on = :on",
            params,
        ):
            row = _status(client, world, "M")
    # The earlier 90.00 counts again.
    assert _parcial(row) == ("passed", "90.00", 1)
    assert row["status"] == "completed"


def test_a_late_evaluation_after_the_end_shows_on_the_next_read(client, world):
    with closed_out(world):
        late = evaluation(
            world, world.students["L"], "70.00", evaluated_on=world.after.date()
        )
        try:
            row = _status(client, world, "L")
        finally:
            run_sql(world.url, "DELETE FROM evaluations WHERE id = :id", {"id": late})
    assert _parcial(row) == ("passed", "70.00", 1)
    assert row["status"] == "completed"


def test_a_read_writes_nothing(client, world):
    def counts() -> tuple[int, int]:
        [row] = run_sql(
            world.url,
            "SELECT (SELECT count(*) FROM audit_events) AS a,"
            " (SELECT count(*) FROM evaluations) AS b",
        )
        return int(row.a), int(row.b)

    path = f"{COHORTS}/{world.a['id']}/completion"
    with frozen_clock(world.mid):
        # Signing in writes its own audit rows: sign in first.
        cookies = [
            sign_in(client, world.users[name].username)
            for name in (OWNER, "T", "S", "GM")
        ]
        before = counts()
        for cookie in cookies:
            assert code_of(get(client, cookie, path)) == OK
    assert counts() == before


# --- C3: the statement budget ---------------------------------------------------------

BUDGET = 10
AUDIENCES = [
    # label, user, roles, the outcome on C and D
    ("owner", OWNER, (OWNER,), "all"),
    ("coordinator", ACADEMIC_COORDINATOR, (ACADEMIC_COORDINATOR,), "all"),
    ("coordinator + guardian", "CG", (ACADEMIC_COORDINATOR, GUARDIAN), "all"),
    ("teaching tutor", "T", (TUTOR,), "all"),
    ("teaching tutor + guardian", "T", (TUTOR, GUARDIAN), "all"),
    ("own student", "X", (STUDENT,), "own"),
    ("minor's guardian", "GX", (GUARDIAN,), "own"),
    ("adult's guardian (consent)", "GY", (GUARDIAN,), "own"),
    ("guardian + adult without consent", "GXN", (GUARDIAN,), "own"),
    ("other tutor + guardian", "TG", (TUTOR, GUARDIAN), "own"),
    ("finance + guardian", "FGX", (FINANCE_STAFF, GUARDIAN), "own"),
    ("finance", FINANCE_STAFF, (FINANCE_STAFF,), "denied"),
    ("other tutor", "T2", (TUTOR,), "hidden"),
    ("unlinked guardian", "GU", (GUARDIAN,), "hidden"),
]


def _measure(world: World, user_id: uuid.UUID, roles: tuple[str, ...], cohort: str):
    principal = principal_of(user_id, *roles)

    async def _call(session: Any) -> Any:
        try:
            return await course_completion.get_cohort_completion(
                session, principal, uuid.UUID(cohort)
            )
        except (PermissionDeniedError, CohortNotFoundError) as error:
            return error

    return count_statements(world.url, _call)


def test_every_caller_stays_within_the_budget_whatever_the_size(world):
    users = world.users
    measured: dict[str, list[int]] = {}
    with frozen_clock(world.mid):
        for name, user, roles, outcome in AUDIENCES:
            user_id = users[user].id
            counts = []
            for cohort, size in ((world.c, 2), (world.d, 30)):
                count, result = _measure(world, user_id, roles, cohort["id"])
                counts.append(count)
                if outcome == "all":
                    assert len(result.students) == size, name
                    assert all(r.assessments.items for r in result.students)
                elif outcome == "own":
                    assert [r.student.id for r in result.students] == [
                        world.students["Y" if user == "GY" else "X"]
                    ], name
                elif outcome == "denied":
                    assert isinstance(result, PermissionDeniedError), name
                else:
                    assert isinstance(result, CohortNotFoundError), name
            measured[name] = counts
        unknown, result = _measure(world, users[OWNER].id, (OWNER,), str(uuid.uuid4()))
        assert isinstance(result, CohortNotFoundError)
    for name, (small, large) in measured.items():
        assert small == large, (name, small, large)  # flat
        assert large <= BUDGET, (name, large)
    assert unknown <= BUDGET
    # D's data: every student has held sessions and two counted evaluations.
    with frozen_clock(world.mid):
        _, result = _measure(world, users[OWNER].id, (OWNER,), world.d["id"])
    for row in result.students:
        assert row.attendance.held == 4
        [parcial] = [i for i in row.assessments.items if i.title == "Parcial"]
        assert parcial.attempts == 2
