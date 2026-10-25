"""Concurrent attendance writes (#76 H4), with two connections (``race``
from ``test_assignment_offer_concurrency``): transaction A writes and keeps
its transaction open for a moment; B starts once A's change is flushed, so
B waits on the session row lock and then sees A's version.

- two tutor ``PUT``s with the same ``version``: one ``ok``, one
  ``version_conflict``;
- a staff correction racing a tutor ``PUT`` (both orders): the same.

Each case runs 5 times, on new sessions.
"""

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal
from app.modules.identity.permissions import OWNER, ROLE_PERMISSIONS, TUTOR
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.attendance_schemas import (
    AttendanceCorrectionCreate,
    AttendanceMark,
)
from app.modules.scheduling.models import ClassSession
from tests.conftest import frozen_clock
from tests.factories.identity import persist_user
from tests.factories.scheduling import add_participant, persist_class_session
from tests.integration.conftest import run_sql_async
from tests.integration.test_assignment_offer_concurrency import (
    REPEATS,
    _in_uow,
    race,
)

pytestmark = pytest.mark.anyio

NOW = datetime(2031, 3, 5, 18, 0, tzinfo=UTC)

Change = Callable[[AsyncSession], Awaitable[Any]]


@pytest.fixture(autouse=True)
def frozen() -> Any:
    with frozen_clock(NOW) as clock:
        yield clock


def _principal(user_id: uuid.UUID, role: str) -> Principal:
    return Principal(
        user_id=user_id,
        roles=frozenset({role}),
        permissions=frozenset(ROLE_PERMISSIONS[role]),
    )


async def _lesson(url: str) -> tuple[uuid.UUID, uuid.UUID, list[uuid.UUID]]:
    """A started confirmed group session with two students: ``(session id,
    tutor id, students)``."""

    async def _make(session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID, list[Any]]:
        tutor = await persist_user(session, roles=(TUTOR,))
        made: ClassSession = await persist_class_session(
            session,
            kind="group",
            state="confirmed",
            tutor_user_id=tutor.id,
            starts_at=NOW - timedelta(minutes=30),
            ends_at=NOW + timedelta(minutes=30),
            scheduled_minutes=60,
        )
        students = [(await add_participant(session, made)).student_id for _ in range(2)]
        return made.id, tutor.id, students

    return await _in_uow(url, _make)


def _mark(
    session_id: uuid.UUID, tutor: uuid.UUID, student: uuid.UUID, status: str
) -> Change:
    async def _run(db: AsyncSession) -> None:
        await scheduling.record_attendance(
            db,
            actor=_principal(tutor, TUTOR),
            class_session_id=session_id,
            version=1,
            marks=[AttendanceMark(student_id=student, status=status)],  # type: ignore[arg-type]
        )

    return _run


def _correct(session_id: uuid.UUID, owner: uuid.UUID, student: uuid.UUID) -> Change:
    async def _run(db: AsyncSession) -> None:
        await scheduling.correct_attendance(
            db,
            actor=_principal(owner, OWNER),
            class_session_id=session_id,
            body=AttendanceCorrectionCreate(
                version=1, student_id=student, status="absent", reason="Recepción"
            ),
        )

    return _run


async def _version_and_rows(url: str, session_id: uuid.UUID) -> tuple[int, int]:
    [row] = await run_sql_async(
        url,
        "SELECT s.version, (SELECT count(*) FROM class_session_attendance a"
        " WHERE a.class_session_id = s.id) AS marks FROM class_sessions s"
        " WHERE s.id = :id",
        {"id": session_id},
    )
    return int(row.version), int(row.marks)


async def test_two_tutor_puts_with_one_version(seeded_url):
    for _ in range(REPEATS):
        session_id, tutor, (first, second) = await _lesson(seeded_url)

        outcomes = await race(
            seeded_url,
            _mark(session_id, tutor, first, "present"),
            _mark(session_id, tutor, second, "late"),
        )

        assert outcomes == ["ok", "version_conflict"]
        assert await _version_and_rows(seeded_url, session_id) == (2, 1)


@pytest.mark.parametrize("correction_first", [False, True])
async def test_a_correction_racing_a_tutor_put(seeded_url, correction_first):
    owner_id = await _in_uow(seeded_url, lambda db: _owner_id(db))
    for _ in range(REPEATS):
        session_id, tutor, (first, second) = await _lesson(seeded_url)
        tutor_put = _mark(session_id, tutor, second, "late")
        correction = _correct(session_id, owner_id, first)
        changes = (
            (correction, tutor_put) if correction_first else (tutor_put, correction)
        )

        outcomes = await race(seeded_url, *changes)

        assert outcomes == ["ok", "version_conflict"]
        assert await _version_and_rows(seeded_url, session_id) == (2, 1)


async def _owner_id(session: AsyncSession) -> uuid.UUID:
    return (await persist_user(session, roles=(OWNER,))).id
