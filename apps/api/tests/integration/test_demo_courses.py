"""The demo course (#303 C, E2, E6): ``courses.demo_courses``."""

import asyncio
from datetime import date
from typing import Any

import pytest

from app.core.db import create_sessionmaker
from app.core.uow import UnitOfWork
from app.modules.courses.models import CourseTemplate
from tests.factories import demo
from tests.factories.base import GeneratorSkipped
from tests.factories.demo_courses import (
    TEMPLATE_DESCRIPTION,
    TEMPLATE_NAME,
    DemoCoursesGenerator,
)
from tests.integration.conftest import run_sql, short_lived_engine
from tests.integration.demo_support import (
    COURSES_GENERATOR,
    DEMO_COURSES_ROWS,
    FROZEN_NOW,
    TUTOR2_GENERATOR,
    count,
    demo_env,
    first_run_lines,
    generators_through,
    migrate,
)
from tests.integration.test_demo_schedule import ZONE, student_labels

THROUGH_COURSES = generators_through(COURSES_GENERATOR)

# T = Wednesday 2026-11-04: D(2) = Monday 11-09, D(4) = Wednesday 11-11, then
# D(7), D(9), D(12), D(14), D(17), D(19).
COHORT_DATES = [
    date(2026, 11, 9),
    date(2026, 11, 11),
    date(2026, 11, 16),
    date(2026, 11, 18),
    date(2026, 11, 23),
    date(2026, 11, 25),
    date(2026, 11, 30),
    date(2026, 12, 2),
]


@pytest.fixture
def migrated_url(fresh_database_url: str) -> str:
    return migrate(fresh_database_url)


@pytest.fixture
def env_for(monkeypatch):
    return lambda app_env, dsn: demo_env(monkeypatch, app_env, dsn)


def test_a_first_run_writes_the_course(migrated_url, env_for, clock, capsys):
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)

    assert demo.main([], generators=THROUGH_COURSES) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines == first_run_lines(THROUGH_COURSES)
    assert lines[-1] == f"{COURSES_GENERATOR}: {DEMO_COURSES_ROWS} rows"

    # C2: the template, its subjects, modules and assessments.
    [template] = run_sql(
        migrated_url,
        "SELECT id, name, description, session_count, session_minutes,"
        " min_attendance_percent, is_active, version FROM course_templates",
    )
    assert tuple(template)[1:] == (
        TEMPLATE_NAME,
        TEMPLATE_DESCRIPTION,
        8,
        90,
        80,
        True,
        1,
    )
    subjects = run_sql(
        migrated_url,
        "SELECT s.name || ' (' || l.name || ')' FROM course_template_subjects t"
        " JOIN subjects s ON s.id = t.subject_id"
        " JOIN education_levels l ON l.id = s.education_level_id ORDER BY 1",
    )
    assert [row[0] for row in subjects] == [
        "Ciencias (Secundaria)",
        "Matemáticas (Secundaria)",
    ]
    modules = run_sql(
        migrated_url,
        "SELECT m.position, m.title, s.name, m.planned_sessions, m.is_required,"
        " m.materials FROM course_template_modules m"
        " JOIN subjects s ON s.id = m.subject_id ORDER BY m.position",
    )
    assert [tuple(m) for m in modules] == [
        (1, "Aritmética y álgebra", "Matemáticas", 3, True,
         "Guía de ejercicios, unidad 1"),
        (2, "Geometría", "Matemáticas", 2, True, None),
        (3, "Ciencias naturales", "Ciencias", 2, True, None),
    ]  # fmt: skip
    assessments = run_sql(
        migrated_url,
        "SELECT position, title, module_id, is_required, min_score_percent"
        " FROM course_template_assessments ORDER BY position",
    )
    assert [tuple(a) for a in assessments] == [
        (1, "Examen diagnóstico", None, False, None),
        (2, "Simulacro final", None, True, 60),
    ]
    [snapshot] = run_sql(
        migrated_url,
        "SELECT template_id, template_version, content->>'name' AS name"
        " FROM course_template_snapshots",
    )
    assert tuple(snapshot) == (template.id, 1, TEMPLATE_NAME)

    # C3: the cohort, its series (accepted by demo.tutor2) and 8 sessions.
    [cohort] = run_sql(
        migrated_url,
        "SELECT c.id, c.name, c.max_students, s.name AS subject,"
        " c.template_snapshot_id IS NOT NULL AS frozen"
        " FROM course_cohorts c JOIN subjects s ON s.id = c.subject_id",
    )
    assert tuple(cohort)[1:] == ("Examen de ingreso, grupo 1", 6, "Matemáticas", True)
    [series] = run_sql(
        migrated_url,
        "SELECT s.id, u.username, c.name AS room, s.assignment_status"
        " FROM class_series s JOIN users u ON u.id = s.tutor_user_id"
        " JOIN classrooms c ON c.id = s.classroom_id WHERE s.cohort_id = :id",
        {"id": cohort.id},
    )
    assert tuple(series)[1:] == ("demo.tutor2", "Aula 2", "accepted")
    sessions = run_sql(
        migrated_url,
        "SELECT cs.state, u.username, c.name AS room,"
        " (cs.starts_at AT TIME ZONE :zone)::date AS day,"
        " to_char(cs.starts_at AT TIME ZONE :zone, 'HH24:MI') AS starts,"
        " to_char(cs.ends_at AT TIME ZONE :zone, 'HH24:MI') AS ends,"
        " cs.series_id"
        " FROM class_sessions cs JOIN users u ON u.id = cs.tutor_user_id"
        " JOIN classrooms c ON c.id = cs.classroom_id"
        " WHERE cs.cohort_id = :id ORDER BY cs.starts_at",
        {"zone": ZONE, "id": cohort.id},
    )
    assert [tuple(s)[:6] for s in sessions] == [
        ("confirmed", "demo.tutor2", "Aula 2", day, "15:00", "16:30")
        for day in COHORT_DATES
    ]
    assert {s.series_id for s in sessions} == {series.id}
    [offer] = run_sql(
        migrated_url,
        "SELECT o.status, u.username, (SELECT count(*) FROM assignment_offer_sessions"
        "  x WHERE x.offer_id = o.id) AS sessions FROM assignment_offers o"
        " JOIN users u ON u.id = o.tutor_user_id WHERE o.class_series_id = :id",
        {"id": series.id},
    )
    assert tuple(offer) == ("accepted", "demo.tutor2", 8)

    # C4: S then A, active, in the series and every session.
    labels = student_labels(migrated_url)
    enrollments = run_sql(
        migrated_url,
        "SELECT e.student_id, e.withdrawn_at, u.username AS enrolled_by"
        " FROM cohort_enrollments e JOIN users u ON u.id = e.enrolled_by_user_id"
        " WHERE e.cohort_id = :id ORDER BY e.enrolled_at, e.id",
        {"id": cohort.id},
    )
    assert [
        (labels[e.student_id], e.withdrawn_at, e.enrolled_by) for e in enrollments
    ] == [("S", None, "demo.owner"), ("A", None, "demo.owner")]
    series_students = run_sql(
        migrated_url,
        "SELECT student_id FROM class_series_participants WHERE class_series_id = :id",
        {"id": series.id},
    )
    assert sorted(labels[r.student_id] for r in series_students) == ["A", "S"]
    participants = run_sql(
        migrated_url,
        "SELECT p.student_id, count(*) AS n FROM class_session_participants p"
        " JOIN class_sessions cs ON cs.id = p.class_session_id"
        " WHERE cs.cohort_id = :id GROUP BY p.student_id",
        {"id": cohort.id},
    )
    assert sorted((labels[p.student_id], p.n) for p in participants) == [
        ("A", 8),
        ("S", 8),
    ]
    # C5: the one notification, the series offer to demo.tutor2.
    received = run_sql(
        migrated_url,
        "SELECT count(*) FROM notifications n JOIN users u"
        " ON u.id = n.recipient_user_id WHERE u.username = 'demo.tutor2'"
        " AND n.notification_type = 'scheduling.offer_received'",
    )
    assert received[0][0] == 1


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


def test_an_existing_template_means_0_rows(migrated_url, env_for, clock):
    # E6 / C1: the marker (the template's name, here inactive and without
    # subjects or a cohort).
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    assert demo.main([], generators=generators_through(TUTOR2_GENERATOR)) == 0

    async def add_template(session: Any) -> None:
        session.add(
            CourseTemplate(
                name=TEMPLATE_NAME, session_count=8, session_minutes=90, is_active=False
            )
        )
        await session.flush()

    local = DemoCoursesGenerator(app_env=lambda: "local")
    assert asyncio.run(_run(migrated_url, add_template, local)) == [0]
    assert count(migrated_url, "course_templates") == 1
    assert count(migrated_url, "course_cohorts") == 0
