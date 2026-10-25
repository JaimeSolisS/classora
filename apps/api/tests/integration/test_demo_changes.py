"""The #303 demo changes (#303 D, E2-E7): ``scheduling.demo_changes``, the
whole run, and what each demo account sees after it."""

import asyncio
import dataclasses
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.modules.scheduling.errors import SlotUnavailableError
from tests.factories import demo
from tests.factories.base import GeneratorSkipped
from tests.factories.demo_changes import (
    CANCELLATION_REASON,
    CHANGE_REQUEST_NOTE,
    COHORT_NAME,
    SESSION_REQUEST_NOTE,
    STALE_SCHEDULE,
    SUBSTITUTION_REASON,
    TIME_OFF_NOTE,
    DemoChangesGenerator,
    DemoChangesPlan,
    plan_demo_changes,
)
from tests.factories.demo_courses import TEMPLATE_NAME, DemoCoursesGenerator
from tests.factories.demo_schedule import plan_demo_schedule
from tests.factories.identity import DemoPrerequisiteMissingError
from tests.factories.staff import DemoSecondTutorGenerator
from tests.integration.auth_support import auth_client
from tests.integration.catalog_support import get
from tests.integration.conftest import run_sql
from tests.integration.demo_support import (
    ALL_TABLE_COUNTS,
    CHANGES_GENERATOR,
    COURSES_GENERATOR,
    DEMO_303_TABLE_COUNTS,
    DEV_LINES,
    FIRST_RUN_LINES,
    FROZEN_NOW,
    GENERATOR_NAMES,
    NEW_303_GENERATORS,
    RERUN_LINES,
    RERUN_REFERENCE_LINES,
    SCHEDULE_GENERATOR,
    TUTOR2_GENERATOR,
    UntouchedSession,
    count,
    demo_env,
    demo_table_counts,
    generators_before_303,
    migrate,
)
from tests.integration.test_demo_schedule import (
    ZONE,
    ranged,
    session_id,
    signed_in,
    student_labels,
)

# T = Wednesday 2026-11-04: D(1) = Friday 11-06, D(6) = Friday 11-13,
# D(7) = Monday 11-16, D(8) = Tuesday 11-17, Sat1 + 7 = Saturday 11-14.
U1 = "2026-11-06 16:00"
D8 = "2026-11-17 17:00"
CANCELLED = "2026-11-16 15:00"
COHORT_STARTS = [
    "2026-11-09 15:00",
    "2026-11-11 15:00",
    CANCELLED,
    "2026-11-18 15:00",
    "2026-11-23 15:00",
    "2026-11-25 15:00",
    "2026-11-30 15:00",
    "2026-12-02 15:00",
]


@pytest.fixture
def migrated_url(fresh_database_url: str) -> str:
    return migrate(fresh_database_url)


@pytest.fixture
def env_for(monkeypatch):
    return lambda app_env, dsn: demo_env(monkeypatch, app_env, dsn)


def user_id(url: str, username: str) -> Any:
    [row] = run_sql(url, "SELECT id FROM users WHERE username = :u", {"u": username})
    return row.id


def cohort_states(url: str) -> list[tuple[str, str]]:
    rows = run_sql(
        url,
        "SELECT to_char(cs.starts_at AT TIME ZONE :zone, 'YYYY-MM-DD HH24:MI')"
        " AS starts, cs.state FROM class_sessions cs"
        " WHERE cs.kind = 'course' ORDER BY cs.starts_at",
        {"zone": ZONE},
    )
    return [(r.starts, r.state) for r in rows]


def test_a_first_run_writes_every_change(migrated_url, env_for, clock, capsys):
    # E2 on Wednesday 2026-11-04, 11:00 local.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)

    assert demo.main([]) == 0

    assert capsys.readouterr().out.splitlines() == FIRST_RUN_LINES
    tutor = user_id(migrated_url, "demo.tutor")
    tutor2 = user_id(migrated_url, "demo.tutor2")
    labels = student_labels(migrated_url)

    # D3: the D(8) series occurrence, now taught by demo.tutor2.
    d8 = session_id(migrated_url, D8)
    [row] = run_sql(
        migrated_url,
        "SELECT state, tutor_user_id FROM class_sessions WHERE id = :id",
        {"id": d8},
    )
    assert (row.state, row.tutor_user_id) == ("confirmed", tutor2)
    [substitution] = run_sql(
        migrated_url,
        "SELECT s.original_tutor_user_id, s.substitute_tutor_user_id, s.reason,"
        " u.username AS requested_by, o.status, o.kind"
        " FROM class_session_substitutions s"
        " JOIN assignment_offers o ON o.id = s.offer_id"
        " JOIN users u ON u.id = s.requested_by_user_id"
        " WHERE s.class_session_id = :id",
        {"id": d8},
    )
    assert tuple(substitution) == (
        tutor,
        tutor2,
        SUBSTITUTION_REASON,
        "demo.owner",
        "accepted",
        "substitution",
    )

    # D4: the D(7) cohort session cancelled; the other 7 confirmed.
    assert cohort_states(migrated_url) == [
        (starts, "cancelled" if starts == CANCELLED else "confirmed")
        for starts in COHORT_STARTS
    ]
    [cancelled] = run_sql(
        migrated_url,
        "SELECT cancellation_reason FROM class_sessions WHERE id = :id",
        {"id": session_id(migrated_url, CANCELLED)},
    )
    assert cancelled.cancellation_reason == CANCELLATION_REASON

    # D5: the session request, pending, with its open Approvals item.
    [request] = run_sql(
        migrated_url,
        "SELECT r.student_id, s.name || ' (' || l.name || ')' AS subject,"
        " to_char(r.starts_at AT TIME ZONE :zone, 'YYYY-MM-DD HH24:MI') AS starts,"
        " r.duration_minutes, r.note, r.status, u.username AS requested_by,"
        " a.status AS item"
        " FROM session_requests r JOIN subjects s ON s.id = r.subject_id"
        " JOIN education_levels l ON l.id = s.education_level_id"
        " JOIN users u ON u.id = r.requested_by_user_id"
        " JOIN approval_requests a ON a.subject_id = r.id"
        " AND a.request_type = 'scheduling.session_request'",
        {"zone": ZONE},
    )
    assert (labels[request.student_id], *tuple(request)[1:]) == (
        "S",
        "Ciencias (Secundaria)",
        "2026-11-13 16:00",
        60,
        SESSION_REQUEST_NOTE,
        "pending",
        "demo.guardian",
        "pending",
    )

    # D6: the reschedule request for U1, pending; U1 unchanged.
    u1 = session_id(migrated_url, U1)
    [change] = run_sql(
        migrated_url,
        "SELECT c.class_session_id, c.kind, c.initiated_by, c.student_id,"
        " to_char(c.proposed_starts_at AT TIME ZONE :zone, 'YYYY-MM-DD HH24:MI')"
        " AS proposed, c.note, c.status, u.username AS requested_by,"
        " a.status AS item"
        " FROM session_change_requests c"
        " JOIN users u ON u.id = c.requested_by_user_id"
        " JOIN approval_requests a ON a.subject_id = c.id"
        " AND a.request_type = 'scheduling.change_request'",
        {"zone": ZONE},
    )
    assert (change.class_session_id, labels[change.student_id]) == (u1, "S")
    assert (
        change.kind,
        change.initiated_by,
        change.proposed,
        change.note,
        change.status,
        change.requested_by,
        change.item,
    ) == (
        "reschedule",
        "family",
        "2026-11-06 18:00",
        CHANGE_REQUEST_NOTE,
        "pending",
        "demo.guardian",
        "pending",
    )
    [u1_row] = run_sql(
        migrated_url,
        "SELECT state, tutor_user_id FROM class_sessions WHERE id = :id",
        {"id": u1},
    )
    assert (u1_row.state, u1_row.tutor_user_id) == ("confirmed", tutor)

    # D7: demo.tutor's time off on Sat1 + 7, pending; no exception row, the
    # group occurrence unchanged.
    [availability] = run_sql(
        migrated_url,
        "SELECT c.kind, c.status, c.payload, u.username AS requested_by,"
        " a.status AS item FROM tutor_availability_change_requests c"
        " JOIN users u ON u.id = c.requested_by_user_id"
        " JOIN approval_requests a ON a.subject_id = c.id"
        " AND a.request_type = 'staff.availability_change'"
        " WHERE c.tutor_user_id = :id",
        {"id": tutor},
    )
    assert (availability.status, availability.requested_by, availability.item) == (
        "pending",
        "demo.tutor",
        "pending",
    )
    assert availability.payload["starts_on"] == "2026-11-14"
    assert availability.payload["ends_on"] == "2026-11-14"
    assert availability.payload["note"] == TIME_OFF_NOTE
    exceptions = run_sql(
        migrated_url,
        "SELECT starts_on::text FROM tutor_availability_exceptions"
        " WHERE tutor_user_id = :id",
        {"id": tutor},
    )
    assert [e.starts_on for e in exceptions] == ["2026-11-18"]  # #299's day off
    [group_occurrence] = run_sql(
        migrated_url,
        "SELECT state, tutor_user_id FROM class_sessions WHERE id = :id",
        {"id": session_id(migrated_url, "2026-11-14 10:00")},
    )
    assert tuple(group_occurrence) == ("confirmed", tutor)

    # The #303 notifications, by type and recipient.
    notifications = run_sql(
        migrated_url,
        "SELECT n.notification_type, u.username, count(*) AS n FROM notifications n"
        " JOIN users u ON u.id = n.recipient_user_id"
        " WHERE n.notification_type IN ('scheduling.tutor_substituted',"
        " 'scheduling.session_tutor_changed', 'scheduling.session_cancelled')"
        " OR u.username = 'demo.tutor2'"
        " GROUP BY 1, 2 ORDER BY 1, 2",
    )
    assert [tuple(n) for n in notifications] == [
        ("scheduling.offer_received", "demo.tutor2", 2),
        ("scheduling.session_cancelled", "demo.guardian", 1),
        ("scheduling.session_cancelled", "demo.student", 1),
        ("scheduling.session_cancelled", "demo.tutor2", 1),
        ("scheduling.session_tutor_changed", "demo.guardian", 1),
        ("scheduling.session_tutor_changed", "demo.student", 1),
        ("scheduling.tutor_substituted", "demo.tutor", 1),
        ("staff.qualification_approved", "demo.tutor2", 2),
    ]

    # E2: no new students or families; the eighth account is demo.tutor2.
    assert count(migrated_url, "users") == 8
    assert count(migrated_url, "students") == 5
    assert count(migrated_url, "families") == 3
    assert demo_table_counts(migrated_url, ALL_TABLE_COUNTS) == ALL_TABLE_COUNTS


@pytest.mark.parametrize(
    "now",
    [
        datetime(2026, 11, 8, 5, 30, tzinfo=UTC),  # Saturday 23:30 local
        datetime(2026, 11, 8, 18, 0, tzinfo=UTC),  # Sunday 12:00 local
    ],
    ids=["saturday-night", "sunday"],
)
def test_a_weekend_run_writes_the_same_counts(
    migrated_url, env_for, clock, capsys, now
):
    # E3: T + 2 falls on a weekend.
    clock.set(now)
    env_for("local", migrated_url)

    assert demo.main([]) == 0

    assert capsys.readouterr().out.splitlines() == FIRST_RUN_LINES
    assert demo_table_counts(migrated_url, ALL_TABLE_COUNTS) == ALL_TABLE_COUNTS


def test_a_rerun_writes_nothing_even_days_later(migrated_url, env_for, clock, capsys):
    # E4: every generator (15) prints 0 rows.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    assert demo.main([]) == 0
    capsys.readouterr()
    assert len(GENERATOR_NAMES) == 15

    assert demo.main([]) == 0
    assert capsys.readouterr().out.splitlines() == RERUN_LINES

    clock.advance(timedelta(days=10))
    assert demo.main([]) == 0
    assert capsys.readouterr().out.splitlines() == RERUN_LINES
    assert demo_table_counts(migrated_url, ALL_TABLE_COUNTS) == ALL_TABLE_COUNTS


def test_dev_writes_no_303_data(migrated_url, env_for, capsys):
    # E5.
    env_for("dev", migrated_url)

    assert demo.main([]) == 0

    assert capsys.readouterr().out.splitlines() == DEV_LINES
    for table in (
        "users",
        "course_templates",
        "session_requests",
        "class_session_substitutions",
    ):
        assert count(migrated_url, table) == 0, table


NEW_GENERATORS = [DemoSecondTutorGenerator, DemoCoursesGenerator, DemoChangesGenerator]


@pytest.mark.parametrize("app_env", ["dev", "prod", "staging"])
@pytest.mark.parametrize("generator", NEW_GENERATORS, ids=lambda g: g.name)
def test_a_new_generator_writes_nothing_outside_local(generator, app_env):
    # E5: skipped without touching the session.
    result = asyncio.run(
        generator(app_env=lambda: app_env).generate(UntouchedSession())  # type: ignore[arg-type]
    )

    assert result == GeneratorSkipped(f"APP_ENV={app_env}; local only")


def test_a_schedule_from_an_earlier_day_is_skipped(
    migrated_url, env_for, clock, capsys
):
    # E6 / G3: the #299 run on 2026-11-04, then every generator 10 days later.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    assert demo.main([], generators=generators_before_303()) == 0
    capsys.readouterr()

    clock.advance(timedelta(days=10))
    assert demo.main([]) == 0

    written = {"identity.demo_tutor2": 1, TUTOR2_GENERATOR: 12}
    skipped = {COURSES_GENERATOR, CHANGES_GENERATOR}
    assert capsys.readouterr().out.splitlines() == [
        *RERUN_REFERENCE_LINES,
        *(
            f"{name}: skipped ({STALE_SCHEDULE})"
            if name in skipped
            else f"{name}: {written.get(name, 0)} rows"
            for name in GENERATOR_NAMES
        ),
    ]
    for table in (
        "course_templates",
        "course_cohorts",
        "class_session_substitutions",
        "session_requests",
        "session_change_requests",
        "tutor_availability_change_requests",
    ):
        assert count(migrated_url, table) == 0, table
    assert count(migrated_url, "tutor_availability_windows") == 6 + 5


def test_a_missing_schedule_fails_the_run(migrated_url, env_for, clock):
    # E6: without scheduling.demo_schedule, nothing is written.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    generators = [g for g in demo.DEMO_GENERATORS if g.name != SCHEDULE_GENERATOR]

    with pytest.raises(DemoPrerequisiteMissingError, match="demo_schedule"):
        demo.main([], generators=generators)

    assert count(migrated_url, "users") == 0
    assert count(migrated_url, "tutor_qualifications") == 0


def test_a_refused_service_call_rolls_the_run_back(migrated_url, env_for, clock):
    # E6: a plan double that puts the session request on the closure; the
    # slot check refuses it and nothing at all is written.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)

    def on_the_closure(today: date) -> DemoChangesPlan:
        closure = plan_demo_schedule(today).closure_day
        return dataclasses.replace(plan_demo_changes(today), request_day=closure)

    generators = [
        *demo.DEMO_GENERATORS[:-1],
        DemoChangesGenerator(plan=on_the_closure),
    ]
    assert generators[-2].name == COURSES_GENERATOR
    with pytest.raises(SlotUnavailableError):
        demo.main([], generators=generators)

    assert count(migrated_url, "users") == 0
    assert count(migrated_url, "class_sessions") == 0
    assert count(migrated_url, "course_templates") == 0


def test_the_303_counts_cover_its_new_tables():
    # E8: the four #303 generators and the tables only #303 writes.
    assert NEW_303_GENERATORS == (
        "identity.demo_tutor2",
        TUTOR2_GENERATOR,
        COURSES_GENERATOR,
        CHANGES_GENERATOR,
    )
    assert GENERATOR_NAMES[-3:] == list(NEW_303_GENERATORS[1:])
    assert DEMO_303_TABLE_COUNTS["course_templates"] == 1


# --- E7: what each account sees -------------------------------------------------------


def _local(item: dict[str, Any]) -> str:
    starts = datetime.fromisoformat(item["starts_at"]).astimezone(UTC)
    return (starts - timedelta(hours=6)).strftime("%Y-%m-%d %H:%M")


def _ok(response: Any) -> Any:
    assert response.status_code == 200, response.text
    return response.json()


def _notification_types(client: TestClient, cookie: Any) -> list[str]:
    body = _ok(get(client, cookie, "/api/v1/notifications", limit=100))
    return [item["notification_type"] for item in body["items"]]


def test_every_account_sees_the_303_data(migrated_url, env_for, clock):
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    assert demo.main([]) == 0
    labels = student_labels(migrated_url)
    ids = {label: str(student) for student, label in labels.items()}
    tutor = user_id(migrated_url, "demo.tutor")
    tutor2 = user_id(migrated_url, "demo.tutor2")
    d8 = str(session_id(migrated_url, D8))
    confirmed_cohort = {s for s in COHORT_STARTS if s != CANCELLED}

    with auth_client(migrated_url) as client:
        for username in ("demo.owner", "demo.admin"):
            cookie = signed_in(client, username)
            templates = _ok(get(client, cookie, "/api/v1/course-templates"))
            assert [t["name"] for t in templates["items"]] == [TEMPLATE_NAME]
            [cohort] = _ok(get(client, cookie, "/api/v1/cohorts"))["items"]
            assert (
                cohort["name"],
                cohort["enrolled_count"],
                cohort["status"],
                cohort["tutor"]["id"],
                cohort["assignment_status"],
            ) == (COHORT_NAME, 2, "upcoming", str(tutor2), "accepted")
            approvals = _ok(get(client, cookie, "/api/v1/approval-requests", limit=100))
            assert sorted(item["request_type"] for item in approvals["items"]) == [
                "scheduling.change_request",
                "scheduling.offer_reassignment",
                "scheduling.session_request",
                "staff.availability_change",
                "staff.qualification",
            ]
            [substitution] = _ok(
                get(client, cookie, f"/api/v1/class-sessions/{d8}/substitutions")
            )["items"]
            assert (
                substitution["status"],
                substitution["original_tutor"]["id"],
                substitution["substitute_tutor"]["id"],
            ) == ("accepted", str(tutor), str(tutor2))

        # demo.tutor2: My courses, its schedule, its students and the bell.
        cookie = signed_in(client, "demo.tutor2")
        [cohort] = _ok(get(client, cookie, "/api/v1/cohorts"))["items"]
        assert cohort["name"] == COHORT_NAME
        schedule = ranged(client, cookie, f"/api/v1/tutors/{tutor2}/schedule")
        shown = {_local(item): item["state"] for item in schedule}
        assert {s for s, state in shown.items() if state == "confirmed"} == {
            *confirmed_cohort,
            D8,
        }
        assert set(shown) - confirmed_cohort - {D8} == TUTOR2_CANCELLED_SHOWN
        students = _ok(get(client, cookie, "/api/v1/students", limit=100))
        assert {item["id"] for item in students["items"]} == {ids["S"], ids["A"]}
        unread = _ok(get(client, cookie, "/api/v1/notifications/unread-count"))
        # 2 qualification approvals, 2 offers received, 1 cancellation.
        assert unread["count"] == 5

        # demo.tutor: D(8) gone from the schedule, one pending change, the
        # substitution notice.
        cookie = signed_in(client, "demo.tutor")
        schedule = ranged(client, cookie, f"/api/v1/tutors/{tutor}/schedule")
        assert D8 not in {_local(item) for item in schedule}
        assert len(schedule) == 22 - 1
        availability = _ok(get(client, cookie, f"/api/v1/tutors/{tutor}/availability"))
        [pending] = availability["pending_changes"]
        assert pending["status"] == "pending"
        assert "scheduling.tutor_substituted" in _notification_types(client, cookie)
        unread = _ok(get(client, cookie, "/api/v1/notifications/unread-count"))
        assert unread["count"] == 9 + 1

        # demo.student: the cohort, its confirmed sessions on S's schedule.
        cookie = signed_in(client, "demo.student")
        [cohort] = _ok(get(client, cookie, "/api/v1/cohorts"))["items"]
        assert cohort["name"] == COHORT_NAME
        own = ranged(client, cookie, f"/api/v1/students/{ids['S']}/schedule")
        course = {_local(i): i["state"] for i in own if i["kind"] == "course"}
        assert course == FAMILY_COURSE_SESSIONS
        # #299's 19, the D(8) occurrence still there with demo.tutor2.
        d8_item = next(i for i in own if i["id"] == d8)
        assert d8_item["tutor"]["display_name"] == "Demo Second Tutor"

        # demo.guardian: the two requests, the cohort and the cancellation.
        cookie = signed_in(client, "demo.guardian")
        [request] = _ok(get(client, cookie, "/api/v1/session-requests"))["items"]
        assert request["status"] == "pending"
        [change] = _ok(get(client, cookie, "/api/v1/change-requests"))["items"]
        assert (change["status"], change["kind"]) == ("pending", "reschedule")
        [cohort] = _ok(get(client, cookie, "/api/v1/cohorts"))["items"]
        assert cohort["name"] == COHORT_NAME
        assert "scheduling.session_cancelled" in _notification_types(client, cookie)


TUTOR2_CANCELLED_SHOWN: set[str] = {CANCELLED}
"""Whether the tutor schedule shows the cancelled cohort class (pinned)."""
FAMILY_COURSE_SESSIONS: dict[str, str] = {
    starts: "cancelled" if starts == CANCELLED else "confirmed"
    for starts in COHORT_STARTS
}
"""The cohort sessions on S's schedule (the family-visibility rule, pinned)."""
