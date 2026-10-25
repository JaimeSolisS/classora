"""The second demo tutor (#303 A1, B, E2, E6): ``identity.demo_tutor2`` and
``staff.demo_tutor2``."""

import asyncio
from typing import Any

import pytest

from app.core.db import create_sessionmaker
from app.core.uow import UnitOfWork
from app.modules.staff.models import TutorQualification
from tests.factories import demo
from tests.factories.base import GeneratorSkipped
from tests.factories.identity import DEMO_TUTOR2_USERNAME
from tests.factories.staff import DemoSecondTutorGenerator
from tests.integration.conftest import run_sql, short_lived_engine
from tests.integration.demo_support import (
    CATALOG_GENERATOR,
    DEMO_TUTOR2_ROWS,
    FROZEN_NOW,
    TUTOR2_GENERATOR,
    count,
    demo_env,
    generators_through,
    migrate,
)


@pytest.fixture
def migrated_url(fresh_database_url: str) -> str:
    return migrate(fresh_database_url)


@pytest.fixture
def env_for(monkeypatch):
    return lambda app_env, dsn: demo_env(monkeypatch, app_env, dsn)


def tutor2_id(url: str) -> Any:
    [row] = run_sql(
        url,
        "SELECT id FROM users WHERE username = :username",
        {"username": DEMO_TUTOR2_USERNAME},
    )
    return row.id


def test_a_first_run_sets_up_demo_tutor2(migrated_url, env_for, clock, capsys):
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)

    assert demo.main([]) == 0

    lines = capsys.readouterr().out.splitlines()
    # A1: the account right after demo.guardian, its setup right after the
    # #299 schedule.
    assert lines.index("identity.demo_tutor2: 1 rows") == (
        lines.index("identity.demo_guardian: 1 rows") + 1
    )
    assert lines.index(f"{TUTOR2_GENERATOR}: {DEMO_TUTOR2_ROWS} rows") == (
        lines.index("scheduling.demo_schedule: 111 rows") + 1
    )
    tutor = tutor2_id(migrated_url)
    [account] = run_sql(
        migrated_url,
        "SELECT u.display_name, u.state, array_agg(r.code) AS roles FROM users u"
        " JOIN user_roles ur ON ur.user_id = u.id JOIN roles r ON r.id = ur.role_id"
        " WHERE u.id = :id GROUP BY u.id",
        {"id": tutor},
    )
    assert tuple(account) == ("Demo Second Tutor", "active", ["tutor"])

    # B2: requested by the tutor, approved by demo.owner.
    qualifications = run_sql(
        migrated_url,
        "SELECT s.name, l.name AS level, q.status, a.status AS request,"
        " a.requester_user_id, d.username AS decided_by"
        " FROM tutor_qualifications q JOIN subjects s ON s.id = q.subject_id"
        " JOIN education_levels l ON l.id = s.education_level_id"
        " JOIN approval_requests a ON a.subject_id = q.id"
        " AND a.request_type = 'staff.qualification'"
        " JOIN users d ON d.id = a.resolved_by_user_id"
        " WHERE q.tutor_user_id = :id ORDER BY s.name",
        {"id": tutor},
    )
    assert [
        (q.name, q.level, q.status, q.request, q.decided_by) for q in qualifications
    ] == [
        ("Ciencias", "Secundaria", "approved", "approved", "demo.owner"),
        ("Matemáticas", "Secundaria", "approved", "approved", "demo.owner"),
    ]
    assert {q.requester_user_id for q in qualifications} == {tutor}
    notifications = run_sql(
        migrated_url,
        "SELECT notification_type, count(*) AS n FROM notifications"
        " WHERE recipient_user_id = :id AND notification_type LIKE 'staff.%'"
        " GROUP BY notification_type",
        {"id": tutor},
    )
    assert [(n.notification_type, n.n) for n in notifications] == [
        ("staff.qualification_approved", 2)
    ]

    # B3: Monday-Friday 15:00-20:00; no exception, limits, preferred room or
    # profile.
    windows = run_sql(
        migrated_url,
        "SELECT weekday, to_char(starts_at, 'HH24:MI') AS s,"
        " to_char(ends_at, 'HH24:MI') AS e FROM tutor_availability_windows"
        " WHERE tutor_user_id = :id ORDER BY weekday",
        {"id": tutor},
    )
    assert [tuple(w) for w in windows] == [
        (day, "15:00", "20:00") for day in range(1, 6)
    ]
    [settings] = run_sql(
        migrated_url,
        "SELECT daily_limit_minutes, weekly_limit_minutes, required_break_minutes"
        " FROM tutor_availability_settings WHERE user_id = :id",
        {"id": tutor},
    )
    assert tuple(settings) == (None, None, None)
    for table, column in (
        ("tutor_availability_exceptions", "tutor_user_id"),
        ("tutor_preferred_classrooms", "tutor_user_id"),
        ("tutor_profiles", "user_id"),
    ):
        rows = run_sql(
            migrated_url,
            f"SELECT count(*) FROM {table} WHERE {column} = :id",  # noqa: S608
            {"id": tutor},
        )
        assert rows[0][0] == 0, table


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


def test_a_tutor2_with_a_qualification_means_0_rows(migrated_url, env_for):
    # E6 / B1: the marker (any qualification, even without availability).
    env_for("local", migrated_url)
    assert demo.main([], generators=generators_through(CATALOG_GENERATOR)) == 0
    tutor = tutor2_id(migrated_url)
    [subject] = run_sql(migrated_url, "SELECT id FROM subjects LIMIT 1")

    async def add_qualification(session: Any) -> None:
        session.add(
            TutorQualification(
                tutor_user_id=tutor, subject_id=subject.id, status="pending"
            )
        )
        await session.flush()

    local = DemoSecondTutorGenerator(app_env=lambda: "local")
    assert asyncio.run(_run(migrated_url, add_qualification, local)) == [0]
    assert count(migrated_url, "tutor_availability_windows") == 0
    assert count(migrated_url, "approval_requests") == 0
