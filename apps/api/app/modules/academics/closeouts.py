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

from sqlalchemy import Select, case, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.academics.models import (
    CLOSEOUT_APPROVED,
    CLOSEOUT_SUBMITTED,
    SessionCloseout,
    SessionCloseoutApproval,
)
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
    session: AsyncSession, closeout_ids: Collection[uuid.UUID], *, fresh: bool = False
) -> dict[uuid.UUID, SessionCloseout]:
    """The closeouts with these ids (unknown ids left out). One statement,
    none for an empty collection. ``fresh`` re-reads rows already in the
    session (no lock)."""
    if not closeout_ids:
        return {}
    statement = select(SessionCloseout).where(SessionCloseout.id.in_(set(closeout_ids)))
    if fresh:
        statement = statement.execution_options(populate_existing=True)
    rows = await session.scalars(statement)
    return {row.id: row for row in rows}


def closeout_session_keys(
    closeout_ids: Collection[uuid.UUID],
) -> Select[uuid.UUID, uuid.UUID, str, int | None, uuid.UUID | None]:
    """A select of ``(subject_id, class_session_id, kind_of_review,
    approved_minutes, on_behalf_by_user_id)`` for these closeouts, for a
    caller that joins it into its own read (the approval inbox summaries
    read the sessions in the same statement, #77 E1; the caller passes the
    columns after the first two through). ``kind_of_review`` follows
    :func:`~app.modules.academics.closeout_rules.kind_of_review` (#78 C4):
    ``correction`` with a current approval revision, ``resubmission`` after
    changes were requested (the reason stays until an approval), else
    ``first``; ``approved_minutes`` is the current revision's;
    ``on_behalf_by_user_id`` the reviewer who made the latest submission on
    the tutor's behalf (#301), for the caller to name. Not executed here."""
    return (
        select(
            SessionCloseout.id.label("subject_id"),
            SessionCloseout.class_session_id.label("class_session_id"),
            case(
                (SessionCloseout.current_approval_id.is_not(None), "correction"),
                (SessionCloseout.review_reason.is_not(None), "resubmission"),
                else_="first",
            ).label("kind_of_review"),
            SessionCloseoutApproval.approved_minutes.label("approved_minutes"),
            SessionCloseout.on_behalf_by_user_id.label("on_behalf_by_user_id"),
        )
        .outerjoin(
            SessionCloseoutApproval,
            SessionCloseoutApproval.id == SessionCloseout.current_approval_id,
        )
        .where(SessionCloseout.id.in_(set(closeout_ids)))
    )


async def create_session_closeout(
    session: AsyncSession,
    *,
    class_session_id: uuid.UUID,
    tutor_user_id: uuid.UUID,
    summary: CloseoutSummaryInput,
    submitted_at: datetime,
    on_behalf_by_user_id: uuid.UUID | None = None,
    on_behalf_reason: str | None = None,
) -> SessionCloseout:
    """A ``submitted`` closeout (D3 step 2), flushed; its automatic audit
    event records the create. The summary is stored trimmed, an empty field
    as ``NULL`` (the input model has trimmed it). #301: a reviewer's
    submission on the tutor's behalf sets ``on_behalf_by_user_id`` and
    ``on_behalf_reason``."""
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
        last_submitted_at=submitted_at,
        on_behalf_by_user_id=on_behalf_by_user_id,
        on_behalf_reason=on_behalf_reason,
    )
    session.add(row)
    await session.flush()
    return row


def approved_summary_columns() -> Select[
    uuid.UUID, str | None, str | None, str | None, str | None
]:
    """#84 C3: ``SELECT class_session_id, topics, performance, homework,
    next_steps`` of the closeouts that are ``approved`` **now** and have at
    least one summary field, **not executed** (``scheduling`` joins it into
    the session log, so the log takes one statement). A closeout that is
    ``submitted`` or ``changes_requested`` (also an approved one back in
    review after a tutor's correction request, #305: its columns hold the
    new, unapproved text) is never selected."""
    return select(
        SessionCloseout.class_session_id,
        SessionCloseout.topics,
        SessionCloseout.performance,
        SessionCloseout.homework,
        SessionCloseout.next_steps,
    ).where(
        SessionCloseout.status == CLOSEOUT_APPROVED,
        or_(
            SessionCloseout.topics.is_not(None),
            SessionCloseout.performance.is_not(None),
            SessionCloseout.homework.is_not(None),
            SessionCloseout.next_steps.is_not(None),
        ),
    )


async def approved_closeout_session_ids(
    session: AsyncSession, class_session_ids: Collection[uuid.UUID]
) -> set[uuid.UUID]:
    """#94 E2, L3: the sessions among ``class_session_ids`` whose closeout
    has an approval revision (revision 1 and any later one; a closeout back
    in review after a correction request still has its approval in force).
    One statement (none for no ids), no lock. Billing reads it: an
    approved individual session is covered by a package only once its
    credit was consumed."""
    if not class_session_ids:
        return set()
    rows = await session.scalars(
        select(SessionCloseoutApproval.class_session_id)
        .where(SessionCloseoutApproval.class_session_id.in_(set(class_session_ids)))
        .distinct()
    )
    return set(rows)


def closeout_sessions_of(tutor_user_id: uuid.UUID) -> Select[uuid.UUID]:
    """``SELECT class_session_id FROM session_closeouts`` of the closeouts
    ``tutor_user_id`` wrote (the closeout's tutor), **not executed**: the
    #84 former-tutor rule ("taught" = the session's current tutor or its
    closeout's tutor), joined by ``scheduling`` into its read."""
    return select(SessionCloseout.class_session_id).where(
        SessionCloseout.tutor_user_id == tutor_user_id
    )


def summary_of(row: SessionCloseout) -> CloseoutSummary:
    """The stored summary of ``row``."""
    return CloseoutSummary(
        topics=row.topics,
        performance=row.performance,
        homework=row.homework,
        next_steps=row.next_steps,
    )
