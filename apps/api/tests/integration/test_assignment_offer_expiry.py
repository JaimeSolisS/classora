"""The offer expiry task (#52 E3, I2): ``scheduling.tasks.run_expire_offers``
with a frozen clock, on a database of its own per test (the run scans every
offer).
"""

import asyncio
import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.scheduling import service as scheduling
from app.modules.scheduling import tasks
from app.modules.scheduling.offer_flow import lock_sessions
from app.modules.scheduling.schemas import ClassSessionCreate
from tests.conftest import frozen_clock
from tests.integration.auth_support import create_user
from tests.integration.conftest import run_sql, settings_for
from tests.integration.lifecycle_support import events, in_uow
from tests.integration.test_class_session_create_concurrency import (
    START,
    _references,
)


def offered(url: str, refs: dict[str, uuid.UUID], minutes_later: int = 0) -> Any:
    """A session offered to ``refs["tutor"]`` (its own student and room
    time); returns the created detail."""
    start = START + timedelta(minutes=minutes_later)
    body = ClassSessionCreate(
        kind="individual",
        student_id=refs["student"],
        subject_id=refs["subject"],
        tutor_user_id=refs["tutor"],
        classroom_id=refs["room"],
        starts_at=start,
        ends_at=start + timedelta(minutes=60),
    )

    async def _create(session: AsyncSession) -> Any:
        return await scheduling.create_class_session(
            session, body, actor_id=refs["tutor"]
        )

    return in_uow(url, _create)


def run(url: str) -> tasks.ExpiryResult:
    return asyncio.run(tasks.run_expire_offers(settings_for(url)))


def offer_row(url: str, offer_id: uuid.UUID) -> Any:
    [row] = run_sql(
        url,
        "SELECT o.status, o.responded_at, s.state, s.tutor_user_id"
        " FROM assignment_offers o"
        " JOIN assignment_offer_sessions x ON x.offer_id = o.id"
        " JOIN class_sessions s ON s.id = x.class_session_id WHERE o.id = :id",
        {"id": offer_id},
    )
    return row


def inbox(url: str, offer_id: uuid.UUID) -> list[Any]:
    return run_sql(
        url,
        "SELECT status, priority, requester_user_id FROM approval_requests"
        " WHERE request_type = 'scheduling.offer_reassignment' AND subject_id = :id",
        {"id": offer_id},
    )


def expired_notices(url: str, offer_id: uuid.UUID) -> int:
    [row] = run_sql(
        url,
        "SELECT count(*) AS n FROM notifications"
        " WHERE notification_type = 'scheduling.offer_expired'"
        " AND params->>'offer_id' = :id",
        {"id": str(offer_id)},
    )
    return int(row.n)


@pytest.fixture
def refs(seeded_url: str) -> dict[str, uuid.UUID]:
    references = asyncio.run(_references(seeded_url))
    # An Owner to notify (and one Finance user who never is).
    references["owner"] = create_user(seeded_url, roles=("owner",)).id
    references["finance"] = create_user(seeded_url, roles=("finance_staff",)).id
    return references


def test_an_overdue_offer_expires_and_the_session_is_reassigned(seeded_url, refs):
    created = offered(seeded_url, refs)
    offer = created.pending_offer

    with frozen_clock(offer.respond_by + timedelta(minutes=3)) as clock:
        result = run(seeded_url)

    assert result == tasks.ExpiryResult(expired=1, skipped=0)
    row = offer_row(seeded_url, offer.id)
    assert (row.status, row.state, row.tutor_user_id) == ("expired", "unassigned", None)
    assert row.responded_at == clock.now()
    [item] = inbox(seeded_url, offer.id)
    assert (item.status, item.requester_user_id) == ("pending", None)
    assert expired_notices(seeded_url, offer.id) >= 1
    [notified] = run_sql(
        seeded_url,
        "SELECT count(*) AS n FROM notifications WHERE recipient_user_id = :u"
        " AND notification_type = 'scheduling.offer_expired'",
        {"u": refs["finance"]},
    )
    assert notified.n == 0
    # The task acts as nobody.
    updates = [
        e
        for e in events(seeded_url, record_type="assignment_offers", record_id=offer.id)
        if e.action == "update"
    ]
    assert [e.actor_user_id for e in updates] == [None]


def test_the_deadline_boundary_and_a_second_run(seeded_url, refs):
    created = offered(seeded_url, refs)
    offer = created.pending_offer

    with frozen_clock(offer.respond_by - timedelta(minutes=1)):
        early = run(seeded_url)
    assert early == tasks.ExpiryResult(expired=0, skipped=0)
    assert offer_row(seeded_url, offer.id).status == "pending"

    with frozen_clock(offer.respond_by):
        exactly = run(seeded_url)
        again = run(seeded_url)

    assert exactly == tasks.ExpiryResult(expired=1, skipped=0)
    assert again == tasks.ExpiryResult(expired=0, skipped=0)
    assert len(inbox(seeded_url, offer.id)) == 1
    assert offer_row(seeded_url, offer.id).status == "expired"


def test_an_offer_answered_after_selection_is_skipped(seeded_url, refs):
    created = offered(seeded_url, refs)
    offer = created.pending_offer
    in_uow(
        seeded_url,
        lambda session: scheduling.answer_offer(
            session, offer.id, caller_id=refs["tutor"], accept=True
        ),
    )

    # The task selected it before the answer committed; under the lock it
    # finds it answered.
    changed = in_uow(
        seeded_url,
        lambda session: scheduling.expire_offer(
            session, offer.id, offer.respond_by + timedelta(minutes=1)
        ),
    )

    assert changed is False
    row = offer_row(seeded_url, offer.id)
    assert (row.status, row.state) == ("accepted", "confirmed")
    assert inbox(seeded_url, offer.id) == []


def test_a_session_cancelled_meanwhile_stays_cancelled(seeded_url, refs):
    created = offered(seeded_url, refs)
    offer = created.pending_offer

    async def _cancel(session: AsyncSession) -> None:
        # A later path that did not withdraw the offer (none does today).
        [row] = await lock_sessions(session, [created.id])
        await scheduling.transition_class_session(
            session,
            created.id,
            to_state="cancelled",
            expected_version=row.version,
            reason="Cancelada",
        )

    in_uow(seeded_url, _cancel)

    with frozen_clock(offer.respond_by):
        result = run(seeded_url)

    assert result == tasks.ExpiryResult(expired=1, skipped=0)
    row = offer_row(seeded_url, offer.id)
    assert (row.status, row.state) == ("expired", "cancelled")
    # Nothing to reassign: no inbox item, no notification.
    assert inbox(seeded_url, offer.id) == []
    assert expired_notices(seeded_url, offer.id) == 0


def test_several_overdue_offers_each_expire(seeded_url, refs):
    first = offered(seeded_url, refs)
    second_refs = asyncio.run(_references(seeded_url))
    second = offered(seeded_url, second_refs, minutes_later=120)

    with frozen_clock(first.pending_offer.respond_by + timedelta(days=3)):
        result = run(seeded_url)

    assert result == tasks.ExpiryResult(expired=2, skipped=0)
    assert offer_row(seeded_url, second.pending_offer.id).status == "expired"
