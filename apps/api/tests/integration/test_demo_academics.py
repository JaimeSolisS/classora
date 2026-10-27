"""The #353 notes and evaluations (#353 D, E, F2-F7): ``academics.demo_notes``,
``academics.demo_evaluations``, the whole run with the four #353
generators, and what each demo account sees after it."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core import clock as app_clock
from tests.factories import demo
from tests.factories.base import GeneratorSkipped
from tests.factories.demo_academics import (
    COHORT_NAME,
    EV1_FEEDBACK,
    EV2_FEEDBACK,
    FINAL_A_FEEDBACK,
    FINAL_S_FEEDBACK,
    N1_BODY,
    N2_BODY,
    N3_BODY,
    DemoEvaluationsGenerator,
    DemoHeldSessionsGenerator,
    DemoNotesGenerator,
)
from tests.factories.demo_changes import STALE_SCHEDULE
from tests.factories.demo_past_cohort import DemoPastCohortGenerator
from tests.integration.auth_support import auth_client
from tests.integration.catalog_support import get
from tests.integration.conftest import run_sql
from tests.integration.demo_support import (
    ALL_TABLE_COUNTS,
    CHANGES_GENERATOR,
    DEMO_353_TABLE_COUNTS,
    DEV_LINES,
    FIRST_RUN_LINES,
    FROZEN_NOW,
    GENERATOR_NAMES,
    NEW_353_GENERATORS,
    RERUN_LINES,
    RERUN_REFERENCE_LINES,
    UntouchedSession,
    count,
    demo_env,
    demo_table_counts,
    generators_through,
    migrate,
)
from tests.integration.test_demo_schedule import (
    ZONE,
    session_id,
    signed_in,
    student_labels,
)

# T = Wednesday 2026-11-04 (see test_demo_held_sessions and
# test_demo_past_cohort for the dates).
H1 = "2026-10-27 16:00"
H2 = "2026-10-26 16:00"
H3 = "2026-10-23 16:00"
C3 = "2026-10-23 18:00"
C6 = "2026-11-03 18:00"
COHORT = [
    "2026-10-16 18:00",
    "2026-10-20 18:00",
    C3,
    "2026-10-27 18:00",
    "2026-10-30 18:00",
    C6,
]
NEW_TABLES = (
    "class_session_attendance",
    "session_closeouts",
    "session_closeout_approvals",
    "session_notes",
    "evaluation_templates",
    "evaluations",
)


@pytest.fixture
def migrated_url(fresh_database_url: str) -> str:
    return migrate(fresh_database_url)


@pytest.fixture
def env_for(monkeypatch):
    return lambda app_env, dsn: demo_env(monkeypatch, app_env, dsn)


def test_a_first_run_writes_the_notes_and_evaluations(
    migrated_url, env_for, clock, capsys
):
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)

    assert demo.main([]) == 0

    assert capsys.readouterr().out.splitlines() == FIRST_RUN_LINES
    assert app_clock.now() == FROZEN_NOW
    labels = student_labels(migrated_url)

    # D2: N1 and N2 shared with the family, N3 internal.
    notes = run_sql(
        migrated_url,
        "SELECT to_char(cs.starts_at AT TIME ZONE :zone, 'YYYY-MM-DD HH24:MI')"
        " AS starts, n.student_id,"
        " u.username AS author, n.body, n.visibility,"
        " n.published_at IS NOT NULL AS published"
        " FROM session_notes n JOIN class_sessions cs ON cs.id = n.class_session_id"
        " JOIN users u ON u.id = n.author_user_id ORDER BY n.body",
        {"zone": ZONE},
    )
    assert sorted(
        (n.starts, labels[n.student_id], n.author, n.body, n.visibility, n.published)
        for n in notes
    ) == sorted(
        [
            (H1, "S", "demo.tutor", N1_BODY, "family", True),
            (C6, "S", "demo.tutor2", N2_BODY, "family", True),
            (H2, "Sib", "demo.tutor", N3_BODY, "internal", False),
        ]
    )

    # E2: the templates and their criteria.
    templates = run_sql(
        migrated_url,
        "SELECT t.name, t.kind, s.name AS subject, t.is_active,"
        " array_agg(c.title || ' ' || c.max_score ORDER BY c.position) AS criteria"
        " FROM evaluation_templates t"
        " LEFT JOIN subjects s ON s.id = t.subject_id"
        " JOIN evaluation_template_criteria c ON c.template_id = t.id"
        " GROUP BY t.id, s.name ORDER BY t.name",
    )
    assert [tuple(t) for t in templates] == [
        ("Diagnóstico de matemáticas", "diagnostic", "Matemáticas", True,
         ["Operaciones con fracciones 10.00", "Ecuaciones de primer grado 10.00"]),
        ("Examen escrito", "ongoing", None, True, ["Calificación del examen 100.00"]),
    ]  # fmt: skip

    # E3, E4: the evaluations, their state, percentage and links.
    rows = run_sql(
        migrated_url,
        "SELECT e.student_id, u.username AS author, e.template_name,"
        " s.name || ' (' || l.name || ')' AS subject, e.status, e.score_percent,"
        " to_char(cs.starts_at AT TIME ZONE :zone, 'YYYY-MM-DD HH24:MI')"
        " AS class_starts, e.evaluated_on::text AS evaluated_on,"
        " e.course_assessment_title, c.name AS cohort, e.feedback,"
        " (SELECT array_agg(x.score ORDER BY x.position) FROM evaluation_scores x"
        "  WHERE x.evaluation_id = e.id) AS scores"
        " FROM evaluations e JOIN users u ON u.id = e.author_user_id"
        " JOIN subjects s ON s.id = e.subject_id"
        " JOIN education_levels l ON l.id = s.education_level_id"
        " JOIN class_sessions cs ON cs.id = e.class_session_id"
        " LEFT JOIN course_cohorts c ON c.id = e.cohort_id",
        {"zone": ZONE},
    )
    math, english, primary = (
        "Matemáticas (Secundaria)",
        "Inglés (Secundaria)",
        "Matemáticas (Primaria)",
    )
    got = sorted(
        (
            labels[r.student_id], r.author, r.template_name, r.subject, r.status,
            r.score_percent, r.class_starts, r.evaluated_on,
            r.course_assessment_title, r.cohort, r.feedback,
            [Decimal(x) for x in r.scores],
        )
        for r in rows
    )  # fmt: skip
    d = Decimal
    assert got == sorted(
        [
            ("S", "demo.tutor", "Diagnóstico de matemáticas", math, "final",
             d("75.00"), H1, "2026-10-27", None, None, EV1_FEEDBACK, [d(7), d(8)]),
            ("S", "demo.tutor", "Examen escrito", english, "final", d("88.00"), H3,
             "2026-10-23", None, None, EV2_FEEDBACK, [d(88)]),
            ("Sib", "demo.tutor", "Examen escrito", primary, "draft", None, H2,
             "2026-10-26", None, None, None, [d(80)]),
            ("S", "demo.tutor2", "Examen escrito", math, "final", d("90.00"), C3,
             "2026-10-23", "Examen parcial", COHORT_NAME, None, [d(90)]),
            ("A", "demo.tutor2", "Examen escrito", math, "final", d("55.00"), C3,
             "2026-10-23", "Examen parcial", COHORT_NAME, None, [d(55)]),
            ("B", "demo.tutor2", "Examen escrito", math, "final", d("70.00"), C3,
             "2026-10-23", "Examen parcial", COHORT_NAME, None, [d(70)]),
            ("S", "demo.tutor2", "Examen escrito", math, "final", d("85.00"), C6,
             "2026-11-03", "Examen final", COHORT_NAME, FINAL_S_FEEDBACK, [d(85)]),
            ("A", "demo.tutor2", "Examen escrito", math, "final", d("45.00"), C6,
             "2026-11-03", "Examen final", COHORT_NAME, FINAL_A_FEEDBACK, [d(45)]),
            ("B", "demo.tutor2", "Examen escrito", math, "final", d("72.00"), C6,
             "2026-11-03", "Examen final", COHORT_NAME, None, [d(72)]),
        ]
    )  # fmt: skip

    # The #353 notifications, by type and recipient.
    notifications = run_sql(
        migrated_url,
        "SELECT n.notification_type, u.username, count(*) AS n FROM notifications n"
        " JOIN users u ON u.id = n.recipient_user_id"
        " WHERE n.notification_type LIKE 'academics.%' GROUP BY 1, 2 ORDER BY 1, 2",
    )
    assert [tuple(n) for n in notifications] == [
        ("academics.closeout_approved", "demo.tutor", 3),
        ("academics.closeout_approved", "demo.tutor2", 6),
        ("academics.evaluation_finalized", "demo.guardian", 4),
        ("academics.evaluation_finalized", "demo.student", 4),
        ("academics.learning_note_published", "demo.guardian", 2),
        ("academics.learning_note_published", "demo.student", 2),
    ]
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
    # F3.
    clock.set(now)
    env_for("local", migrated_url)

    assert demo.main([]) == 0

    assert capsys.readouterr().out.splitlines() == FIRST_RUN_LINES
    assert demo_table_counts(migrated_url, ALL_TABLE_COUNTS) == ALL_TABLE_COUNTS
    assert app_clock.now() == now


def test_a_rerun_writes_nothing_even_days_later(migrated_url, env_for, clock, capsys):
    # F4: every generator (19) prints 0 rows.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    assert demo.main([]) == 0
    capsys.readouterr()
    assert len(GENERATOR_NAMES) == 19

    assert demo.main([]) == 0
    assert capsys.readouterr().out.splitlines() == RERUN_LINES

    clock.advance(timedelta(days=10))
    assert demo.main([]) == 0
    assert capsys.readouterr().out.splitlines() == RERUN_LINES
    assert demo_table_counts(migrated_url, ALL_TABLE_COUNTS) == ALL_TABLE_COUNTS


def test_dev_writes_no_353_data(migrated_url, env_for, capsys):
    # F5.
    env_for("dev", migrated_url)

    assert demo.main([]) == 0

    assert capsys.readouterr().out.splitlines() == DEV_LINES
    for table in NEW_TABLES:
        assert count(migrated_url, table) == 0, table


NEW_GENERATORS = [
    DemoHeldSessionsGenerator,
    DemoPastCohortGenerator,
    DemoNotesGenerator,
    DemoEvaluationsGenerator,
]


@pytest.mark.parametrize("app_env", ["dev", "prod", "staging"])
@pytest.mark.parametrize("generator", NEW_GENERATORS, ids=lambda g: g.name)
def test_a_new_generator_writes_nothing_outside_local(generator, app_env):
    # F5: skipped without touching the session.
    result = asyncio.run(
        generator(app_env=lambda: app_env).generate(UntouchedSession())  # type: ignore[arg-type]
    )

    assert result == GeneratorSkipped(f"APP_ENV={app_env}; local only")


def test_a_schedule_from_an_earlier_day_is_skipped(
    migrated_url, env_for, clock, capsys
):
    # F6 / G3: the #303 run on 2026-11-04, then every generator 10 days later.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    assert demo.main([], generators=generators_through(CHANGES_GENERATOR)) == 0
    capsys.readouterr()
    before = demo_table_counts(migrated_url, ALL_TABLE_COUNTS)

    clock.advance(timedelta(days=10))
    assert demo.main([]) == 0

    assert capsys.readouterr().out.splitlines() == [
        *RERUN_REFERENCE_LINES,
        *(
            f"{name}: skipped ({STALE_SCHEDULE})"
            if name in NEW_353_GENERATORS
            else f"{name}: 0 rows"
            for name in GENERATOR_NAMES
        ),
    ]
    assert demo_table_counts(migrated_url, ALL_TABLE_COUNTS) == before
    for table in NEW_TABLES:
        assert count(migrated_url, table) == 0, table


def test_the_353_counts_cover_its_new_tables():
    assert GENERATOR_NAMES[-4:] == list(NEW_353_GENERATORS)
    assert DEMO_353_TABLE_COUNTS["session_closeouts"] == 9
    assert DEMO_353_TABLE_COUNTS["evaluations"] == 9


# --- F7: what each account sees -------------------------------------------------------


def _ok(response: Any) -> Any:
    assert response.status_code == 200, response.text
    return response.json()


def _local(value: str) -> str:
    starts = datetime.fromisoformat(value).astimezone(UTC)
    return (starts - timedelta(hours=6)).strftime("%Y-%m-%d %H:%M")


def _completion(
    client: TestClient, cookie: Any, cohort: str, names: dict[str, str]
) -> dict[str, tuple[Any, ...]]:
    body = _ok(get(client, cookie, f"/api/v1/cohorts/{cohort}/completion"))
    assert body["cohort_status"] == "ended"
    result: dict[str, tuple[Any, ...]] = {}
    for row in body["students"]:
        final = next(
            a for a in row["assessments"]["items"] if a["title"] == "Examen final"
        )
        result[names[row["student"]["id"]]] = (
            row["status"],
            row["attendance"]["status"],
            row["attendance"]["attendance_percent"],
            final["status"],
            [m["basis"] for m in row["modules"]["items"] if m["status"] == "met"],
        )
    return result


COMPLETION = {
    "S": ("completed", "met", "100.00", "passed", ["course_covered"] * 2),
    "A": ("not_completed", "met", "83.33", "failed", ["course_covered"] * 2),
    "B": ("not_completed", "not_met", "66.67", "passed", ["course_covered"] * 2),
}
"""E5: the finished cohort's completion."""


def _session_log(client: TestClient, cookie: Any, student: str) -> dict[str, Any]:
    body = _ok(get(client, cookie, f"/api/v1/students/{student}/session-log", limit=50))
    return {_local(item["starts_at"]): item for item in body["items"]}


def test_every_account_sees_the_353_data(migrated_url, env_for, clock):
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    assert demo.main([]) == 0
    labels = student_labels(migrated_url)
    ids = {label: str(student) for student, label in labels.items()}
    names = {str(student): label for student, label in labels.items()}
    h1, h2, h3 = (str(session_id(migrated_url, s)) for s in (H1, H2, H3))
    [cohort] = run_sql(
        migrated_url,
        "SELECT id FROM course_cohorts WHERE name = :n",
        {"n": COHORT_NAME},
    )
    past = str(cohort.id)

    with auth_client(migrated_url) as client:
        for username in ("demo.coordinator", "demo.owner"):
            cookie = signed_in(client, username)
            closeout = _ok(get(client, cookie, f"/api/v1/class-sessions/{h1}/closeout"))
            record = closeout["closeout"]
            assert (
                record["status"],
                record["approval"]["revision"],
                record["approval"]["approved_by"]["display_name"],
                record["summary"]["topics"],
                record["actual_minutes"],
            ) == (
                "approved",
                1,
                "Demo Coordinator",
                "Despeje de ecuaciones de primer grado",
                60,
            )
            overdue = _ok(get(client, cookie, "/api/v1/closeouts/overdue"))
            assert overdue["total"] == 0
            approvals = _ok(get(client, cookie, "/api/v1/approval-requests", limit=100))
            types = sorted(item["request_type"] for item in approvals["items"])
            assert "academics.session_closeout" not in types
            if username == "demo.owner":
                # Unchanged from #303: #299's two items and #303's three.
                assert types == [
                    "scheduling.change_request",
                    "scheduling.offer_reassignment",
                    "scheduling.session_request",
                    "staff.availability_change",
                    "staff.qualification",
                ]
            templates = _ok(get(client, cookie, "/api/v1/evaluation-templates"))
            assert [t["name"] for t in templates["items"]] == [
                "Diagnóstico de matemáticas",
                "Examen escrito",
            ]
            assert _completion(client, cookie, past, names) == COMPLETION
            progress = _ok(get(client, cookie, f"/api/v1/students/{ids['S']}/progress"))
            # P1, P3, P4 (not recorded), H1, H3 and C1-C6.
            assert (
                progress["attendance"]["sessions"],
                progress["attendance"]["present"],
                progress["attendance"]["late"],
                progress["attendance"]["absent"],
                progress["attendance"]["not_recorded"],
                progress["attendance"]["attendance_percent"],
            ) == (11, 7, 1, 0, 3, "100.00")
            # Ev1, Ev2 and the two course evaluations: (75 + 88 + 90 + 85) / 4.
            assert (
                progress["grades"]["evaluation_count"],
                progress["grades"]["evaluation_average_percent"],
            ) == (4, "84.50")

        # demo.tutor: the closeouts, the evaluations (with the draft), N1 and
        # the bell.
        cookie = signed_in(client, "demo.tutor")
        for held in (h1, h2, h3):
            closeout = _ok(
                get(client, cookie, f"/api/v1/class-sessions/{held}/closeout")
            )
            assert closeout["closeout"]["status"] == "approved"
        own = _ok(get(client, cookie, f"/api/v1/students/{ids['S']}/evaluations"))
        mine = sorted(
            (e["template"]["name"], e["status"]) for e in own["items"] if e["is_own"]
        )
        assert mine == [
            ("Diagnóstico de matemáticas", "final"),
            ("Examen escrito", "final"),
        ]
        sibling = _ok(get(client, cookie, f"/api/v1/students/{ids['Sib']}/evaluations"))
        assert [(e["template"]["name"], e["status"]) for e in sibling["items"]] == [
            ("Examen escrito", "draft")
        ]
        notes = _ok(get(client, cookie, f"/api/v1/class-sessions/{h1}/notes"))
        assert [(n["body"], n["visibility"]) for n in notes["items"]] == [
            (N1_BODY, "family")
        ]
        bell = _ok(get(client, cookie, "/api/v1/notifications", limit=100))
        types = [item["notification_type"] for item in bell["items"]]
        assert types.count("academics.closeout_approved") == 3

        # demo.tutor2: the closeouts and B among its students. A tutor reads a
        # cohort only while its series produces classes (#246 A2), so the
        # finished cohort and its completion are 404 for its former tutor.
        cookie = signed_in(client, "demo.tutor2")
        gone = get(client, cookie, f"/api/v1/cohorts/{past}/completion")
        assert gone.status_code == 404, gone.text
        every = _ok(get(client, cookie, "/api/v1/cohorts", status="all"))["items"]
        assert [c["name"] for c in every] == ["Examen de ingreso, grupo 1"]
        for starts in COHORT:
            sid = session_id(migrated_url, starts)
            closeout = _ok(
                get(client, cookie, f"/api/v1/class-sessions/{sid}/closeout")
            )
            assert closeout["closeout"]["status"] == "approved"
        students = _ok(get(client, cookie, "/api/v1/students", limit=100))
        assert {item["id"] for item in students["items"]} == {
            ids["S"],
            ids["A"],
            ids["B"],
        }

        # demo.student and demo.guardian: S's cohorts, completion, log, notes
        # and history.
        for username in ("demo.student", "demo.guardian"):
            cookie = signed_in(client, username)
            current = _ok(get(client, cookie, "/api/v1/cohorts"))["items"]
            assert [c["name"] for c in current] == ["Examen de ingreso, grupo 1"]
            every = _ok(get(client, cookie, "/api/v1/cohorts", status="all"))["items"]
            assert {c["name"] for c in every} == {
                COHORT_NAME,
                "Examen de ingreso, grupo 1",
            }
            assert _completion(client, cookie, past, names) == {"S": COMPLETION["S"]}
            log = _session_log(client, cookie, ids["S"])
            closed = {
                starts: (item["attendance"], item["summary"]["topics"],
                         item["published_note_count"])
                for starts, item in log.items()
                if item["summary"] is not None
            }  # fmt: skip
            assert set(closed) == {H1, H3, *COHORT}
            assert closed[H1] == ("present", "Despeje de ecuaciones de primer grado", 1)
            assert closed[C3][0] == "late"
            assert closed[C6] == ("present", "Examen final", 1)
            assert all(closed[s][2] == 0 for s in closed if s not in {H1, C6})
            assert "Fracciones equivalentes" not in str(log)  # offers.spec step 12
            learning = _ok(
                get(client, cookie, f"/api/v1/students/{ids['S']}/learning-notes")
            )
            assert sorted(n["body"] for n in learning["items"]) == sorted(
                [N1_BODY, N2_BODY]
            )
            history = _ok(
                get(client, cookie, f"/api/v1/students/{ids['S']}/academic-history")
            )
            assert sorted(
                (e["template_name"], e["score_percent"], (e["course"] or {}).get(
                    "assessment_title"))
                for e in history["entries"]
            ) == [
                ("Diagnóstico de matemáticas", "75.00", None),
                ("Examen escrito", "85.00", "Examen final"),
                ("Examen escrito", "88.00", None),
                ("Examen escrito", "90.00", "Examen parcial"),
            ]  # fmt: skip

        # demo.guardian for Sib: H2 with its summary, no shared note, no Ev3.
        log = _session_log(client, cookie, ids["Sib"])
        assert log[H2]["attendance"] == "late"
        assert log[H2]["summary"]["topics"] == "Sumas y restas de fracciones"
        assert log[H2]["published_note_count"] == 0
        learning = _ok(
            get(client, cookie, f"/api/v1/students/{ids['Sib']}/learning-notes")
        )
        assert learning["items"] == []
        history = _ok(
            get(client, cookie, f"/api/v1/students/{ids['Sib']}/academic-history")
        )
        assert history["entries"] == []

        # demo.finance: the cohort exists, its completion is refused.
        cookie = signed_in(client, "demo.finance")
        refused = get(client, cookie, f"/api/v1/cohorts/{past}/completion")
        assert refused.status_code == 403, refused.text
