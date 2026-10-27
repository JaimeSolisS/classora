"""``demo.tutor``'s held classes (#353 B, F2, F6): the
``scheduling.demo_held_sessions`` generator, through the route services
under the seed clock."""

import asyncio
import dataclasses
from datetime import date
from typing import Any

import pytest

from app.core import clock as app_clock
from app.core.db import create_sessionmaker
from app.core.uow import UnitOfWork
from app.modules.scheduling.errors import SchedulingConflictError
from tests.factories import demo
from tests.factories.base import GeneratorSkipped
from tests.factories.demo_academics import (
    H1_HOMEWORK,
    NEXT_STEPS,
    PERFORMANCE,
    DemoAcademicsPlan,
    DemoHeldSessionsGenerator,
    plan_demo_academics,
)
from tests.integration.conftest import run_sql, short_lived_engine
from tests.integration.demo_support import (
    ALL_TABLE_COUNTS,
    CHANGES_GENERATOR,
    FIRST_RUN_LINES,
    FROZEN_NOW,
    HELD_SESSIONS_GENERATOR,
    count,
    demo_env,
    demo_table_counts,
    generators_through,
    migrate,
)
from tests.integration.test_demo_schedule import ZONE, student_labels

# T = Wednesday 2026-11-04: P(6) = Tuesday 10-27, P(7) = Monday 10-26,
# P(8) = Friday 10-23; T0 = Wednesday 10-14 12:00.
HELD = [
    # Starts, student, subject, attendance, minutes, topics, homework.
    ("2026-10-23 16:00", "S", "Inglés (Secundaria)", "present", 60,
     "Comprensión de lectura: textos informativos", None),
    ("2026-10-26 16:00", "Sib", "Matemáticas (Primaria)", "late", 55,
     "Sumas y restas de fracciones", None),
    ("2026-10-27 16:00", "S", "Matemáticas (Secundaria)", "present", 60,
     "Despeje de ecuaciones de primer grado", H1_HOMEWORK),
]  # fmt: skip


@pytest.fixture
def migrated_url(fresh_database_url: str) -> str:
    return migrate(fresh_database_url)


@pytest.fixture
def env_for(monkeypatch):
    return lambda app_env, dsn: demo_env(monkeypatch, app_env, dsn)


def held_rows(url: str) -> list[Any]:
    """``demo.tutor``'s closed-out classes with their closeout and
    attendance, by start."""
    return run_sql(
        url,
        "SELECT to_char(cs.starts_at AT TIME ZONE :zone, 'YYYY-MM-DD HH24:MI')"
        " AS starts, to_char(cs.ends_at AT TIME ZONE :zone, 'HH24:MI') AS ends,"
        " cs.kind, cs.state, cs.actual_minutes, r.name AS room,"
        " s.name || ' (' || l.name || ')' AS subject,"
        " a.student_id, a.status AS attendance, am.username AS marked_by,"
        " to_char(a.marked_at AT TIME ZONE :zone, 'YYYY-MM-DD HH24:MI') AS marked,"
        " c.status, c.topics, c.performance, c.homework, c.next_steps,"
        " to_char(c.submitted_at AT TIME ZONE :zone, 'YYYY-MM-DD HH24:MI')"
        " AS submitted, ap.revision, ap.approved_minutes, ap.source,"
        " au.username AS approved_by,"
        " to_char(ap.approved_at AT TIME ZONE :zone, 'YYYY-MM-DD HH24:MI')"
        " AS approved, rq.status AS item, ru.username AS item_resolved_by"
        " FROM class_sessions cs"
        " JOIN users tu ON tu.id = cs.tutor_user_id"
        " JOIN classrooms r ON r.id = cs.classroom_id"
        " JOIN subjects s ON s.id = cs.subject_id"
        " JOIN education_levels l ON l.id = s.education_level_id"
        " JOIN class_session_attendance a ON a.class_session_id = cs.id"
        " JOIN users am ON am.id = a.marked_by_user_id"
        " JOIN session_closeouts c ON c.class_session_id = cs.id"
        " JOIN session_closeout_approvals ap ON ap.id = c.current_approval_id"
        " JOIN users au ON au.id = ap.approved_by_user_id"
        " JOIN approval_requests rq ON rq.subject_id = c.id"
        " AND rq.request_type = 'academics.session_closeout'"
        " LEFT JOIN users ru ON ru.id = rq.resolved_by_user_id"
        " WHERE tu.username = 'demo.tutor' ORDER BY cs.starts_at",
        {"zone": ZONE},
    )


def test_a_first_run_writes_the_held_classes(migrated_url, env_for, clock, capsys):
    # F2 on Wednesday 2026-11-04, 11:00 local.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)

    assert demo.main([]) == 0

    assert capsys.readouterr().out.splitlines() == FIRST_RUN_LINES
    # The seed clock was restored.
    assert app_clock.now() == FROZEN_NOW
    labels = student_labels(migrated_url)
    rows = held_rows(migrated_url)
    assert [
        (
            r.starts, r.ends, r.kind, r.state, r.room, r.subject,
            labels[r.student_id], r.attendance, r.actual_minutes, r.topics,
            r.homework,
        )
        for r in rows
    ] == [
        (starts, "17:00", "individual", "completed", "Aula 1", subject, student,
         attendance, minutes, topics, homework)
        for starts, student, subject, attendance, minutes, topics, homework in HELD
    ]  # fmt: skip
    for r in rows:
        day = r.starts[:10]
        # B3: marked and submitted by demo.tutor at the end + 15 minutes,
        # approved by demo.coordinator at the end + 2 hours (revision 1).
        assert (r.marked_by, r.marked, r.submitted) == (
            "demo.tutor",
            f"{day} 17:15",
            f"{day} 17:15",
        )
        assert (r.status, r.performance, r.next_steps) == (
            "approved",
            PERFORMANCE,
            NEXT_STEPS,
        )
        assert (r.revision, r.approved_minutes, r.source, r.approved_by) == (
            1,
            r.actual_minutes,
            "review",
            "demo.coordinator",
        )
        assert r.approved == f"{day} 19:00"
        assert (r.item, r.item_resolved_by) == ("approved", "demo.coordinator")

    # B2: the offers, accepted by demo.tutor.
    offers = run_sql(
        migrated_url,
        "SELECT o.status, u.username FROM assignment_offers o"
        " JOIN assignment_offer_sessions x ON x.offer_id = o.id"
        " JOIN session_closeouts c ON c.class_session_id = x.class_session_id"
        " JOIN users u ON u.id = o.tutor_user_id WHERE u.username = 'demo.tutor'",
    )
    assert [tuple(o) for o in offers] == [("accepted", "demo.tutor")] * 3
    # No overdue demo class and no pending closeout item (#353 G4).
    pending = run_sql(
        migrated_url,
        "SELECT count(*) FROM approval_requests WHERE status = 'pending'"
        " AND request_type = 'academics.session_closeout'",
    )
    assert pending[0][0] == 0
    unclosed = run_sql(
        migrated_url,
        "SELECT count(*) FROM class_sessions cs WHERE cs.ends_at < :now"
        " AND cs.state = 'confirmed'",
        {"now": FROZEN_NOW},
    )
    assert unclosed[0][0] == 0
    # The #353 notifications of demo.tutor: 3 offers and 3 approvals.
    approved = run_sql(
        migrated_url,
        "SELECT count(*) FROM notifications n JOIN users u"
        " ON u.id = n.recipient_user_id WHERE u.username = 'demo.tutor'"
        " AND n.notification_type = 'academics.closeout_approved'",
    )
    assert approved[0][0] == 3
    assert count(migrated_url, "users") == 8
    assert count(migrated_url, "students") == 5
    assert count(migrated_url, "families") == 3
    assert demo_table_counts(migrated_url, ALL_TABLE_COUNTS) == ALL_TABLE_COUNTS


async def _run(url: str, *generators: Any) -> list[int | GeneratorSkipped]:
    engine = short_lived_engine(url)
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            return [await g.generate(uow.session) for g in generators]
    finally:
        await engine.dispose()


def test_a_closeout_of_s_means_0_rows(migrated_url, env_for, clock):
    # B1: the marker. The generator runs twice in one Unit of Work.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    assert demo.main([], generators=generators_through(CHANGES_GENERATOR)) == 0

    local = DemoHeldSessionsGenerator(app_env=lambda: "local")
    assert asyncio.run(_run(migrated_url, local, local)) == [30, 0]
    assert count(migrated_url, "session_closeouts") == 3
    assert app_clock.now() == FROZEN_NOW


def test_a_refused_service_call_rolls_the_run_back(migrated_url, env_for, clock):
    # F6: a plan double that moves H1 onto P(1) 16:00, where #299's P1 has S;
    # the overlap check refuses it, nothing is written and the clock is back.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)

    def onto_p1(today: date) -> DemoAcademicsPlan:
        plan = plan_demo_academics(today)
        h1, *rest = plan.held
        return dataclasses.replace(
            plan, held=(dataclasses.replace(h1, day=plan.cohort[-1].day), *rest)
        )

    generators = [
        *generators_through(CHANGES_GENERATOR),
        DemoHeldSessionsGenerator(plan=onto_p1),
    ]
    assert generators[-1].name == HELD_SESSIONS_GENERATOR
    with pytest.raises(SchedulingConflictError):
        demo.main([], generators=generators)

    assert app_clock.now() == FROZEN_NOW
    assert count(migrated_url, "users") == 0
    assert count(migrated_url, "class_sessions") == 0
    assert count(migrated_url, "session_closeouts") == 0
