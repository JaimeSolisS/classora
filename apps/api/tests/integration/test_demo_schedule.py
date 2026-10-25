"""The demo schedule (#299 D, E2-E7): ``scheduling.demo_schedule`` and what
each demo account sees after a first run."""

import dataclasses
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.modules.scheduling.errors import SchedulingConflictError
from tests.factories import demo
from tests.factories.demo_schedule import (
    DECLINE_NOTE,
    DemoScheduleGenerator,
    DemoSchedulePlan,
    plan_demo_schedule,
)
from tests.factories.identity import DEMO_ACCOUNTS
from tests.integration.auth_support import auth_client, login, only_set_cookie
from tests.integration.catalog_support import get
from tests.integration.conftest import run_sql
from tests.integration.demo_support import (
    DEMO_TABLE_COUNTS,
    DEV_LINES,
    FIRST_RUN_LINES,
    FROZEN_NOW,
    RERUN_LINES,
    count,
    demo_env,
    demo_table_counts,
    migrate,
)
from tests.integration.test_demo_accounts import accept_the_notice

ZONE = "America/Mexico_City"
PASSWORDS = {account.username: account.password for account in DEMO_ACCOUNTS}

MATH_SEC = "Matemáticas (Secundaria)"
MATH_PRI = "Matemáticas (Primaria)"
ENGLISH = "Inglés (Secundaria)"
SPANISH = "Español (Primaria)"
SALA = "Sala de usos múltiples"
TUTOR = "demo.tutor"

SERIES_DATES = [date(2026, 11, 10) + timedelta(weeks=k) for k in range(8)]
GROUP_DATES = [date(2026, 11, 7) + timedelta(weeks=k) for k in range(8)]

EXPECTED_SESSIONS = sorted(
    [
        # D3: past, written directly (P(1)…P(4) before Wednesday 2026-11-04).
        ("individual", "completed", ("S",), MATH_SEC, TUTOR, "Aula 1",
         "2026-11-03 16:00", "17:00"),
        ("individual", "completed", ("Sib",), MATH_PRI, TUTOR, "Aula 1",
         "2026-11-02 16:00", "17:00"),
        ("individual", "completed", ("S",), MATH_SEC, TUTOR, "Aula 1",
         "2026-10-30 16:00", "17:00"),
        ("individual", "completed", ("S",), ENGLISH, TUTOR, "Aula 1",
         "2026-10-29 17:00", "18:00"),
        # D4: U1-U5 on D(1) = Fri 11-06, D(2) = Mon 11-09, D(4) = Wed 11-11,
        # D(5) = Thu 11-12.
        ("individual", "confirmed", ("S",), MATH_SEC, TUTOR, "Aula 1",
         "2026-11-06 16:00", "17:00"),
        ("individual", "confirmed", ("Sib",), MATH_PRI, TUTOR, "Aula 1",
         "2026-11-09 16:00", "17:00"),
        ("individual", "offered", ("S",), ENGLISH, TUTOR, None,
         "2026-11-09 18:00", "19:00"),
        ("individual", "unassigned", ("A",), MATH_SEC, None, "Aula 2",
         "2026-11-11 17:00", "18:00"),
        ("individual", "unassigned", ("B",), SPANISH, None, "Aula 2",
         "2026-11-12 15:00", "16:00"),
        # D5: Tuesdays from the D(3) closure (skipped).
        *(
            ("individual", "skipped_closure" if day == SERIES_DATES[0] else "confirmed",
             ("S",), MATH_SEC, TUTOR, SALA, f"{day} 17:00", "18:00")
            for day in SERIES_DATES
        ),
        # D6: Saturdays from Sat1 = 11-07.
        *(
            ("group", "confirmed", ("A", "S", "Sib"), MATH_SEC, TUTOR, SALA,
             f"{day} 10:00", "11:30")
            for day in GROUP_DATES
        ),
    ]
)  # fmt: skip


@pytest.fixture
def migrated_url(fresh_database_url: str) -> str:
    return migrate(fresh_database_url)


@pytest.fixture
def env_for(monkeypatch):
    return lambda app_env, dsn: demo_env(monkeypatch, app_env, dsn)


def student_labels(url: str) -> dict[Any, str]:
    """#299 D2: S, Sib, A and B by student id."""
    [s] = run_sql(
        url,
        "SELECT st.id FROM students st JOIN users u ON u.id = st.user_id"
        " WHERE u.username = 'demo.student'",
    )
    [sib] = run_sql(
        url,
        "SELECT fs.student_id FROM family_students fs WHERE fs.family_id ="
        " (SELECT family_id FROM family_students WHERE student_id = :s)"
        " AND fs.student_id <> :s",
        {"s": s.id},
    )
    pair = run_sql(
        url,
        "SELECT st.id FROM students st JOIN student_guardians sg"
        " ON sg.student_id = st.id WHERE sg.relationship = 'father'"
        " ORDER BY st.given_names, st.id",
    )
    return {s.id: "S", sib.student_id: "Sib", pair[0].id: "A", pair[1].id: "B"}


def session_rows(url: str) -> list[tuple[Any, ...]]:
    labels = student_labels(url)
    rows = run_sql(
        url,
        "SELECT cs.id, cs.kind, cs.state, sub.name || ' (' || l.name || ')' AS subject,"
        " u.username, c.name AS room,"
        " to_char(cs.starts_at AT TIME ZONE :zone, 'YYYY-MM-DD HH24:MI') AS starts,"
        " to_char(cs.ends_at AT TIME ZONE :zone, 'HH24:MI') AS ends,"
        " array_agg(p.student_id) AS students"
        " FROM class_sessions cs"
        " JOIN subjects sub ON sub.id = cs.subject_id"
        " JOIN education_levels l ON l.id = sub.education_level_id"
        " LEFT JOIN users u ON u.id = cs.tutor_user_id"
        " LEFT JOIN classrooms c ON c.id = cs.classroom_id"
        " JOIN class_session_participants p ON p.class_session_id = cs.id"
        " GROUP BY cs.id, sub.name, l.name, u.username, c.name",
        {"zone": ZONE},
    )
    return sorted(
        (
            r.kind,
            r.state,
            tuple(sorted(labels[student] for student in r.students)),
            r.subject,
            r.username,
            r.room,
            r.starts,
            r.ends,
        )
        for r in rows
    )


def session_id(url: str, starts: str) -> Any:
    [row] = run_sql(
        url,
        "SELECT id FROM class_sessions"
        " WHERE to_char(starts_at AT TIME ZONE :zone, 'YYYY-MM-DD HH24:MI') = :starts",
        {"zone": ZONE, "starts": starts},
    )
    return row.id


def test_a_first_run_writes_the_schedule(migrated_url, env_for, clock, capsys):
    # E2 on Wednesday 2026-11-04, 11:00 local.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)

    assert demo.main([]) == 0

    assert capsys.readouterr().out.splitlines() == FIRST_RUN_LINES
    assert session_rows(migrated_url) == EXPECTED_SESSIONS

    offers = run_sql(
        migrated_url,
        "SELECT o.scope, o.status, o.decline_note, o.class_series_id IS NOT NULL"
        " AS series, (SELECT count(*) FROM assignment_offer_sessions s"
        "  WHERE s.offer_id = o.id) AS sessions"
        " FROM assignment_offers o ORDER BY o.scope, o.status, sessions",
    )
    assert [tuple(o) for o in offers] == [
        ("series", "accepted", None, True, 7),
        ("series", "accepted", None, True, 8),
        ("session", "accepted", None, False, 1),
        ("session", "accepted", None, False, 1),
        ("session", "declined", DECLINE_NOTE, False, 1),
        ("session", "pending", None, False, 1),
    ]
    u3 = session_id(migrated_url, "2026-11-09 18:00")
    [pending] = run_sql(
        migrated_url,
        "SELECT s.class_session_id FROM assignment_offers o"
        " JOIN assignment_offer_sessions s ON s.offer_id = o.id"
        " WHERE o.status = 'pending'",
    )
    assert pending.class_session_id == u3

    # Exactly one open reassignment item, for U4's declined offer.
    u4 = session_id(migrated_url, "2026-11-11 17:00")
    [item] = run_sql(
        migrated_url,
        "SELECT a.status, s.class_session_id FROM approval_requests a"
        " JOIN assignment_offers o ON o.id = a.subject_id"
        " JOIN assignment_offer_sessions s ON s.offer_id = o.id"
        " WHERE a.request_type = 'scheduling.offer_reassignment'",
    )
    assert (item.status, item.class_session_id) == ("pending", u4)

    [group] = run_sql(migrated_url, "SELECT name, max_students FROM class_groups")
    assert tuple(group) == ("Club de Matemáticas", 6)
    assert count(migrated_url, "group_enrollments") == 3
    # G4: no new users, students or families.
    assert count(migrated_url, "users") == 7
    assert count(migrated_url, "students") == 5
    assert count(migrated_url, "families") == 3
    assert demo_table_counts(migrated_url) == DEMO_TABLE_COUNTS


@pytest.mark.parametrize(
    "now",
    [
        datetime(2026, 11, 8, 5, 30, tzinfo=UTC),  # Saturday 23:30 local
        datetime(2026, 11, 8, 18, 0, tzinfo=UTC),  # Sunday 12:00 local
    ],
    ids=["saturday-night", "sunday"],
)
def test_a_weekend_run_writes_the_same_shape(migrated_url, env_for, clock, capsys, now):
    # E3: T + 2 falls on a weekend.
    clock.set(now)
    env_for("local", migrated_url)

    assert demo.main([]) == 0

    assert capsys.readouterr().out.splitlines() == FIRST_RUN_LINES
    assert demo_table_counts(migrated_url) == DEMO_TABLE_COUNTS


def test_a_rerun_writes_nothing_even_days_later(migrated_url, env_for, clock, capsys):
    # E4.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    assert demo.main([]) == 0
    capsys.readouterr()
    first = session_rows(migrated_url)

    assert demo.main([]) == 0
    assert capsys.readouterr().out.splitlines() == RERUN_LINES

    clock.advance(timedelta(days=10))
    assert demo.main([]) == 0
    assert capsys.readouterr().out.splitlines() == RERUN_LINES
    assert demo_table_counts(migrated_url) == DEMO_TABLE_COUNTS
    assert session_rows(migrated_url) == first


def test_dev_writes_no_demo_data(migrated_url, env_for, capsys):
    # E5.
    env_for("dev", migrated_url)

    assert demo.main([]) == 0

    assert capsys.readouterr().out.splitlines() == DEV_LINES
    for table in (
        "subjects",
        "classrooms",
        "tutor_profiles",
        "class_sessions",
        "notifications",
    ):
        assert count(migrated_url, table) == 0, table


def test_a_refused_service_call_rolls_the_run_back(migrated_url, env_for, clock):
    # E6: a plan that puts U1 on the closure day; create_class_session refuses
    # (center_closed, no override) and nothing is written.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)

    def on_the_closure(today: date) -> DemoSchedulePlan:
        plan = plan_demo_schedule(today)
        u1, *rest = plan.upcoming
        return dataclasses.replace(
            plan, upcoming=(dataclasses.replace(u1, day=plan.closure_day), *rest)
        )

    generators = [
        *demo.DEMO_GENERATORS[:-1],
        DemoScheduleGenerator(plan=on_the_closure),
    ]
    with pytest.raises(SchedulingConflictError):
        demo.main([], generators=generators)

    assert count(migrated_url, "users") == 0
    assert count(migrated_url, "subjects") == 0
    assert count(migrated_url, "class_sessions") == 0


# --- E7: what each account sees -------------------------------------------------------

RANGES = [
    ("2026-10-29T00:00:00-06:00", "2026-12-02T00:00:00-06:00"),
    ("2026-12-02T00:00:00-06:00", "2027-01-02T00:00:00-06:00"),
]
"""``P(4)`` to ``Sat1 + 8 weeks`` in two reads (at most 45 days each)."""


def signed_in(client: TestClient, username: str) -> Any:
    client.cookies.clear()
    response = login(client, username, PASSWORDS[username])
    assert response.status_code == 200, response.text
    client.cookies.clear()
    cookie = only_set_cookie(response)
    accept_the_notice(client, cookie)
    return cookie


def ranged(client: TestClient, cookie: Any, path: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for start, end in RANGES:
        response = get(client, cookie, path, **{"from": start, "to": end})
        assert response.status_code == 200, (path, response.text)
        body = response.json()
        assert body["truncated"] is False
        items.extend(body["items"])
    return items


def _local(item: dict[str, Any]) -> str:
    starts = datetime.fromisoformat(item["starts_at"]).astimezone(UTC)
    return (starts - timedelta(hours=6)).strftime("%Y-%m-%d %H:%M")


def test_every_account_sees_its_demo_data(migrated_url, env_for, clock):
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    assert demo.main([]) == 0
    labels = student_labels(migrated_url)
    ids = {label: student for student, label in labels.items()}
    [tutor] = run_sql(
        migrated_url, "SELECT id FROM users WHERE username = 'demo.tutor'"
    )
    u3 = str(session_id(migrated_url, "2026-11-09 18:00"))
    hidden_from_families = {
        "2026-11-09 18:00",  # U3 (offered)
        "2026-11-11 17:00",  # U4 (unassigned)
        "2026-11-12 15:00",  # U5 (unassigned)
        "2026-11-10 17:00",  # the series occurrence on the closure
    }

    with auth_client(migrated_url) as client:
        # Staff with schedule.view: every demo session (cancelled aside).
        for username in (
            "demo.owner",
            "demo.admin",
            "demo.coordinator",
            "demo.finance",
        ):
            cookie = signed_in(client, username)
            items = ranged(client, cookie, "/api/v1/class-sessions")
            assert len(items) == 25, username

        # The Approvals page: the pending qualification and U4's item.
        for username in ("demo.owner", "demo.admin"):
            cookie = signed_in(client, username)
            response = get(client, cookie, "/api/v1/approval-requests", limit=100)
            assert response.status_code == 200, response.text
            types = sorted(item["request_type"] for item in response.json()["items"])
            assert types == ["scheduling.offer_reassignment", "staff.qualification"]

        # demo.tutor: schedule, My offers, the bell and the students.
        cookie = signed_in(client, "demo.tutor")
        schedule = ranged(client, cookie, f"/api/v1/tutors/{tutor.id}/schedule")
        states: dict[str, int] = {}
        for item in schedule:
            states[item["state"]] = states.get(item["state"], 0) + 1
        # 4 completed; U1, U2, 7 series and 8 group occurrences; U3.
        assert states == {"completed": 4, "confirmed": 17, "offered": 1}
        offers = get(client, cookie, "/api/v1/assignment-offers", status="pending")
        assert offers.status_code == 200, offers.text
        [offer] = offers.json()["items"]
        assert [s["id"] for s in offer["sessions"]] == [u3]
        unread = get(client, cookie, "/api/v1/notifications/unread-count")
        assert unread.json()["count"] >= 3
        # 3 qualification approvals and 6 offers received.
        assert unread.json()["count"] == 9
        students = get(client, cookie, "/api/v1/students", limit=100)
        assert students.status_code == 200, students.text
        assert {item["id"] for item in students.json()["items"]} == {
            str(ids["S"]),
            str(ids["Sib"]),
            str(ids["A"]),
        }

        # demo.student: only confirmed and completed sessions of S.
        cookie = signed_in(client, "demo.student")
        own = ranged(client, cookie, f"/api/v1/students/{ids['S']}/schedule")
        # P1, P3, P4; U1; 7 series and 8 group occurrences.
        assert len(own) == 19
        assert {item["state"] for item in own} == {"completed", "confirmed"}
        assert not {_local(item) for item in own} & hidden_from_families

        # demo.guardian: each child's confirmed and completed sessions.
        cookie = signed_in(client, "demo.guardian")
        assert (
            len(ranged(client, cookie, f"/api/v1/students/{ids['S']}/schedule")) == 19
        )
        sibling = ranged(client, cookie, f"/api/v1/students/{ids['Sib']}/schedule")
        # P2; U2; 8 group occurrences.
        assert len(sibling) == 10
        assert {item["state"] for item in sibling} == {"completed", "confirmed"}
