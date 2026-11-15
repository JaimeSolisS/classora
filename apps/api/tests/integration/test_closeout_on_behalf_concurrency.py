"""Concurrent closeouts on behalf of a tutor (#301 G1, H3), with two
connections (``race`` from ``test_assignment_offer_concurrency``):
transaction A writes and keeps its transaction open for a moment; B starts
once A's change is flushed, so B waits on the session (or closeout) row
lock and then sees A's committed state.

- the tutor's submit vs a reviewer's submit on behalf, one version: one
  ``ok``, one ``409``; one closeout, one approval request;
- two reviewers' submits on behalf: one ``ok``, one ``409``;
- a reviewer's takeover vs the tutor's resubmission (``changes_requested``),
  both orders: one ``ok``, one ``409``;
- a submit on behalf vs a #76 staff attendance correction, both orders:
  serialized by the session row lock.

Each case runs 5 times, on new sessions. The clock stands one hour after
the classes started, so they have ended.
"""

import uuid
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal
from app.modules.academics import service as academics
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    OWNER,
    ROLE_PERMISSIONS,
)
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.attendance_schemas import AttendanceCorrectionCreate
from app.wiring import register_approval_wiring
from app.workflows import closeout_on_behalf as on_behalf
from tests.conftest import frozen_clock
from tests.factories.identity import persist_user
from tests.integration.conftest import run_sql_async
from tests.integration.test_assignment_offer_concurrency import (
    REPEATS,
    _in_uow,
    race,
)
from tests.integration.test_closeout_review_concurrency import (
    _resubmit,
    _send_back,
)
from tests.integration.test_session_closeout_concurrency import (
    NOW,
    SUMMARY,
    _lesson,
    _submit,
)

pytestmark = pytest.mark.anyio

LATER = NOW + timedelta(hours=1)

Change = Callable[[AsyncSession], Awaitable[Any]]


@pytest.fixture(autouse=True)
def frozen() -> Any:
    register_approval_wiring()
    with frozen_clock(LATER) as clock:
        yield clock


def _staff(user_id: uuid.UUID, role: str) -> Principal:
    return Principal(
        user_id=user_id,
        roles=frozenset({role}),
        permissions=frozenset(ROLE_PERMISSIONS[role]),
    )


async def _reviewer(url: str, role: str = ACADEMIC_COORDINATOR) -> Principal:
    async def _make(session: AsyncSession) -> uuid.UUID:
        return (await persist_user(session, roles=(role,))).id

    user_id: uuid.UUID = await _in_uow(url, _make)
    return _staff(user_id, role)


def _for_tutor(session_id: uuid.UUID, reviewer: Principal, version: int) -> Change:
    async def _run(db: AsyncSession) -> None:
        await on_behalf.submit_closeout_on_behalf(
            db,
            principal=reviewer,
            class_session_id=session_id,
            body=academics.CloseoutOnBehalfSubmit.model_validate(
                {
                    "version": version,
                    "actual_minutes": 50,
                    "summary": SUMMARY,
                    "reason": "El tutor no pudo",
                }
            ),
        )

    return _run


def _take_over(session_id: uuid.UUID, reviewer: Principal, version: int) -> Change:
    async def _run(db: AsyncSession) -> None:
        await on_behalf.take_over_closeout(
            db,
            principal=reviewer,
            class_session_id=session_id,
            body=academics.CloseoutTakeOver.model_validate(
                {
                    "version": version,
                    "actual_minutes": 50,
                    "summary": SUMMARY,
                    "reason": "El tutor se fue",
                }
            ),
        )

    return _run


def _correct(session_id: uuid.UUID, reviewer: Principal, student: uuid.UUID) -> Change:
    async def _run(db: AsyncSession) -> None:
        await scheduling.correct_attendance(
            db,
            actor=reviewer,
            class_session_id=session_id,
            body=AttendanceCorrectionCreate.model_validate(
                {
                    "version": 2,
                    "student_id": student,
                    "status": "late",
                    "reason": "Recepción",
                }
            ),
        )

    return _run


async def _state(url: str, session_id: uuid.UUID) -> tuple[str, int, int, Any]:
    """The session's state and version, the closeout and request counts and
    the closeout's on-behalf submitter."""
    [row] = await run_sql_async(
        url,
        "SELECT s.state, s.version,"
        " (SELECT count(*) FROM session_closeouts c"
        "  WHERE c.class_session_id = s.id) AS closeouts,"
        " (SELECT count(*) FROM approval_requests r JOIN session_closeouts c"
        "  ON r.subject_id = c.id WHERE c.class_session_id = s.id) AS requests,"
        " (SELECT c.on_behalf_by_user_id FROM session_closeouts c"
        "  WHERE c.class_session_id = s.id) AS by"
        " FROM class_sessions s WHERE s.id = :id",
        {"id": session_id},
    )
    assert row.closeouts == row.requests
    return str(row.state), int(row.version), int(row.closeouts), row.by


@pytest.mark.parametrize("tutor_first", [False, True])
async def test_the_tutor_and_a_reviewer_submit_at_once(seeded_url, tutor_first):
    reviewer = await _reviewer(seeded_url)
    for _ in range(REPEATS):
        session_id, tutor, _students = await _lesson(seeded_url)
        mine = _submit(session_id, tutor)
        theirs = _for_tutor(session_id, reviewer, 2)

        outcomes = await race(
            seeded_url, *((mine, theirs) if tutor_first else (theirs, mine))
        )

        assert outcomes[0] == "ok"
        assert outcomes[1] in ("version_conflict", "closeout_already_submitted")
        state, version, closeouts, by = await _state(seeded_url, session_id)
        assert (state, version, closeouts) == ("completed", 3, 1)
        assert by == (None if tutor_first else reviewer.user_id)


async def test_two_reviewers_submit_at_once(seeded_url):
    first = await _reviewer(seeded_url)
    second = await _reviewer(seeded_url, OWNER)
    for _ in range(REPEATS):
        session_id, _tutor, _students = await _lesson(seeded_url)

        outcomes = await race(
            seeded_url,
            _for_tutor(session_id, first, 2),
            _for_tutor(session_id, second, 2),
        )

        assert outcomes[0] == "ok"
        assert outcomes[1] in ("version_conflict", "closeout_already_submitted")
        assert await _state(seeded_url, session_id) == (
            "completed",
            3,
            1,
            first.user_id,
        )


@pytest.mark.parametrize("tutor_first", [False, True])
async def test_a_takeover_against_the_tutor_resubmitting(seeded_url, tutor_first):
    reviewer = await _reviewer(seeded_url)
    sender = (await _reviewer(seeded_url, OWNER)).user_id
    for _ in range(REPEATS):
        session_id, tutor, _students = await _lesson(seeded_url)
        await _in_uow(seeded_url, _submit(session_id, tutor))
        await _in_uow(seeded_url, _send_back(session_id, sender, 1))
        mine = _resubmit(session_id, tutor, 2)
        theirs = _take_over(session_id, reviewer, 2)

        outcomes = await race(
            seeded_url, *((mine, theirs) if tutor_first else (theirs, mine))
        )

        assert outcomes[0] == "ok"
        assert outcomes[1] in (
            "version_conflict",
            "closeout_not_with_tutor",
            "closeout_under_review",
        )
        [row] = await run_sql_async(
            seeded_url,
            "SELECT status, version, on_behalf_by_user_id FROM session_closeouts"
            " WHERE class_session_id = :id",
            {"id": session_id},
        )
        assert (row.status, row.version) == ("submitted", 3)
        assert row.on_behalf_by_user_id == (None if tutor_first else reviewer.user_id)


@pytest.mark.parametrize("submit_first", [False, True])
async def test_a_submit_on_behalf_against_an_attendance_correction(
    seeded_url, submit_first
):
    reviewer = await _reviewer(seeded_url)
    corrector = await _reviewer(seeded_url, OWNER)
    for _ in range(REPEATS):
        session_id, _tutor, (first, _second) = await _lesson(seeded_url)
        submit = _for_tutor(session_id, reviewer, 2)
        fix = _correct(session_id, corrector, first)

        outcomes = await race(
            seeded_url, *((submit, fix) if submit_first else (fix, submit))
        )

        assert outcomes == ["ok", "version_conflict"]
        state, version, closeouts, _by = await _state(seeded_url, session_id)
        if submit_first:
            assert (state, version, closeouts) == ("completed", 3, 1)
        else:
            assert (state, version, closeouts) == ("confirmed", 3, 0)
