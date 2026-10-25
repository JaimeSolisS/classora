"""Session closeout rows (#77 A2, D3 step 2, E1).

Every function runs in the caller's Unit of Work and never commits. The
session row lock (``scheduling.service.complete_class_session``) is taken
by the workflow before a closeout is written, so two submissions of one
session are serialized; ``uq_session_closeouts_class_session_id`` is the
backstop.
"""

import uuid
from collections.abc import Collection
from datetime import datetime
from typing import cast

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.academics.models import CLOSEOUT_SUBMITTED, SessionCloseout
from app.modules.academics.schemas import CloseoutSummary, CloseoutSummaryInput


async def closeout_for_session(
    session: AsyncSession, class_session_id: uuid.UUID
) -> SessionCloseout | None:
    """The session's closeout, or ``None``. One statement, no lock."""
    return cast(
        SessionCloseout | None,
        await session.scalar(
            select(SessionCloseout).where(
                SessionCloseout.class_session_id == class_session_id
            )
        ),
    )


async def closeouts_by_id(
    session: AsyncSession, closeout_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, SessionCloseout]:
    """The closeouts with these ids (unknown ids left out). One statement,
    none for an empty collection."""
    if not closeout_ids:
        return {}
    rows = await session.scalars(
        select(SessionCloseout).where(SessionCloseout.id.in_(set(closeout_ids)))
    )
    return {row.id: row for row in rows}


def closeout_session_keys(
    closeout_ids: Collection[uuid.UUID],
) -> Select[uuid.UUID, uuid.UUID]:
    """A select of ``(subject_id, class_session_id)`` for these closeouts,
    for a caller that joins it into its own read (the approval inbox
    summaries read the sessions in the same statement, #77 E1). Not
    executed here."""
    return select(
        SessionCloseout.id.label("subject_id"),
        SessionCloseout.class_session_id.label("class_session_id"),
    ).where(SessionCloseout.id.in_(set(closeout_ids)))


async def create_session_closeout(
    session: AsyncSession,
    *,
    class_session_id: uuid.UUID,
    tutor_user_id: uuid.UUID,
    summary: CloseoutSummaryInput,
    submitted_at: datetime,
) -> SessionCloseout:
    """A ``submitted`` closeout (D3 step 2), flushed; its automatic audit
    event records the create. The summary is stored trimmed, an empty field
    as ``NULL`` (the input model has trimmed it)."""
    row = SessionCloseout(
        id=uuid.uuid7(),
        class_session_id=class_session_id,
        tutor_user_id=tutor_user_id,
        status=CLOSEOUT_SUBMITTED,
        topics=summary.topics,
        performance=summary.performance,
        homework=summary.homework,
        next_steps=summary.next_steps,
        submitted_at=submitted_at,
    )
    session.add(row)
    await session.flush()
    return row


def summary_of(row: SessionCloseout) -> CloseoutSummary:
    """The stored summary of ``row``."""
    return CloseoutSummary(
        topics=row.topics,
        performance=row.performance,
        homework=row.homework,
        next_steps=row.next_steps,
    )
