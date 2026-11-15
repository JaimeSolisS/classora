"""The scheduling seam of session notes (#79 B2, E1, E2, F2).

``app.workflows.session_notes`` composes these with ``academics`` (the
note rows) and ``families``; scheduling never imports ``academics``.

- :func:`note_session`: the session facts a note write or the session list
  needs (state, start, current tutor, subject), one statement; with
  ``share`` the row is taken ``FOR SHARE`` (C2 step 5), so a create can't
  interleave with a state transition or a substitution (which lock the row
  ``FOR UPDATE``).
- :func:`is_participant`: whether a student takes part in the session (one
  statement).
- :func:`note_session_columns`: an **unexecuted** select of ``(id,
  starts_at, subject_id)`` for academics to join into its note lists (one
  statement per list); with ``family_visible`` it carries the #67
  family-visible condition (``role_schedules.family_visible_condition``),
  so the family list never returns a note of a session families don't see.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.scheduling.models import ClassSession, ClassSessionParticipant
from app.modules.scheduling.role_schedules import family_visible_condition


@dataclass(frozen=True)
class NoteSession:
    """What a note needs of its session (B2)."""

    id: uuid.UUID
    state: str
    starts_at: datetime
    tutor_user_id: uuid.UUID | None
    subject_id: uuid.UUID


async def note_session(
    session: AsyncSession, class_session_id: uuid.UUID, *, share: bool = False
) -> NoteSession | None:
    """The session (``None`` when unknown), unlocked or ``FOR SHARE``
    (re-read). One statement."""
    query = select(ClassSession).where(ClassSession.id == class_session_id)
    if share:
        query = query.with_for_update(read=True).execution_options(
            populate_existing=True
        )
    row = cast(ClassSession | None, await session.scalar(query))
    if row is None:
        return None
    return NoteSession(
        id=row.id,
        state=row.state,
        starts_at=row.starts_at.astimezone(UTC),
        tutor_user_id=row.tutor_user_id,
        subject_id=row.subject_id,
    )


async def is_participant(
    session: AsyncSession, class_session_id: uuid.UUID, student_id: uuid.UUID
) -> bool:
    """Whether ``student_id`` takes part in the session. One statement."""
    found = await session.scalar(
        select(ClassSessionParticipant.id).where(
            ClassSessionParticipant.class_session_id == class_session_id,
            ClassSessionParticipant.student_id == student_id,
        )
    )
    return found is not None


def note_session_columns(
    *, family_visible: bool = False
) -> Select[uuid.UUID, datetime, uuid.UUID]:
    """``SELECT id, starts_at, subject_id FROM class_sessions`` (with
    ``family_visible``, only the sessions families see), not executed."""
    query = select(ClassSession.id, ClassSession.starts_at, ClassSession.subject_id)
    if family_visible:
        query = query.where(family_visible_condition())
    return query
