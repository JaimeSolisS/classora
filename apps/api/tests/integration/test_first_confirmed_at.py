"""``class_sessions.first_confirmed_at`` (#67 A1, A2, G2): set once by the
ORM when a row is written ``confirmed``, never changed or cleared, and
required for ``confirmed``/``completed`` rows by
``ck_class_sessions_confirmed_has_first_confirmed_at``.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.scheduling import service as scheduling
from app.modules.scheduling.models import ClassSession
from tests.conftest import frozen_clock
from tests.factories.scheduling import persist_subject, persist_tutor_user
from tests.integration.conftest import run_sql
from tests.integration.lifecycle_support import in_uow

T1 = datetime(2031, 3, 1, 12, 0, tzinfo=UTC)
T2 = T1 + timedelta(days=1)
T3 = T1 + timedelta(days=2)
START = datetime(2031, 3, 10, 16, 0, tzinfo=UTC)
CHECK = "ck_class_sessions_confirmed_has_first_confirmed_at"


def _new_row(url: str, *, state: str, start: datetime, **values: Any) -> uuid.UUID:
    async def _make(session: AsyncSession) -> uuid.UUID:
        subject = await persist_subject(session)
        tutor = (
            await persist_tutor_user(session)
            if state in {"offered", "confirmed", "completed"}
            else None
        )
        row = ClassSession(
            kind="individual",
            state=state,
            subject_id=subject.id,
            tutor_user_id=tutor,
            starts_at=start,
            ends_at=start + timedelta(minutes=60),
            scheduled_minutes=60,
            **values,
        )
        session.add(row)
        await session.flush()
        return row.id

    return in_uow(url, _make)


def stored_value(url: str, session_id: uuid.UUID) -> Any:
    [row] = run_sql(
        url,
        "SELECT state, first_confirmed_at, version FROM class_sessions WHERE id = :id",
        {"id": str(session_id)},
    )
    return row


def move(url: str, session_id: uuid.UUID, to_state: str, **kwargs: Any) -> None:
    version = stored_value(url, session_id).version

    async def _move(session: AsyncSession) -> None:
        await scheduling.transition_class_session(
            session,
            session_id,
            to_state=to_state,
            expected_version=version,
            **kwargs,
        )

    in_uow(url, _move)


def test_a_row_inserted_confirmed_gets_it(module_seeded_url):
    with frozen_clock(T1):
        session_id = _new_row(module_seeded_url, state="confirmed", start=START)

    assert stored_value(module_seeded_url, session_id).first_confirmed_at == T1


def test_an_unconfirmed_row_has_none(module_seeded_url):
    for state, hours in (("unassigned", 0), ("offered", 2)):
        session_id = _new_row(
            module_seeded_url, state=state, start=START + timedelta(hours=hours)
        )
        assert stored_value(module_seeded_url, session_id).first_confirmed_at is None


def test_it_is_set_once_and_survives_every_later_state(module_seeded_url):
    url = module_seeded_url
    session_id = _new_row(url, state="unassigned", start=START + timedelta(days=1))
    tutor = in_uow(url, persist_tutor_user)

    move(url, session_id, "offered", tutor_user_id=tutor)
    assert stored_value(url, session_id).first_confirmed_at is None
    with frozen_clock(T1):
        move(url, session_id, "confirmed")
    # confirmed → skipped_closure → restored to offered → confirmed again.
    with frozen_clock(T2):
        move(url, session_id, "skipped_closure")
        assert stored_value(url, session_id).first_confirmed_at == T1
        move(url, session_id, "offered", tutor_user_id=tutor)
        assert stored_value(url, session_id).first_confirmed_at == T1
    with frozen_clock(T3):
        move(url, session_id, "confirmed")
        move(url, session_id, "completed")

    assert stored_value(url, session_id).first_confirmed_at == T1


def test_it_survives_a_cancellation(module_seeded_url):
    url = module_seeded_url
    with frozen_clock(T1):
        session_id = _new_row(url, state="confirmed", start=START + timedelta(days=2))
    with frozen_clock(T2):
        move(url, session_id, "cancelled", reason="Cancelada por el centro")

    row = stored_value(url, session_id)
    assert (row.state, row.first_confirmed_at) == ("cancelled", T1)


def test_an_explicit_value_is_kept(module_seeded_url):
    earlier = T1 - timedelta(days=30)
    with frozen_clock(T1):
        session_id = _new_row(
            module_seeded_url,
            state="confirmed",
            start=START + timedelta(days=3),
            first_confirmed_at=earlier,
        )

    assert stored_value(module_seeded_url, session_id).first_confirmed_at == earlier


@pytest.mark.parametrize("state", ["confirmed", "completed"])
def test_the_check_rejects_a_confirmed_row_without_it(module_seeded_url, state):
    url = module_seeded_url
    offered = _new_row(url, state="offered", start=START + timedelta(days=4))
    [refs] = run_sql(
        url,
        "SELECT subject_id, tutor_user_id FROM class_sessions WHERE id = :id",
        {"id": str(offered)},
    )

    with pytest.raises(IntegrityError, match=CHECK):
        run_sql(
            url,
            "INSERT INTO class_sessions (kind, state, subject_id, tutor_user_id,"
            " starts_at, ends_at, scheduled_minutes) VALUES ('individual', :state,"
            " :subject, :tutor, '2031-04-01 16:00Z', '2031-04-01 17:00Z', 60)",
            {"state": state, "subject": refs.subject_id, "tutor": refs.tutor_user_id},
        )
    with pytest.raises(IntegrityError, match=CHECK):
        run_sql(
            url,
            "UPDATE class_sessions SET state = :state WHERE id = :id",
            {"state": state, "id": str(offered)},
        )
