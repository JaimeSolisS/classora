"""The #354 learning plans and homework (#354 B, C, E2-E6):
``academics.demo_learning_plans``, ``academics.demo_homework``, the whole run
with the three #354 generators, reruns, ``dev``, markers, the stale-schedule
skip, prerequisites and rollback. The reports, the schedule and what each
account sees are in ``test_demo_reports``."""

import asyncio
import dataclasses
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from app.core import clock as app_clock
from app.core.db import create_sessionmaker
from app.core.uow import UnitOfWork
from app.modules.academics.errors import AssignmentFieldError
from app.modules.academics.models import LearningPlan, ProgressReportSchedule
from tests.factories import demo
from tests.factories.base import GeneratorSkipped
from tests.factories.demo_changes import STALE_SCHEDULE
from tests.factories.demo_learning import (
    HW1_ANSWER,
    HW1_FEEDBACK,
    HW1_TITLE,
    HW2_ANSWER,
    HW2_TITLE,
    HW3_TITLE,
    LP1_TITLE,
    LP2_TITLE,
    DemoHomeworkGenerator,
    DemoLearningPlan,
    DemoLearningPlansGenerator,
    DemoProgressReportsGenerator,
    plan_demo_learning,
)
from tests.factories.identity import DemoPrerequisiteMissingError
from tests.integration.conftest import run_sql, short_lived_engine
from tests.integration.demo_support import (
    ALL_TABLE_COUNTS,
    DEMO_354_TABLE_COUNTS,
    DEV_LINES,
    EVALUATIONS_GENERATOR,
    FIRST_RUN_LINES,
    FROZEN_NOW,
    GENERATOR_NAMES,
    HOMEWORK_GENERATOR,
    NEW_354_GENERATORS,
    NOTES_GENERATOR,
    PROGRESS_REPORTS_GENERATOR,
    RERUN_LINES,
    RERUN_REFERENCE_LINES,
    TABLE_COUNTS_THROUGH_353,
    UntouchedSession,
    count,
    demo_env,
    demo_table_counts,
    generators_through,
    migrate,
)
from tests.integration.test_demo_schedule import ZONE, student_labels

# T = Wednesday 2026-11-04: P(1) = 11-03, P(2) = 11-02, P(3) = 10-30,
# P(4) = 10-29, P(5) = 10-28, P(6) = 10-27, P(7) = 10-26, P(8) = 10-23,
# P(10) = 10-21, P(13) = 10-16, D(3) = 11-10, D(8) = 11-17.
H1 = "2026-10-27 16:00"
H3 = "2026-10-23 16:00"
NEW_TABLES = (
    "learning_plans",
    "assignments",
    "progress_reports",
    "progress_report_schedules",
)
MATH = "Matemáticas (Secundaria)"
ENGLISH = "Inglés (Secundaria)"


@pytest.fixture
def migrated_url(fresh_database_url: str) -> str:
    return migrate(fresh_database_url)


@pytest.fixture
def env_for(monkeypatch):
    return lambda app_env, dsn: demo_env(monkeypatch, app_env, dsn)


def plan_rows(url: str) -> list[tuple[Any, ...]]:
    rows = run_sql(
        url,
        "SELECT p.title, s.name || ' (' || l.name || ')' AS subject, p.status,"
        " p.starts_on::text AS starts_on, p.target_on::text AS target_on,"
        " to_char(p.activated_at AT TIME ZONE :zone, 'YYYY-MM-DD HH24:MI') AS act,"
        " to_char(p.closed_at AT TIME ZONE :zone, 'YYYY-MM-DD HH24:MI') AS closed,"
        " u.username, p.deleted_at IS NULL AS live"
        " FROM learning_plans p JOIN users u ON u.id = p.created_by_user_id"
        " JOIN subjects s ON s.id = p.subject_id"
        " JOIN education_levels l ON l.id = s.education_level_id ORDER BY p.title",
        {"zone": ZONE},
    )
    return [tuple(r) for r in rows]


def milestone_rows(url: str) -> list[tuple[Any, ...]]:
    rows = run_sql(
        url,
        "SELECT p.title AS plan, o.title AS objective, m.title, m.due_on::text AS due,"
        " m.status,"
        " to_char(m.completed_at AT TIME ZONE :zone, 'YYYY-MM-DD HH24:MI') AS done"
        " FROM learning_plan_milestones m"
        " JOIN learning_plan_objectives o ON o.id = m.objective_id"
        " JOIN learning_plans p ON p.id = m.plan_id"
        " ORDER BY p.title, o.position, m.position",
        {"zone": ZONE},
    )
    return [tuple(r) for r in rows]


def homework_rows(url: str) -> list[tuple[Any, ...]]:
    rows = run_sql(
        url,
        "SELECT a.title, s.name || ' (' || l.name || ')' AS subject,"
        " to_char(cs.starts_at AT TIME ZONE :zone, 'YYYY-MM-DD HH24:MI') AS class,"
        " a.due_on::text AS due, a.due_time, a.max_score, x.student_id,"
        " to_char(x.submitted_at AT TIME ZONE :zone, 'YYYY-MM-DD HH24:MI') AS sub,"
        " su.username AS submitted_by, x.submitted_via, x.text, x.score,"
        " x.score_percent, x.feedback, x.graded_on::text AS graded_on,"
        " gu.username AS graded_by, cu.username AS creator"
        " FROM assignments a JOIN assignment_submissions x ON x.assignment_id = a.id"
        " JOIN subjects s ON s.id = a.subject_id"
        " JOIN education_levels l ON l.id = s.education_level_id"
        " JOIN users cu ON cu.id = a.creator_user_id"
        " LEFT JOIN class_sessions cs ON cs.id = a.class_session_id"
        " LEFT JOIN users su ON su.id = x.submitted_by_user_id"
        " LEFT JOIN users gu ON gu.id = x.graded_by_user_id ORDER BY a.due_on",
        {"zone": ZONE},
    )
    return [tuple(r) for r in rows]


def test_a_first_run_writes_the_plans_and_homework(
    migrated_url, env_for, clock, capsys
):
    # E2 on Wednesday 2026-11-04, 11:00 local.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)

    assert demo.main([]) == 0

    assert capsys.readouterr().out.splitlines() == FIRST_RUN_LINES
    # The seed clock was restored.
    assert app_clock.now() == FROZEN_NOW
    labels = student_labels(migrated_url)

    # B2, B3: LP1 active since P(10) 18:00; LP2 active at P(13) 17:00 and
    # completed at P(3) 17:30.
    assert plan_rows(migrated_url) == [
        (LP1_TITLE, MATH, "active", "2026-10-21", "2026-12-16", "2026-10-21 18:00",
         None, "demo.tutor", True),
        (LP2_TITLE, ENGLISH, "completed", "2026-10-16", "2026-11-03",
         "2026-10-16 17:00", "2026-10-30 17:30", "demo.tutor", True),
    ]  # fmt: skip
    [owner] = run_sql(migrated_url, "SELECT DISTINCT student_id FROM learning_plans")
    assert labels[owner.student_id] == "S"
    assert milestone_rows(migrated_url) == [
        (LP1_TITLE, "Ecuaciones de primer grado", "Despejar la incógnita",
         "2026-10-23", "done", "2026-10-26 17:30"),
        (LP1_TITLE, "Ecuaciones de primer grado", "Ecuaciones con fracciones",
         "2026-11-02", "pending", None),
        (LP1_TITLE, "Sistemas de ecuaciones 2×2", "Método de sustitución",
         "2026-11-10", "in_progress", None),
        (LP1_TITLE, "Sistemas de ecuaciones 2×2", "Método de igualación",
         "2026-11-17", "pending", None),
        (LP2_TITLE, "Leer textos informativos", "Identificar la idea principal",
         "2026-10-21", "done", "2026-10-21 17:30"),
        (LP2_TITLE, "Leer textos informativos", "Vocabulario clave del texto",
         "2026-10-23", "done", "2026-10-23 17:45"),
        (LP2_TITLE, "Leer textos informativos", "Resumen escrito de un texto",
         "2026-10-29", "pending", None),
    ]  # fmt: skip

    # C2, C3: HW1 graded, HW2 handed in, HW3 assigned; S only, by the student.
    rows = homework_rows(migrated_url)
    assert {labels[r[6]] for r in rows} == {"S"}
    d = Decimal
    assert [r[:6] + r[7:] for r in rows] == [
        (HW1_TITLE, ENGLISH, H3, "2026-10-27", None, d("10.00"), "2026-10-26 20:30",
         "demo.student", "student", HW1_ANSWER, d("9.00"), d("90.00"),
         HW1_FEEDBACK, "2026-10-28", "demo.tutor", "demo.tutor"),
        (HW2_TITLE, MATH, H1, "2026-11-02", None, d("10.00"), "2026-10-30 20:30",
         "demo.student", "student", HW2_ANSWER, None, None, None, None, None,
         "demo.tutor"),
        (HW3_TITLE, MATH, None, "2026-11-10", None, d("10.00"), None, None, None,
         None, None, None, None, None, None, "demo.tutor"),
    ]  # fmt: skip

    # B4, C4: the #354 notifications, by type and recipient.
    notifications = run_sql(
        migrated_url,
        "SELECT n.notification_type, u.username, count(*) AS n FROM notifications n"
        " JOIN users u ON u.id = n.recipient_user_id"
        " WHERE n.notification_type LIKE 'academics.learning_plan%'"
        " OR n.notification_type LIKE 'academics.assignment%'"
        " OR n.notification_type LIKE 'academics.progress_report%'"
        " GROUP BY 1, 2 ORDER BY 1, 2",
    )
    assert [tuple(n) for n in notifications] == [
        ("academics.assignment_graded", "demo.guardian", 1),
        ("academics.assignment_graded", "demo.student", 1),
        ("academics.assignment_set", "demo.guardian", 3),
        ("academics.assignment_set", "demo.student", 3),
        ("academics.assignment_submitted", "demo.tutor", 2),
        ("academics.learning_plan_activated", "demo.guardian", 2),
        ("academics.learning_plan_activated", "demo.student", 2),
        ("academics.learning_plan_completed", "demo.guardian", 1),
        ("academics.learning_plan_completed", "demo.student", 1),
        ("academics.progress_report_approved", "demo.guardian", 1),
        ("academics.progress_report_approved", "demo.student", 1),
        ("academics.progress_report_approved", "demo.tutor", 1),
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
    # E3.
    clock.set(now)
    env_for("local", migrated_url)

    assert demo.main([]) == 0

    assert capsys.readouterr().out.splitlines() == FIRST_RUN_LINES
    assert demo_table_counts(migrated_url, ALL_TABLE_COUNTS) == ALL_TABLE_COUNTS
    assert app_clock.now() == now


def test_a_rerun_writes_nothing_even_days_later(migrated_url, env_for, clock, capsys):
    # E4: every generator (22) prints 0 rows.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    assert demo.main([]) == 0
    capsys.readouterr()
    assert len(GENERATOR_NAMES) == 22

    assert demo.main([]) == 0
    assert capsys.readouterr().out.splitlines() == RERUN_LINES

    clock.advance(timedelta(days=10))
    assert demo.main([]) == 0
    assert capsys.readouterr().out.splitlines() == RERUN_LINES
    assert demo_table_counts(migrated_url, ALL_TABLE_COUNTS) == ALL_TABLE_COUNTS
    assert app_clock.now() == FROZEN_NOW + timedelta(days=10)


def test_dev_writes_no_354_data(migrated_url, env_for, capsys):
    # E5.
    env_for("dev", migrated_url)

    assert demo.main([]) == 0

    assert capsys.readouterr().out.splitlines() == DEV_LINES
    for table in NEW_TABLES:
        assert count(migrated_url, table) == 0, table


NEW_GENERATORS = [
    DemoLearningPlansGenerator,
    DemoHomeworkGenerator,
    DemoProgressReportsGenerator,
]


@pytest.mark.parametrize("app_env", ["dev", "prod", "staging"])
@pytest.mark.parametrize("generator", NEW_GENERATORS, ids=lambda g: g.name)
def test_a_new_generator_writes_nothing_outside_local(generator, app_env):
    # E5: skipped without touching the session.
    result = asyncio.run(
        generator(app_env=lambda: app_env).generate(UntouchedSession())  # type: ignore[arg-type]
    )

    assert result == GeneratorSkipped(f"APP_ENV={app_env}; local only")


def test_the_354_generators_come_last():
    # A1.
    assert GENERATOR_NAMES[-3:] == list(NEW_354_GENERATORS)
    assert GENERATOR_NAMES[-4] == EVALUATIONS_GENERATOR
    assert DEMO_354_TABLE_COUNTS["learning_plan_milestones"] == 7


# --- E6: markers, stale data, prerequisites, rollback ---------------------------------


def test_a_schedule_from_an_earlier_day_is_skipped(
    migrated_url, env_for, clock, capsys
):
    # The #353 run on 2026-11-04, then every generator 10 days later.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    assert demo.main([], generators=generators_through(EVALUATIONS_GENERATOR)) == 0
    capsys.readouterr()

    clock.advance(timedelta(days=10))
    assert demo.main([]) == 0

    assert capsys.readouterr().out.splitlines() == [
        *RERUN_REFERENCE_LINES,
        *(
            f"{name}: skipped ({STALE_SCHEDULE})"
            if name in NEW_354_GENERATORS
            else f"{name}: 0 rows"
            for name in GENERATOR_NAMES
        ),
    ]
    assert (
        demo_table_counts(migrated_url, TABLE_COUNTS_THROUGH_353)
        == TABLE_COUNTS_THROUGH_353
    )
    for table in NEW_TABLES:
        assert count(migrated_url, table) == 0, table


async def _run(url: str, *steps: Any) -> list[int | GeneratorSkipped]:
    engine = short_lived_engine(url)
    results: list[int | GeneratorSkipped] = []
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            for step in steps:
                if hasattr(step, "generate"):
                    results.append(await step.generate(uow.session))
                else:
                    await step(uow.session)
    finally:
        await engine.dispose()
    return results


def _s_and_tutor(url: str) -> tuple[Any, Any, Any]:
    [s] = run_sql(
        url,
        "SELECT st.id FROM students st JOIN users u ON u.id = st.user_id"
        " WHERE u.username = 'demo.student'",
    )
    [tutor] = run_sql(url, "SELECT id FROM users WHERE username = 'demo.tutor'")
    [subject] = run_sql(
        url,
        "SELECT s.id FROM subjects s JOIN education_levels l"
        " ON l.id = s.education_level_id"
        " WHERE s.name = 'Matemáticas' AND l.name = 'Secundaria'",
    )
    return s.id, tutor.id, subject.id


def test_an_existing_plan_of_s_means_0_rows(migrated_url, env_for, clock):
    # B1: the marker (here a deleted draft of another title).
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    assert demo.main([], generators=generators_through(EVALUATIONS_GENERATOR)) == 0
    s_id, tutor_id, subject_id = _s_and_tutor(migrated_url)

    async def add_plan(session: Any) -> None:
        session.add(
            LearningPlan(
                student_id=s_id,
                subject_id=subject_id,
                created_by_user_id=tutor_id,
                title="Otro plan",
                starts_on=date(2026, 11, 1),
                deleted_at=FROZEN_NOW,
                deleted_by_user_id=tutor_id,
            )
        )
        await session.flush()

    local = DemoLearningPlansGenerator(app_env=lambda: "local")
    assert asyncio.run(_run(migrated_url, add_plan, local, local)) == [0, 0]
    assert count(migrated_url, "learning_plans") == 1


def test_the_homework_marker_means_0_rows(migrated_url, env_for, clock):
    # C1: the generator runs twice in one Unit of Work.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    assert demo.main([], generators=generators_through(EVALUATIONS_GENERATOR)) == 0

    local = DemoHomeworkGenerator(app_env=lambda: "local")
    assert asyncio.run(_run(migrated_url, local, local)) == [16, 0]
    assert count(migrated_url, "assignments") == 3
    assert app_clock.now() == FROZEN_NOW


def test_an_existing_report_schedule_of_s_means_0_rows(migrated_url, env_for, clock):
    # D1: the marker (here only a schedule, no report).
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    assert demo.main([], generators=generators_through(HOMEWORK_GENERATOR)) == 0
    s_id, tutor_id, subject_id = _s_and_tutor(migrated_url)

    async def add_schedule(session: Any) -> None:
        session.add(
            ProgressReportSchedule(
                student_id=s_id,
                author_user_id=tutor_id,
                cadence="monthly",
                subject_id=subject_id,
                starts_on=date(2026, 11, 1),
                created_by_user_id=tutor_id,
            )
        )
        await session.flush()

    local = DemoProgressReportsGenerator(app_env=lambda: "local")
    assert asyncio.run(_run(migrated_url, add_schedule, local)) == [0]
    assert count(migrated_url, "progress_reports") == 0
    assert count(migrated_url, "progress_report_schedules") == 1


def test_reports_without_the_353_evaluations_fail_the_run(migrated_url, env_for, clock):
    # D1: the generators through academics.demo_notes, then only the reports.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    generators = [
        *generators_through(NOTES_GENERATOR),
        DemoProgressReportsGenerator(),
    ]
    assert generators[-1].name == PROGRESS_REPORTS_GENERATOR

    with pytest.raises(DemoPrerequisiteMissingError, match="demo_evaluations"):
        demo.main([], generators=generators)

    assert count(migrated_url, "users") == 0
    assert count(migrated_url, "progress_reports") == 0


def test_a_refused_service_call_rolls_the_run_back(migrated_url, env_for, clock):
    # A plan double that moves HW1's due date before its creation day: the
    # service refuses it (422 due_in_past), nothing is written and the clock
    # is back.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)

    def due_in_past(today: date) -> DemoLearningPlan:
        plan = plan_demo_learning(today)
        hw1, *rest = plan.homework
        return dataclasses.replace(
            plan,
            homework=(
                dataclasses.replace(hw1, due_on=hw1.due_on - timedelta(days=10)),
                *rest,
            ),
        )

    generators = [
        *generators_through(EVALUATIONS_GENERATOR),
        DemoLearningPlansGenerator(),
        DemoHomeworkGenerator(plan=due_in_past),
    ]
    with pytest.raises(AssignmentFieldError) as raised:
        demo.main([], generators=generators)

    assert [e["code"] for e in raised.value.errors or []] == ["due_in_past"]
    assert app_clock.now() == FROZEN_NOW
    assert count(migrated_url, "users") == 0
    assert count(migrated_url, "learning_plans") == 0
    assert count(migrated_url, "assignments") == 0
