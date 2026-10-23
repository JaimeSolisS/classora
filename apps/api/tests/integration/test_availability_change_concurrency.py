"""Concurrent writes around an availability change request (M4-18 #63 I3),
with two connections (the #59 / #60 race: transaction A writes and keeps
its transaction open for a moment; B starts once A's writes are flushed and
uncommitted and waits on the locks A holds).

- **approve vs a #48 create** with the tutor in the removed time: either
  the create commits first and the approval flags its session, or the
  create runs after and its own ``tutor_unavailable`` check refuses it;
  never an unflagged session inside the removed time (the tutor lock, D5);
- **approve vs the requester's withdrawal:** exactly one wins, the other
  is ``409``;
- **a direct weekly change vs approving a weekly request:** consistent
  either way (``409 version_conflict`` or ``409
  availability_change_stale``; the request stays pending when the direct
  change wins).

Each case runs 6 times in alternating orders, on new references. The clock
is real: the sessions are in 2031.
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
from app.modules.catalog.schemas import AvailabilityWindow
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.schemas import ClassSessionCreate
from app.modules.staff import service as staff
from app.modules.staff.models import TutorAvailabilityWindow, TutorQualification
from app.modules.staff.schemas import WeeklyAvailabilityUpdate
from tests.factories.families import persist_student
from tests.factories.identity import persist_user
from tests.factories.scheduling import (
    add_participant,
    persist_class_session,
    persist_subject,
)
from tests.integration.conftest import run_sql_async, short_lived_engine
from tests.integration.test_session_request_review_concurrency import race

pytestmark = pytest.mark.anyio

MX = ZoneInfo("America/Mexico_City")
MONDAY_16 = datetime(2031, 1, 6, 16, 0, tzinfo=MX)
MONDAY_17 = MONDAY_16 + timedelta(hours=1)
KEPT = [
    AvailabilityWindow(weekday=1, starts_at="08:00", ends_at="16:00"),
    AvailabilityWindow(weekday=1, starts_at="18:00", ends_at="20:00"),
]
ORDERS = [True, False] * 3


def _principal(user_id: Any, role: str, permissions: frozenset[str]) -> Principal:
    return Principal(user_id=user_id, roles=frozenset({role}), permissions=permissions)


async def _references(url: str) -> dict[str, Any]:
    """A tutor (Monday 08:00-20:00) with a ``confirmed`` session Monday
    16:00-17:00 and a pending weekly request taking 16:00-18:00 away, a
    second student and an Owner."""
    engine = short_lived_engine(url)
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            session = uow.session
            subject = (await persist_subject(session)).id
            tutor = await persist_user(session, roles=("tutor",))
            session.add(
                TutorAvailabilityWindow(
                    tutor_user_id=tutor.id,
                    weekday=1,
                    starts_at=time(8),
                    ends_at=time(20),
                )
            )
            session.add(
                TutorQualification(
                    tutor_user_id=tutor.id,
                    subject_id=subject,
                    status="approved",
                    decided_at=MONDAY_16 - timedelta(days=400),
                    decided_by_user_id=tutor.id,
                )
            )
            row = await persist_class_session(
                session,
                subject_id=subject,
                tutor_user_id=tutor.id,
                starts_at=MONDAY_16,
                ends_at=MONDAY_17,
            )
            await add_participant(
                session, row, (await persist_student(session, contacts=0)).id
            )
            owner = await persist_user(session, roles=("owner",))
            refs: dict[str, Any] = {
                "tutor": _principal(tutor.id, "tutor", frozenset({"tutor.view"})),
                "owner": _principal(
                    owner.id,
                    "owner",
                    frozenset({"tutor.view", "tutor.manage", "schedule.manage"}),
                ),
                "subject": subject,
                "student": (await persist_student(session, contacts=0)).id,
            }
            item = await staff.replace_weekly_availability(
                session,
                principal=refs["tutor"],
                user_id=tutor.id,
                body=WeeklyAvailabilityUpdate(version=0, windows=KEPT),
                impact=scheduling.AVAILABILITY_IMPACT_SOURCE,
            )
            refs["request"] = item.id  # type: ignore[union-attr]
            refs["approval"] = item.approval_request_id  # type: ignore[union-attr]
            return refs
    finally:
        await engine.dispose()


def approve(refs: dict[str, Any]) -> Any:
    async def _approve(session: AsyncSession) -> None:
        await administration.decide_approval_request(
            session,
            principal=refs["owner"],
            request_id=refs["approval"],
            action="approve",
        )

    return _approve


def withdraw(refs: dict[str, Any]) -> Any:
    async def _withdraw(session: AsyncSession) -> None:
        await staff.cancel_change_request(
            session,
            principal=refs["tutor"],
            user_id=refs["tutor"].user_id,
            change_request_id=refs["request"],
            version=1,
        )

    return _withdraw


def book(refs: dict[str, Any]) -> Any:
    """A #48 create with the tutor, Monday 17:00-18:00 (in the removed
    time, available until the change applies)."""

    async def _book(session: AsyncSession) -> None:
        await scheduling.create_class_session(
            session,
            ClassSessionCreate(
                kind="individual",
                student_id=refs["student"],
                subject_id=refs["subject"],
                tutor_user_id=refs["tutor"].user_id,
                classroom_id=None,
                starts_at=MONDAY_17,
                ends_at=MONDAY_17 + timedelta(hours=1),
            ),
            actor_id=refs["owner"].user_id,
        )

    return _book


def direct_weekly(refs: dict[str, Any]) -> Any:
    """A direct weekly change that only adds a Friday window (version 0)."""

    async def _change(session: AsyncSession) -> None:
        await staff.replace_weekly_availability(
            session,
            principal=refs["tutor"],
            user_id=refs["tutor"].user_id,
            body=WeeklyAvailabilityUpdate(
                version=0,
                windows=[
                    AvailabilityWindow(weekday=1, starts_at="08:00", ends_at="20:00"),
                    AvailabilityWindow(weekday=5, starts_at="09:00", ends_at="12:00"),
                ],
            ),
            impact=scheduling.AVAILABILITY_IMPACT_SOURCE,
        )

    return _change


async def request_state(url: str, refs: dict[str, Any]) -> tuple[str, str]:
    [row] = await run_sql_async(
        url,
        "SELECT r.status, a.status AS inbox"
        " FROM tutor_availability_change_requests r"
        " JOIN approval_requests a ON a.id = r.approval_request_id"
        " WHERE r.id = :id",
        {"id": refs["request"]},
    )
    return row.status, row.inbox


async def unflagged_in_removed_time(url: str, refs: dict[str, Any]) -> int:
    """The tutor's offered/confirmed sessions inside Monday 16:00-18:00 that
    have no flag."""
    [row] = await run_sql_async(
        url,
        "SELECT count(*) AS n FROM class_sessions s"
        " WHERE s.tutor_user_id = :tutor AND s.state IN ('offered', 'confirmed')"
        " AND s.starts_at < :end AND s.ends_at > :start"
        " AND NOT EXISTS (SELECT 1 FROM class_session_reassignment_flags f"
        " WHERE f.class_session_id = s.id)",
        {
            "tutor": refs["tutor"].user_id,
            "start": MONDAY_16,
            "end": MONDAY_16 + timedelta(hours=2),
        },
    )
    return int(row.n)


async def flagged_count(url: str, refs: dict[str, Any]) -> int:
    [row] = await run_sql_async(
        url,
        "SELECT count(*) AS n FROM class_session_reassignment_flags"
        " WHERE source_id = :id",
        {"id": refs["request"]},
    )
    return int(row.n)


async def test_approve_vs_a_booking_in_the_removed_time(seeded_url):
    for approve_first in ORDERS:
        refs = await _references(seeded_url)
        pair = (approve(refs), book(refs))
        first, second = pair if approve_first else pair[::-1]

        outcomes = await race(seeded_url, first, second)

        if approve_first:
            assert outcomes == ["ok", "scheduling_conflict"], outcomes
            assert await flagged_count(seeded_url, refs) == 1
        else:
            assert outcomes == ["ok", "ok"], outcomes
            assert await flagged_count(seeded_url, refs) == 2
        assert await unflagged_in_removed_time(seeded_url, refs) == 0
        assert await request_state(seeded_url, refs) == ("approved", "approved")


async def test_approve_vs_withdraw(seeded_url):
    for approve_first in ORDERS:
        refs = await _references(seeded_url)
        pair = (approve(refs), withdraw(refs))
        first, second = pair if approve_first else pair[::-1]

        outcomes = await race(seeded_url, first, second)

        if approve_first:
            assert outcomes == ["ok", "availability_change_not_pending"], outcomes
            assert await request_state(seeded_url, refs) == ("approved", "approved")
        else:
            assert outcomes == ["ok", "approval_transition_invalid"], outcomes
            assert await request_state(seeded_url, refs) == ("cancelled", "cancelled")
            assert await flagged_count(seeded_url, refs) == 0


async def test_a_direct_weekly_change_vs_approving_a_weekly_request(seeded_url):
    for approve_first in ORDERS:
        refs = await _references(seeded_url)
        pair = (approve(refs), direct_weekly(refs))
        first, second = pair if approve_first else pair[::-1]

        outcomes = await race(seeded_url, first, second)

        if approve_first:
            assert outcomes == ["ok", "version_conflict"], outcomes
            assert await request_state(seeded_url, refs) == ("approved", "approved")
        else:
            # Stale on the version, or (no settings row yet: the first
            # change creates it) the unique row: nothing applied either way.
            assert outcomes[0] == "ok", outcomes
            assert outcomes[1] in {"availability_change_stale", "version_conflict"}
            assert await request_state(seeded_url, refs) == ("pending", "pending")
