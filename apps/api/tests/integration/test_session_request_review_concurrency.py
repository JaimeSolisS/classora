"""Concurrent decisions on one session request (#60 I2), with two
connections (the #47 / #48 / #59 pattern of
``test_session_requests_concurrency``).

Transaction A decides and keeps its transaction open for a moment;
transaction B starts once A's writes are flushed and uncommitted and waits
on the locks A holds (the student lock, the request row, the inbox item).
Once A commits, B sees A's outcome:

- **confirm vs withdraw**, **confirm vs confirm**, **confirm vs reject:**
  exactly one wins; the other gets ``409 version_conflict`` (both sent the
  version they loaded, and the version check comes first, C2 step 5 / D2
  step 4) or ``session_request_not_pending``. Never a ``rejected`` or
  ``withdrawn`` request with a session, never two sessions.
- **the inbox's generic approve vs the dedicated confirm**, in both orders:
  no deadlock (the handler reads the request without a lock); the generic
  approve never decides a pending request.

Each case runs 5 times, on new references.
"""

from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal
from app.core.db import create_sessionmaker
from app.core.errors import AppError
from app.core.models import StaleVersionError
from app.core.uow import UnitOfWork
from app.modules.administration import service as administration
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.request_schemas import (
    SessionRequestConfirm,
    SessionRequestReject,
)
from app.modules.staff.models import TutorAvailabilityWindow, TutorQualification
from app.workflows import confirm_session_request as workflow
from tests.factories.base import persist
from tests.factories.families import GuardianFactory, link_guardian, persist_student
from tests.factories.identity import persist_user
from tests.factories.scheduling import persist_subject
from tests.integration.conftest import run_sql_async, short_lived_engine
from tests.integration.session_request_support import insert_request_row
from tests.integration.test_session_requests_concurrency import Change
from tests.integration.test_session_requests_concurrency import race as _race

pytestmark = pytest.mark.anyio

REPEATS = 5
MX = ZoneInfo("America/Mexico_City")
START = datetime(2031, 1, 8, 10, 0, tzinfo=MX)  # a Wednesday
STAFF_PERMISSIONS = frozenset({"schedule.manage", "schedule.assign"})
LOSERS = {"version_conflict", "session_request_not_pending"}


async def _references(url: str) -> dict[str, Any]:
    """A guardian, their child's pending request at :data:`START`, two
    tutors free all week and qualified for the subject, and two Owners."""
    engine = short_lived_engine(url)
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            session = uow.session
            subject = (await persist_subject(session)).id
            tutors = []
            for _ in range(2):
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
                tutors.append(tutor.id)
            user = await persist_user(session, roles=("guardian",))
            student = await persist_student(session, contacts=0)
            guardian = await persist(session, GuardianFactory, user_id=user.id)
            await link_guardian(session, student, guardian)
            staff = [await persist_user(session, roles=("owner",)) for _ in range(2)]
            request_id = await insert_request_row(
                session,
                student_id=student.id,
                subject_id=subject,
                requested_by=user.id,
                starts_at=START,
            )
            return {
                "request": request_id,
                "student": student.id,
                "tutors": tutors,
                "guardian": Principal(
                    user_id=user.id,
                    roles=frozenset({"guardian"}),
                    permissions=frozenset(),
                ),
                "staff": [
                    Principal(
                        user_id=member.id,
                        roles=frozenset({"owner"}),
                        permissions=STAFF_PERMISSIONS,
                    )
                    for member in staff
                ],
            }
    finally:
        await engine.dispose()


def _versioned(change: Change) -> Change:
    # A stale version is the HTTP 409 version_conflict (app.core.errors).
    async def _run(session: AsyncSession) -> None:
        try:
            await change(session)
        except StaleVersionError:
            raise _VersionConflict() from None

    return _run


class _VersionConflict(AppError):
    status = 409
    code = "version_conflict"


async def race(url: str, first: Change, second: Change) -> list[str]:
    """The #59 race, with a stale version reported as ``version_conflict``."""
    return await _race(url, _versioned(first), _versioned(second))


def confirm(refs: dict[str, Any], who: int = 0, tutor: int = 0) -> Change:
    body = SessionRequestConfirm(version=1, tutor_user_id=refs["tutors"][tutor])

    async def _confirm(session: AsyncSession) -> None:
        await workflow.confirm_session_request(
            session, refs["staff"][who], refs["request"], body
        )

    return _confirm


def reject(refs: dict[str, Any], who: int = 1) -> Change:
    body = SessionRequestReject(version=1, reason="Sin cupo")

    async def _reject(session: AsyncSession) -> None:
        await workflow.reject_session_request(
            session, refs["staff"][who], refs["request"], body
        )

    return _reject


def withdraw(refs: dict[str, Any]) -> Change:
    async def _withdraw(session: AsyncSession) -> None:
        await scheduling.withdraw_session_request(
            session, refs["guardian"], refs["request"], version=1
        )

    return _withdraw


def generic_approve(refs: dict[str, Any]) -> Change:
    async def _approve(session: AsyncSession) -> None:
        approval_id = await administration.approval_request_id_for_subject(
            session,
            request_type=scheduling.SESSION_REQUEST_TYPE,
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


async def state(url: str, refs: dict[str, Any]) -> tuple[str, int, str]:
    """The request's status, the student's session count and the inbox
    item's status."""
    [row] = await run_sql_async(
        url,
        "SELECT r.status, r.class_session_id,"
        " (SELECT count(*) FROM class_session_participants p"
        "  WHERE p.student_id = r.student_id) AS sessions,"
        " (SELECT a.status FROM approval_requests a WHERE a.subject_id = r.id)"
        "  AS inbox FROM session_requests r WHERE r.id = :id",
        {"id": refs["request"]},
    )
    assert (row.status == "confirmed") == (row.class_session_id is not None)
    return row.status, int(row.sessions), row.inbox


async def test_confirm_vs_withdraw(seeded_url):
    for first_confirms in [True, False] * 3:
        refs = await _references(seeded_url)
        pair = (confirm(refs), withdraw(refs))
        first, second = pair if first_confirms else pair[::-1]

        outcomes = await race(seeded_url, first, second)

        assert outcomes[0] == "ok" and outcomes[1] in LOSERS, outcomes
        expected = (
            ("confirmed", 1, "approved")
            if first_confirms
            else ("withdrawn", 0, "cancelled")
        )
        assert await state(seeded_url, refs) == expected


async def test_confirm_vs_confirm(seeded_url):
    for _ in range(REPEATS):
        refs = await _references(seeded_url)

        outcomes = await race(
            seeded_url, confirm(refs, who=0, tutor=0), confirm(refs, who=1, tutor=1)
        )

        assert outcomes[0] == "ok" and outcomes[1] in LOSERS, outcomes
        assert await state(seeded_url, refs) == ("confirmed", 1, "approved")


async def test_confirm_vs_reject(seeded_url):
    for first_confirms in [True, False] * 3:
        refs = await _references(seeded_url)
        pair = (confirm(refs), reject(refs))
        first, second = pair if first_confirms else pair[::-1]

        outcomes = await race(seeded_url, first, second)

        assert outcomes[0] == "ok" and outcomes[1] in LOSERS, outcomes
        expected = (
            ("confirmed", 1, "approved")
            if first_confirms
            else ("rejected", 0, "rejected")
        )
        assert await state(seeded_url, refs) == expected


async def test_the_generic_approve_never_races_a_confirm_into_a_deadlock(seeded_url):
    for approve_first in [True, False] * 3:
        refs = await _references(seeded_url)

        if approve_first:
            outcomes = await race(seeded_url, generic_approve(refs), confirm(refs))
            assert outcomes == ["session_request_review_route_required", "ok"]
        else:
            outcomes = await race(seeded_url, confirm(refs), generic_approve(refs))
            assert outcomes == ["ok", "approval_transition_invalid"]
        assert await state(seeded_url, refs) == ("confirmed", 1, "approved")
