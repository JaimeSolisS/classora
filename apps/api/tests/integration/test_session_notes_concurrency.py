"""Concurrent note writes (#79 D6), with two connections (``race`` from
``test_assignment_offer_concurrency``): transaction A writes and keeps its
transaction open for a moment; B starts once A's change is flushed, so B
waits on the note row lock and then sees A's version.

- two publishes with the same version: one ``ok``, one
  ``version_conflict``;
- a publish racing a delete (both orders): exactly one succeeds;
- an edit racing a staff unpublish of a shared note: serialized by the row
  lock (the edit refused either way: ``note_published`` before, a version
  conflict after).

Each case runs 5 times, on new notes.
"""

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal
from app.modules.academics.schemas import (
    SessionNoteCreate,
    SessionNoteEdit,
    SessionNoteUnpublish,
)
from app.modules.identity.permissions import OWNER, ROLE_PERMISSIONS, TUTOR
from app.modules.scheduling.models import ClassSession
from app.workflows import session_notes as workflow
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


async def _note(url: str, *, shared: bool = False) -> tuple[uuid.UUID, uuid.UUID]:
    """A note of a new tutor on their started session: ``(note id, tutor
    id)``; ``shared`` publishes it (version 2)."""

    async def _make(session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
        tutor = await persist_user(session, roles=(TUTOR,))
        made: ClassSession = await persist_class_session(
            session,
            state="confirmed",
            tutor_user_id=tutor.id,
            starts_at=NOW - timedelta(minutes=30),
            ends_at=NOW + timedelta(minutes=30),
            scheduled_minutes=60,
        )
        student = (await add_participant(session, made)).student_id
        view = await workflow.create_note(
            session,
            principal=_principal(tutor.id, TUTOR),
            class_session_id=made.id,
            body=SessionNoteCreate(student_id=student, body="Nota"),
        )
        if shared:
            await workflow.publish_note(
                session,
                principal=_principal(tutor.id, TUTOR),
                note_id=view.id,
                version=1,
            )
        return view.id, tutor.id

    return await _in_uow(url, _make)


def _publish(note_id: uuid.UUID, tutor: uuid.UUID) -> Change:
    async def _run(db: AsyncSession) -> None:
        await workflow.publish_note(
            db, principal=_principal(tutor, TUTOR), note_id=note_id, version=1
        )

    return _run


def _delete(note_id: uuid.UUID, tutor: uuid.UUID) -> Change:
    async def _run(db: AsyncSession) -> None:
        await workflow.delete_note(
            db, principal=_principal(tutor, TUTOR), note_id=note_id, version=1
        )

    return _run


def _edit(note_id: uuid.UUID, tutor: uuid.UUID, version: int) -> Change:
    async def _run(db: AsyncSession) -> None:
        await workflow.edit_note(
            db,
            principal=_principal(tutor, TUTOR),
            note_id=note_id,
            body=SessionNoteEdit(version=version, body="Cambio"),
        )

    return _run


def _staff_unpublish(note_id: uuid.UUID, owner: uuid.UUID) -> Change:
    async def _run(db: AsyncSession) -> None:
        await workflow.unpublish_note(
            db,
            principal=_principal(owner, OWNER),
            note_id=note_id,
            body=SessionNoteUnpublish(version=2, reason="Dato médico"),
        )

    return _run


async def _row(url: str, note_id: uuid.UUID) -> tuple[str, int, bool]:
    [row] = await run_sql_async(
        url,
        "SELECT visibility, version, deleted_at IS NOT NULL AS deleted"
        " FROM session_notes WHERE id = :id",
        {"id": note_id},
    )
    return str(row.visibility), int(row.version), bool(row.deleted)


async def test_two_publishes_with_one_version(seeded_url):
    for _ in range(REPEATS):
        note_id, tutor = await _note(seeded_url)

        outcomes = await race(
            seeded_url, _publish(note_id, tutor), _publish(note_id, tutor)
        )

        assert outcomes == ["ok", "version_conflict"]
        assert await _row(seeded_url, note_id) == ("family", 2, False)


@pytest.mark.parametrize("publish_first", [True, False])
async def test_a_publish_racing_a_delete(seeded_url, publish_first):
    for _ in range(REPEATS):
        note_id, tutor = await _note(seeded_url)
        changes = (
            (_publish(note_id, tutor), _delete(note_id, tutor))
            if publish_first
            else (_delete(note_id, tutor), _publish(note_id, tutor))
        )

        outcomes = await race(seeded_url, *changes)

        assert outcomes[0] == "ok"
        assert outcomes[1] in {"version_conflict", "not_found"}
        assert await _row(seeded_url, note_id) == (
            ("family", 2, False) if publish_first else ("internal", 2, True)
        )


@pytest.mark.parametrize("unpublish_first", [True, False])
async def test_an_edit_racing_a_staff_unpublish(seeded_url, unpublish_first):
    owner = await _in_uow(seeded_url, lambda db: persist_user(db, roles=(OWNER,)))
    for _ in range(REPEATS):
        note_id, tutor = await _note(seeded_url, shared=True)
        unpublish = _staff_unpublish(note_id, owner.id)
        edit = _edit(note_id, tutor, 2)
        changes = (unpublish, edit) if unpublish_first else (edit, unpublish)

        outcomes = await race(seeded_url, *changes)

        # The edit of a shared note is refused before (note_published) or
        # after the unpublish (its version is stale): never both written.
        expected = (
            ["ok", "version_conflict"] if unpublish_first else ["note_published", "ok"]
        )
        assert outcomes == expected
        assert await _row(seeded_url, note_id) == ("internal", 3, False)
