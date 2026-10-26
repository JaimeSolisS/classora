"""The progress dashboard (#84 A, B, C, G1, AM1) with real logins through the
real app: ``GET /api/v1/students/{id}/progress`` and ``GET
/api/v1/students/{id}/session-log``.

Every matrix row (the ``404``s compared with an unknown id), consent revoked
on the next request, the original tutor after a real #55 substitution, a
former tutor through a closeout, a tutor who lost the role; the B1 / C1
query errors; the E6 metrics (attendance by mark, the excluded states, the
subject filter, month buckets across a year boundary with an empty month,
the center zone at 23:30 local, voided and amended evaluations,
feedback-only and deleted homework, missing vs pending, plans and overdue
milestones, nulls); AM1's ``subjects``; the B4 consistency with #83's
facts; the C3 summary states; group summaries; attendance corrected after
approval; cursor paging; the B5 and C4 statement budgets. The family leak
walk over every ``GET`` route is ``test_session_notes_leak.py``.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    STUDENT,
    TUTOR,
)
from app.workflows import progress_dashboard as workflow
from tests.authz.assertions import assert_same_not_found
from tests.conftest import FrozenClock, frozen_clock
from tests.factories.scheduling import add_participant, persist_class_session
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import count_statements, get, post
from tests.integration.conftest import run_sql
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, in_uow
from tests.integration.schedule_support import principal_of, stored
from tests.integration.session_request_support import (
    Family,
    guardian_with_child,
    revoke_academic_consent,
    student_account,
)
from tests.integration.test_assignment_offers import (
    T0,
    Setup,
    answer,
    new_tutor_user,
    offered,
)
from tests.integration.test_attendance import Lesson, correct, lesson
from tests.integration.test_class_session_create import MONDAY as OFFER_MONDAY
from tests.integration.test_class_session_create import (
    at,
    new_room,
    new_student,
    new_subject,
)
from tests.integration.test_closeout_review import (
    approve,
    approved,
    resubmit,
    send_back,
    version_of,
)
from tests.integration.test_progress_reports import _evaluation, _template
from tests.integration.test_session_closeout import (
    SUMMARY,
    marked,
    submit,
    submitted,
)
from tests.integration.test_session_substitutions import substituted

NOW = datetime(2031, 3, 5, 18, 0, tzinfo=UTC)
TODAY = date(2031, 3, 5)  # NOW in the center's zone (America/Mexico_City, UTC-6)
STARTED = NOW - timedelta(minutes=30)

OK = (200, None)
NOT_FOUND = (404, "not_found")
CONSENT = (403, "student_consent_required")
INVALID = (422, "validation_error")

PROGRESS_KEYS = {
    "student_id",
    "from",
    "to",
    "subject",
    "subjects",
    "attendance",
    "attendance_by_month",
    "grades",
    "homework",
    "plans",
}
ATTENDANCE_KEYS = {
    "sessions",
    "present",
    "late",
    "absent",
    "not_recorded",
    "attendance_percent",
}
MONTH_KEYS = {"month", "sessions", "present", "late", "absent", "not_recorded"}
GRADE_VALUE_KEYS = {
    "evaluation_count",
    "evaluation_average_percent",
    "assignment_count",
    "assignment_average_percent",
}
HOMEWORK_KEYS = {
    "pending",
    "missing",
    "submitted",
    "graded",
    "late",
    "completion_percent",
}
PLANS_KEYS = {
    "active",
    "completed",
    "objectives_complete",
    "objectives_total",
    "milestones_overdue",
    "items",
}
PLAN_ITEM_KEYS = {
    "id",
    "title",
    "subject",
    "status",
    "progress_percent",
    "objectives_complete",
    "objectives_total",
    "milestones_done",
    "milestones_total",
    "milestones_overdue",
    "target_on",
}
LOG_ITEM_KEYS = {
    "class_session_id",
    "starts_at",
    "subject",
    "session_kind",
    "tutor",
    "attendance",
    "summary",
    "published_note_count",
}
SUMMARY_KEYS = {"topics", "performance", "homework", "next_steps"}


@pytest.fixture(scope="module")
def frozen() -> Any:
    with frozen_clock(NOW) as clock:
        yield clock


@pytest.fixture(autouse=True)
def at_now(frozen: FrozenClock) -> Any:
    frozen.set(NOW)
    yield frozen
    frozen.set(NOW)


@pytest.fixture(scope="module")
def client(module_seeded_url: str, frozen: FrozenClock) -> Any:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def owner(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(OWNER,), display_name="Dueña Centro")


@pytest.fixture(scope="module")
def coordinator(module_seeded_url: str) -> CreatedUser:
    return create_user(
        module_seeded_url, roles=(ACADEMIC_COORDINATOR,), display_name="Coordinación"
    )


# --- Helpers --------------------------------------------------------------------------


def progress_path(student_id: Any) -> str:
    return f"/api/v1/students/{student_id}/progress"


def log_path(student_id: Any) -> str:
    return f"/api/v1/students/{student_id}/session-log"


def ok(response: Response) -> dict[str, Any]:
    assert code_of(response) == OK, response.text
    body: dict[str, Any] = response.json()
    return body


def field_codes(response: Response) -> list[tuple[list[Any], str]]:
    assert code_of(response) == INVALID, response.text
    return [(item["path"], item["code"]) for item in response.json()["errors"]]


def a_tutor(url: str, name: str = "Tutora Progreso", *roles: str) -> CreatedUser:
    return create_user(url, roles=(TUTOR, *roles), display_name=name)


def _utc(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


def named_subject(url: str, name: str) -> uuid.UUID:
    subject = new_subject(url)
    run_sql(
        url,
        "UPDATE subjects SET name = :n WHERE id = :id",
        {"n": f"{name} {uuid.uuid4().hex[:6]}", "id": subject},
    )
    return subject


def subject_name(url: str, subject: uuid.UUID) -> str:
    [row] = run_sql(url, "SELECT name FROM subjects WHERE id = :id", {"id": subject})
    return str(row.name)


def session_at(
    url: str,
    tutor: CreatedUser,
    students: tuple[uuid.UUID, ...],
    subject: uuid.UUID,
    starts_at: datetime,
    *,
    state: str = "confirmed",
    marks: tuple[str | None, ...] = (),
    kind: str | None = None,
) -> str:
    extra: dict[str, Any] = {} if kind is None else {"kind": kind}
    session_id = stored(
        url,
        starts_at,
        state=state,
        students=students,
        tutor_user_id=tutor.id,
        subject_id=subject,
        **extra,
    )
    for student, status in zip(students, marks, strict=False):
        if status is not None:
            run_sql(
                url,
                "INSERT INTO class_session_attendance (class_session_id, student_id,"
                " status, marked_by_user_id, marked_at) VALUES (:c, :s, :m, :u, :at)",
                {"c": session_id, "s": student, "m": status, "u": tutor.id, "at": NOW},
            )
    return session_id


def homework(
    url: str,
    student: uuid.UUID,
    subject: uuid.UUID,
    creator: uuid.UUID,
    due_on: date,
    *,
    due_time: time | None = None,
    submitted_at: datetime | None = None,
    graded_on: date | None = None,
    percent: str | None = None,
    deleted: bool = False,
) -> None:
    """One assignment of ``creator`` with one recipient; graded on
    ``graded_on`` (with ``percent``: scored; without: feedback only)."""
    [made] = run_sql(
        url,
        "INSERT INTO assignments (creator_user_id, subject_id, title, instructions,"
        " due_on, due_time, max_score, deleted_at, deleted_by_user_id)"
        " VALUES (:u, :sub, 'Tarea secreta', 'Instrucciones secretas', :due, :time,"
        " 10, :del, :del_by) RETURNING id",
        {
            "u": creator,
            "sub": subject,
            "due": due_on,
            "time": due_time,
            "del": NOW if deleted else None,
            "del_by": creator if deleted else None,
        },
    )
    graded = graded_on is not None
    run_sql(
        url,
        "INSERT INTO assignment_submissions (assignment_id, student_id, text,"
        " submitted_at, submitted_by_user_id, submitted_via, score, score_percent,"
        " feedback, graded_at, graded_on, graded_by_user_id)"
        " VALUES (:a, :st, :text, :sub_at, :sub_by, :via, :score, :pct, :fb, :g_at,"
        " :g_on, :g_by)",
        {
            "a": made.id,
            "st": student,
            "text": "Mi tarea secreta" if submitted_at else None,
            "sub_at": submitted_at,
            "sub_by": creator if submitted_at else None,
            "via": "tutor" if submitted_at else None,
            "score": Decimal(percent) / 10 if percent else None,
            "pct": Decimal(percent) if percent else None,
            "fb": "Retroalimentación secreta" if graded else None,
            "g_at": _utc(graded_on, 18) if graded_on else None,
            "g_on": graded_on,
            "g_by": creator if graded else None,
        },
    )


def plan(
    url: str,
    student: uuid.UUID,
    subject: uuid.UUID,
    creator: uuid.UUID,
    *,
    title: str,
    status: str,
    target_on: date | None = None,
    closed_on: date | None = None,
    milestones: tuple[tuple[str, date | None], ...] = (("pending", None),),
    deleted: bool = False,
) -> str:
    """A plan starting 2030-01-01 with one objective holding ``milestones``
    (status, due date) and secret text everywhere."""
    activated = status != "draft"
    [made] = run_sql(
        url,
        "INSERT INTO learning_plans (student_id, subject_id, created_by_user_id,"
        " title, description, starts_on, target_on, status, activated_at, closed_at,"
        " deleted_at, deleted_by_user_id)"
        " VALUES (:st, :sub, :u, :title, 'Descripción secreta', '2030-01-01',"
        " :target, :status, :act, :closed, :del, :del_by) RETURNING id",
        {
            "st": student,
            "sub": subject,
            "u": creator,
            "title": title,
            "target": target_on,
            "status": status,
            "act": _utc(date(2030, 1, 2), 18) if activated else None,
            "closed": _utc(closed_on, 18) if closed_on else None,
            "del": NOW if deleted else None,
            "del_by": creator if deleted else None,
        },
    )
    [objective] = run_sql(
        url,
        "INSERT INTO learning_plan_objectives (plan_id, position, title)"
        " VALUES (:p, 1, 'Objetivo secreto') RETURNING id",
        {"p": made.id},
    )
    for position, (state, due_on) in enumerate(milestones, start=1):
        run_sql(
            url,
            "INSERT INTO learning_plan_milestones (plan_id, objective_id, position,"
            " title, status, due_on, completed_at, completed_by_user_id)"
            " VALUES (:p, :o, :pos, 'Hito secreto', :s, :due, :at, :by)",
            {
                "p": made.id,
                "o": objective.id,
                "pos": position,
                "s": state,
                "due": due_on,
                "at": NOW if state == "done" else None,
                "by": creator if state == "done" else None,
            },
        )
    return str(made.id)


# --- A: access ------------------------------------------------------------------------


@dataclass
class Matrix:
    student: uuid.UUID
    """A minor with a guardian, taught by the current and the former tutor."""
    guardian: Family
    current: CreatedUser
    former: CreatedUser
    former_session: str
    other_session: str


@pytest.fixture(scope="module")
def matrix(module_seeded_url: str) -> Matrix:
    url = module_seeded_url
    family = guardian_with_child(url)
    subject = new_subject(url)
    current = a_tutor(url, "Tutora Actual")
    former = a_tutor(url, "Tutor Anterior")
    session_at(url, current, (family.student_id,), subject, STARTED, marks=("present",))
    former_session = session_at(
        url,
        former,
        (family.student_id,),
        subject,
        NOW - timedelta(days=60),
        state="completed",
        marks=("late",),
    )
    other_session = session_at(
        url, current, (family.student_id,), subject, NOW - timedelta(days=50)
    )
    return Matrix(
        student=family.student_id,
        guardian=family,
        current=current,
        former=former,
        former_session=former_session,
        other_session=other_session,
    )


def both(client: TestClient, cookie: Any, student: Any) -> tuple[Response, Response]:
    return get(client, cookie, progress_path(student)), get(
        client, cookie, log_path(student)
    )


def assert_hidden(client: TestClient, cookie: Any, student: Any) -> None:
    unknown = uuid.uuid4()
    for path in (progress_path, log_path):
        assert_same_not_found(
            get(client, cookie, path(student)), get(client, cookie, path(unknown))
        )


def log_ids(response: Response) -> list[str]:
    return [item["class_session_id"] for item in ok(response)["items"]]


def test_staff_read_both(module_seeded_url, client, matrix, owner, coordinator):
    admin = create_user(module_seeded_url, roles=(ADMINISTRATOR,))
    for user in (owner, admin, coordinator):
        cookie = sign_in(client, user.username)
        progress, log = both(client, cookie, matrix.student)
        assert ok(progress)["student_id"] == str(matrix.student)
        ids = log_ids(log)
        assert len(ids) == 3
        assert {matrix.former_session, matrix.other_session} <= set(ids)
        assert_hidden(client, cookie, uuid.uuid4())


def test_finance_and_other_tutors_get_the_404(module_seeded_url, client, matrix):
    url = module_seeded_url
    finance = create_user(url, roles=(FINANCE_STAFF,))
    other = a_tutor(url, "Otra Tutora")
    session_at(url, other, (new_student(url),), new_subject(url), STARTED)
    for user in (finance, other):
        assert_hidden(client, sign_in(client, user.username), matrix.student)


def test_the_current_tutor_reads_every_tutors_sessions(client, matrix):
    cookie = sign_in(client, matrix.current.username)
    progress, log = both(client, cookie, matrix.student)
    body = ok(progress)
    assert body["attendance"]["sessions"] == 3
    assert matrix.former_session in log_ids(log)


def test_a_former_tutor_reads_only_their_sessions(client, matrix):
    cookie = sign_in(client, matrix.former.username)
    progress, log = both(client, cookie, matrix.student)
    assert_same_not_found(progress, get(client, cookie, progress_path(uuid.uuid4())))
    [item] = ok(log)["items"]
    assert item["class_session_id"] == matrix.former_session
    assert item["attendance"] == "late"
    assert item["tutor"] == {"display_name": "Tutor Anterior"}
    # An unknown student is still the 404.
    assert_same_not_found(
        get(client, cookie, log_path(uuid.uuid4())),
        get(client, cookie, log_path(uuid.uuid4())),
    )


def test_the_family(module_seeded_url, client, matrix):
    url = module_seeded_url
    # The minor's guardian.
    cookie = sign_in(client, matrix.guardian.user.username)
    progress, log = both(client, cookie, matrix.student)
    assert ok(progress)["attendance"]["sessions"] == 3
    assert len(log_ids(log)) == 3
    # An unlinked guardian (of another child).
    stranger = guardian_with_child(url)
    assert_hidden(client, sign_in(client, stranger.user.username), matrix.student)
    # The adult student themself, and not another student.
    login, own = student_account(url)
    cookie = sign_in(client, login.username)
    progress, log = both(client, cookie, own)
    assert ok(progress)["attendance"]["sessions"] == 0
    assert ok(log) == {"items": [], "next_cursor": None}
    assert_hidden(client, cookie, matrix.student)


def test_an_adult_needs_the_consent_and_a_revocation_applies_at_once(
    module_seeded_url, client
):
    url = module_seeded_url
    without = guardian_with_child(url, adult=True)
    cookie = sign_in(client, without.user.username)
    for response in both(client, cookie, without.student_id):
        assert code_of(response) == CONSENT
    allowed = guardian_with_child(url, adult=True, consent=True)
    cookie = sign_in(client, allowed.user.username)
    for response in both(client, cookie, allowed.student_id):
        assert code_of(response) == OK
    revoke_academic_consent(url, allowed)
    for response in both(client, cookie, allowed.student_id):
        assert code_of(response) == CONSENT


def test_a_former_tutor_who_is_a_guardian_without_consent(module_seeded_url, client):
    """Role union: a tutor who taught an adult and is that adult's linked
    guardian without the consent reads their own sessions on the log; the
    progress summary is the consent 403."""
    url = module_seeded_url
    family = guardian_with_child(url, adult=True)
    run_sql(
        url,
        "INSERT INTO user_roles (user_id, role_id)"
        " SELECT :id, id FROM roles WHERE code = 'tutor'",
        {"id": family.user.id},
    )
    taught = stored(
        url,
        NOW - timedelta(days=45),
        state="completed",
        students=(family.student_id,),
        tutor_user_id=family.user.id,
    )
    cookie = sign_in(client, family.user.username)
    progress, log = both(client, cookie, family.student_id)
    assert code_of(progress) == CONSENT
    assert log_ids(log) == [taught]


def test_a_tutor_who_lost_the_role(module_seeded_url, client):
    url = module_seeded_url
    tutor = a_tutor(url, "Tutora Sin Rol", GUARDIAN)
    student = new_student(url)
    session_at(url, tutor, (student,), new_subject(url), STARTED)
    cookie = sign_in(client, tutor.username)
    for response in both(client, cookie, student):
        assert code_of(response) == OK
    run_sql(
        url,
        "DELETE FROM user_roles WHERE user_id = :id"
        " AND role_id = (SELECT id FROM roles WHERE code = 'tutor')",
        {"id": tutor.id},
    )
    assert_hidden(client, sign_in(client, tutor.username), student)


def test_a_former_tutor_through_the_closeout(module_seeded_url, client, coordinator):
    """'Taught' is the session's current tutor or its closeout's tutor: once
    the session's tutor changes, the closeout's tutor still reads it."""
    url = module_seeded_url
    made = lesson(url, starts_at=NOW - timedelta(days=40))
    approved(client, made, coordinator)
    replacement = a_tutor(url, "Tutora Nueva")
    run_sql(
        url,
        "UPDATE class_sessions SET tutor_user_id = :t WHERE id = :id",
        {"t": replacement.id, "id": made.id},
    )
    cookie = sign_in(client, made.tutor.username)
    progress, log = both(client, cookie, made.students[0])
    assert code_of(progress) == NOT_FOUND
    [item] = ok(log)["items"]
    assert item["class_session_id"] == made.id
    assert item["tutor"] == {"display_name": "Tutora Nueva"}
    assert item["summary"] == SUMMARY


def test_the_original_tutor_after_a_real_substitution(
    module_seeded_url, client, owner, at_now
):
    """#55: once the substitute accepted, the original tutor who never taught
    a started session of the student gets 404 on both routes; one who
    taught an earlier session reads only that one on the log."""
    url = module_seeded_url
    at_now.set(T0)
    subject = new_subject(url)
    boss = sign_in(client, owner.username)
    outcomes = []
    for taught_before in (False, True):
        setup = Setup(
            student=new_student(url),
            subject=subject,
            tutor=new_tutor_user(url, qualified_for=[subject]),
            room=new_room(url),
        )
        earlier = None
        if taught_before:
            earlier = stored(
                url,
                T0 - timedelta(days=40),
                state="completed",
                students=(setup.student,),
                tutor_user_id=setup.tutor.id,
                subject_id=subject,
            )
            stored(
                url,
                T0 - timedelta(days=35),
                state="completed",
                students=(setup.student,),
                tutor_user_id=a_tutor(url).id,
                subject_id=subject,
            )
        substitute = new_tutor_user(url, qualified_for=[subject])
        created, offer_id = offered(
            client, boss, setup, starts_at=at(OFFER_MONDAY, "16:00")
        )
        original = sign_in(client, setup.tutor.username)
        assert code_of(answer(client, original, offer_id, "accept")) == OK
        detail = get(client, boss, f"/api/v1/class-sessions/{created['id']}").json()
        item = substituted(client, boss, detail, substitute.id)
        taker = sign_in(client, substitute.username)
        assert code_of(answer(client, taker, item["offer_id"], "accept")) == OK
        original = sign_in(client, setup.tutor.username)
        outcomes.append((original, setup.student, earlier))

    (never, never_student, _), (taught, taught_student, earlier) = outcomes
    assert_hidden(client, never, never_student)
    progress, log = both(client, taught, taught_student)
    assert code_of(progress) == NOT_FOUND
    assert log_ids(log) == [earlier]


# --- B1, C1: query errors -------------------------------------------------------------


def test_the_query_errors(module_seeded_url, client, owner, matrix):
    url = module_seeded_url
    cookie = sign_in(client, owner.username)
    path = progress_path(matrix.student)
    for params, expected in (
        (
            {"from": "2031-03-06", "to": "2031-03-05"},
            (["query", "from"], "range_invalid"),
        ),
        (
            {"from": "1999-12-31", "to": "2000-01-31"},
            (["query", "from"], "range_invalid"),
        ),
        ({"to": "2031-03-06"}, (["query", "to"], "range_in_future")),
        ({"from": "2029-03-31"}, (["query", "from"], "range_too_long")),
        (
            {"subject_id": str(uuid.uuid4())},
            (["query", "subject_id"], "subject_unknown"),
        ),
    ):
        assert field_codes(get(client, cookie, path, **params)) == [expected], params
    # The bounds: 24 calendar months, today, 2000-01-01.
    ok(get(client, cookie, path, **{"from": "2029-04-01", "to": "2031-03-05"}))
    ok(get(client, cookie, path, **{"from": "2000-01-01", "to": "2000-01-31"}))
    body = ok(get(client, cookie, path, **{"to": "2031-03-05"}))
    assert (body["from"], body["to"]) == ("2030-04-01", "2031-03-05")
    # A schema error first.
    assert code_of(get(client, cookie, path, **{"from": "x"})) == INVALID
    # An inactive subject is allowed (history).
    inactive = new_subject(url, is_active=False)
    body = ok(get(client, cookie, path, subject_id=str(inactive)))
    assert body["subject"]["id"] == str(inactive)
    # The log: from after to; the limit; a foreign cursor.
    log = log_path(matrix.student)
    assert field_codes(
        get(client, cookie, log, **{"from": "2031-03-06", "to": "2031-03-05"})
    ) == [(["query", "from"], "range_invalid")]
    for limit in ("0", "51"):
        assert code_of(get(client, cookie, log, limit=limit)) == INVALID
    assert code_of(get(client, cookie, log, cursor="nope")) == (400, "invalid_cursor")
    # 422 before the record policy: a hidden student gets the 422 too.
    finance = sign_in(client, create_user(url, roles=(FINANCE_STAFF,)).username)
    assert code_of(get(client, finance, path, to="2031-03-06")) == INVALID


# --- B: the metrics -------------------------------------------------------------------


@dataclass
class Metrics:
    student: uuid.UUID
    tutor: CreatedUser
    algebra: uuid.UUID
    biology: uuid.UUID
    history: uuid.UUID
    zoology: uuid.UUID
    names: dict[uuid.UUID, str]
    plans: dict[str, str]


RANGE = {"from": "2030-12-01", "to": "2031-03-05"}


@pytest.fixture(scope="module")
def metrics(module_seeded_url: str) -> Metrics:
    """E6 data for one student (see each block)."""
    url = module_seeded_url
    tutor = a_tutor(url, "Tutora Métricas")
    student = new_student(url)
    # Lower and upper case: ordered by casefold (algebra, Biologia, historia).
    algebra = named_subject(url, "algebra")
    biology = named_subject(url, "Biologia")
    history = named_subject(url, "historia")
    zoology = named_subject(url, "Zoologia")
    me = (student,)

    # Attendance (local dates, UTC-6).
    session_at(
        url, tutor, me, algebra, _utc(date(2030, 12, 10), 15), marks=("present",)
    )
    # 2030-12-31 23:30 local: December, not January.
    session_at(
        url,
        tutor,
        me,
        algebra,
        _utc(date(2031, 1, 1), 5, 30),
        state="completed",
        marks=("late",),
    )
    # 2031-02-28 23:30 local: February.
    session_at(
        url,
        tutor,
        me,
        algebra,
        _utc(date(2031, 3, 1), 5, 30),
        state="completed",
        marks=("absent",),
    )
    session_at(url, tutor, me, biology, _utc(date(2031, 3, 2), 16))  # not recorded
    # Never counted: cancelled, skipped, future; before the range.
    session_at(url, tutor, me, algebra, _utc(date(2031, 2, 10), 16), state="cancelled")
    session_at(
        url, tutor, me, algebra, _utc(date(2031, 2, 15), 16), state="skipped_closure"
    )
    session_at(url, tutor, me, zoology, _utc(date(2031, 2, 12), 16), state="cancelled")
    session_at(url, tutor, me, zoology, NOW + timedelta(hours=2))
    session_at(
        url, tutor, me, algebra, _utc(date(2030, 11, 30), 16), marks=("present",)
    )

    # Evaluations.
    template = _template(url, algebra)
    for subject, on, percent, extra in (
        (algebra, date(2030, 12, 15), "80.00", {}),
        (algebra, date(2031, 2, 10), "70.50", {}),
        (biology, date(2031, 3, 1), "90.00", {}),
        (algebra, date(2031, 2, 11), "10.00", {"voided": True}),
        (algebra, date(2031, 2, 12), "0", {"status": "draft"}),
        (algebra, date(2030, 11, 20), "40.00", {}),
    ):
        _evaluation(
            url,
            student,
            subject,
            tutor.id,
            template,
            on,
            percent,
            feedback="Comentario secreto",
            **extra,
        )
    amended = _evaluation(
        url, student, algebra, tutor.id, template, date(2031, 3, 2), "50.00"
    )
    run_sql(
        url,
        "UPDATE evaluations SET score_total = 6, score_percent = 60 WHERE id = :id",
        {"id": amended},
    )

    # Homework (states at NOW, 12:00 local).
    homework(  # graded (Feb), on time
        url,
        student,
        algebra,
        tutor.id,
        date(2031, 2, 20),
        submitted_at=_utc(date(2031, 2, 20), 12),
        graded_on=date(2031, 2, 21),
        percent="80.00",
    )
    homework(url, student, algebra, tutor.id, date(2031, 2, 22))  # missing
    homework(  # submitted late
        url,
        student,
        biology,
        tutor.id,
        date(2031, 3, 4),
        submitted_at=_utc(date(2031, 3, 5), 12),
    )
    homework(  # due after the range, graded in it: only the averages
        url,
        student,
        algebra,
        tutor.id,
        date(2031, 3, 6),
        submitted_at=_utc(date(2031, 3, 5), 10),
        graded_on=date(2031, 3, 5),
        percent="100.00",
    )
    homework(url, student, algebra, tutor.id, TODAY, due_time=time(23, 0))  # pending
    homework(  # graded with feedback only: a state, no average
        url,
        student,
        biology,
        tutor.id,
        date(2031, 2, 25),
        submitted_at=_utc(date(2031, 2, 25), 12),
        graded_on=date(2031, 2, 26),
    )
    homework(  # deleted: never
        url,
        student,
        algebra,
        tutor.id,
        date(2031, 2, 26),
        submitted_at=_utc(date(2031, 2, 26), 12),
        graded_on=date(2031, 2, 26),
        percent="50.00",
        deleted=True,
    )

    # Plans.
    plans = {
        "P1": plan(
            url,
            student,
            algebra,
            tutor.id,
            title="Plan álgebra",
            status="active",
            target_on=date(2031, 6, 1),
            milestones=(("done", date(2031, 2, 1)), ("pending", date(2031, 3, 1))),
        ),
        "P2": plan(
            url,
            student,
            biology,
            tutor.id,
            title="Plan biología",
            status="active",
            milestones=(("done", None),),
        ),
        "P3": plan(
            url,
            student,
            biology,
            tutor.id,
            title="Plan biología cerrado",
            status="completed",
            closed_on=date(2031, 2, 1),
            milestones=(("done", None),),
        ),
        "P3b": plan(
            url,
            student,
            algebra,
            tutor.id,
            title="Plan álgebra cerrado",
            status="completed",
            closed_on=date(2031, 2, 20),
            milestones=(("done", None), ("done", None)),
        ),
        "P7": plan(
            url,
            student,
            history,
            tutor.id,
            title="Plan historia",
            status="active",
            target_on=date(2031, 4, 1),
            milestones=(("in_progress", date(2031, 4, 1)),),
        ),
    }
    plan(url, student, algebra, tutor.id, title="Plan borrador secreto", status="draft")
    plan(
        url,
        student,
        algebra,
        tutor.id,
        title="Plan interrumpido secreto",
        status="discontinued",
        closed_on=date(2031, 1, 20),
    )
    plan(
        url,
        student,
        zoology,
        tutor.id,
        title="Plan borrado secreto",
        status="draft",
        deleted=True,
    )
    return Metrics(
        student=student,
        tutor=tutor,
        algebra=algebra,
        biology=biology,
        history=history,
        zoology=zoology,
        names={s: subject_name(url, s) for s in (algebra, biology, history, zoology)},
        plans=plans,
    )


def read_progress(
    client: TestClient, owner: CreatedUser, student: Any, **params: Any
) -> dict[str, Any]:
    return ok(
        get(client, sign_in(client, owner.username), progress_path(student), **params)
    )


def subject_ref(metrics: Metrics, subject: uuid.UUID) -> dict[str, str]:
    return {"id": str(subject), "name": metrics.names[subject]}


def test_the_body_is_a_closed_allow_list(client, owner, metrics):
    body = read_progress(client, owner, metrics.student, **RANGE)
    assert set(body) == PROGRESS_KEYS
    assert set(body["attendance"]) == ATTENDANCE_KEYS
    assert {frozenset(m) for m in body["attendance_by_month"]} == {
        frozenset(MONTH_KEYS)
    }
    assert set(body["grades"]) == GRADE_VALUE_KEYS | {"by_subject", "by_month"}
    for group in body["grades"]["by_subject"]:
        assert set(group) == GRADE_VALUE_KEYS | {"subject"}
        assert set(group["subject"]) == {"id", "name"}
    for group in body["grades"]["by_month"]:
        assert set(group) == GRADE_VALUE_KEYS | {"subject", "month"}
    assert set(body["homework"]) == HOMEWORK_KEYS
    assert set(body["plans"]) == PLANS_KEYS
    for item in body["plans"]["items"]:
        assert set(item) == PLAN_ITEM_KEYS
    for subject in body["subjects"]:
        assert set(subject) == {"id", "name"}
    # B6: no record text, only plan titles, subject names and numbers.
    for secret in ("secret", "Instrucciones", "Mi tarea", "Tutora", "Comentario"):
        assert secret not in str(body), secret


def test_the_attendance(client, owner, metrics):
    body = read_progress(client, owner, metrics.student, **RANGE)
    assert (body["from"], body["to"], body["subject"]) == (
        "2030-12-01",
        "2031-03-05",
        None,
    )
    assert body["attendance"] == {
        "sessions": 4,
        "present": 1,
        "late": 1,
        "absent": 1,
        "not_recorded": 1,
        "attendance_percent": "66.67",
    }

    def month(name: str, *counts: int) -> dict[str, Any]:
        keys = ("sessions", "present", "late", "absent", "not_recorded")
        return {"month": name, **dict(zip(keys, counts, strict=True))}

    assert body["attendance_by_month"] == [
        month("2030-12", 2, 1, 1, 0, 0),  # the 23:30 local session too
        month("2031-01", 0, 0, 0, 0, 0),
        month("2031-02", 1, 0, 0, 1, 0),
        month("2031-03", 1, 0, 0, 0, 1),
    ]
    # The default range: 12 calendar months ending today.
    default = read_progress(client, owner, metrics.student)
    assert (default["from"], default["to"]) == ("2030-04-01", "2031-03-05")
    assert len(default["attendance_by_month"]) == 12
    assert default["attendance"]["sessions"] == 5  # with 2030-11-30


def test_the_grades(client, owner, metrics):
    body = read_progress(client, owner, metrics.student, **RANGE)
    grades = body["grades"]
    algebra = subject_ref(metrics, metrics.algebra)
    biology = subject_ref(metrics, metrics.biology)
    # 80, 70.50, 90 and the amended 60 (the voided, draft and earlier left
    # out): 75.125 half-up. Homework: 80 and 100 (graded in the range; the
    # feedback-only and the deleted left out).
    assert {k: grades[k] for k in GRADE_VALUE_KEYS} == {
        "evaluation_count": 4,
        "evaluation_average_percent": "75.13",
        "assignment_count": 2,
        "assignment_average_percent": "90.00",
    }
    assert grades["by_subject"] == [
        {
            "subject": algebra,
            "evaluation_count": 3,
            "evaluation_average_percent": "70.17",
            "assignment_count": 2,
            "assignment_average_percent": "90.00",
        },
        {
            "subject": biology,
            "evaluation_count": 1,
            "evaluation_average_percent": "90.00",
            "assignment_count": 0,
            "assignment_average_percent": None,
        },
    ]

    def entry(month: str, subject: dict[str, str], *values: Any) -> dict[str, Any]:
        return {
            "month": month,
            "subject": subject,
            **dict(zip(sorted(GRADE_VALUE_KEYS), values, strict=True)),
        }

    # sorted keys: assignment_average_percent, assignment_count,
    # evaluation_average_percent, evaluation_count.
    assert grades["by_month"] == [
        entry("2030-12", algebra, None, 0, "80.00", 1),
        entry("2031-02", algebra, "80.00", 1, "70.50", 1),
        entry("2031-03", algebra, "100.00", 1, "60.00", 1),
        entry("2031-03", biology, None, 0, "90.00", 1),
    ]


def test_the_homework(client, owner, metrics):
    body = read_progress(client, owner, metrics.student, **RANGE)
    assert body["homework"] == {
        "pending": 1,
        "missing": 1,
        "submitted": 1,
        "graded": 2,
        "late": 1,
        "completion_percent": "75.00",
    }


def test_the_plans(client, owner, metrics):
    body = read_progress(client, owner, metrics.student, **RANGE)
    plans = body["plans"]
    assert {k: plans[k] for k in PLANS_KEYS - {"items"}} == {
        "active": 3,
        "completed": 2,
        "objectives_complete": 3,
        "objectives_total": 5,
        "milestones_overdue": 1,
    }
    ids = metrics.plans
    assert [item["id"] for item in plans["items"]] == [
        ids["P7"],  # active, target 2031-04-01
        ids["P1"],  # active, target 2031-06-01
        ids["P2"],  # active, no target
        ids["P3b"],  # completed 2031-02-20
        ids["P3"],  # completed 2031-02-01
    ]
    assert plans["items"][1] == {
        "id": ids["P1"],
        "title": "Plan álgebra",
        "subject": subject_ref(metrics, metrics.algebra),
        "status": "active",
        "progress_percent": "50",
        "objectives_complete": 0,
        "objectives_total": 1,
        "milestones_done": 1,
        "milestones_total": 2,
        "milestones_overdue": 1,
        "target_on": "2031-06-01",
    }
    # Plans aren't range-filtered.
    old = read_progress(
        client, owner, metrics.student, **{"from": "2030-01-01", "to": "2030-01-31"}
    )
    assert old["plans"]["active"] == 3


def test_the_subjects_and_the_filter(client, owner, metrics):
    expected = [
        subject_ref(metrics, s)
        for s in (metrics.algebra, metrics.biology, metrics.history)
    ]
    body = read_progress(client, owner, metrics.student, **RANGE)
    # AM1: sessions, grades, homework and plans; never a subject with only a
    # cancelled or a future session (Zoologia).
    assert body["subjects"] == expected

    only = read_progress(
        client, owner, metrics.student, subject_id=str(metrics.biology), **RANGE
    )
    assert only["subject"] == subject_ref(metrics, metrics.biology)
    assert only["subjects"] == expected  # the filter doesn't narrow them
    assert only["attendance"] == {
        "sessions": 1,
        "present": 0,
        "late": 0,
        "absent": 0,
        "not_recorded": 1,
        "attendance_percent": None,
    }
    assert [m["sessions"] for m in only["attendance_by_month"]] == [0, 0, 0, 1]
    assert (
        only["grades"]["evaluation_count"],
        only["grades"]["assignment_count"],
        only["grades"]["assignment_average_percent"],
    ) == (1, 0, None)
    assert [g["subject"]["id"] for g in only["grades"]["by_subject"]] == [
        str(metrics.biology)
    ]
    assert only["homework"] == {
        "pending": 0,
        "missing": 0,
        "submitted": 1,
        "graded": 1,
        "late": 1,
        "completion_percent": "100.00",
    }
    assert [i["id"] for i in only["plans"]["items"]] == [
        metrics.plans["P2"],
        metrics.plans["P3"],
    ]
    # A subject with only a plan.
    history = read_progress(
        client, owner, metrics.student, subject_id=str(metrics.history), **RANGE
    )
    assert history["attendance"]["sessions"] == 0
    assert history["grades"]["by_month"] == []
    assert history["plans"]["active"] == 1
    assert history["subjects"] == expected


def test_a_student_without_records(client, owner, module_seeded_url):
    body = read_progress(client, owner, new_student(module_seeded_url))
    assert body["subjects"] == []
    assert body["attendance"]["attendance_percent"] is None
    assert all(m["sessions"] == 0 for m in body["attendance_by_month"])
    assert body["grades"] == {
        "evaluation_count": 0,
        "evaluation_average_percent": None,
        "assignment_count": 0,
        "assignment_average_percent": None,
        "by_subject": [],
        "by_month": [],
    }
    assert body["homework"]["completion_percent"] is None
    assert body["plans"]["items"] == []


@pytest.mark.parametrize("subject", [None, "algebra", "biology"])
@pytest.mark.parametrize(
    ("start", "end"),
    [(date(2031, 2, 1), date(2031, 2, 28)), (date(2030, 12, 1), date(2031, 2, 28))],
)
def test_the_numbers_equal_the_report_facts(
    module_seeded_url, client, owner, metrics, subject, start, end
):
    """B4: for the same student, range and subject, attendance, the
    evaluations, the homework states and (every graded row here is due in
    the range) the homework average equal #83's facts."""
    url = module_seeded_url
    subject_id = None if subject is None else getattr(metrics, subject)
    params: dict[str, Any] = {"from": start.isoformat(), "to": end.isoformat()}
    if subject_id is not None:
        params["subject_id"] = str(subject_id)
    body = read_progress(client, owner, metrics.student, **params)

    async def _facts(session: AsyncSession) -> tuple[Any, Any]:
        from app.modules.academics import service as academics
        from app.modules.catalog import service as catalog
        from app.modules.scheduling import service as scheduling

        attendance = await scheduling.student_attendance_summary(
            session,
            metrics.student,
            start,
            end,
            subject_id=subject_id,
            time_zone="America/Mexico_City",
            now=NOW,
        )
        facts = await academics.academic_facts(
            session,
            metrics.student,
            start,
            end,
            subject_id,
            subjects=catalog.subject_name_columns(),
            zone="America/Mexico_City",
            now=NOW,
        )
        return attendance, facts

    attendance, facts = in_uow(url, _facts)
    assert {
        k: v for k, v in body["attendance"].items() if k != "attendance_percent"
    } == {
        "sessions": attendance.sessions,
        "present": attendance.present,
        "late": attendance.late,
        "absent": attendance.absent,
        "not_recorded": attendance.not_recorded,
    }
    evaluations = facts["evaluations"]
    assert (
        body["grades"]["evaluation_count"],
        body["grades"]["evaluation_average_percent"],
    ) == (evaluations["count"], evaluations["average_percent"])
    by_subject = {
        g["subject"]["id"]: (g["evaluation_count"], g["evaluation_average_percent"])
        for g in body["grades"]["by_subject"]
        if g["evaluation_count"]
    }
    assert by_subject == {
        g["subject"]["id"]: (g["count"], g["average_percent"])
        for g in evaluations["by_subject"]
    }
    assignments = facts["assignments"]
    assert {
        k: body["homework"][k]
        for k in ("pending", "missing", "submitted", "graded", "late")
    } == {
        k: assignments[k] for k in ("pending", "missing", "submitted", "graded", "late")
    }
    assert (
        body["grades"]["assignment_average_percent"] == assignments["average_percent"]
    )


# --- C: the session log ---------------------------------------------------------------


def log_of(
    client: TestClient, cookie: Any, student: Any, **params: Any
) -> dict[str, Any]:
    return ok(get(client, cookie, log_path(student), **params))


def item_of(body: dict[str, Any], session_id: str) -> dict[str, Any]:
    [item] = [i for i in body["items"] if i["class_session_id"] == session_id]
    return item


def test_the_summary_states(module_seeded_url, client, owner, coordinator):
    """C3: only while the closeout is approved; hidden during a correction
    review; the re-approved text; an approved empty summary is null."""
    url = module_seeded_url
    staff = sign_in(client, owner.username)
    reviewer = sign_in(client, coordinator.username)
    tutor = a_tutor(url, "Tutora Resúmenes")
    student = new_student(url)

    def made(days: int) -> Lesson:
        session_id = stored(
            url,
            NOW - timedelta(days=days),
            students=(student,),
            tutor_user_id=tutor.id,
        )
        return Lesson(id=session_id, tutor=tutor, students=[student])

    none = made(9)
    pending = made(8)
    submitted(client, pending)
    sent = made(7)
    body = submitted(client, sent)
    assert code_of(send_back(client, reviewer, sent.id, version_of(body))) == OK
    shown = made(6)
    body = approved(client, shown, coordinator)
    log = log_of(client, staff, student)
    assert len(log["items"]) == 4
    for item in log["items"]:
        assert set(item) == LOG_ITEM_KEYS
    for lesson_ in (none, pending, sent):
        assert item_of(log, lesson_.id)["summary"] is None
    item = item_of(log, shown.id)
    assert item["summary"] == SUMMARY
    assert set(item["summary"]) == SUMMARY_KEYS
    assert (item["attendance"], item["session_kind"], item["tutor"]) == (
        "present",
        "individual",
        {"display_name": "Tutora Resúmenes"},
    )
    assert item["subject"]["name"]
    assert item["starts_at"] == (NOW - timedelta(days=6)).isoformat().replace(
        "+00:00", "Z"
    )

    # The tutor asks for a correction: the new text is under review, hidden.
    cookie = sign_in(client, tutor.username)
    corrected = {**SUMMARY, "topics": "Texto corregido en revisión"}
    response = resubmit(
        client,
        cookie,
        shown.id,
        version_of(body),
        summary=corrected,
        reason="Me equivoqué de tema",
    )
    assert code_of(response) == OK, response.text
    assert item_of(log_of(client, staff, student), shown.id)["summary"] is None
    # Re-approved: the new text.
    response = approve(client, reviewer, shown.id, version_of(response.json()))
    assert code_of(response) == OK, response.text
    assert item_of(log_of(client, staff, student), shown.id)["summary"] == corrected

    # Attendance corrected by staff after approval: the live status, the
    # approved summary unchanged.
    [row] = run_sql(
        url, "SELECT version FROM class_sessions WHERE id = :id", {"id": shown.id}
    )
    response = correct(client, staff, shown.id, row.version, student, "late")
    assert code_of(response) in ((200, None), (201, None)), response.text
    item = item_of(log_of(client, staff, student), shown.id)
    assert (item["attendance"], item["summary"]) == ("late", corrected)


def test_an_all_absent_approved_closeout_has_no_summary(
    module_seeded_url, client, owner, coordinator
):
    made = lesson(module_seeded_url, kind="group", students=2)
    cookie, version = marked(client, made, ["absent", "absent"])
    response = submit(client, cookie, made.id, version, minutes=0, summary={})
    assert code_of(response) == (201, None), response.text
    response = approve(
        client,
        sign_in(client, coordinator.username),
        made.id,
        version_of(response.json()),
    )
    assert code_of(response) == OK, response.text
    staff = sign_in(client, owner.username)
    for student in made.students:
        item = item_of(log_of(client, staff, student), made.id)
        assert (item["summary"], item["attendance"]) == (None, "absent")


def test_a_group_summary_reaches_every_participant(
    module_seeded_url, client, owner, coordinator
):
    made = lesson(module_seeded_url, kind="group", students=2)
    cookie, version = marked(client, made, ["present", "absent"])
    response = submit(client, cookie, made.id, version)
    assert code_of(response) == (201, None), response.text
    assert (
        code_of(
            approve(
                client,
                sign_in(client, coordinator.username),
                made.id,
                version_of(response.json()),
            )
        )
        == OK
    )
    staff = sign_in(client, owner.username)
    first, second = (item_of(log_of(client, staff, s), made.id) for s in made.students)
    assert first["summary"] == second["summary"] == SUMMARY
    assert (first["attendance"], second["attendance"]) == ("present", "absent")
    assert first["session_kind"] == "group"


def test_the_published_note_count(module_seeded_url, client, owner):
    made = lesson(module_seeded_url, starts_at=NOW - timedelta(days=3))
    student = made.students[0]
    cookie = sign_in(client, made.tutor.username)

    def note() -> str:
        response = post(
            client,
            cookie,
            f"/api/v1/class-sessions/{made.id}/notes",
            {"student_id": str(student), "body": "Una nota"},
        )
        assert code_of(response) == (201, None), response.text
        return str(response.json()["id"])

    note()  # internal
    for _ in range(2):
        response = post(
            client, cookie, f"/api/v1/notes/{note()}/publish", {"version": 1}
        )
        assert code_of(response) == OK, response.text
    item = item_of(log_of(client, sign_in(client, owner.username), student), made.id)
    assert item["published_note_count"] == 2


def test_the_cursor_and_the_filters(module_seeded_url, client, owner):
    url = module_seeded_url
    tutor = a_tutor(url)
    student = new_student(url)
    algebra, biology = new_subject(url), new_subject(url)
    ids = [
        session_at(
            url,
            tutor,
            (student,),
            algebra if day % 2 else biology,
            _utc(date(2031, 2, day), 15),
        )
        for day in range(1, 6)
    ]
    # Never on the log: cancelled, future.
    session_at(
        url, tutor, (student,), algebra, _utc(date(2031, 2, 10), 15), state="cancelled"
    )
    session_at(url, tutor, (student,), algebra, NOW + timedelta(hours=1))
    cookie = sign_in(client, owner.username)
    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        params: dict[str, Any] = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        body = log_of(client, cookie, student, **params)
        pages += 1
        seen += [item["class_session_id"] for item in body["items"]]
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert (pages, seen) == (3, list(reversed(ids)))
    # The local-date range (inclusive) and the subject.
    body = log_of(client, cookie, student, **{"from": "2031-02-02", "to": "2031-02-04"})
    assert [i["class_session_id"] for i in body["items"]] == list(reversed(ids[1:4]))
    body = log_of(client, cookie, student, subject_id=str(algebra))
    assert [i["class_session_id"] for i in body["items"]] == [ids[4], ids[2], ids[0]]
    # 23:30 local on 2031-02-05 is 05:30Z on the 6th: the 5th locally.
    late = session_at(url, tutor, (student,), algebra, _utc(date(2031, 2, 6), 5, 30))
    body = log_of(client, cookie, student, **{"from": "2031-02-05", "to": "2031-02-05"})
    assert [i["class_session_id"] for i in body["items"]] == [late, ids[4]]


# --- B5, C4: statement budgets --------------------------------------------------------


def bulk_sessions(
    url: str,
    tutor: CreatedUser,
    student: uuid.UUID,
    subject: uuid.UUID,
    count: int,
    *,
    first: date = date(2030, 7, 1),
) -> list[str]:
    """``count`` started, confirmed sessions one day apart from ``first``."""

    async def _make(session: AsyncSession) -> list[str]:
        made = []
        for day in range(count):
            starts_at = _utc(first, 15) + timedelta(days=day)
            row = await persist_class_session(
                session,
                state="confirmed",
                starts_at=starts_at,
                ends_at=starts_at + timedelta(minutes=60),
                scheduled_minutes=60,
                tutor_user_id=tutor.id,
                subject_id=subject,
            )
            await add_participant(session, row, student)
            made.append(str(row.id))
        return made

    return in_uow(url, _make)


def bulk_grades(
    url: str, tutor: CreatedUser, student: uuid.UUID, subject: uuid.UUID, count: int
) -> None:
    run_sql(
        url,
        "WITH made AS (INSERT INTO assignments (creator_user_id, subject_id, title,"
        " instructions, due_on, max_score)"
        " SELECT :u, :s, 'T', 'I', DATE '2030-08-01' + g, 10"
        " FROM generate_series(0, :n - 1) AS g RETURNING id, due_on)"
        " INSERT INTO assignment_submissions (assignment_id, student_id,"
        " submitted_at, submitted_by_user_id, submitted_via, score, score_percent,"
        " graded_at, graded_on, graded_by_user_id)"
        " SELECT id, :st, due_on + TIME '12:00', :u, 'tutor', 7, 70,"
        " due_on + TIME '18:00', due_on, :u FROM made",
        {"u": tutor.id, "s": subject, "n": count, "st": student},
    )


@pytest.mark.parametrize(("sessions", "grades", "plans"), [(1, 1, 1), (200, 100, 20)])
def test_the_progress_budget(module_seeded_url, sessions, grades, plans):
    url = module_seeded_url
    family = guardian_with_child(url)
    tutor = a_tutor(url)
    subject = new_subject(url)
    student = family.student_id
    bulk_sessions(url, tutor, student, subject, sessions)
    # An upcoming class keeps the tutor current (counted nowhere).
    session_at(url, tutor, (student,), subject, NOW + timedelta(hours=1))
    bulk_grades(url, tutor, student, subject, grades)
    template = _template(url, subject)
    _evaluation(url, student, subject, tutor.id, template, date(2030, 9, 1), "50.00")
    for index in range(plans):
        plan(
            url,
            student,
            subject,
            tutor.id,
            title=f"Plan {index}",
            status="completed",
            closed_on=date(2031, 1, 1),
            milestones=(("done", None), ("pending", None)),
        )
    for principal in (
        principal_of(family.user, GUARDIAN),
        principal_of(tutor, TUTOR),
        principal_of(create_user(url, roles=(OWNER,)), OWNER),
    ):
        statements, body = count_statements(
            url,
            lambda s, p=principal: workflow.student_progress(
                s,
                p,
                student,
                from_=date(2030, 7, 1),
                to=TODAY,
                subject_id=subject,
            ),
        )
        assert statements <= 12, (principal.roles, statements)
        assert body.attendance.sessions == sessions
        assert body.grades.assignment_count == grades
        assert body.plans.completed == plans
        assert len(body.plans.items) == min(plans, 20)


def add_role(url: str, user: CreatedUser, role: str) -> None:
    run_sql(
        url,
        "INSERT INTO user_roles (user_id, role_id)"
        " SELECT :id, id FROM roles WHERE code = :role",
        {"id": user.id, "role": role},
    )


@dataclass
class Union:
    label: str
    principal: Any
    student: uuid.UUID
    progress: str
    """The expected ``progress`` answer: ``ok``, ``consent`` or ``not_found``."""


@pytest.fixture(scope="module")
def unions(module_seeded_url: str) -> list[Union]:
    """Every audience of the A1 ladder and the role unions it allows (#84 QA
    D1), each on a student with data. The current tutor teaches every
    student (an upcoming class keeps them current); the former tutor taught
    the minor 45 days ago."""
    url = module_seeded_url
    tutor = a_tutor(url, "Tutora Presupuesto")
    former = a_tutor(url, "Tutor Pasado")
    subject = new_subject(url)
    minor = guardian_with_child(url)
    allowed = guardian_with_child(url, adult=True, consent=True)
    refused = guardian_with_child(url, adult=True)
    login, own = student_account(url)
    # A tutor who is also the guardian of an adult who gave the consent,
    # never their tutor: in through the family check (QA D1 case 1).
    add_role(url, allowed.user, TUTOR)
    # A tutor who taught the adult and is their guardian without the
    # consent: a former tutor (QA D1 case 2).
    add_role(url, refused.user, TUTOR)
    # A student with a login who is also a tutor (not theirs).
    add_role(url, login, TUTOR)
    students = (minor.student_id, allowed.student_id, refused.student_id, own)
    for index, student in enumerate(students):
        bulk_sessions(url, tutor, student, subject, 1, first=date(2030, 7, 1 + index))
        session_at(url, tutor, (student,), subject, NOW + timedelta(hours=1 + index))
    stored(
        url,
        NOW - timedelta(days=45),
        state="completed",
        students=(minor.student_id,),
        tutor_user_id=former.id,
        subject_id=subject,
    )
    stored(
        url,
        NOW - timedelta(days=45),
        state="completed",
        students=(refused.student_id,),
        tutor_user_id=refused.user.id,
        subject_id=subject,
    )
    owner_user = create_user(url, roles=(OWNER,))
    finance = create_user(url, roles=(FINANCE_STAFF,))
    other = a_tutor(url, "Tutor Ajeno")
    return [
        Union("owner", principal_of(owner_user, OWNER), minor.student_id, "ok"),
        Union("current tutor", principal_of(tutor, TUTOR), minor.student_id, "ok"),
        Union(
            "minor's guardian",
            principal_of(minor.user, GUARDIAN),
            minor.student_id,
            "ok",
        ),
        Union(
            "adult's guardian",
            principal_of(allowed.user, GUARDIAN),
            allowed.student_id,
            "ok",
        ),
        Union("own student", principal_of(login, STUDENT), own, "ok"),
        Union(
            "former tutor", principal_of(former, TUTOR), minor.student_id, "not_found"
        ),
        Union(
            "tutor + guardian with consent",
            principal_of(allowed.user, GUARDIAN, TUTOR),
            allowed.student_id,
            "ok",
        ),
        Union(
            "former tutor + guardian without consent",
            principal_of(refused.user, GUARDIAN, TUTOR),
            refused.student_id,
            "consent",
        ),
        Union(
            "guardian without consent",
            principal_of(refused.user, GUARDIAN),
            refused.student_id,
            "consent",
        ),
        Union("tutor + own student", principal_of(login, STUDENT, TUTOR), own, "ok"),
        Union(
            "tutor + guardian, another student",
            principal_of(allowed.user, GUARDIAN, TUTOR),
            minor.student_id,
            "not_found",
        ),
        Union("other tutor", principal_of(other, TUTOR), minor.student_id, "not_found"),
        Union(
            "finance",
            principal_of(finance, FINANCE_STAFF),
            minor.student_id,
            "not_found",
        ),
    ]


def counted(url: str, call: Any) -> tuple[int, Any]:
    """``count_statements`` that also counts a refused call (returns the
    error's code)."""

    async def _call(session: AsyncSession) -> Any:
        try:
            return await call(session)
        except AppError as error:
            return error.code

    return count_statements(url, _call)


LOG_EXPECTED = {
    "ok": "log",
    "not_found": "not_found",
    "consent": "student_consent_required",
}


def test_every_caller_stays_within_the_budgets(module_seeded_url, unions):
    """B5 (≤ 12) and C4 (≤ 6) for every audience and role union, with and
    without a date range, the same count for 1 and 50 sessions (QA D1, O2);
    the answers are unchanged."""
    url = module_seeded_url
    counts: dict[tuple[str, str, bool], int] = {}
    refused_student = next(
        c.student for c in unions if c.label == "guardian without consent"
    )
    for size in (1, 50):
        if size == 50:
            for student in {case.student for case in unions}:
                bulk_sessions(
                    url,
                    a_tutor(url, "Tutora Masiva"),
                    student,
                    new_subject(url),
                    49,
                    first=date(2030, 9, 1),
                )
        for case in unions:
            for dated in (False, True):
                bounds = {"from_": date(2030, 1, 1), "to": TODAY} if dated else {}
                log_n, log = counted(
                    url,
                    lambda s, c=case, b=bounds: workflow.session_log(
                        s,
                        c.principal,
                        c.student,
                        from_=b.get("from_"),
                        to=b.get("to"),
                        subject_id=None,
                        cursor=None,
                        limit=50,
                    ),
                )
                progress_n, progress = counted(
                    url,
                    lambda s, c=case, b=bounds: workflow.student_progress(
                        s,
                        c.principal,
                        c.student,
                        from_=b.get("from_"),
                        to=b.get("to"),
                        subject_id=None,
                    ),
                )
                where = (case.label, size, dated)
                assert log_n <= 6, (where, log_n)
                assert progress_n <= 12, (where, progress_n)
                expected_progress = {
                    "ok": None,
                    "consent": "student_consent_required",
                    "not_found": "not_found",
                }[case.progress]
                if expected_progress is None:
                    assert progress.student_id == case.student, where
                else:
                    assert progress == expected_progress, where
                former = "former" in case.label
                if case.progress == "ok" or former:
                    assert hasattr(log, "items"), (where, log)
                    if former:
                        assert len(log.items) == 1, where  # only what they taught
                    else:
                        # 1 or 50 of the tutors' sessions; the minor and the
                        # refused adult also have the 45-day-old one.
                        extra = case.student in (unions[0].student, refused_student)
                        assert len(log.items) == min(size + extra, 50), where
                else:
                    assert log == LOG_EXPECTED[case.progress], (where, log)
                for route, n in (("log", log_n), ("progress", progress_n)):
                    key = (case.label, route, dated)
                    counts.setdefault(key, n)
                    assert counts[key] == n, (key, size, n)


def test_the_plan_items_are_capped_at_20(module_seeded_url):
    url = module_seeded_url
    tutor = a_tutor(url)
    subject = new_subject(url)
    student = new_student(url)
    for index in range(25):
        plan(
            url,
            student,
            subject,
            tutor.id,
            title=f"Plan {index}",
            status="completed",
            closed_on=date(2031, 1, 1) + timedelta(days=index),
            milestones=(("done", None),),
        )
    owner_user = create_user(url, roles=(OWNER,))
    _, body = count_statements(
        url,
        lambda s: workflow.student_progress(
            s,
            principal_of(owner_user, OWNER),
            student,
            from_=None,
            to=None,
            subject_id=None,
        ),
    )
    assert body.plans.completed == 25
    assert body.plans.objectives_complete == 25
    assert [item.title for item in body.plans.items] == [
        f"Plan {index}" for index in range(24, 4, -1)
    ]


def test_the_family_student_role_reads_without_a_guardian(module_seeded_url, client):
    """The student's own login (STUDENT role) on both routes, with data."""
    url = module_seeded_url
    login, own = student_account(url)
    tutor = a_tutor(url)
    session_at(url, tutor, (own,), new_subject(url), STARTED, marks=("present",))
    cookie = sign_in(client, login.username)
    assert ok(get(client, cookie, progress_path(own)))["attendance"]["present"] == 1
    [item] = log_of(client, cookie, own)["items"]
    assert item["attendance"] == "present"
