"""Progress report schedules (#316 B1, B3, C1-C3, F2) through the real app.

Every C2 and C3 order step; the S1 shape, ordering, 100 cap, eligible
authors (role, state, active subjects only), each ``ineligible_reason``, the
B3 status for each value (a matching report the tutor wrote, a deleted
placeholder; no ``report_id`` for drafts); the statement budget (the same
for 1 and 20 schedules); reactivation moves ``starts_on``; the B1 seam
agrees with ``student_ids_for_tutor`` and ``subject_ids_for_tutor_student``.
The access matrix is ``tests/authz/matrix.toml``; the job is
``test_progress_report_placeholders.py``; the races
``test_progress_report_placeholders_concurrency.py``.
"""

import uuid
from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    TUTOR,
)
from app.modules.scheduling import service as scheduling
from app.workflows import progress_report_schedules as workflow
from tests.authz.assertions import assert_same_not_found
from tests.conftest import FrozenClock, frozen_clock
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import count_statements, get, patch, post
from tests.integration.conftest import run_sql
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, events, in_uow
from tests.integration.report_schedule_support import (
    FEBRUARY,
    LAST_MONDAY,
    MARCH,
    MONDAY,
    NOW,
    a_tutor,
    insert_report,
    insert_schedule,
    schedule_path,
    schedules_path,
    slot,
    teaching,
)
from tests.integration.schedule_support import stored
from tests.integration.test_class_session_create import new_student, new_subject

OK = (200, None)
CREATED = (201, None)
NOT_FOUND = (404, "not_found")
INVALID = (422, "validation_error")
FORBIDDEN = (403, "permission_denied")
EXISTS = (409, "progress_report_schedule_exists")
VERSION_CONFLICT = (409, "version_conflict")
STUDENT_INACTIVE = (422, "student_inactive")

VIEW_KEYS = {
    "id",
    "version",
    "student_id",
    "author",
    "cadence",
    "subject",
    "starts_on",
    "is_active",
    "created_at",
    "created_by",
    "ineligible_reason",
    "latest_period",
}


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
def coordinator(module_seeded_url: str) -> CreatedUser:
    return create_user(
        module_seeded_url,
        roles=(ACADEMIC_COORDINATOR,),
        display_name="Coordinadora Programas",
    )


@pytest.fixture(scope="module")
def staff(client: TestClient, coordinator: CreatedUser) -> Any:
    return sign_in(client, coordinator.username)


def ok(response: Response, expected: tuple[int, None] = OK) -> dict[str, Any]:
    assert code_of(response) == expected, response.text
    body: dict[str, Any] = response.json()
    return body


def field_codes(response: Response) -> list[tuple[list[Any], str]]:
    assert code_of(response) == INVALID, response.text
    return [(item["path"], item["code"]) for item in response.json()["errors"]]


def add(client: TestClient, cookie: Any, student: Any, **body: Any) -> Response:
    values = {
        k: str(v) if isinstance(v, (uuid.UUID, date)) else v
        for k, v in body.items()
        if v is not ...
    }
    return post(client, cookie, schedules_path(student), values)


def change(
    client: TestClient, cookie: Any, schedule: Any, version: int, active: bool
) -> Response:
    return patch(
        client,
        cookie,
        schedule_path(schedule),
        {"version": version, "is_active": active},
    )


def listed(client: TestClient, cookie: Any, student: Any) -> dict[str, Any]:
    return ok(get(client, cookie, schedules_path(student)))


def substituted(url: str, tutor: CreatedUser) -> None:
    """Every session of ``tutor`` goes to a new tutor (a substitution): the
    tutor no longer teaches anyone."""
    run_sql(
        url,
        "UPDATE class_sessions SET tutor_user_id = :new WHERE tutor_user_id = :old",
        {"new": a_tutor(url, "Suplente").id, "old": tutor.id},
    )


def item_of(body: dict[str, Any], schedule: Any) -> dict[str, Any]:
    [found] = [item for item in body["items"] if item["id"] == str(schedule)]
    return found


# --- Access (E4) ----------------------------------------------------------------------


@pytest.mark.parametrize("role", [FINANCE_STAFF, TUTOR, GUARDIAN])
def test_everyone_without_the_permission_gets_403(module_seeded_url, client, role):
    url = module_seeded_url
    tutor, student, _ = teaching(url)
    schedule = insert_schedule(url, student=student, author=tutor.id)
    # A tutor who teaches the student is still no holder.
    caller = (
        tutor
        if role == TUTOR
        else create_user(url, roles=(role,), display_name="Sin permiso")
    )
    cookie = sign_in(client, caller.username)

    assert code_of(get(client, cookie, schedules_path(student))) == FORBIDDEN
    assert (
        code_of(add(client, cookie, student, author_user_id=tutor.id, cadence="weekly"))
        == FORBIDDEN
    )
    assert code_of(change(client, cookie, schedule, 1, False)) == FORBIDDEN
    # Unknown ids too: nothing is read before the permission.
    assert code_of(get(client, cookie, schedules_path(uuid.uuid4()))) == FORBIDDEN


@pytest.mark.parametrize("role", [OWNER, ADMINISTRATOR, ACADEMIC_COORDINATOR])
def test_every_holder_reads_and_unknown_ids_are_404(module_seeded_url, client, role):
    url = module_seeded_url
    holder = create_user(url, roles=(role,), display_name="Titular")
    cookie = sign_in(client, holder.username)
    _, student, _ = teaching(url)

    assert ok(get(client, cookie, schedules_path(student)))["items"] == []
    missing = get(client, cookie, schedules_path(uuid.uuid4()))
    assert code_of(missing) == NOT_FOUND
    assert code_of(change(client, cookie, uuid.uuid4(), 1, False)) == NOT_FOUND
    tutor = a_tutor(url)
    assert (
        code_of(
            add(client, cookie, uuid.uuid4(), author_user_id=tutor.id, cadence="weekly")
        )
        == NOT_FOUND
    )
    # A tutor teaching nobody: still the same 404 body for an unknown student.
    assert_same_not_found(missing, get(client, cookie, schedules_path(uuid.uuid4())))


# --- S2: the add order (C2) -----------------------------------------------------------


def test_the_add_body_is_closed(module_seeded_url, client, staff):
    tutor, student, _ = teaching(module_seeded_url)

    assert code_of(add(client, staff, student, author_user_id=tutor.id)) == INVALID
    assert (
        code_of(
            add(client, staff, student, author_user_id=tutor.id, cadence="on_demand")
        )
        == INVALID
    )
    extra = add(
        client,
        staff,
        student,
        author_user_id=tutor.id,
        cadence="weekly",
        schedule_id=str(uuid.uuid4()),
    )
    assert code_of(extra) == INVALID
    # The schema comes before the student lookup.
    assert code_of(add(client, staff, uuid.uuid4(), cadence="weekly")) == INVALID


def test_the_add_order(module_seeded_url, client, staff):
    url = module_seeded_url
    tutor, student, subject = teaching(url)
    inactive_student = new_student(url, is_active=False)
    stored(url, slot(), students=(inactive_student,), tutor_user_id=tutor.id)

    # 422 student_inactive before the author.
    assert (
        code_of(
            add(
                client,
                staff,
                inactive_student,
                author_user_id=uuid.uuid4(),
                cadence="weekly",
            )
        )
        == STUDENT_INACTIVE
    )
    # schedule_author_invalid: unknown, inactive, not a tutor, not teaching.
    suspended = create_user(url, roles=(TUTOR,), state="suspended")
    stored(url, slot(), students=(student,), tutor_user_id=suspended.id)
    coordinator_teaching = create_user(url, roles=(ACADEMIC_COORDINATOR,))
    stored(url, slot(), students=(student,), tutor_user_id=coordinator_teaching.id)
    stranger = a_tutor(url)
    for author in (uuid.uuid4(), suspended.id, coordinator_teaching.id, stranger.id):
        # The author comes before the subject.
        response = add(
            client,
            staff,
            student,
            author_user_id=author,
            cadence="weekly",
            subject_id=uuid.uuid4(),
        )
        assert field_codes(response) == [
            (["body", "author_user_id"], "schedule_author_invalid")
        ]
    # subject_unknown: unknown or inactive (or its level), before not assigned.
    for unknown in (
        uuid.uuid4(),
        new_subject(url, is_active=False),
        new_subject(url, level_active=False),
    ):
        response = add(
            client,
            staff,
            student,
            author_user_id=tutor.id,
            cadence="weekly",
            subject_id=unknown,
            starts_on=date(2031, 3, 4),
        )
        assert field_codes(response) == [(["body", "subject_id"], "subject_unknown")]
    response = add(
        client,
        staff,
        student,
        author_user_id=tutor.id,
        cadence="weekly",
        subject_id=new_subject(url),
        starts_on=date(2031, 3, 4),
    )
    assert field_codes(response) == [
        (["body", "subject_id"], "schedule_subject_not_assigned")
    ]
    # starts_on: a Tuesday, the previous period, too far ahead.
    for starts_on, cadence in (
        (date(2031, 3, 4), "weekly"),
        (LAST_MONDAY, "weekly"),
        (date(2032, 3, 8), "weekly"),
        (date(2031, 3, 2), "monthly"),
        (FEBRUARY, "monthly"),
    ):
        response = add(
            client,
            staff,
            student,
            author_user_id=tutor.id,
            cadence=cadence,
            subject_id=subject,
            starts_on=starts_on,
        )
        assert field_codes(response) == [
            (["body", "starts_on"], "schedule_starts_on_invalid")
        ], (starts_on, cadence)
    assert not run_sql(
        url,
        "SELECT 1 FROM progress_report_schedules WHERE student_id = :s",
        {"s": student},
    )


def test_add_creates_and_refuses_a_second_active_schedule(
    module_seeded_url, client, staff, coordinator
):
    url = module_seeded_url
    tutor, student, subject = teaching(url)

    weekly = ok(
        add(client, staff, student, author_user_id=tutor.id, cadence="weekly"), CREATED
    )
    assert set(weekly) == VIEW_KEYS
    assert weekly["student_id"] == str(student)
    assert weekly["author"] == {
        "user_id": str(tutor.id),
        "display_name": "Tutora Programada",
    }
    assert (weekly["cadence"], weekly["subject"], weekly["starts_on"]) == (
        "weekly",
        None,
        MONDAY.isoformat(),
    )
    assert (weekly["is_active"], weekly["version"]) == (True, 1)
    assert weekly["created_by"] == {"display_name": "Coordinadora Programas"}
    # This week's period hasn't ended: nothing is due yet.
    assert (weekly["ineligible_reason"], weekly["latest_period"]) == (None, None)

    monthly = ok(
        add(
            client,
            staff,
            student,
            author_user_id=tutor.id,
            cadence="monthly",
            subject_id=subject,
        ),
        CREATED,
    )
    assert monthly["starts_on"] == MARCH.isoformat()
    assert monthly["subject"]["id"] == str(subject)
    # The same tutor, cadence and subject again: 409 naming the active one.
    again = add(
        client,
        staff,
        student,
        author_user_id=tutor.id,
        cadence="weekly",
        starts_on=date(2031, 3, 10),
    )
    assert code_of(again) == EXISTS
    assert again.json()["details"] == {"schedule_id": weekly["id"]}
    # Another subject (or all subjects with monthly) is another key.
    ok(
        add(
            client,
            staff,
            student,
            author_user_id=tutor.id,
            cadence="weekly",
            subject_id=subject,
        ),
        CREATED,
    )
    # Audited with the staff user as actor; nobody notified.
    [created] = events(
        url,
        action="create",
        record_type="progress_report_schedules",
        record_id=uuid.UUID(weekly["id"]),
    )
    assert created.actor_user_id == coordinator.id
    assert not run_sql(
        url,
        "SELECT 1 FROM notifications WHERE recipient_user_id = :u",
        {"u": tutor.id},
    )


# --- S3: deactivate / reactivate (C3) -------------------------------------------------


def test_deactivate_and_reactivate(
    module_seeded_url, client, staff, frozen, coordinator
):
    url = module_seeded_url
    tutor, student, _ = teaching(url)
    view = ok(
        add(client, staff, student, author_user_id=tutor.id, cadence="weekly"), CREATED
    )
    schedule = view["id"]

    assert (
        code_of(patch(client, staff, schedule_path(schedule), {"version": 1}))
        == INVALID
    )
    assert (
        code_of(
            patch(
                client,
                staff,
                schedule_path(schedule),
                {"version": 1, "is_active": False, "starts_on": "2031-03-10"},
            )
        )
        == INVALID
    )
    assert code_of(change(client, staff, schedule, 2, False)) == VERSION_CONFLICT
    # Unchanged: 200, no write.
    same = ok(change(client, staff, schedule, 1, True))
    assert same["version"] == 1
    off = ok(change(client, staff, schedule, 1, False))
    assert (off["is_active"], off["version"], off["latest_period"]) == (False, 2, None)
    # Two weeks later the schedule is reactivated: starts_on moves to that
    # week, never back to the weeks it was off.
    frozen.set(NOW + timedelta(days=14))
    teaching(url, tutor=tutor, student=student, starts_at=NOW + timedelta(days=14))
    later = sign_in(client, coordinator.username)
    on = ok(change(client, later, schedule, 2, True))
    assert (on["is_active"], on["version"], on["starts_on"]) == (True, 3, "2031-03-17")
    assert on["latest_period"] is None


def test_reactivation_rechecks_the_add_rules(module_seeded_url, client, staff):
    url = module_seeded_url
    tutor, student, subject = teaching(url)
    schedule = insert_schedule(
        url, student=student, author=tutor.id, subject=subject, active=False
    )
    # Another active schedule with the key: 409.
    other = insert_schedule(url, student=student, author=tutor.id, subject=subject)
    refused = change(client, staff, schedule, 1, True)
    assert code_of(refused) == EXISTS
    assert refused.json()["details"] == {"schedule_id": str(other)}
    run_sql(
        url,
        "UPDATE progress_report_schedules SET is_active = false WHERE id = :id",
        {"id": other},
    )
    # The subject no longer taught, inactive, the author not current, the
    # student inactive: top-level codes, in the C2 order.
    run_sql(
        url,
        "UPDATE class_sessions SET subject_id = :other WHERE tutor_user_id = :t",
        {"other": new_subject(url), "t": tutor.id},
    )
    assert code_of(change(client, staff, schedule, 1, True)) == (
        422,
        "schedule_subject_not_assigned",
    )
    run_sql(url, "UPDATE subjects SET is_active = false WHERE id = :s", {"s": subject})
    assert code_of(change(client, staff, schedule, 1, True)) == (422, "subject_unknown")
    substituted(url, tutor)
    assert code_of(change(client, staff, schedule, 1, True)) == (
        422,
        "schedule_author_invalid",
    )
    run_sql(url, "UPDATE students SET is_active = false WHERE id = :s", {"s": student})
    assert code_of(change(client, staff, schedule, 1, True)) == STUDENT_INACTIVE
    # Deactivating an inactive schedule is no write.
    assert ok(change(client, staff, schedule, 1, False))["version"] == 1


# --- S1: shape, order, cap, eligible authors (C1) -------------------------------------


def test_the_list_order_and_the_eligible_authors(module_seeded_url, client, staff):
    url = module_seeded_url
    beta = a_tutor(url, "beta Tutor")
    alpha = a_tutor(url, "Alfa Tutora")
    student = new_student(url)
    math, art, retired = new_subject(url), new_subject(url), new_subject(url)
    teaching(url, tutor=beta, student=student, subject=math)
    teaching(url, tutor=alpha, student=student, subject=art)
    teaching(url, tutor=alpha, student=student, subject=retired)
    run_sql(url, "UPDATE subjects SET is_active = false WHERE id = :s", {"s": retired})
    # Not eligible: a coordinator teaching, an offered-only suspended tutor.
    staff_teacher = create_user(url, roles=(ACADEMIC_COORDINATOR,))
    teaching(url, tutor=staff_teacher, student=student)
    suspended = create_user(url, roles=(TUTOR,), state="suspended")
    teaching(url, tutor=suspended, student=student, state="offered")

    old_inactive = insert_schedule(url, student=student, author=beta.id, active=False)
    first = insert_schedule(url, student=student, author=alpha.id)
    second = insert_schedule(
        url, student=student, author=beta.id, cadence="monthly", starts_on=FEBRUARY
    )

    body = listed(client, staff, student)

    assert set(body) == {"items", "eligible_authors"}
    assert [item["id"] for item in body["items"]] == [
        str(second),
        str(first),
        str(old_inactive),
    ]
    assert [a["display_name"] for a in body["eligible_authors"]] == [
        "Alfa Tutora",
        "beta Tutor",
    ]
    [alpha_entry, beta_entry] = body["eligible_authors"]
    assert set(alpha_entry) == {"user_id", "display_name", "subjects"}
    assert [s["id"] for s in alpha_entry["subjects"]] == [str(art)]
    assert [s["id"] for s in beta_entry["subjects"]] == [str(math)]
    # Nobody teaches this one.
    assert listed(client, staff, new_student(url)) == {
        "items": [],
        "eligible_authors": [],
    }


def test_at_most_100_schedules_active_first(module_seeded_url, client, staff):
    url = module_seeded_url
    tutor, student, _ = teaching(url)
    active = insert_schedule(url, student=student, author=tutor.id)
    run_sql(
        url,
        "INSERT INTO progress_report_schedules (student_id, author_user_id,"
        " cadence, starts_on, is_active, created_by_user_id)"
        " SELECT :s, :a, 'weekly', DATE '2031-02-24', false, :a"
        " FROM generate_series(1, 104)",
        {"s": student, "a": tutor.id},
    )

    items = listed(client, staff, student)["items"]

    assert len(items) == 100
    assert items[0]["id"] == str(active)
    assert [item["is_active"] for item in items].count(True) == 1


# --- ineligible_reason and B3 ---------------------------------------------------------


def test_each_ineligible_reason(module_seeded_url, client, staff):
    url = module_seeded_url

    def reason(schedule: Any, student: Any) -> Any:
        return item_of(listed(client, staff, student), schedule)["ineligible_reason"]

    tutor, student, subject = teaching(url)
    with_subject = insert_schedule(
        url, student=student, author=tutor.id, subject=subject
    )
    assert reason(with_subject, student) is None

    other = new_subject(url)
    not_taught = insert_schedule(url, student=student, author=tutor.id, subject=other)
    assert reason(not_taught, student) == "subject_not_assigned"
    run_sql(url, "UPDATE subjects SET is_active = false WHERE id = :s", {"s": other})
    assert reason(not_taught, student) == "subject_inactive"

    substituted(url, tutor)
    assert reason(with_subject, student) == "author_not_current"
    run_sql(
        url,
        "DELETE FROM user_roles WHERE user_id = :u",
        {"u": tutor.id},
    )
    assert reason(with_subject, student) == "author_not_tutor"
    run_sql(url, "UPDATE students SET is_active = false WHERE id = :s", {"s": student})
    body = listed(client, staff, student)
    # Inactive schedules show the reason too.
    assert {item["ineligible_reason"] for item in body["items"]} == {"student_inactive"}


@pytest.mark.parametrize(
    ("status", "deleted", "linked"),
    [
        ("draft", False, False),
        ("draft", True, False),
        ("changes_requested", True, False),
        ("submitted", False, True),
        ("changes_requested", False, True),
        ("approved_for_family", False, True),
        ("retracted", False, True),
    ],
)
def test_the_latest_period_of_a_placeholder(
    module_seeded_url, client, staff, status, deleted, linked
):
    url = module_seeded_url
    tutor, student, _ = teaching(url)
    schedule = insert_schedule(url, student=student, author=tutor.id)
    assert item_of(listed(client, staff, student), schedule)["latest_period"] == {
        "period_start": LAST_MONDAY.isoformat(),
        "period_end": "2031-03-02",
        "status": "not_created",
        "report_id": None,
    }
    report = insert_report(
        url,
        student=student,
        author=tutor.id,
        schedule=schedule,
        status=status,
        deleted=deleted,
    )

    latest = item_of(listed(client, staff, student), schedule)["latest_period"]

    assert latest["status"] == ("deleted" if deleted else status)
    assert latest["report_id"] == (str(report) if linked else None)


def test_the_latest_period_of_a_report_the_tutor_wrote(
    module_seeded_url, client, staff
):
    url = module_seeded_url
    tutor, student, subject = teaching(url)
    monthly = insert_schedule(
        url,
        student=student,
        author=tutor.id,
        cadence="monthly",
        starts_on=FEBRUARY,
        subject=subject,
    )
    whole = insert_schedule(
        url, student=student, author=tutor.id, cadence="monthly", starts_on=FEBRUARY
    )
    # A retracted and a deleted report don't count; another subject doesn't.
    insert_report(
        url,
        student=student,
        author=tutor.id,
        kind="monthly",
        period_start=FEBRUARY,
        subject=subject,
        status="retracted",
    )
    insert_report(
        url,
        student=student,
        author=tutor.id,
        kind="monthly",
        period_start=FEBRUARY,
        subject=subject,
        deleted=True,
    )
    insert_report(
        url,
        student=student,
        author=tutor.id,
        kind="monthly",
        period_start=FEBRUARY,
        subject=new_subject(url),
        status="submitted",
    )
    body = listed(client, staff, student)
    assert item_of(body, monthly)["latest_period"]["status"] == "not_created"
    assert item_of(body, whole)["latest_period"]["status"] == "not_created"

    written = insert_report(
        url,
        student=student,
        author=tutor.id,
        kind="monthly",
        period_start=FEBRUARY,
        subject=subject,
        status="submitted",
    )
    draft = insert_report(
        url, student=student, author=tutor.id, kind="monthly", period_start=FEBRUARY
    )

    body = listed(client, staff, student)
    assert item_of(body, monthly)["latest_period"] == {
        "period_start": FEBRUARY.isoformat(),
        "period_end": "2031-02-28",
        "status": "submitted",
        "report_id": str(written),
    }
    assert item_of(body, whole)["latest_period"]["status"] == "draft"
    assert item_of(body, whole)["latest_period"]["report_id"] is None
    assert str(draft) not in str(body)


def test_no_latest_period_before_it_is_due_or_when_inactive(
    module_seeded_url, client, staff
):
    url = module_seeded_url
    tutor, student, _ = teaching(url)
    future = insert_schedule(url, student=student, author=tutor.id, starts_on=MONDAY)
    inactive = insert_schedule(
        url,
        student=student,
        author=tutor.id,
        cadence="monthly",
        starts_on=FEBRUARY,
        active=False,
    )

    body = listed(client, staff, student)

    assert item_of(body, future)["latest_period"] is None
    assert item_of(body, inactive)["latest_period"] is None


# --- Budgets and the B1 seam ----------------------------------------------------------


def test_the_list_takes_the_same_statements_for_1_and_20(module_seeded_url):
    url = module_seeded_url
    tutor, one, _ = teaching(url)
    insert_schedule(url, student=one, author=tutor.id)
    many = new_student(url)
    for _ in range(4):
        other, _, subject = teaching(url, student=many)
        for cadence, starts_on in (("weekly", LAST_MONDAY), ("monthly", FEBRUARY)):
            insert_schedule(
                url, student=many, author=other.id, cadence=cadence, starts_on=starts_on
            )
            insert_schedule(
                url,
                student=many,
                author=other.id,
                cadence=cadence,
                starts_on=starts_on,
                subject=subject,
            )
        insert_schedule(url, student=many, author=other.id, active=False)
        insert_report(url, student=many, author=other.id, status="submitted")

    with frozen_clock(NOW):
        counted_one, body_one = count_statements(
            url, lambda s: workflow.list_schedules(s, one)
        )
        counted_many, body_many = count_statements(
            url, lambda s: workflow.list_schedules(s, many)
        )

    assert len(body_one.items) == 1
    assert len(body_many.items) == 20
    assert counted_one == counted_many <= 8


def test_the_current_tutors_seam_agrees_with_the_tutor_seams(module_seeded_url):
    url = module_seeded_url
    student = new_student(url)
    first, second = new_subject(url), new_subject(url)
    tutors: dict[str, CreatedUser] = {}

    def session(
        name: str, starts_at: Any, subject: Any = first, state: str = "confirmed"
    ) -> None:
        tutor = tutors.setdefault(name, a_tutor(url, name))
        teaching(
            url,
            tutor=tutor,
            student=student,
            subject=subject,
            state=state,
            starts_at=starts_at,
        )

    session("offered", NOW + timedelta(days=2), state="offered")
    session("confirmed", NOW + timedelta(days=1))
    session("confirmed", NOW + timedelta(days=3), subject=second)
    session("completed", NOW - timedelta(days=3), state="completed")
    session("ended_29", NOW - timedelta(days=29, hours=1), state="completed")
    session("ended_31", NOW - timedelta(days=31, hours=1), state="completed")
    tutors["cancelled"] = a_tutor(url, "cancelled")
    stored(
        url,
        NOW + timedelta(days=1),
        state="cancelled",
        students=(student,),
        tutor_user_id=tutors["cancelled"].id,
        subject_id=first,
        cancelled_at=NOW,
        cancellation_reason="Cancelada",
    )
    session("substituted", NOW + timedelta(days=4))
    substitute = a_tutor(url, "substitute")
    run_sql(
        url,
        "UPDATE class_sessions SET tutor_user_id = :new WHERE tutor_user_id = :old",
        {"new": substitute.id, "old": tutors["substituted"].id},
    )
    tutors["substitute"] = substitute

    async def facts(db: Any) -> tuple[Any, dict[str, Any]]:
        seam = await scheduling.current_tutor_subjects_for_student(db, student, now=NOW)
        each = {}
        for tutor in tutors.values():
            if student in await scheduling.student_ids_for_tutor(db, tutor.id, now=NOW):
                each[tutor.id] = await scheduling.subject_ids_for_tutor_student(
                    db, tutor.id, student, now=NOW
                )
        return seam, each

    seam, each = in_uow(url, facts)

    assert seam == each
    names = {tutor.id: name for name, tutor in tutors.items()}
    assert {names[t] for t in seam} == {
        "offered",
        "confirmed",
        "completed",
        "ended_29",
        "substitute",
    }
    assert seam[tutors["confirmed"].id] == {first, second}
    counted, _ = count_statements(
        url,
        lambda db: scheduling.current_tutor_subjects_for_student(db, student, now=NOW),
    )
    assert counted == 1
