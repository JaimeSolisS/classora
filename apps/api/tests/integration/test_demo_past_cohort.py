"""The finished demo course (#353 C, F2, F6): the
``courses.demo_past_cohort`` generator, through the route services under
the seed clock."""

import asyncio
from datetime import date
from typing import Any

import pytest

from app.core import clock as app_clock
from app.core.db import create_sessionmaker
from app.core.uow import UnitOfWork
from app.modules.courses.models import CourseTemplate
from tests.factories import demo
from tests.factories.base import GeneratorSkipped
from tests.factories.demo_academics import COHORT_NAME, NEXT_STEPS, PERFORMANCE
from tests.factories.demo_past_cohort import (
    TEMPLATE_DESCRIPTION,
    TEMPLATE_NAME,
    DemoPastCohortGenerator,
)
from tests.factories.identity import DemoPrerequisiteMissingError
from tests.integration.conftest import run_sql, short_lived_engine
from tests.integration.demo_support import (
    CHANGES_GENERATOR,
    COURSES_GENERATOR,
    FROZEN_NOW,
    TUTOR2_GENERATOR,
    count,
    demo_env,
    generators_through,
    migrate,
)
from tests.integration.test_demo_schedule import ZONE, student_labels

# T = Wednesday 2026-11-04: P(13) = Friday 10-16, P(11) = Tuesday 10-20,
# P(8) = Friday 10-23, P(6) = Tuesday 10-27, P(3) = Friday 10-30, P(1) =
# Tuesday 11-03; T0 = Wednesday 10-14 12:00.
CLASSES = [
    # Day, S, A, B, topics.
    (date(2026, 10, 16), "present", "present", "present",
     "Ecuaciones de primer grado con una incógnita"),
    (date(2026, 10, 20), "present", "present", "absent", "Ecuaciones con fracciones"),
    (date(2026, 10, 23), "late", "present", "present",
     "Examen parcial y repaso de ecuaciones"),
    (date(2026, 10, 27), "present", "absent", "present",
     "Sistemas de ecuaciones por sustitución"),
    (date(2026, 10, 30), "present", "present", "absent",
     "Sistemas de ecuaciones por igualación"),
    (date(2026, 11, 3), "present", "present", "present", "Examen final"),
]  # fmt: skip


@pytest.fixture
def migrated_url(fresh_database_url: str) -> str:
    return migrate(fresh_database_url)


@pytest.fixture
def env_for(monkeypatch):
    return lambda app_env, dsn: demo_env(monkeypatch, app_env, dsn)


def test_a_first_run_writes_the_finished_cohort(migrated_url, env_for, clock):
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)

    assert demo.main([]) == 0
    assert app_clock.now() == FROZEN_NOW

    # C2: the template, its subject, modules and assessments.
    [template] = run_sql(
        migrated_url,
        "SELECT id, description, session_count, session_minutes,"
        " min_attendance_percent, is_active FROM course_templates WHERE name = :n",
        {"n": TEMPLATE_NAME},
    )
    assert tuple(template)[1:] == (TEMPLATE_DESCRIPTION, 6, 90, 80, True)
    subjects = run_sql(
        migrated_url,
        "SELECT s.name || ' (' || l.name || ')' FROM course_template_subjects t"
        " JOIN subjects s ON s.id = t.subject_id"
        " JOIN education_levels l ON l.id = s.education_level_id"
        " WHERE t.template_id = :id",
        {"id": template.id},
    )
    assert [row[0] for row in subjects] == ["Matemáticas (Secundaria)"]
    modules = run_sql(
        migrated_url,
        "SELECT position, title, planned_sessions, is_required, materials"
        " FROM course_template_modules WHERE template_id = :id ORDER BY position",
        {"id": template.id},
    )
    assert [tuple(m) for m in modules] == [
        (1, "Ecuaciones lineales", 3, True, "Cuaderno de ejercicios"),
        (2, "Sistemas de ecuaciones", 3, True, None),
    ]
    assessments = run_sql(
        migrated_url,
        "SELECT position, title, module_id, is_required, min_score_percent"
        " FROM course_template_assessments WHERE template_id = :id"
        " ORDER BY position",
        {"id": template.id},
    )
    assert [tuple(a) for a in assessments] == [
        (1, "Examen parcial", None, False, None),
        (2, "Examen final", None, True, 60),
    ]

    # The cohort, its accepted series and its six completed classes.
    [cohort] = run_sql(
        migrated_url,
        "SELECT c.id, c.max_students, s.name AS subject FROM course_cohorts c"
        " JOIN subjects s ON s.id = c.subject_id WHERE c.name = :n",
        {"n": COHORT_NAME},
    )
    assert tuple(cohort)[1:] == (6, "Matemáticas")
    [series] = run_sql(
        migrated_url,
        "SELECT u.username, r.name AS room, s.assignment_status,"
        " (SELECT o.status FROM assignment_offers o"
        "  WHERE o.class_series_id = s.id) AS offer"
        " FROM class_series s JOIN users u ON u.id = s.tutor_user_id"
        " JOIN classrooms r ON r.id = s.classroom_id WHERE s.cohort_id = :id",
        {"id": cohort.id},
    )
    assert tuple(series) == ("demo.tutor2", "Aula 2", "accepted", "accepted")
    labels = student_labels(migrated_url)
    enrolled = run_sql(
        migrated_url,
        "SELECT e.student_id, u.username,"
        " to_char(e.enrolled_at AT TIME ZONE :zone, 'YYYY-MM-DD HH24:MI') AS at"
        " FROM cohort_enrollments e JOIN users u ON u.id = e.enrolled_by_user_id"
        " WHERE e.cohort_id = :id AND e.withdrawn_at IS NULL"
        " ORDER BY e.enrolled_at, e.id",
        {"id": cohort.id, "zone": ZONE},
    )
    assert [(labels[e.student_id], e.username, e.at) for e in enrolled] == [
        (key, "demo.owner", "2026-10-14 12:00") for key in ("S", "A", "B")
    ]
    sessions = run_sql(
        migrated_url,
        "SELECT cs.id, (cs.starts_at AT TIME ZONE :zone)::date AS day,"
        " to_char(cs.starts_at AT TIME ZONE :zone, 'HH24:MI') AS starts,"
        " to_char(cs.ends_at AT TIME ZONE :zone, 'HH24:MI') AS ends,"
        " cs.state, cs.actual_minutes, u.username, r.name AS room,"
        " c.status, c.topics, c.performance, c.homework, c.next_steps,"
        " cu.username AS submitted_by, ap.revision, ap.approved_minutes,"
        " au.username AS approved_by,"
        " to_char(ap.approved_at AT TIME ZONE :zone, 'HH24:MI') AS approved,"
        " rq.status AS item"
        " FROM class_sessions cs JOIN users u ON u.id = cs.tutor_user_id"
        " JOIN classrooms r ON r.id = cs.classroom_id"
        " JOIN session_closeouts c ON c.class_session_id = cs.id"
        " JOIN users cu ON cu.id = c.tutor_user_id"
        " JOIN session_closeout_approvals ap ON ap.id = c.current_approval_id"
        " JOIN users au ON au.id = ap.approved_by_user_id"
        " JOIN approval_requests rq ON rq.subject_id = c.id"
        " WHERE cs.cohort_id = :id ORDER BY cs.starts_at",
        {"id": cohort.id, "zone": ZONE},
    )
    assert [
        (s.day, s.starts, s.ends, s.state, s.actual_minutes, s.username, s.room,
         s.status, s.topics, s.performance, s.homework, s.next_steps,
         s.submitted_by, s.revision, s.approved_minutes, s.approved_by,
         s.approved, s.item)
        for s in sessions
    ] == [
        (day, "18:00", "19:30", "completed", 90, "demo.tutor2", "Aula 2",
         "approved", topics, PERFORMANCE, None, NEXT_STEPS, "demo.tutor2", 1, 90,
         "demo.coordinator", "21:30", "approved")
        for day, *_, topics in CLASSES
    ]  # fmt: skip
    marks = run_sql(
        migrated_url,
        "SELECT (cs.starts_at AT TIME ZONE :zone)::date AS day, a.student_id,"
        " a.status FROM class_session_attendance a"
        " JOIN class_sessions cs ON cs.id = a.class_session_id"
        " WHERE cs.cohort_id = :id",
        {"id": cohort.id, "zone": ZONE},
    )
    got = {(m.day, labels[m.student_id]): m.status for m in marks}
    assert got == {
        (day, key): status
        for day, s, a, b, _ in CLASSES
        for key, status in (("S", s), ("A", a), ("B", b))
    }


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
    # C1 / F6: the marker (here an inactive template without a cohort).
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    assert demo.main([], generators=generators_through(CHANGES_GENERATOR)) == 0

    async def add_template(session: Any) -> None:
        session.add(
            CourseTemplate(
                name=TEMPLATE_NAME, session_count=6, session_minutes=90, is_active=False
            )
        )
        await session.flush()

    local = DemoPastCohortGenerator(app_env=lambda: "local")
    assert asyncio.run(_run(migrated_url, add_template, local)) == [0]
    assert count(migrated_url, "course_cohorts") == 1  # #303's


def test_a_missing_second_tutor_setup_fails_the_run(migrated_url, env_for, clock):
    # F6: without staff.demo_tutor2 (and the #303 generators that need it),
    # courses.demo_past_cohort raises and nothing at all is written.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    skipped = {TUTOR2_GENERATOR, COURSES_GENERATOR, CHANGES_GENERATOR}
    generators = [g for g in demo.DEMO_GENERATORS if g.name not in skipped]

    with pytest.raises(DemoPrerequisiteMissingError, match="demo_past_cohort"):
        demo.main([], generators=generators)

    assert app_clock.now() == FROZEN_NOW
    assert count(migrated_url, "users") == 0
    assert count(migrated_url, "session_closeouts") == 0
    assert count(migrated_url, "course_templates") == 0
