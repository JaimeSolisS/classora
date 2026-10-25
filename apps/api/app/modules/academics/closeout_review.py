"""Closeout review rows (#78 A1, B2, C, D, E1).

Every function runs in the caller's Unit of Work and never commits.

- :func:`lock_closeout`: the session's closeout ``FOR UPDATE`` (re-read),
  the first lock of every #78 writer (closeout → session → approval
  request).
- :func:`closeout_awaits_tutor`: whether the session's closeout is
  ``changes_requested`` (one statement, no lock), registered in
  ``app.wiring`` as the scheduling ``completed`` attendance unlock (E6).
- :func:`add_approval`: the next immutable approval revision (E1), and the
  closeout ``approved`` on it.
- :func:`add_correction`: one append-only staff correction row (E5).
- :func:`current_approval` / :func:`closeout_history`: the reads of ``GET
  …/closeout``.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import cast

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.modules.academics.models import (
    CLOSEOUT_APPROVED,
    CLOSEOUT_CHANGES_REQUESTED,
    SessionCloseout,
    SessionCloseoutApproval,
    SessionCloseoutCorrection,
)


async def lock_closeout(
    session: AsyncSession, class_session_id: uuid.UUID
) -> SessionCloseout | None:
    """The session's closeout ``FOR UPDATE``, re-read; ``None`` when it has
    none."""
    return cast(
        SessionCloseout | None,
        await session.scalar(
            select(SessionCloseout)
            .where(SessionCloseout.class_session_id == class_session_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ),
    )


async def closeout_awaits_tutor(
    session: AsyncSession, class_session_id: uuid.UUID
) -> bool:
    """B2/E6: the session's closeout is ``changes_requested`` (the tutor
    may mark attendance on the ``completed`` session again). One
    statement, no lock: the caller holds the session row lock, and every
    status change of the closeout takes that row after the closeout's."""
    found = await session.scalar(
        select(
            exists().where(
                SessionCloseout.class_session_id == class_session_id,
                SessionCloseout.status == CLOSEOUT_CHANGES_REQUESTED,
            )
        )
    )
    return bool(found)


@dataclass(frozen=True)
class ApprovalSnapshot:
    """What an approval revision freezes (the payroll fields, M7-04)."""

    class_session_id: uuid.UUID
    tutor_user_id: uuid.UUID
    session_starts_at: datetime
    session_kind: str
    scheduled_minutes: int
    approved_minutes: int
    attendance: Mapping[uuid.UUID, str]


async def add_approval(
    session: AsyncSession,
    closeout: SessionCloseout,
    *,
    source: str,
    snapshot: ApprovalSnapshot,
    approved_by_user_id: uuid.UUID,
    changes: Mapping[str, str | None] | None = None,
) -> SessionCloseoutApproval:
    """Insert revision ``n + 1`` of the (locked) closeout, superseding its
    current revision (``None`` for revision 1), and point the closeout at
    it: ``approved``, no request-changes reason, plus ``changes`` (summary
    columns a staff correction sets), in one update, so the closeout's
    version goes up by exactly one (the caller has no other pending change
    on it). Flushed."""
    previous = (
        None
        if closeout.current_approval_id is None
        else await session.scalar(
            select(SessionCloseoutApproval.revision).where(
                SessionCloseoutApproval.id == closeout.current_approval_id
            )
        )
    )
    approval = SessionCloseoutApproval(
        id=uuid.uuid7(),
        closeout_id=closeout.id,
        revision=1 if previous is None else previous + 1,
        supersedes_approval_id=closeout.current_approval_id,
        source=source,
        class_session_id=snapshot.class_session_id,
        tutor_user_id=snapshot.tutor_user_id,
        session_starts_at=snapshot.session_starts_at,
        session_kind=snapshot.session_kind,
        scheduled_minutes=snapshot.scheduled_minutes,
        approved_minutes=snapshot.approved_minutes,
        attendance={str(k): v for k, v in sorted(snapshot.attendance.items(), key=str)},
        approved_by_user_id=approved_by_user_id,
        approved_at=clock.now(),
    )
    session.add(approval)
    await session.flush()
    for column, value in (changes or {}).items():
        setattr(closeout, column, value)
    closeout.status = CLOSEOUT_APPROVED
    closeout.current_approval_id = approval.id
    closeout.review_reason = None
    await session.flush()
    return approval


async def add_correction(
    session: AsyncSession,
    closeout: SessionCloseout,
    *,
    fields: Sequence[str],
    previous_minutes: int | None,
    new_minutes: int | None,
    reason: str,
    closeout_status: str,
    approval_id: uuid.UUID | None,
    corrected_by_user_id: uuid.UUID,
) -> SessionCloseoutCorrection:
    """One staff correction row, flushed."""
    correction = SessionCloseoutCorrection(
        id=uuid.uuid7(),
        closeout_id=closeout.id,
        fields=list(fields),
        previous_minutes=previous_minutes,
        new_minutes=new_minutes,
        reason=reason,
        closeout_status=closeout_status,
        approval_id=approval_id,
        corrected_by_user_id=corrected_by_user_id,
    )
    session.add(correction)
    await session.flush()
    return correction


async def current_approval(
    session: AsyncSession, closeout_id: uuid.UUID
) -> SessionCloseoutApproval | None:
    """The closeout's current approval revision, or ``None``. One
    statement."""
    return cast(
        SessionCloseoutApproval | None,
        await session.scalar(
            select(SessionCloseoutApproval)
            .join(
                SessionCloseout,
                SessionCloseout.current_approval_id == SessionCloseoutApproval.id,
            )
            .where(SessionCloseout.id == closeout_id)
        ),
    )


@dataclass(frozen=True)
class CloseoutHistory:
    """A closeout's approval revisions and staff corrections, newest
    first."""

    revisions: list[SessionCloseoutApproval]
    corrections: list[SessionCloseoutCorrection]


async def closeout_history(
    session: AsyncSession, closeout_id: uuid.UUID
) -> CloseoutHistory:
    """Two statements whatever the number of rows."""
    revisions = list(
        await session.scalars(
            select(SessionCloseoutApproval)
            .where(SessionCloseoutApproval.closeout_id == closeout_id)
            .order_by(SessionCloseoutApproval.revision.desc())
        )
    )
    corrections = list(
        await session.scalars(
            select(SessionCloseoutCorrection)
            .where(SessionCloseoutCorrection.closeout_id == closeout_id)
            .order_by(
                SessionCloseoutCorrection.created_at.desc(),
                SessionCloseoutCorrection.id.desc(),
            )
        )
    )
    return CloseoutHistory(revisions=revisions, corrections=corrections)
