"""The E2E attendance fixture (#76 E5, H9; #77 H8):
``python -m tests.factories.e2e_attendance`` after ``make seed-demo``.

One started ``confirmed`` group session of ``demo.tutor`` with two students,
in the latest free hour before now (never on the #299 demo schedule's
sessions), and (#77) one started ``confirmed`` individual session with one
student in the latest free hour before that one; idempotent, guarded by
``APP_ENV``, counts only.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.core.db import create_sessionmaker
from app.core.uow import UnitOfWork
from tests.factories import demo, e2e_attendance
from tests.factories.e2e_attendance import (
    CLOSEOUT_SUBJECT_NAME,
    SUBJECT_NAME,
    latest_free_slot,
)
from tests.factories.scheduling import persist_class_session
from tests.integration.conftest import run_sql, short_lived_engine
from tests.integration.demo_support import FROZEN_NOW, demo_env, migrate

FIRST_RUN = f"{e2e_attendance.NAME}: 12 rows"
RERUN = f"{e2e_attendance.NAME}: 0 rows"


@pytest.fixture
def migrated_url(fresh_database_url: str) -> str:
    return migrate(fresh_database_url)


@pytest.fixture
def env_for(monkeypatch):
    return lambda app_env, dsn: demo_env(monkeypatch, app_env, dsn)


def fixture_sessions(url: str, subject: str = SUBJECT_NAME) -> list[Any]:
    return run_sql(
        url,
        "SELECT s.id, s.kind, s.state, s.starts_at, s.ends_at, s.classroom_id,"
        " s.first_confirmed_at, s.tutor_user_id,"
        " (SELECT count(*) FROM class_session_participants p"
        "  WHERE p.class_session_id = s.id) AS students"
        " FROM class_sessions s JOIN subjects j ON j.id = s.subject_id"
        " JOIN users u ON u.id = s.tutor_user_id"
        " WHERE j.name = :subject AND u.username = 'demo.tutor'",
        {"subject": subject},
    )


def overlaps(url: str, session_id: Any) -> int:
    [row] = run_sql(
        url,
        "SELECT count(*) AS n FROM class_sessions o, class_sessions s"
        " WHERE s.id = :id AND o.id <> s.id AND o.tutor_user_id = s.tutor_user_id"
        " AND o.state IN ('offered', 'confirmed', 'completed')"
        " AND o.starts_at < s.ends_at AND o.ends_at > s.starts_at",
        {"id": session_id},
    )
    return int(row.n)


def test_after_the_demo_it_inserts_one_started_session(
    migrated_url, env_for, clock, capsys
):
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    assert demo.main([]) == 0
    capsys.readouterr()

    assert e2e_attendance.main([]) == 0

    assert capsys.readouterr().out.splitlines() == [FIRST_RUN]
    [made] = fixture_sessions(migrated_url)
    assert (made.kind, made.state, made.classroom_id, made.students) == (
        "group",
        "confirmed",
        None,
        2,
    )
    assert made.ends_at <= FROZEN_NOW
    assert made.ends_at - made.starts_at == timedelta(minutes=60)
    assert made.starts_at >= FROZEN_NOW - timedelta(days=7)
    assert made.first_confirmed_at is not None
    assert overlaps(migrated_url, made.id) == 0
    # #77: the closeout session ends at or before the attendance one starts.
    [closeout] = fixture_sessions(migrated_url, CLOSEOUT_SUBJECT_NAME)
    assert (
        closeout.kind,
        closeout.state,
        closeout.classroom_id,
        closeout.students,
    ) == ("individual", "confirmed", None, 1)
    assert closeout.ends_at <= made.starts_at
    assert closeout.ends_at - closeout.starts_at == timedelta(minutes=60)
    assert closeout.first_confirmed_at is not None
    assert overlaps(migrated_url, closeout.id) == 0

    # A rerun (even later) writes nothing.
    clock.set(FROZEN_NOW + timedelta(days=1))
    assert e2e_attendance.main([]) == 0
    assert capsys.readouterr().out.splitlines() == [RERUN]
    assert len(fixture_sessions(migrated_url)) == 1
    assert len(fixture_sessions(migrated_url, CLOSEOUT_SUBJECT_NAME)) == 1


def test_a_busy_hour_is_skipped(migrated_url, env_for, clock, capsys):
    now = FROZEN_NOW + timedelta(minutes=30)
    clock.set(now)
    env_for("local", migrated_url)
    assert demo.main([]) == 0
    [tutor] = run_sql(
        migrated_url, "SELECT id FROM users WHERE username = 'demo.tutor'"
    )
    hour = now.replace(minute=0)

    async def _busy() -> None:
        engine = short_lived_engine(migrated_url)
        try:
            async with UnitOfWork(create_sessionmaker(engine)) as uow:
                await persist_class_session(
                    uow.session,
                    state="confirmed",
                    tutor_user_id=tutor.id,
                    starts_at=hour - timedelta(minutes=90),
                    ends_at=hour,
                    scheduled_minutes=90,
                )
        finally:
            await engine.dispose()

    asyncio.run(_busy())
    capsys.readouterr()

    assert e2e_attendance.main([]) == 0

    [made] = fixture_sessions(migrated_url)
    assert made.ends_at <= hour - timedelta(minutes=90)
    assert overlaps(migrated_url, made.id) == 0


@pytest.mark.parametrize("app_env", ["prod"])
def test_other_environments_are_refused_before_connecting(monkeypatch, app_env, capsys):
    demo_env(monkeypatch, app_env, "postgresql://x:y@127.0.0.1:1/never")

    def no_engine(settings: Any) -> Any:
        raise AssertionError("connected")

    assert e2e_attendance.main([], engine_factory=no_engine) == 2
    assert "Refusing to generate demo data" in capsys.readouterr().err


def test_without_demo_tutor_it_exits_1(migrated_url, env_for, capsys):
    env_for("local", migrated_url)

    assert e2e_attendance.main([]) == 1

    assert "demo.tutor must exist" in capsys.readouterr().err
    assert run_sql(migrated_url, "SELECT count(*) AS n FROM subjects")[0].n == 0


NOW = datetime(2031, 3, 5, 18, 20, tzinfo=UTC)


@pytest.mark.parametrize(
    ("busy", "expected_end"),
    [
        ([], NOW.replace(minute=0)),
        ([(NOW - timedelta(hours=1), NOW)], NOW.replace(minute=0) - timedelta(hours=1)),
        # Back-to-back is free (half-open ranges).
        ([(NOW.replace(minute=0), NOW + timedelta(hours=1))], NOW.replace(minute=0)),
        (
            [(NOW - timedelta(hours=3, minutes=30), NOW)],
            NOW.replace(minute=0) - timedelta(hours=4),
        ),
    ],
)
def test_latest_free_slot(busy, expected_end):
    start, end = latest_free_slot(NOW, busy)  # type: ignore[misc]

    assert end == expected_end
    assert end - start == timedelta(minutes=60)


def test_a_full_week_has_no_slot():
    assert latest_free_slot(NOW, [(NOW - timedelta(days=8), NOW)]) is None
