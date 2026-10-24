"""The demo tutor (#299 C, E2, E6): ``staff.demo_tutor``."""

import asyncio
from typing import Any

import pytest

from app.core.db import create_sessionmaker
from app.core.uow import UnitOfWork
from app.modules.staff.models import TutorQualification
from tests.factories import demo
from tests.factories.base import GeneratorSkipped
from tests.factories.identity import DEMO_TUTOR_USERNAME
from tests.factories.staff import PROFESSIONAL_EXPERIENCE, DemoTutorGenerator
from tests.integration.conftest import run_sql, short_lived_engine
from tests.integration.demo_support import (
    DEMO_TUTOR_ROWS,
    FROZEN_NOW,
    TUTOR_GENERATOR,
    count,
    demo_env,
    migrate,
)


@pytest.fixture
def migrated_url(fresh_database_url: str) -> str:
    return migrate(fresh_database_url)


@pytest.fixture
def env_for(monkeypatch):
    return lambda app_env, dsn: demo_env(monkeypatch, app_env, dsn)


def tutor_id(url: str) -> Any:
    [row] = run_sql(
        url,
        "SELECT id FROM users WHERE username = :username",
        {"username": DEMO_TUTOR_USERNAME},
    )
    return row.id


def test_a_first_run_sets_up_demo_tutor(migrated_url, env_for, clock, capsys):
    clock.set(FROZEN_NOW)  # T = Wednesday 2026-11-04: D(4) + 7 = 2026-11-18
    env_for("local", migrated_url)

    assert demo.main([]) == 0

    assert f"{TUTOR_GENERATOR}: {DEMO_TUTOR_ROWS} rows" in (
        capsys.readouterr().out.splitlines()
    )
    tutor = tutor_id(migrated_url)
    [profile] = run_sql(
        migrated_url,
        "SELECT professional_experience FROM tutor_profiles WHERE user_id = :id",
        {"id": tutor},
    )
    assert profile.professional_experience == PROFESSIONAL_EXPERIENCE

    # C3: three approved, Física pending, each with its approval request.
    qualifications = run_sql(
        migrated_url,
        "SELECT s.name, l.name AS level, q.status, a.status AS request,"
        " a.requester_user_id"
        " FROM tutor_qualifications q JOIN subjects s ON s.id = q.subject_id"
        " JOIN education_levels l ON l.id = s.education_level_id"
        " JOIN approval_requests a ON a.subject_id = q.id"
        " AND a.request_type = 'staff.qualification'"
        " WHERE q.tutor_user_id = :id ORDER BY l.sort_order, s.name",
        {"id": tutor},
    )
    assert [(q.name, q.level, q.status, q.request) for q in qualifications] == [
        ("Matemáticas", "Primaria", "approved", "approved"),
        ("Inglés", "Secundaria", "approved", "approved"),
        ("Matemáticas", "Secundaria", "approved", "approved"),
        ("Física", "Preparatoria", "pending", "pending"),
    ]
    assert {q.requester_user_id for q in qualifications} == {tutor}
    open_items = run_sql(
        migrated_url,
        "SELECT count(*) FROM approval_requests"
        " WHERE status = 'pending' AND request_type = 'staff.qualification'",
    )
    assert open_items[0][0] == 1
    notifications = run_sql(
        migrated_url,
        "SELECT notification_type, count(*) AS n FROM notifications"
        " WHERE recipient_user_id = :id AND notification_type LIKE 'staff.%'"
        " GROUP BY notification_type",
        {"id": tutor},
    )
    assert [(n.notification_type, n.n) for n in notifications] == [
        ("staff.qualification_approved", 3)
    ]

    # C4: windows, the day off, limits and the preferred classroom.
    windows = run_sql(
        migrated_url,
        "SELECT weekday, to_char(starts_at, 'HH24:MI') AS s,"
        " to_char(ends_at, 'HH24:MI') AS e FROM tutor_availability_windows"
        " WHERE tutor_user_id = :id ORDER BY weekday",
        {"id": tutor},
    )
    assert [tuple(w) for w in windows] == [
        *((day, "15:00", "20:00") for day in range(1, 6)),
        (6, "09:00", "13:00"),
    ]
    [exception] = run_sql(
        migrated_url,
        "SELECT kind, starts_on::text, ends_on::text, starts_at, ends_at"
        " FROM tutor_availability_exceptions WHERE tutor_user_id = :id",
        {"id": tutor},
    )
    assert tuple(exception) == ("unavailable", "2026-11-18", "2026-11-18", None, None)
    [settings] = run_sql(
        migrated_url,
        "SELECT daily_limit_minutes, weekly_limit_minutes, required_break_minutes"
        " FROM tutor_availability_settings WHERE user_id = :id",
        {"id": tutor},
    )
    assert tuple(settings) == (240, 1200, 10)
    [preferred] = run_sql(
        migrated_url,
        "SELECT c.name FROM tutor_preferred_classrooms p"
        " JOIN classrooms c ON c.id = p.classroom_id WHERE p.tutor_user_id = :id",
        {"id": tutor},
    )
    assert preferred.name == "Aula 1"
    # No change request: the availability came before any session.
    assert count(migrated_url, "tutor_availability_change_requests") == 0


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


def test_a_tutor_with_a_qualification_means_0_rows(migrated_url, env_for):
    # E6: the marker (any qualification, even without a profile).
    env_for("local", migrated_url)
    assert demo.main([], generators=demo.DEMO_GENERATORS[:9]) == 0
    tutor = tutor_id(migrated_url)
    [subject] = run_sql(migrated_url, "SELECT id FROM subjects LIMIT 1")

    async def add_qualification(session: Any) -> None:
        session.add(
            TutorQualification(
                tutor_user_id=tutor, subject_id=subject.id, status="pending"
            )
        )
        await session.flush()

    local = DemoTutorGenerator(app_env=lambda: "local")
    assert asyncio.run(_run(migrated_url, add_qualification, local)) == [0]
    assert count(migrated_url, "tutor_profiles") == 0
    assert count(migrated_url, "tutor_availability_windows") == 0
