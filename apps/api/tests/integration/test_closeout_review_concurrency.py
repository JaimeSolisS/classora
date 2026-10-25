"""Concurrent closeout review writes (#78 H1, H2, I3), with two connections
(``race`` from ``test_assignment_offer_concurrency``): transaction A writes
and keeps its transaction open for a moment; B starts once A's change is
flushed, so B waits on the closeout (or session) row lock and then sees
A's committed state.

- two approves with one version → ``ok`` / ``version_conflict``;
- approve vs request changes → one wins, the other ``version_conflict``;
- the tutor's resubmission vs a staff correction → one ``ok``, one ``409``;
- the tutor's attendance ``PUT`` vs the tutor's resubmission, both orders:
  serialized by the session row (the resubmission sees the committed mark,
  or the ``PUT`` is ``attendance_locked`` once the closeout is
  ``submitted``);
- two staff corrections of an approved closeout → consecutive revisions or
  a ``409``, never two revisions superseding the same one.

Each case runs 5 times, on new sessions.
"""

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal
from app.modules.academics import service as academics
from app.modules.identity.permissions import OWNER, ROLE_PERMISSIONS
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.attendance_schemas import AttendanceMark
from app.workflows import session_closeout_review as review
from tests.factories.identity import persist_user
from tests.integration.conftest import run_sql_async
from tests.integration.test_assignment_offer_concurrency import (
    REPEATS,
    _in_uow,
    race,
)
from tests.integration.test_session_closeout_concurrency import (
    SUMMARY,
    _lesson,
    _submit,
    _tutor,
    frozen,
)

pytestmark = pytest.mark.anyio

__all__ = ["frozen"]  # the autouse fixture (wiring, frozen clock)

Change = Callable[[AsyncSession], Awaitable[Any]]


def _owner(user_id: uuid.UUID) -> Principal:
    return Principal(
        user_id=user_id,
        roles=frozenset({OWNER}),
        permissions=frozenset(ROLE_PERMISSIONS[OWNER]),
    )


async def _reviewer(url: str) -> uuid.UUID:
    async def _make(session: AsyncSession) -> uuid.UUID:
        return (await persist_user(session, roles=(OWNER,))).id

    reviewer: uuid.UUID = await _in_uow(url, _make)
    return reviewer


async def _submitted(url: str) -> tuple[uuid.UUID, uuid.UUID, list[uuid.UUID]]:
    """A submitted closeout (closeout version 1, session version 3)."""
    session_id, tutor, students = await _lesson(url)
    await _in_uow(url, _submit(session_id, tutor))
    return session_id, tutor, students


def _approve(session_id: uuid.UUID, reviewer: uuid.UUID, version: int) -> Change:
    async def _run(db: AsyncSession) -> None:
        await review.approve_closeout(
            db,
            principal=_owner(reviewer),
            class_session_id=session_id,
            body=academics.CloseoutApprove(version=version),
        )

    return _run


def _send_back(session_id: uuid.UUID, reviewer: uuid.UUID, version: int) -> Change:
    async def _run(db: AsyncSession) -> None:
        await review.request_closeout_changes(
            db,
            principal=_owner(reviewer),
            class_session_id=session_id,
            body=academics.CloseoutRequestChanges.model_validate(
                {"version": version, "reason": "Añade la tarea"}
            ),
        )

    return _run


def _correct(
    session_id: uuid.UUID, reviewer: uuid.UUID, version: int, minutes: int
) -> Change:
    async def _run(db: AsyncSession) -> None:
        await review.correct_closeout(
            db,
            principal=_owner(reviewer),
            class_session_id=session_id,
            body=academics.CloseoutCorrection.model_validate(
                {"version": version, "actual_minutes": minutes, "reason": "Ajuste"}
            ),
        )

    return _run


def _resubmit(session_id: uuid.UUID, tutor: uuid.UUID, version: int) -> Change:
    async def _run(db: AsyncSession) -> None:
        await review.resubmit_closeout(
            db,
            principal=_tutor(tutor),
            class_session_id=session_id,
            body=academics.CloseoutResubmit.model_validate(
                {"version": version, "actual_minutes": 55, "summary": SUMMARY}
            ),
        )

    return _run


def _mark(session_id: uuid.UUID, tutor: uuid.UUID, student: uuid.UUID) -> Change:
    async def _run(db: AsyncSession) -> None:
        await scheduling.record_attendance(
            db,
            actor=_tutor(tutor),
            class_session_id=session_id,
            version=3,
            marks=[AttendanceMark(student_id=student, status="late")],
        )

    return _run


async def _closeout(url: str, session_id: uuid.UUID) -> tuple[str, int, int]:
    [row] = await run_sql_async(
        url,
        "SELECT c.status, c.version,"
        " (SELECT count(*) FROM session_closeout_approvals a"
        "  WHERE a.closeout_id = c.id) AS revisions"
        " FROM session_closeouts c WHERE c.class_session_id = :id",
        {"id": session_id},
    )
    return str(row.status), int(row.version), int(row.revisions)


async def test_two_approves_with_one_version(seeded_url):
    reviewer = await _reviewer(seeded_url)
    for _ in range(REPEATS):
        session_id, _tutor_id, _students = await _submitted(seeded_url)

        outcomes = await race(
            seeded_url,
            _approve(session_id, reviewer, 1),
            _approve(session_id, reviewer, 1),
        )

        assert outcomes == ["ok", "version_conflict"]
        assert await _closeout(seeded_url, session_id) == ("approved", 2, 1)


async def test_approve_against_request_changes(seeded_url):
    reviewer = await _reviewer(seeded_url)
    for _ in range(REPEATS):
        session_id, _tutor_id, _students = await _submitted(seeded_url)

        outcomes = await race(
            seeded_url,
            _send_back(session_id, reviewer, 1),
            _approve(session_id, reviewer, 1),
        )

        assert outcomes == ["ok", "version_conflict"]
        assert await _closeout(seeded_url, session_id) == ("changes_requested", 2, 0)


@pytest.mark.parametrize("tutor_first", [False, True])
async def test_a_resubmission_against_a_staff_correction(seeded_url, tutor_first):
    reviewer = await _reviewer(seeded_url)
    for _ in range(REPEATS):
        session_id, tutor, _students = await _submitted(seeded_url)
        await _in_uow(seeded_url, _send_back(session_id, reviewer, 1))
        put = _resubmit(session_id, tutor, 2)
        fix = _correct(session_id, reviewer, 2, 50)

        outcomes = await race(seeded_url, *((put, fix) if tutor_first else (fix, put)))

        if tutor_first:
            assert outcomes == ["ok", "version_conflict"]
            assert await _closeout(seeded_url, session_id) == ("submitted", 3, 0)
        else:
            # The correction is refused while the tutor owns the record.
            assert outcomes == ["closeout_with_tutor", "ok"]
            assert await _closeout(seeded_url, session_id) == ("submitted", 3, 0)


@pytest.mark.parametrize("resubmit_first", [False, True])
async def test_an_attendance_put_against_a_resubmission(seeded_url, resubmit_first):
    reviewer = await _reviewer(seeded_url)
    for _ in range(REPEATS):
        session_id, tutor, (first, _second) = await _submitted(seeded_url)
        await _in_uow(seeded_url, _send_back(session_id, reviewer, 1))
        put = _resubmit(session_id, tutor, 2)
        marks = _mark(session_id, tutor, first)

        outcomes = await race(
            seeded_url, *((put, marks) if resubmit_first else (marks, put))
        )

        if resubmit_first:
            assert outcomes == ["ok", "attendance_locked"]
        else:
            assert outcomes == ["ok", "ok"]
        assert await _closeout(seeded_url, session_id) == ("submitted", 3, 0)
        [row] = await run_sql_async(
            seeded_url,
            "SELECT status FROM class_session_attendance"
            " WHERE class_session_id = :s AND student_id = :st",
            {"s": session_id, "st": first},
        )
        assert row.status == ("present" if resubmit_first else "late")


async def test_two_corrections_of_an_approved_closeout(seeded_url):
    reviewer = await _reviewer(seeded_url)
    for _ in range(REPEATS):
        session_id, _tutor_id, _students = await _submitted(seeded_url)
        await _in_uow(seeded_url, _approve(session_id, reviewer, 1))

        outcomes = await race(
            seeded_url,
            _correct(session_id, reviewer, 2, 50),
            _correct(session_id, reviewer, 2, 45),
        )

        assert outcomes == ["ok", "version_conflict"]
        assert await _closeout(seeded_url, session_id) == ("approved", 3, 2)
        revisions = await run_sql_async(
            seeded_url,
            "SELECT a.revision, a.approved_minutes FROM session_closeout_approvals a"
            " JOIN session_closeouts c ON c.id = a.closeout_id"
            " WHERE c.class_session_id = :s ORDER BY a.revision",
            {"s": session_id},
        )
        assert [(r.revision, r.approved_minutes) for r in revisions] == [
            (1, 55),
            (2, 50),
        ]
