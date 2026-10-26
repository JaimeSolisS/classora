"""Session note rows (#79 B1, E1, E2, F2).

Every function runs in the caller's Unit of Work and never commits. They
take plain ids and values: who may call them is decided by
``app.workflows.session_notes`` with the pure rules in
:mod:`~app.modules.academics.note_rules`.

The list queries join a caller-built select of the notes' sessions
(``id``, ``starts_at``, ``subject_id``: ``scheduling.service
.note_session_columns``) as a subquery, so a list and its session facts
come in **one** statement while academics never imports scheduling (#77
E2). For the family list that select already carries the #67
family-visible condition. Deleted notes are never returned.

Writes take the row ``FOR UPDATE`` (:func:`get_note_for_update`); the
model's version column adds the version to every update (``+ 1``), and
the automatic audit events record the diffs (E8).
"""

import uuid
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import Select, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import InvalidCursorError, decode_cursor, encode_cursor
from app.modules.academics.models import NOTE_FAMILY, NOTE_INTERNAL, SessionNote


@dataclass(frozen=True)
class NoteRow:
    """A live note with its session's start and subject."""

    note: SessionNote
    session_starts_at: datetime
    subject_id: uuid.UUID


@dataclass(frozen=True)
class NotePage:
    rows: list[NoteRow]
    next_cursor: str | None


# --- Reads ----------------------------------------------------------------------------


async def note_by_id(session: AsyncSession, note_id: uuid.UUID) -> SessionNote | None:
    """The live note, or ``None`` (unknown or deleted). One statement, no
    lock."""
    return cast(
        SessionNote | None,
        await session.scalar(
            select(SessionNote).where(
                SessionNote.id == note_id, SessionNote.deleted_at.is_(None)
            )
        ),
    )


async def get_note_for_update(
    session: AsyncSession, note_id: uuid.UUID
) -> SessionNote | None:
    """The live note ``FOR UPDATE``, re-read (``populate_existing``), or
    ``None`` when it is gone or was deleted meanwhile."""
    return cast(
        SessionNote | None,
        await session.scalar(
            select(SessionNote)
            .where(SessionNote.id == note_id, SessionNote.deleted_at.is_(None))
            .with_for_update()
            .execution_options(populate_existing=True)
        ),
    )


def _joined(sessions: Select[*tuple[Any, ...]]) -> Select[*tuple[Any, ...]]:
    joined = sessions.subquery()
    return select(SessionNote, joined.c.starts_at, joined.c.subject_id).join(
        joined, joined.c.id == SessionNote.class_session_id
    )


def _rows(result: Any) -> list[NoteRow]:
    return [
        NoteRow(note=note, session_starts_at=starts_at, subject_id=subject_id)
        for note, starts_at, subject_id in result
    ]


async def notes_for_session(
    session: AsyncSession,
    class_session_id: uuid.UUID,
    *,
    sessions: Select[*tuple[Any, ...]],
    student_id: uuid.UUID | None = None,
) -> list[NoteRow]:
    """The session's live notes (every author), oldest first (``created_at``
    then ``id``), optionally of one student. One statement."""
    query = _joined(sessions).where(
        SessionNote.class_session_id == class_session_id,
        SessionNote.deleted_at.is_(None),
    )
    if student_id is not None:
        query = query.where(SessionNote.student_id == student_id)
    result = await session.execute(
        query.order_by(SessionNote.created_at, SessionNote.id)
    )
    return _rows(result)


def _position(cursor: str) -> tuple[datetime, uuid.UUID]:
    created_at, note_id = decode_cursor(cursor, types=(str, str))
    try:
        when = datetime.fromisoformat(str(created_at))
        identifier = uuid.UUID(str(note_id))
    except ValueError:
        raise InvalidCursorError() from None
    if when.tzinfo is None:
        raise InvalidCursorError()
    return when, identifier


async def _page(
    session: AsyncSession,
    query: Select[*tuple[Any, ...]],
    *,
    cursor: str | None,
    limit: int,
) -> NotePage:
    if cursor is not None:
        when, identifier = _position(cursor)
        query = query.where(
            tuple_(SessionNote.created_at, SessionNote.id) < tuple_(when, identifier)
        )
    result = await session.execute(
        query.order_by(SessionNote.created_at.desc(), SessionNote.id.desc()).limit(
            limit + 1
        )
    )
    rows = _rows(result)
    items = rows[:limit]
    next_cursor = (
        encode_cursor([items[-1].note.created_at.isoformat(), str(items[-1].note.id)])
        if len(rows) > limit and items
        else None
    )
    return NotePage(rows=items, next_cursor=next_cursor)


async def notes_for_student(
    session: AsyncSession,
    student_id: uuid.UUID,
    *,
    sessions: Select[*tuple[Any, ...]],
    cursor: str | None,
    limit: int,
    author_user_id: uuid.UUID | None = None,
) -> NotePage:
    """E2: the student's live notes, newest first by ``(created_at, id)``,
    optionally only one author's (a former student's tutor). One
    statement."""
    query = _joined(sessions).where(
        SessionNote.student_id == student_id, SessionNote.deleted_at.is_(None)
    )
    if author_user_id is not None:
        query = query.where(SessionNote.author_user_id == author_user_id)
    return await _page(session, query, cursor=cursor, limit=limit)


async def family_notes_for_student(
    session: AsyncSession,
    student_id: uuid.UUID,
    *,
    family_sessions: Select[*tuple[Any, ...]],
    cursor: str | None,
    limit: int,
    class_session_id: uuid.UUID | None = None,
) -> NotePage:
    """F2: the student's **shared** live notes on family-visible sessions
    (``family_sessions`` carries that condition), newest first, optionally
    of one session. One statement. ``visibility = 'family'``, ``deleted_at
    IS NULL`` and the student are always applied here: internal,
    unpublished and deleted notes never leave this function."""
    query = _joined(family_sessions).where(
        SessionNote.student_id == student_id,
        SessionNote.visibility == NOTE_FAMILY,
        SessionNote.deleted_at.is_(None),
    )
    if class_session_id is not None:
        query = query.where(SessionNote.class_session_id == class_session_id)
    return await _page(session, query, cursor=cursor, limit=limit)


async def family_note_counts(
    session: AsyncSession,
    student_id: uuid.UUID,
    class_session_ids: Collection[uuid.UUID],
) -> dict[uuid.UUID, int]:
    """#84 C2: how many **shared** (``visibility = 'family'``) live notes the
    student has in each of these sessions (sessions without one are left
    out). One statement, none for an empty collection."""
    ids = set(class_session_ids)
    if not ids:
        return {}
    rows = await session.execute(
        select(SessionNote.class_session_id, func.count())
        .where(
            SessionNote.student_id == student_id,
            SessionNote.class_session_id.in_(ids),
            SessionNote.visibility == NOTE_FAMILY,
            SessionNote.deleted_at.is_(None),
        )
        .group_by(SessionNote.class_session_id)
    )
    return {class_session_id: int(count) for class_session_id, count in rows}


# --- Writes ---------------------------------------------------------------------------


async def create_note(
    session: AsyncSession,
    *,
    class_session_id: uuid.UUID,
    student_id: uuid.UUID,
    author_user_id: uuid.UUID,
    body: str,
) -> SessionNote:
    """C2 step 9: an ``internal`` note (always), flushed; its automatic
    audit event records the create. ``body`` is already trimmed."""
    note = SessionNote(
        id=uuid.uuid7(),
        class_session_id=class_session_id,
        student_id=student_id,
        author_user_id=author_user_id,
        body=body,
        visibility=NOTE_INTERNAL,
    )
    session.add(note)
    await session.flush()
    return note


async def update_note_body(
    session: AsyncSession, note: SessionNote, *, body: str, now: datetime
) -> bool:
    """D2: the new text (version + 1); an identical text writes nothing
    (``False``)."""
    if note.body == body:
        return False
    note.body = body
    note.body_updated_at = now
    await session.flush()
    return True


async def publish_note(
    session: AsyncSession, note: SessionNote, *, actor_id: uuid.UUID, now: datetime
) -> None:
    """D3: ``internal`` → ``family``; the last unpublish is cleared."""
    note.visibility = NOTE_FAMILY
    note.published_at = now
    note.published_by_user_id = actor_id
    note.unpublished_at = None
    note.unpublished_by_user_id = None
    note.unpublish_reason = None
    await session.flush()


async def unpublish_note(
    session: AsyncSession,
    note: SessionNote,
    *,
    actor_id: uuid.UUID,
    now: datetime,
    reason: str | None,
) -> None:
    """D4: ``family`` → ``internal``, with who, when and why."""
    note.visibility = NOTE_INTERNAL
    note.published_at = None
    note.published_by_user_id = None
    note.unpublished_at = now
    note.unpublished_by_user_id = actor_id
    note.unpublish_reason = reason
    await session.flush()


async def soft_delete_note(
    session: AsyncSession, note: SessionNote, *, actor_id: uuid.UUID, now: datetime
) -> None:
    """D5: soft delete (an ``internal`` note; the CHECK backs it up)."""
    note.deleted_at = now
    note.deleted_by_user_id = actor_id
    await session.flush()
