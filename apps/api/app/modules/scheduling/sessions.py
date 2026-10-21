"""Class session writes shared by M4-03…M4-22 (#47 C2–C4).

Every function runs in the caller's Unit of Work and never commits. They
check no availability, qualification, closure, operating hours or capacity
(#48) and create no offer, notification or inbox item (#52).
"""

import uuid
from typing import Final

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.authz import RecordNotFoundError
from app.core.models import ensure_version
from app.modules.scheduling.errors import (
    InvalidSessionTransitionError,
    SessionOverlapError,
)
from app.modules.scheduling.models import (
    CANCELLATION_REASON_MAX_LENGTH,
    ROOM_OVERLAP_CONSTRAINT,
    TUTOR_OVERLAP_CONSTRAINT,
    ClassSession,
)
from app.modules.scheduling.states import (
    TUTOR_OCCUPYING_STATES,
    SessionState,
    can_transition,
)

TIME_ZONE_DEPENDENCY: Final = "scheduling.class_sessions"
"""The name of the class-session check in the time-zone registry (#37)."""


async def flush_class_sessions(session: AsyncSession) -> None:
    """Flush; an exclusion-constraint violation raises
    :class:`SessionOverlapError` (``tutor_overlap`` | ``room_overlap``).
    Any other integrity error is re-raised as is."""
    try:
        await session.flush()
    except IntegrityError as exc:
        message = str(exc.orig)
        if TUTOR_OVERLAP_CONSTRAINT in message:
            raise SessionOverlapError("tutor_overlap") from None
        if ROOM_OVERLAP_CONSTRAINT in message:
            raise SessionOverlapError("room_overlap") from None
        raise


def _cancellation_reason(reason: str | None) -> str:
    text = (reason or "").strip()
    if not 1 <= len(text) <= CANCELLATION_REASON_MAX_LENGTH:
        raise ValueError("a cancellation needs a reason of 1-1000 characters")
    return text


async def transition_class_session(
    session: AsyncSession,
    class_session_id: uuid.UUID,
    *,
    to_state: SessionState | str,
    expected_version: int,
    tutor_user_id: uuid.UUID | None = None,
    reason: str | None = None,
) -> ClassSession:
    """Move one session to ``to_state`` (ADR-0005 D2, D3, D6).

    Order: the row ``FOR UPDATE`` (missing → ``RecordNotFoundError``) →
    ``ensure_version`` (``StaleVersionError``) → ``can_transition``
    (:class:`InvalidSessionTransitionError`) → the change, flushed through
    :func:`flush_class_sessions` (an overlap → :class:`SessionOverlapError`).

    - → ``offered``: ``tutor_user_id`` is required and becomes the tutor;
    - → ``unassigned``: the tutor is cleared (D3: the tutor's slot is
      released, the room and students stay held);
    - → ``cancelled``: ``reason`` (trimmed, 1–1000) is required; it sets
      ``cancelled_at`` from :func:`app.core.clock.now`;
    - other targets keep the tutor; ``confirmed`` / ``completed`` need one.

    A missing or unexpected argument is a ``ValueError`` (a programming
    error of the caller, raised before anything changes).
    """
    target = SessionState(to_state)
    row = await session.scalar(
        select(ClassSession)
        .where(ClassSession.id == class_session_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise RecordNotFoundError()
    ensure_version(row, expected_version)
    if not can_transition(row.state, target):
        raise InvalidSessionTransitionError(row.state, target.value)

    if target == SessionState.OFFERED:
        if tutor_user_id is None:
            raise ValueError("an offer needs tutor_user_id")
    elif tutor_user_id is not None:
        raise ValueError("tutor_user_id is only taken for an offer")
    if target != SessionState.CANCELLED and reason is not None:
        raise ValueError("reason is only taken for a cancellation")

    tutor = row.tutor_user_id
    if target == SessionState.OFFERED:
        tutor = tutor_user_id
    elif target == SessionState.UNASSIGNED:
        tutor = None
    if target in TUTOR_OCCUPYING_STATES and tutor is None:
        raise ValueError(f"a {target.value} session needs a tutor")

    if target == SessionState.CANCELLED:
        row.cancellation_reason = _cancellation_reason(reason)
        row.cancelled_at = clock.now()
    row.tutor_user_id = tutor
    row.state = target.value
    await flush_class_sessions(session)
    return row


async def has_class_sessions(session: AsyncSession) -> bool:
    """Whether any class session exists, in any state (#37 E1 (a)): past and
    cancelled rows also hold instants computed from the center's local time,
    so any row blocks a time-zone change."""
    return bool(await session.scalar(select(select(ClassSession.id).exists())))
