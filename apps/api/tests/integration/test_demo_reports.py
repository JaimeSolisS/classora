"""The #354 progress reports and report schedule (#354 D, E2, E7):
``academics.demo_progress_reports`` after a whole run, the #316 placeholder
job on the seed day and the next Monday, and what each demo account sees of
the #354 data."""

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from app.core.config import get_settings
from app.workflows.progress_report_schedule_tasks import (
    run_progress_report_placeholders,
)
from tests.factories import demo
from tests.factories.demo_learning import (
    HW1_FEEDBACK,
    HW1_TITLE,
    HW2_TITLE,
    HW3_TITLE,
    LP1_TITLE,
    LP2_TITLE,
    R1_NARRATIVE,
    R2_NARRATIVE,
    R3_NARRATIVE,
)
from tests.integration.auth_support import auth_client
from tests.integration.catalog_support import get
from tests.integration.conftest import run_sql
from tests.integration.demo_support import (
    FROZEN_NOW,
    PROGRESS_REPORTS_GENERATOR,
    demo_env,
    generators_through,
    migrate,
)
from tests.integration.test_demo_schedule import signed_in, student_labels

# T = Wednesday 2026-11-04: P(1) = 11-03, P(13) = 10-16; T's Monday is 11-02,
# the week before it 10-26 to 11-01, T's month starts on 11-01.
NARRATIVE = ("summary", "strengths", "areas_for_improvement", "next_steps")


@pytest.fixture
def migrated_url(fresh_database_url: str) -> str:
    return migrate(fresh_database_url)


@pytest.fixture
def env_for(monkeypatch):
    return lambda app_env, dsn: demo_env(monkeypatch, app_env, dsn)


def _narrative(values: dict[str, str]) -> tuple[str | None, ...]:
    return tuple(values.get(field) for field in NARRATIVE)


def test_a_first_run_writes_the_reports_and_the_schedule(migrated_url, env_for, clock):
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)

    assert demo.main([]) == 0

    labels = student_labels(migrated_url)
    rows = run_sql(
        migrated_url,
        "SELECT r.student_id, r.kind, r.status, r.period_start::text AS start,"
        " r.period_end::text AS end, s.name AS subject, a.username AS author,"
        " r.summary, r.strengths, r.areas_for_improvement, r.next_steps,"
        " r.facts IS NOT NULL AS frozen, r.submitted_at IS NOT NULL AS submitted,"
        " v.username AS approver, r.schedule_id, q.status AS item"
        " FROM progress_reports r JOIN users a ON a.id = r.author_user_id"
        " LEFT JOIN subjects s ON s.id = r.subject_id"
        " LEFT JOIN users v ON v.id = r.approved_by_user_id"
        " LEFT JOIN approval_requests q ON q.subject_id = r.id"
        " AND q.request_type = 'academics.progress_report'"
        " ORDER BY r.period_start",
    )
    # D2, D3: R1 approved by demo.coordinator, R2 waiting for review, R3 a
    # draft; none of them a schedule's placeholder.
    assert [
        (labels[r.student_id], r.kind, r.status, r.start, r.end, r.subject,
         r.author, (r.summary, r.strengths, r.areas_for_improvement, r.next_steps),
         r.frozen, r.submitted, r.approver, r.schedule_id, r.item)
        for r in rows
    ] == [
        ("S", "on_demand", "approved_for_family", "2026-10-16", "2026-11-03", None,
         "demo.tutor", _narrative(R1_NARRATIVE), True, True, "demo.coordinator",
         None, "approved"),
        ("S", "weekly", "submitted", "2026-10-26", "2026-11-01", "Matemáticas",
         "demo.tutor", _narrative(R2_NARRATIVE), True, True, None, None,
         "pending"),
        ("S", "monthly", "draft", "2026-11-01", "2026-11-30", "Inglés",
         "demo.tutor", _narrative(R3_NARRATIVE), False, False, None, None, None),
    ]  # fmt: skip

    # D3: the frozen facts' key numbers.
    facts = {
        kind: data
        for kind, data in run_sql(
            migrated_url, "SELECT kind, facts FROM progress_reports"
        )
    }
    r1, r2 = facts["on_demand"], facts["weekly"]
    # R1: #299 P1, P3, P4 (not recorded), H1, H3 and C1-C6; Ev1, Ev2 and the
    # two course evaluations; HW1 graded and HW2 handed in (HW3 is due after
    # the period); LP1 and LP2.
    assert r1["attendance"] == {
        "sessions": 11, "present": 7, "late": 1, "absent": 0, "not_recorded": 3
    }  # fmt: skip
    assert (r1["evaluations"]["count"], r1["evaluations"]["average_percent"]) == (
        4,
        "84.50",
    )
    assert r1["assignments"] == {
        "count": 2, "pending": 0, "missing": 0, "submitted": 1, "late": 0,
        "graded": 1, "average_percent": "90.00",
    }  # fmt: skip
    assert [
        (p["title"], p["status"], p["milestones_done"], p["milestones_total"],
         p["progress_percent"])
        for p in r1["learning_plans"]
    ] == [
        (LP1_TITLE, "active", 1, 4, "25"),
        (LP2_TITLE, "completed", 2, 3, "67"),
    ]  # fmt: skip
    # R2: Matemáticas in the week before T's week: H1, #299's P3 and P4 and
    # C4; Ev1; no homework due that week (HW2 is due on P(2)); LP1.
    assert r2["subject"]["name"] == "Matemáticas"
    assert (r2["attendance"]["sessions"], r2["attendance"]["present"]) == (4, 3)
    assert (r2["evaluations"]["count"], r2["evaluations"]["average_percent"]) == (
        1,
        "75.00",
    )
    assert r2["assignments"]["count"] == 0
    assert [p["title"] for p in r2["learning_plans"]] == [LP1_TITLE]

    # D2, D3: the weekly schedule from T's Monday, by demo.coordinator.
    schedules = run_sql(
        migrated_url,
        "SELECT x.student_id, a.username AS author, x.cadence, s.name AS subject,"
        " x.starts_on::text AS starts_on, x.is_active, c.username AS creator"
        " FROM progress_report_schedules x JOIN users a ON a.id = x.author_user_id"
        " JOIN users c ON c.id = x.created_by_user_id"
        " JOIN subjects s ON s.id = x.subject_id",
    )
    assert [
        (labels[x.student_id], x.author, x.cadence, x.subject, x.starts_on,
         x.is_active, x.creator)
        for x in schedules
    ] == [
        ("S", "demo.tutor", "weekly", "Matemáticas", "2026-11-02", True,
         "demo.coordinator"),
    ]  # fmt: skip


def test_the_placeholder_job_waits_for_the_first_week_to_end(
    migrated_url, env_for, clock
):
    # #316: on the seed day the job finds nothing to fill; the Monday after
    # T's week it adds that week's empty draft for demo.tutor.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    assert demo.main([]) == 0
    settings = get_settings()

    today = asyncio.run(run_progress_report_placeholders(settings, now=FROZEN_NOW))
    assert (today.schedules, today.created, today.failed) == (0, 0, 0)
    assert run_sql(migrated_url, "SELECT count(*) FROM progress_reports")[0][0] == 3

    # Monday 2026-11-09, 01:00 local (the 07:00 UTC tick).
    monday = datetime(2026, 11, 9, 7, 0, tzinfo=UTC)
    later = asyncio.run(run_progress_report_placeholders(settings, now=monday))
    assert (later.schedules, later.created, later.failed) == (1, 1, 0)
    placeholders = run_sql(
        migrated_url,
        "SELECT r.kind, r.status, r.period_start::text AS start, u.username"
        " FROM progress_reports r JOIN users u ON u.id = r.author_user_id"
        " WHERE r.schedule_id IS NOT NULL",
    )
    assert [tuple(p) for p in placeholders] == [
        ("weekly", "draft", "2026-11-02", "demo.tutor")
    ]


# --- E7: what each account sees -------------------------------------------------------


def _ok(response: Any) -> Any:
    assert response.status_code == 200, response.text
    return response.json()


def _plans(client: Any, cookie: Any, student: str) -> list[tuple[Any, ...]]:
    body = _ok(get(client, cookie, f"/api/v1/students/{student}/learning-plans"))
    return sorted(
        (p["title"], p["status"], p["progress"]["percent"], p["overdue_milestones"])
        for p in body["items"]
    )


PLANS = sorted(
    [
        (LP1_TITLE, "active", "25", 1),
        (LP2_TITLE, "completed", "67", 0),
    ]
)
"""B3: LP1 with one overdue milestone (M2), LP2 closed with none (#332)."""


def test_every_account_sees_the_354_data(migrated_url, env_for, clock):
    # Through #354 (M6-demo G8: the billing generators add H4 to S's
    # progress and demo.tutor's notifications).
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    assert demo.main([], generators=generators_through(PROGRESS_REPORTS_GENERATOR)) == 0
    labels = student_labels(migrated_url)
    ids = {label: str(student) for student, label in labels.items()}
    s, sib = ids["S"], ids["Sib"]
    reports = {
        kind: str(report_id)
        for kind, report_id in run_sql(
            migrated_url, "SELECT kind, id FROM progress_reports"
        )
    }
    r1, r2, r3 = reports["on_demand"], reports["weekly"], reports["monthly"]

    with auth_client(migrated_url) as client:
        # demo.tutor: the plans, the working list and its reports.
        cookie = signed_in(client, "demo.tutor")
        assert _plans(client, cookie, s) == PLANS
        working = _ok(get(client, cookie, "/api/v1/assignments"))["items"]
        assert sorted(
            (a["title"], a["counts"]["pending"], a["counts"]["submitted"],
             a["counts"]["graded"])
            for a in working
        ) == sorted(
            [(HW1_TITLE, 0, 0, 1), (HW2_TITLE, 0, 1, 0), (HW3_TITLE, 1, 0, 0)]
        )  # fmt: skip
        editable = {}
        for report_id in (r1, r2, r3):
            view = _ok(get(client, cookie, f"/api/v1/progress-reports/{report_id}"))
            editable[view["status"]] = view["can_edit"]
        assert editable == {
            "approved_for_family": False,
            "submitted": False,
            "draft": True,
        }

        # demo.coordinator: the open review item, the queue, R1's approver,
        # the schedule and S's progress tiles.
        cookie = signed_in(client, "demo.coordinator")
        items = _ok(
            get(
                client,
                cookie,
                "/api/v1/approval-requests",
                request_type="academics.progress_report",
                limit=100,
            )
        )["items"]
        assert [(i["status"], i["subject"]["report_id"]) for i in items] == [
            ("pending", r2)
        ]
        queue = _ok(get(client, cookie, "/api/v1/progress-reports"))["items"]
        assert [
            (q["id"], q["status"]) for q in queue if q["status"] == "submitted"
        ] == [(r2, "submitted")]
        approved = _ok(get(client, cookie, f"/api/v1/progress-reports/{r1}"))
        assert approved["approved"]["by"]["display_name"] == "Demo Coordinator"
        schedules = _ok(
            get(client, cookie, f"/api/v1/students/{s}/progress-report-schedules")
        )["items"]
        assert [
            (x["author"]["display_name"], x["cadence"], x["subject"]["name"],
             x["starts_on"], x["is_active"], x["ineligible_reason"],
             x["latest_period"])
            for x in schedules
        ] == [
            ("Demo Tutor", "weekly", "Matemáticas", "2026-11-02", True, None, None)
        ]  # fmt: skip
        progress = _ok(get(client, cookie, f"/api/v1/students/{s}/progress"))
        assert (
            progress["plans"]["active"],
            progress["plans"]["completed"],
            progress["plans"]["milestones_overdue"],
        ) == (1, 1, 1)
        assert (progress["homework"]["submitted"], progress["homework"]["graded"]) == (
            1,
            1,
        )
        assert progress["attendance"]["sessions"] == 11

        # demo.owner: #303's open items plus one progress report.
        cookie = signed_in(client, "demo.owner")
        approvals = _ok(get(client, cookie, "/api/v1/approval-requests", limit=100))
        assert sorted(item["request_type"] for item in approvals["items"]) == [
            "academics.progress_report",
            "scheduling.change_request",
            "scheduling.offer_reassignment",
            "scheduling.session_request",
            "staff.availability_change",
            "staff.qualification",
        ]

        # demo.student and demo.guardian: S's plans, homework, approved report
        # and progress; nothing for Sib.
        for username in ("demo.student", "demo.guardian"):
            cookie = signed_in(client, username)
            assert _plans(client, cookie, s) == PLANS
            homework = _ok(get(client, cookie, f"/api/v1/students/{s}/assignments"))
            assert sorted(
                (a["title"], a["subject"]["name"], a["submission"]["state"],
                 a["submission"]["score"], a["submission"]["feedback"])
                for a in homework["items"]
            ) == sorted(
                [
                    (HW1_TITLE, "Inglés", "graded", "9.00", HW1_FEEDBACK),
                    (HW2_TITLE, "Matemáticas", "submitted", None, None),
                    (HW3_TITLE, "Matemáticas", "pending", None, None),
                ]
            )  # fmt: skip
            family = _ok(get(client, cookie, f"/api/v1/students/{s}/progress-reports"))
            assert [item["id"] for item in family["items"]] == [r1]
            detail = _ok(
                get(client, cookie, f"/api/v1/students/{s}/progress-reports/{r1}")
            )
            assert detail["summary"] == R1_NARRATIVE["summary"]
            assert detail["facts"]["assignments"]["graded"] == 1
            assert "reason" not in str(detail)
            for hidden in (r2, r3):
                response = get(
                    client, cookie, f"/api/v1/students/{s}/progress-reports/{hidden}"
                )
                assert response.status_code == 404, response.text
            # E1: S's only graded homework is in Inglés, in P(5)'s month.
            grades = _ok(get(client, cookie, f"/api/v1/students/{s}/progress"))[
                "grades"
            ]
            assert (
                grades["assignment_count"],
                grades["assignment_average_percent"],
            ) == (
                1,
                "90.00",
            )
            homework_rows = [
                (row["month"], row["subject"]["name"], row["assignment_count"],
                 row["assignment_average_percent"])
                for row in grades["by_month"]
                if row["assignment_count"]
            ]  # fmt: skip
            assert homework_rows == [("2026-10", "Inglés", 1, "90.00")]
            assert all(
                row["assignment_average_percent"] is None
                for row in grades["by_month"]
                if row["subject"]["name"] == "Matemáticas"
            )

        # demo.guardian for Sib: no plans, homework or reports.
        assert _plans(client, cookie, sib) == []
        empty = _ok(get(client, cookie, f"/api/v1/students/{sib}/assignments"))
        assert empty["items"] == []
        empty = _ok(get(client, cookie, f"/api/v1/students/{sib}/progress-reports"))
        assert empty["items"] == []

        # demo.finance: S's plans, homework and reports are not readable.
        cookie = signed_in(client, "demo.finance")
        for path in (
            f"/api/v1/students/{s}/learning-plans",
            f"/api/v1/students/{s}/assignments",
            f"/api/v1/students/{s}/progress-reports",
            f"/api/v1/progress-reports/{r1}",
        ):
            assert get(client, cookie, path).status_code == 404, path
        for path in (
            "/api/v1/assignments",
            f"/api/v1/students/{s}/progress-report-schedules",
        ):
            assert get(client, cookie, path).status_code == 403, path
