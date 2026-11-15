"""Concurrent closeout writes (#77 D5, H3), with two connections (``race``
from ``test_assignment_offer_concurrency``): transaction A writes and keeps
its transaction open for a moment; B starts once A's change is flushed, so
B waits on the session row lock and then sees A's version.

- two submits with the same ``version``: one ``ok``, one
  ``version_conflict`` (one closeout, one inbox item);
- an attendance ``PUT`` racing a submit, both orders: the session row lock
  serializes them, the second gets ``version_conflict``.

Each case runs 5 times, on new sessions.
"""

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal
from app.modules.identity.permissions import ROLE_PERMISSIONS, TUTOR
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.attendance_schemas import AttendanceMark
from app.modules.scheduling.models import ClassSession
from app.wiring import register_approval_wiring
from app.workflows import session_closeout as workflow
from app.workflows.session_closeout_schemas import SessionCloseoutSubmit
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

SUMMARY = {"topics": "Fracciones", "performance": "Bien", "next_steps": "Decimales"}


@pytest.fixture(autouse=True)
def frozen() -> Any:
    register_approval_wiring()
    with frozen_clock(NOW) as clock:
        yield clock


def _tutor(user_id: uuid.UUID) -> Principal:
    return Principal(
        user_id=user_id,
        roles=frozenset({TUTOR}),
        permissions=frozenset(ROLE_PERMISSIONS[TUTOR]),
    )


async def _lesson(url: str) -> tuple[uuid.UUID, uuid.UUID, list[uuid.UUID]]:
    """A started confirmed group session with two students, both marked
    ``present`` (version 2): ``(session id, tutor id, students)``."""

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

    session_id, tutor, students = await _in_uow(url, _make)

    async def _mark(session: AsyncSession) -> None:
        await scheduling.record_attendance(
            session,
            actor=_tutor(tutor),
            class_session_id=session_id,
            version=1,
            marks=[
                AttendanceMark(student_id=student, status="present")
                for student in students
            ],
        )

    await _in_uow(url, _mark)
    return session_id, tutor, students


def _submit(session_id: uuid.UUID, tutor: uuid.UUID) -> Change:
    async def _run(db: AsyncSession) -> None:
        await workflow.submit_session_closeout(
            db,
            principal=_tutor(tutor),
            class_session_id=session_id,
            body=SessionCloseoutSubmit.model_validate(
                {"version": 2, "actual_minutes": 55, "summary": SUMMARY}
            ),
        )

    return _run


def _put(session_id: uuid.UUID, tutor: uuid.UUID, student: uuid.UUID) -> Change:
    async def _run(db: AsyncSession) -> None:
        await scheduling.record_attendance(
            db,
            actor=_tutor(tutor),
            class_session_id=session_id,
            version=2,
            marks=[AttendanceMark(student_id=student, status="late")],
        )

    return _run


async def _state(url: str, session_id: uuid.UUID) -> tuple[str, int, int, int]:
    [row] = await run_sql_async(
        url,
        "SELECT s.state, s.version,"
        " (SELECT count(*) FROM session_closeouts c"
        "  WHERE c.class_session_id = s.id) AS closeouts,"
        " (SELECT count(*) FROM approval_requests r JOIN session_closeouts c"
        "  ON r.subject_id = c.id WHERE c.class_session_id = s.id) AS requests"
        " FROM class_sessions s WHERE s.id = :id",
        {"id": session_id},
    )
    return str(row.state), int(row.version), int(row.closeouts), int(row.requests)


async def test_two_submits_with_one_version(seeded_url):
    for _ in range(REPEATS):
        session_id, tutor, _students = await _lesson(seeded_url)

        outcomes = await race(
            seeded_url, _submit(session_id, tutor), _submit(session_id, tutor)
        )

        assert outcomes == ["ok", "version_conflict"]
        assert await _state(seeded_url, session_id) == ("completed", 3, 1, 1)


@pytest.mark.parametrize("submit_first", [False, True])
async def test_an_attendance_put_racing_a_submit(seeded_url, submit_first):
    for _ in range(REPEATS):
        session_id, tutor, (first, _second) = await _lesson(seeded_url)
        submit = _submit(session_id, tutor)
        put = _put(session_id, tutor, first)

        outcomes = await race(
            seeded_url, *((submit, put) if submit_first else (put, submit))
        )

        assert outcomes == ["ok", "version_conflict"]
        if submit_first:
            assert await _state(seeded_url, session_id) == ("completed", 3, 1, 1)
        else:
            assert await _state(seeded_url, session_id) == ("confirmed", 3, 0, 0)
