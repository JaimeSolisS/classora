"""The makeup link of a one-off individual session (#423; ADR-0017 E5,
ADR-0005 D6, ``_docs/data-model.md`` §11.13).

- :func:`makeup_window`: whether a cancelled session can be made up, and by
  when. An approved cancellation request gives its stored
  ``final_makeup_allowed`` and ``final_makeup_until``. Any other cancelled
  session (a center cancellation) is allowed until its ``starts_at`` plus the
  ``makeup_window_days`` of the policy in force at ``cancelled_at``, the
  instant billing classifies it at, so a later policy edit moves no deadline
  (#423 E4). Days are of 24 hours, as in ``classify_change``.
- :func:`makeup_refusal`: step 4a of the create. The first rule the link
  breaks, as one field error.
- :func:`ensure_no_live_makeup`: step 4c of the create, ``409
  makeup_already_scheduled``. The index allows one **live** makeup per target
  (#423 E5). Two creates for one target share a student, so ``lock_students``
  serializes them; the index is the backstop.
- :func:`session_makeup`: the ``makeup`` block of a cancelled session.
- :func:`makeup_session_ids`: which sessions are makeups, for payroll (#107 C2,
  E4: each session by itself, with no chain walk).

The rules run at create only: a later move keeps the link (#423 E6), and no
route edits it. Billing never reads the link (ADR-0006 E3).

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import FieldError
from app.modules.administration import service as administration
from app.modules.scheduling.billing_context import approved_cancellation_request
from app.modules.scheduling.errors import MakeupAlreadyScheduledError
from app.modules.scheduling.models import ClassSession, ClassSessionParticipant
from app.modules.scheduling.schemas import SessionMakeup
from app.modules.scheduling.states import SessionKind, SessionState

_MESSAGES: Final = {
    "makeup_target_unknown": "Choose an existing cancelled session.",
    "makeup_target_not_cancelled": "Only a cancelled session can be made up.",
    "makeup_target_not_individual": "Only an individual session can be made up.",
    "makeup_not_allowed": "This cancellation does not allow a makeup.",
    "makeup_student_mismatch": "The student is not in the cancelled session.",
    "makeup_subject_mismatch": "The subject differs from the cancelled session's.",
    "makeup_window_passed": "The makeup must start by its deadline.",
}


@dataclass(frozen=True)
class MakeupWindow:
    """A cancelled session's makeup allowance; ``until`` is ``None`` when it
    isn't ``allowed``."""

    allowed: bool
    until: datetime | None


async def makeup_window(
    session: AsyncSession, class_session_id: uuid.UUID
) -> MakeupWindow | None:
    """The allowance, or ``None`` unless the session is ``cancelled`` (or
    unknown). At most two statements: the session with its approved request,
    then the policy version when there is no such request."""
    request = approved_cancellation_request()
    row = (
        await session.execute(
            select(
                ClassSession.state,
                ClassSession.starts_at,
                ClassSession.cancelled_at,
                request.c.final_classification,
                request.c.final_makeup_allowed,
                request.c.final_makeup_until,
            )
            .outerjoin(request, literal(True))
            .where(ClassSession.id == class_session_id)
        )
    ).first()
    if (
        row is None
        or row.state != SessionState.CANCELLED.value
        or row.cancelled_at is None
    ):
        return None
    if row.final_classification is not None:
        until = row.final_makeup_until
        return MakeupWindow(
            allowed=bool(row.final_makeup_allowed),
            until=None if until is None else until.astimezone(UTC),
        )
    policy = await administration.get_scheduling_policy(session, row.cancelled_at)
    return MakeupWindow(
        allowed=True,
        until=(row.starts_at + timedelta(days=policy.makeup_window_days)).astimezone(
            UTC
        ),
    )


def _refusal(code: str) -> FieldError:
    return {
        "path": ["body", "makeup_of_session_id"],
        "code": code,
        "message": _MESSAGES[code],
    }


async def makeup_refusal(
    session: AsyncSession,
    target_id: uuid.UUID,
    *,
    student_id: uuid.UUID,
    subject_id: uuid.UUID,
    starts_at: datetime,
) -> FieldError | None:
    """The first rule the new session breaks (#423 B4, in that order), or
    ``None``. Never overridable."""
    target = (
        await session.execute(
            select(
                ClassSession.state, ClassSession.kind, ClassSession.subject_id
            ).where(ClassSession.id == target_id)
        )
    ).first()
    if target is None:
        return _refusal("makeup_target_unknown")
    if target.state != SessionState.CANCELLED.value:
        return _refusal("makeup_target_not_cancelled")
    if target.kind != SessionKind.INDIVIDUAL.value:
        return _refusal("makeup_target_not_individual")
    window = await makeup_window(session, target_id)
    if window is None or not window.allowed or window.until is None:
        return _refusal("makeup_not_allowed")
    participant = await session.scalar(
        select(ClassSessionParticipant.id).where(
            ClassSessionParticipant.class_session_id == target_id,
            ClassSessionParticipant.student_id == student_id,
        )
    )
    if participant is None:
        return _refusal("makeup_student_mismatch")
    if target.subject_id != subject_id:
        return _refusal("makeup_subject_mismatch")
    if starts_at > window.until:
        return _refusal("makeup_window_passed")
    return None


async def _live_makeup_id(
    session: AsyncSession, target_id: uuid.UUID
) -> uuid.UUID | None:
    """The target's live makeup: the predicate of the partial unique index."""
    return await session.scalar(
        select(ClassSession.id).where(
            ClassSession.makeup_of_session_id == target_id,
            ClassSession.state != SessionState.CANCELLED.value,
        )
    )


async def ensure_no_live_makeup(session: AsyncSession, target_id: uuid.UUID) -> None:
    """``409 makeup_already_scheduled`` when the target has a live makeup."""
    live = await _live_makeup_id(session, target_id)
    if live is not None:
        raise MakeupAlreadyScheduledError(live)


async def session_makeup(
    session: AsyncSession, class_session_id: uuid.UUID
) -> SessionMakeup | None:
    """The ``makeup`` block of a session, ``None`` unless it is ``cancelled``.
    At most three statements."""
    window = await makeup_window(session, class_session_id)
    if window is None:
        return None
    return SessionMakeup(
        allowed=window.allowed,
        until=window.until,
        session_id=await _live_makeup_id(session, class_session_id),
    )


async def makeup_session_ids(
    session: AsyncSession, class_session_ids: Collection[uuid.UUID]
) -> set[uuid.UUID]:
    """Which of these sessions have ``makeup_of_session_id`` (#107 C2). One
    statement, none for no ids."""
    if not class_session_ids:
        return set()
    return set(
        await session.scalars(
            select(ClassSession.id).where(
                ClassSession.id.in_(set(class_session_ids)),
                ClassSession.makeup_of_session_id.is_not(None),
            )
        )
    )
