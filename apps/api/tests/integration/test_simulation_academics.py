"""The simulated academic record against PostgreSQL (#551 HF-01c: B, C).
One ``small`` Spanish run (shared, read only) with the real clock frozen at
:data:`~tests.integration.simulation_support.FROZEN_NOW`; the HF-01b-only
rerun (B7) uses its own database. B7's run-date independence is #543 C4's
test (``test_simulation_seed.py``, whose canonical dump holds the academic
tables), the English text check is #543 D4's test there, and the full
profile's pinned total is checked by both full runs. The worker parity test
(B4) runs last on the shared database."""

import asyncio
from collections import Counter
from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any

import pytest
from sqlalchemy.engine import make_url

from app.core.config import get_settings
from app.workflows.progress_report_schedule_tasks import (
    run_progress_report_placeholders,
)
from tests.factories.simulation import SIMULATION_MODULES
from tests.factories.simulation.anchor import (
    SIM_ANCHOR,
    SIM_NOW,
    ZONE,
    local_instant,
    sim_clock,
)
from tests.factories.simulation.coursework import (
    EARLY_ENROLLMENT,
    NOTE_AFTER,
    PENDING_SUBMIT_DAYS,
)
from tests.factories.simulation.plan import StudentPlan, plan_simulation
from tests.factories.simulation.texts import POOLS
from tests.factories.simulation.timeline import SEEDED_EARLIER
from tests.integration.auth_support import auth_client, login
from tests.integration.conftest import run_sql
from tests.integration.simulation_support import (
    academic_free_text,
    composed_texts,
    first_run_lines,
    migrate,
    run_command,
    simulation_env,
    table_counts,
)

PLAN = plan_simulation("es", "small", 1)
WORK = PLAN.academics


def _fresh(database_url: str) -> Iterator[str]:
    import uuid

    name = f"test_{uuid.uuid4().hex}"
    run_sql(database_url, f'CREATE DATABASE "{name}"', autocommit=True)
    url = make_url(database_url).set(database=name).render_as_string(False)
    try:
        migrate(url)
        yield url
    finally:
        run_sql(database_url, f'DROP DATABASE "{name}" WITH (FORCE)', autocommit=True)


SMALL_RUN: dict[str, list[str]] = {}


@pytest.fixture(scope="module")
def es_url(database_url: str) -> Iterator[str]:
    for url in _fresh(database_url):
        code, out, err = run_command(url, "es")
        assert code == 0, err
        SMALL_RUN["out"] = out
        yield url


def rows(url: str, sql: str, **params: Any) -> list[Any]:
    return run_sql(url, sql, params or None)


def scalar(url: str, sql: str, **params: Any) -> Any:
    return rows(url, sql, **params)[0][0]


def student_db_id(url: str, student: StudentPlan) -> Any:
    return scalar(
        url,
        "SELECT id FROM students WHERE given_names = :g AND family_names = :f",
        g=student.given_names,
        f=student.family_names,
    )


def _get(client: Any, path: str, **params: Any) -> Any:
    response = client.get(path, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_the_run_prints_the_academics_total(es_url):
    assert SMALL_RUN["out"] == first_run_lines("small")


# --- B1: evaluations ------------------------------------------------------------------


def test_every_evaluation_is_final_dated_before_the_anchor_and_per_the_plan(es_url):
    statuses = Counter(
        dict(rows(es_url, "SELECT status, count(*) FROM evaluations GROUP BY 1"))
    )
    assert statuses == {"final": len(WORK.evaluations)}
    assert scalar(es_url, "SELECT max(evaluated_on) FROM evaluations") < SIM_ANCHOR
    names = {t.key: t.name for t in WORK.templates}
    by_template = Counter(
        dict(rows(es_url, "SELECT template_name, count(*) FROM evaluations GROUP BY 1"))
    )
    assert by_template == Counter(names[e.template] for e in WORK.evaluations)
    early = {
        s.key
        for f in PLAN.families
        for s in f.students
        if f.enrolls_at.astimezone(ZONE).date() < EARLY_ENROLLMENT
    }
    diagnosed = scalar(
        es_url,
        "SELECT count(DISTINCT student_id) FROM evaluations WHERE template_name = :n",
        n=names["diagnostic"],
    )
    assert diagnosed == len(early)
    # A2 in the database: the author held the linked class with the student,
    # in the evaluation's subject, before it was finalized.
    wrong = rows(
        es_url,
        "SELECT e.id FROM evaluations e JOIN class_sessions s"
        " ON s.id = e.class_session_id LEFT JOIN class_session_participants p"
        " ON p.class_session_id = s.id AND p.student_id = e.student_id"
        " WHERE s.tutor_user_id <> e.author_user_id OR s.subject_id <> e.subject_id"
        " OR p.student_id IS NULL OR s.state <> 'completed'"
        " OR s.ends_at >= e.finalized_at",
    )
    assert wrong == []
    assert scalar(
        es_url,
        "SELECT count(*) FROM evaluations WHERE course_assessment_id IS NOT NULL",
    ) == sum(e.template == "course" for e in WORK.evaluations)


# --- B2: learning plans ---------------------------------------------------------------


def test_learning_plans_are_closed_or_active_with_overdue_milestones(es_url):
    statuses = Counter(
        dict(rows(es_url, "SELECT status, count(*) FROM learning_plans GROUP BY 1"))
    )
    assert statuses["completed"] >= 4 and statuses["discontinued"] >= 2
    assert statuses["draft"] == 0
    assert statuses == Counter(p.outcome for p in WORK.plans)
    assert (
        scalar(
            es_url,
            "SELECT count(*) FROM learning_plan_milestones m JOIN learning_plans p"
            " ON p.id = m.plan_id WHERE p.status = 'completed' AND m.status <> 'done'",
        )
        == 0
    )
    overdue = scalar(
        es_url,
        "SELECT count(DISTINCT p.id) FROM learning_plan_milestones m"
        " JOIN learning_plans p ON p.id = m.plan_id WHERE p.status = 'active'"
        " AND m.status = 'in_progress' AND m.due_on < :anchor",
        anchor=SIM_ANCHOR,
    )
    assert overdue >= 2
    assert (
        scalar(
            es_url,
            "SELECT count(*) FROM learning_plan_milestones m JOIN learning_plans p"
            " ON p.id = m.plan_id WHERE p.status = 'active' AND m.status = 'done'"
            " AND m.due_on >= :anchor",
            anchor=SIM_ANCHOR,
        )
        == 0
    )
    # QA #551 D1: every active plan spans the anchor, with an open milestone.
    assert (
        scalar(
            es_url,
            "SELECT count(*) FROM learning_plans p WHERE p.status = 'active'"
            " AND (p.target_on < :anchor OR NOT EXISTS (SELECT 1"
            " FROM learning_plan_milestones m WHERE m.plan_id = p.id"
            " AND m.status <> 'done' AND m.due_on >= :anchor))",
            anchor=SIM_ANCHOR,
        )
        == 0
    )
    reasons = POOLS["es"]["plan_discontinue_reasons"]
    for (description,) in rows(
        es_url, "SELECT description FROM learning_plans WHERE status = 'discontinued'"
    ):
        assert any(description.endswith(reason) for reason in reasons)  # type: ignore[attr-defined]
    closed = rows(
        es_url, "SELECT closed_at FROM learning_plans WHERE closed_at IS NOT NULL"
    )
    assert all(r.closed_at < SIM_NOW for r in closed)


# --- B3: homework ---------------------------------------------------------------------


def test_homework_is_graded_or_missing_before_the_anchor_and_open_after(es_url):
    found = rows(
        es_url,
        "SELECT a.due_on, s.submitted_at, s.graded_at, s.score FROM assignments a"
        " JOIN assignment_submissions s ON s.assignment_id = a.id",
    )
    assert len(found) == len(WORK.homework)
    past = [r for r in found if r.due_on < SIM_ANCHOR]
    future = [r for r in found if r.due_on >= SIM_ANCHOR]
    # Before the anchor: graded, or missing (nothing handed in).
    assert all((r.graded_at is not None) or (r.submitted_at is None) for r in past)
    missing = sum(r.submitted_at is None for r in past) / len(past)
    assert 0.05 <= missing <= 0.15
    # From the anchor on: nothing graded; pending or handed in early.
    assert all(r.graded_at is None for r in future)
    early = [r for r in future if r.submitted_at is not None]
    assert len(early) >= 3
    assert {r.submitted_at.astimezone(ZONE).date() for r in early} <= set(
        PENDING_SUBMIT_DAYS
    )
    assert all(r.graded_at < SIM_NOW for r in past if r.graded_at is not None)


# --- B4: progress reports -------------------------------------------------------------


def test_monthly_reports_are_approved_for_every_regular_student(es_url):
    for month in (date(2026, 9, 1), date(2026, 10, 1)):
        end = (month + timedelta(days=32)).replace(day=1)
        regulars = {
            r.student_id
            for r in rows(
                es_url,
                "SELECT p.student_id FROM class_sessions s"
                " JOIN class_session_participants p ON p.class_session_id = s.id"
                " WHERE s.state = 'completed' AND s.starts_at >= :start"
                " AND s.starts_at < :end GROUP BY p.student_id HAVING count(*) >= 4",
                start=local_instant(month, "00:00"),
                end=local_instant(end, "00:00"),
            )
        }
        reported = rows(
            es_url,
            "SELECT student_id, status FROM progress_reports WHERE kind = 'monthly'"
            " AND period_start = :start",
            start=month,
        )
        assert regulars and {r.student_id for r in reported} == regulars
        assert {r.status for r in reported} == {"approved_for_family"}
    # A resubmit clears changes_requested_at: the audit keeps the send-back.
    sent_back = scalar(
        es_url,
        "SELECT count(*) FROM audit_events"
        " WHERE action = 'academics.progress_report_changes_requested'",
    )
    assert sent_back == sum(r.changes_at is not None for r in WORK.reports) >= 1


def test_reports_waiting_on_approvals_and_no_future_period(es_url):
    assert (
        scalar(
            es_url,
            "SELECT count(*) FROM progress_reports WHERE period_end >= :anchor",
            anchor=SIM_ANCHOR,
        )
        == 0
    )
    on_demand = scalar(
        es_url,
        "SELECT count(*) FROM progress_reports WHERE kind = 'on_demand'"
        " AND status = 'approved_for_family'",
    )
    assert on_demand >= 2
    waiting = rows(
        es_url,
        "SELECT r.kind, r.period_end, r.submitted_at, a.status FROM progress_reports r"
        " JOIN approval_requests a ON a.subject_id = r.id"
        " WHERE r.status = 'submitted'",
    )
    assert len(waiting) >= 2
    assert {(w.kind, w.period_end, w.status) for w in waiting} == {
        ("weekly", date(2026, 11, 15), "pending")
    }
    assert {w.submitted_at.astimezone(ZONE).date() for w in waiting} <= set(
        PENDING_SUBMIT_DAYS
    )
    assert (
        scalar(es_url, "SELECT count(*) FROM progress_reports WHERE status = 'draft'")
        == 0
    )
    coordinator = scalar(
        es_url,
        "SELECT id FROM users WHERE username = :u",
        u=PLAN.coordinator.username,
    )
    schedules = rows(
        es_url, "SELECT cadence, created_by_user_id FROM progress_report_schedules"
    )
    assert Counter(s.cadence for s in schedules) == {"weekly": 2, "monthly": 1}
    assert {s.created_by_user_id for s in schedules} == {coordinator}
    placeholders = scalar(
        es_url, "SELECT count(*) FROM progress_reports WHERE schedule_id IS NOT NULL"
    )
    assert placeholders == sum(r.schedule is not None for r in WORK.reports)


# --- B5: notes ------------------------------------------------------------------------


def test_notes_counts_share_mix_and_timestamps(es_url):
    found = Counter(
        dict(rows(es_url, "SELECT visibility, count(*) FROM session_notes GROUP BY 1"))
    )
    assert found == {
        "family": sum(n.shared for n in WORK.notes),
        "internal": sum(not n.shared for n in WORK.notes),
    }
    published = rows(
        es_url,
        "SELECT n.published_at, s.ends_at FROM session_notes n"
        " JOIN class_sessions s ON s.id = n.class_session_id"
        " WHERE n.visibility = 'family'",
    )
    assert published
    assert all(r.published_at == r.ends_at + NOTE_AFTER for r in published)
    wrong = rows(
        es_url,
        "SELECT n.id FROM session_notes n JOIN class_sessions s"
        " ON s.id = n.class_session_id WHERE s.tutor_user_id <> n.author_user_id"
        " OR s.state <> 'completed'",
    )
    assert wrong == []


def test_a_guardian_sees_only_the_familys_shared_notes(es_url):
    family = next(
        f
        for f in PLAN.families
        if f.guardians
        and f.guardians[0].account is not None
        and any(n.student in {s.key for s in f.students} for n in WORK.notes)
    )
    guardian = family.guardians[0].account
    assert guardian is not None
    child = next(
        s for s in family.students if any(n.student == s.key for n in WORK.notes)
    )
    child_id = student_db_id(es_url, child)
    other = next(s for s in PLAN.students if s not in family.students)
    other_id = student_db_id(es_url, other)
    shared = {
        str(r.id)
        for r in rows(
            es_url,
            "SELECT id FROM session_notes WHERE student_id = :s"
            " AND visibility = 'family'",
            s=child_id,
        )
    }
    internal = {
        str(r.id)
        for r in rows(
            es_url, "SELECT id FROM session_notes WHERE visibility = 'internal'"
        )
    }
    with auth_client(es_url) as client:
        assert login(client, guardian.username, guardian.password).status_code == 200
        page = _get(client, f"/api/v1/students/{child_id}/learning-notes", limit=50)
        assert (
            client.get(f"/api/v1/students/{other_id}/learning-notes").status_code == 404
        )
    seen = {item["id"] for item in page["items"]}
    assert seen == shared
    assert not seen & internal


# --- B6: language ---------------------------------------------------------------------


def test_academic_text_comes_from_the_spanish_pool(es_url):
    values: set[str] = set()

    def walk(value: object) -> None:
        if isinstance(value, str):
            values.add(value)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(POOLS["es"])
    found = academic_free_text(es_url)
    assert {column for column, _ in found} >= {
        "session_notes.body",
        "evaluation_scores.criterion_title",
        "evaluation_scores.comment",
        "learning_plan_milestones.title",
        "assignment_submissions.feedback",
        "progress_reports.summary",
    }
    allowed = values | composed_texts(PLAN)
    assert [(column, value) for column, value in found if value not in allowed] == []


# --- B7: reruns -----------------------------------------------------------------------


def test_a_rerun_writes_nothing(es_url):
    before = table_counts(es_url)

    code, out, _ = run_command(es_url, "es")

    assert code == 0
    assert out[-2:] == ["academics: 0 rows", "billing: 0 rows"]
    assert table_counts(es_url) == before


def test_a_database_seeded_by_hf01b_only_skips_the_academics(database_url):
    for url in _fresh(database_url):
        assert run_command(url, "es", modules=SIMULATION_MODULES[:6])[0] == 0
        before = table_counts(url)

        code, out, _ = run_command(url, "es")

        assert code == 0
        assert out[-3:] == [
            "courses: 0 rows",
            f"academics: skipped ({SEEDED_EARLIER})",
            f"billing: skipped ({SEEDED_EARLIER})",
        ]
        assert table_counts(url) == before


# --- C1: per account, read-only through the API ---------------------------------------


def test_a_tutor_sees_their_students_plans_homework_and_reports(es_url):
    lp = next(p for p in WORK.plans if p.outcome == "active")
    tutor = PLAN.tutor(lp.tutor).account
    student = next(s for s in PLAN.students if s.key == lp.student)
    student_id = student_db_id(es_url, student)
    user_id = scalar(
        es_url, "SELECT id FROM users WHERE username = :u", u=tutor.username
    )
    stranger = next(
        s
        for s in PLAN.students
        if not any(
            c.final.tutor == lp.tutor and s.key in c.students
            for c in PLAN.timetable.classes
        )
    )
    with auth_client(es_url) as client:
        assert login(client, tutor.username, tutor.password).status_code == 200
        plans = _get(client, f"/api/v1/students/{student_id}/learning-plans")
        assert plans["items"]
        assert (
            client.get(
                f"/api/v1/students/{student_db_id(es_url, stranger)}/learning-plans"
            ).status_code
            == 404
        )
        homework = _get(client, "/api/v1/assignments", limit=50)
        reports = _get(client, "/api/v1/progress-reports", limit=50)
    own_homework = {
        str(r.id)
        for r in rows(
            es_url, "SELECT id FROM assignments WHERE creator_user_id = :t", t=user_id
        )
    }
    assert homework["items"]
    assert {item["id"] for item in homework["items"]} <= own_homework
    own_reports = {
        str(r.id)
        for r in rows(
            es_url,
            "SELECT id FROM progress_reports WHERE author_user_id = :t",
            t=user_id,
        )
    }
    assert {item["id"] for item in reports["items"]} <= own_reports


def test_a_guardian_sees_only_shared_and_approved_items_of_their_children(es_url):
    family = next(
        f
        for f in PLAN.families
        if f.guardians
        and f.guardians[0].account is not None
        and any(
            r.student in {s.key for s in f.students}
            and r.status == "approved_for_family"
            for r in WORK.reports
        )
    )
    guardian = family.guardians[0].account
    assert guardian is not None
    child = next(
        s for s in family.students if any(r.student == s.key for r in WORK.reports)
    )
    child_id = student_db_id(es_url, child)
    other = next(s for s in PLAN.students if s not in family.students)
    other_id = student_db_id(es_url, other)
    with auth_client(es_url) as client:
        assert login(client, guardian.username, guardian.password).status_code == 200
        reports = _get(client, f"/api/v1/students/{child_id}/progress-reports")
        homework = _get(client, f"/api/v1/students/{child_id}/assignments", limit=50)
        plans = _get(client, f"/api/v1/students/{child_id}/learning-plans")
        for path in ("progress-reports", "assignments", "learning-plans"):
            assert (
                client.get(f"/api/v1/students/{other_id}/{path}").status_code == 404
            ), path
    approved = {
        str(r.id)
        for r in rows(
            es_url,
            "SELECT id FROM progress_reports WHERE student_id = :s"
            " AND status = 'approved_for_family'",
            s=child_id,
        )
    }
    assert {item["id"] for item in reports["items"]} == approved
    theirs = {
        str(r.assignment_id)
        for r in rows(
            es_url,
            "SELECT assignment_id FROM assignment_submissions WHERE student_id = :s",
            s=child_id,
        )
    }
    assert {item["id"] for item in homework["items"]} <= theirs
    assert {item["status"] for item in plans["items"]} <= {
        "active",
        "completed",
        "discontinued",
    }


def test_a_student_sees_their_own_homework(es_url):
    student = next(s for s in PLAN.students if s.account is not None)
    assert student.account is not None
    own_id = student_db_id(es_url, student)
    other_id = student_db_id(es_url, next(s for s in PLAN.students if s != student))
    with auth_client(es_url) as client:
        assert (
            login(
                client, student.account.username, student.account.password
            ).status_code
            == 200
        )
        homework = _get(client, f"/api/v1/students/{own_id}/assignments", limit=50)
        assert client.get(f"/api/v1/students/{other_id}/assignments").status_code == 404
    theirs = {
        str(r.assignment_id)
        for r in rows(
            es_url,
            "SELECT assignment_id FROM assignment_submissions WHERE student_id = :s",
            s=own_id,
        )
    }
    assert homework["items"]
    assert {item["id"] for item in homework["items"]} <= theirs


def test_the_coordinator_sees_the_review_queue(es_url):
    coordinator = PLAN.coordinator
    submitted = {
        str(r.id)
        for r in rows(
            es_url, "SELECT id FROM progress_reports WHERE status = 'submitted'"
        )
    }
    with auth_client(es_url) as client:
        assert (
            login(client, coordinator.username, coordinator.password).status_code == 200
        )
        queue = _get(client, "/api/v1/progress-reports", status="submitted", limit=50)
        approvals = _get(
            client, "/api/v1/approval-requests", status="pending", limit=100
        )
    assert {item["id"] for item in queue["items"]} == submitted
    assert len(submitted) >= 2
    listed = Counter(item["request_type"] for item in approvals["items"])
    assert listed["academics.progress_report"] == len(submitted)


# --- B4: worker parity at the anchor (last: it would change the shared database) ------


def test_zz_the_report_schedule_step_creates_nothing_at_the_anchor(es_url):
    before = table_counts(es_url)

    with simulation_env(es_url), sim_clock(SIM_NOW):
        result = asyncio.run(
            run_progress_report_placeholders(get_settings(), now=SIM_NOW)
        )

    assert (result.created, result.failed) == (0, 0)
    assert table_counts(es_url) == before
