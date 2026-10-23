"""Concurrent writes on one change request or its session (#62 H2), with two
connections (the #59 / #60 race: transaction A writes and keeps its
transaction open for a moment; B starts once A's writes are flushed and
uncommitted and waits on the locks A holds).

- **two creates on one session** (a guardian and the tutor): one ``201``,
  the other ``409 change_request_pending`` (the session row lock, then the
  partial unique index);
- **approve vs withdraw:** exactly one wins;
- **approve (reschedule) vs a staff cancel:** one wins; never a cancelled
  session with an approved reschedule;
- **approve (reschedule) vs a one-off create** for the same student and
  time: exactly one succeeds (the student lock);
- **the inbox's generic approve vs the dedicated approve:** no deadlock, a
  consistent end state.

Each case runs 5 times (in alternating orders where both orders matter), on
new references. The clock is real: the sessions are in 2031.
"""

from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal
from app.core.db import create_sessionmaker
from app.core.uow import UnitOfWork
from app.modules.administration import service as administration
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.change_request_schemas import (
    ChangeRequestApprove,
    ChangeRequestCreate,
)
from app.modules.scheduling.schemas import ClassSessionCreate
from app.modules.staff.models import TutorAvailabilityWindow, TutorQualification
from app.workflows import change_request_decision as workflow
from tests.factories.base import persist
from tests.factories.families import GuardianFactory, link_guardian, persist_student
from tests.factories.identity import persist_user
from tests.factories.scheduling import (
    add_participant,
    persist_class_session,
    persist_subject,
)
from tests.integration.conftest import run_sql_async, short_lived_engine
from tests.integration.test_session_request_review_concurrency import race

pytestmark = pytest.mark.anyio

REPEATS = 5
MX = ZoneInfo("America/Mexico_City")
START = datetime(2031, 1, 8, 10, 0, tzinfo=MX)  # a Wednesday
PROPOSED = START + timedelta(hours=3)
STAFF_PERMISSIONS = frozenset({"schedule.manage", "schedule.assign"})
LOSERS = {"version_conflict", "change_request_not_pending"}


def _principal(
    user_id: Any, role: str, permissions: frozenset[str] = frozenset()
) -> Principal:
    return Principal(user_id=user_id, roles=frozenset({role}), permissions=permissions)


async def _references(url: str, *, request: str | None = None) -> dict[str, Any]:
    """A guardian's minor child in a ``confirmed`` session at :data:`START`
    with a tutor free all week, two Owners and, with ``request``, a pending
    family request of that kind (a reschedule proposes :data:`PROPOSED`)."""
    engine = short_lived_engine(url)
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            session = uow.session
            subject = (await persist_subject(session)).id
            tutor = await persist_user(session, roles=("tutor",))
            session.add_all(
                TutorAvailabilityWindow(
                    tutor_user_id=tutor.id,
                    weekday=day,
                    starts_at=time(0),
                    ends_at=time(23, 59),
                )
                for day in range(1, 8)
            )
            session.add(
                TutorQualification(
                    tutor_user_id=tutor.id,
                    subject_id=subject,
                    status="approved",
                    decided_at=START - timedelta(days=400),
                    decided_by_user_id=tutor.id,
                )
            )
            user = await persist_user(session, roles=("guardian",))
            student = await persist_student(session, contacts=0)
            guardian = await persist(session, GuardianFactory, user_id=user.id)
            await link_guardian(session, student, guardian)
            row = await persist_class_session(
                session,
                subject_id=subject,
                tutor_user_id=tutor.id,
                starts_at=START,
                ends_at=START + timedelta(hours=1),
            )
            await add_participant(session, row, student.id)
            staff = [await persist_user(session, roles=("owner",)) for _ in range(2)]
            refs: dict[str, Any] = {
                "session": row.id,
                "session_version": row.version,
                "subject": subject,
                "student": student.id,
                "guardian": _principal(user.id, "guardian"),
                "tutor": _principal(tutor.id, "tutor"),
                "staff": [
                    _principal(member.id, "owner", STAFF_PERMISSIONS)
                    for member in staff
                ],
            }
            if request is not None:
                detail = await scheduling.create_change_request(
                    session,
                    refs["guardian"],
                    _body(refs, request),
                )
                refs["request"] = detail.id
            return refs
    finally:
        await engine.dispose()


def _body(refs: dict[str, Any], kind: str) -> ChangeRequestCreate:
    return ChangeRequestCreate(
        class_session_id=refs["session"],
        kind=kind,  # type: ignore[arg-type]
        proposed_starts_at=PROPOSED if kind == "reschedule" else None,
    )


def create(refs: dict[str, Any], who: str) -> Any:
    async def _create(session: AsyncSession) -> None:
        await scheduling.create_change_request(
            session, refs[who], _body(refs, "cancellation")
        )

    return _create


def approve(refs: dict[str, Any], who: int = 0) -> Any:
    async def _approve(session: AsyncSession) -> None:
        await workflow.approve_change_request(
            session,
            refs["staff"][who],
            refs["request"],
            ChangeRequestApprove(version=1),
        )

    return _approve


def withdraw(refs: dict[str, Any]) -> Any:
    async def _withdraw(session: AsyncSession) -> None:
        await scheduling.withdraw_change_request(
            session, refs["guardian"], refs["request"], version=1
        )

    return _withdraw


def staff_cancel(refs: dict[str, Any]) -> Any:
    async def _cancel(session: AsyncSession) -> None:
        await scheduling.cancel_class_session(
            session,
            refs["session"],
            expected_version=refs["session_version"],
            reason="Cierre del aula",
            by_user_id=refs["staff"][1].user_id,
        )

    return _cancel


def one_off_create(refs: dict[str, Any]) -> Any:
    async def _create(session: AsyncSession) -> None:
        await scheduling.create_class_session(
            session,
            ClassSessionCreate(
                kind="individual",
                student_id=refs["student"],
                subject_id=refs["subject"],
                tutor_user_id=None,
                classroom_id=None,
                starts_at=PROPOSED,
                ends_at=PROPOSED + timedelta(hours=1),
            ),
            actor_id=refs["staff"][1].user_id,
        )

    return _create


def generic_approve(refs: dict[str, Any]) -> Any:
    async def _approve(session: AsyncSession) -> None:
        approval_id = await administration.approval_request_id_for_subject(
            session,
            request_type=scheduling.CHANGE_REQUEST_TYPE,
            subject_id=refs["request"],
        )
        assert approval_id is not None
        await administration.decide_approval_request(
            session,
            principal=refs["staff"][1],
            request_id=approval_id,
            action="approve",
        )

    return _approve


async def requests_of(url: str, refs: dict[str, Any]) -> list[str]:
    rows = await run_sql_async(
        url,
        "SELECT status FROM session_change_requests WHERE class_session_id = :s"
        " ORDER BY created_at",
        {"s": refs["session"]},
    )
    return [row.status for row in rows]


async def state(url: str, refs: dict[str, Any]) -> tuple[str, str, Any, str]:
    """The request's status, the session's state and start, and the inbox
    item's status."""
    [row] = await run_sql_async(
        url,
        "SELECT r.status, s.state, s.starts_at,"
        " (SELECT a.status FROM approval_requests a WHERE a.subject_id = r.id)"
        " AS inbox FROM session_change_requests r JOIN class_sessions s"
        " ON s.id = r.class_session_id WHERE r.id = :id",
        {"id": refs["request"]},
    )
    return row.status, row.state, row.starts_at, row.inbox


async def test_two_creates_on_one_session(seeded_url):
    for guardian_first in [True, False] * 3:
        refs = await _references(seeded_url)
        pair = (create(refs, "guardian"), create(refs, "tutor"))
        first, second = pair if guardian_first else pair[::-1]

        outcomes = await race(seeded_url, first, second)

        assert outcomes == ["ok", "change_request_pending"], outcomes
        assert await requests_of(seeded_url, refs) == ["pending"]


async def test_approve_vs_withdraw(seeded_url):
    for approve_first in [True, False] * 3:
        refs = await _references(seeded_url, request="cancellation")
        pair = (approve(refs), withdraw(refs))
        first, second = pair if approve_first else pair[::-1]

        outcomes = await race(seeded_url, first, second)

        assert outcomes[0] == "ok" and outcomes[1] in LOSERS, outcomes
        status, session_state, _, inbox = await state(seeded_url, refs)
        if approve_first:
            assert (status, session_state, inbox) == (
                "approved",
                "cancelled",
                "approved",
            )
        else:
            assert (status, session_state, inbox) == (
                "withdrawn",
                "confirmed",
                "cancelled",
            )


async def test_approve_vs_a_staff_cancel(seeded_url):
    for approve_first in [True, False] * 3:
        refs = await _references(seeded_url, request="reschedule")
        pair = (approve(refs), staff_cancel(refs))
        first, second = pair if approve_first else pair[::-1]

        outcomes = await race(seeded_url, first, second)

        status, session_state, starts_at, _ = await state(seeded_url, refs)
        assert not (status == "approved" and session_state == "cancelled")
        if approve_first:
            assert outcomes == ["ok", "version_conflict"], outcomes
            assert (status, session_state, starts_at) == (
                "approved",
                "confirmed",
                PROPOSED,
            )
        else:
            assert outcomes == ["ok", "change_request_session_changed"], outcomes
            assert (status, session_state) == ("pending", "cancelled")


async def test_approve_vs_a_one_off_for_the_same_student_and_time(seeded_url):
    for approve_first in [True, False] * 3:
        refs = await _references(seeded_url, request="reschedule")
        pair = (approve(refs), one_off_create(refs))
        first, second = pair if approve_first else pair[::-1]

        outcomes = await race(seeded_url, first, second)

        assert outcomes == ["ok", "scheduling_conflict"], outcomes
        status, _, starts_at, _ = await state(seeded_url, refs)
        if approve_first:
            assert (status, starts_at) == ("approved", PROPOSED)
        else:
            assert (status, starts_at) == ("pending", START)


async def test_the_generic_approve_never_deadlocks_with_the_review_route(seeded_url):
    for generic_first in [True, False] * 3:
        refs = await _references(seeded_url, request="cancellation")

        if generic_first:
            outcomes = await race(seeded_url, generic_approve(refs), approve(refs))
            assert outcomes == ["change_request_review_route_required", "ok"]
        else:
            outcomes = await race(seeded_url, approve(refs), generic_approve(refs))
            assert outcomes == ["ok", "approval_transition_invalid"]
        status, session_state, _, inbox = await state(seeded_url, refs)
        assert (status, session_state, inbox) == ("approved", "cancelled", "approved")
