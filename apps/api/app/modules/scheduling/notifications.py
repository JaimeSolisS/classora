"""The notifications of session and series edits (#54 D; ADR-0005 D2
family visibility; the #232 recipient rule).

Three types, registered by ``app.wiring``
(:func:`register_edit_notifications`). Their params are ids, instants,
local dates and counts only (never a reason, a name or a room):

- ``scheduling.session_rescheduled`` ``{class_session_id, starts_at}``: a
  session moved by ``PATCH /api/v1/class-sessions/{id}``;
- ``scheduling.series_changed`` ``{series_id, from_date, changed,
  cancelled}``: a series edit; ``series_id`` is the resulting series (the
  new one after a split), ``changed`` / ``cancelled`` count the rows the
  recipient was told about;
- ``scheduling.series_cancelled`` ``{series_id, from_date}``: a series
  cancellation.

**Recipients** (:func:`audience`), from each affected row's state **before**
the write: the row's tutor when it was ``offered`` or ``confirmed``; for a
``confirmed`` row also the student's own login and the linked guardians
(``families.service.student_notification_recipients``). Nobody hears about
``unassigned``, ``conflict`` or ``skipped_closure`` rows, nor about
re-pointed rows. One notification per recipient per write; the actor is
never notified.
"""

import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Final, Literal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.administration import service as administration
from app.modules.families import service as families
from app.modules.scheduling.models import ClassSessionParticipant
from app.modules.scheduling.offer_records import rfc3339
from app.modules.scheduling.states import SessionState

SESSION_RESCHEDULED: Final = "scheduling.session_rescheduled"
SERIES_CHANGED: Final = "scheduling.series_changed"
SERIES_CANCELLED: Final = "scheduling.series_cancelled"

Audience = Literal["tutor", "family"]
Effect = Literal["changed", "cancelled"]


class SessionRescheduledParams(BaseModel):
    """``scheduling.session_rescheduled``: the session and its new start."""

    class_session_id: str
    starts_at: str


class SeriesChangedParams(BaseModel):
    """``scheduling.series_changed``: the resulting series, the local date the
    change starts on and the recipient's counts."""

    series_id: str
    from_date: str
    changed: int
    cancelled: int


class SeriesCancelledParams(BaseModel):
    """``scheduling.series_cancelled``: the series and the local date the
    cancellation starts on."""

    series_id: str
    from_date: str


def register_edit_notifications() -> None:
    """Register the three types (again: a no-op)."""
    administration.register_notification_type(
        SESSION_RESCHEDULED, SessionRescheduledParams
    )
    administration.register_notification_type(SERIES_CHANGED, SeriesChangedParams)
    administration.register_notification_type(SERIES_CANCELLED, SeriesCancelledParams)


def audience(previous_state: str) -> frozenset[Audience]:
    """Who hears about a row, from its state before the write (D2)."""
    if previous_state == SessionState.CONFIRMED:
        return frozenset({"tutor", "family"})
    if previous_state == SessionState.OFFERED:
        return frozenset({"tutor"})
    return frozenset()


@dataclass(frozen=True)
class Affected:
    """One row a write changed, as it was before: its id, state and tutor,
    and what happened to it."""

    class_session_id: uuid.UUID
    previous_state: str
    tutor_user_id: uuid.UUID | None
    effect: Effect


async def recipients(
    session: AsyncSession,
    rows: Sequence[Affected],
    *,
    actor_id: uuid.UUID | None,
    exclude_ids: Iterable[uuid.UUID] = (),
) -> dict[uuid.UUID, dict[Effect, int]]:
    """Per recipient user, how many affected rows of each effect concern
    them (at most three statements). The actor and ``exclude_ids`` are left
    out."""
    excluded = set(exclude_ids)
    family_rows = [
        row.class_session_id for row in rows if "family" in audience(row.previous_state)
    ]
    students: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    if family_rows:
        for class_session_id, student_id in await session.execute(
            select(
                ClassSessionParticipant.class_session_id,
                ClassSessionParticipant.student_id,
            ).where(ClassSessionParticipant.class_session_id.in_(family_rows))
        ):
            students[class_session_id].append(student_id)
    family_users = await families.student_notification_recipients(
        session, {student for ids in students.values() for student in ids}
    )
    found: dict[uuid.UUID, dict[Effect, int]] = {}
    for row in rows:
        users: set[uuid.UUID] = set()
        who = audience(row.previous_state)
        if "tutor" in who and row.tutor_user_id is not None:
            users.add(row.tutor_user_id)
        if "family" in who:
            for student_id in students.get(row.class_session_id, []):
                users.update(family_users.get(student_id, []))
        if actor_id is not None:
            users.discard(actor_id)
        users -= excluded
        for user_id in users:
            counts = found.setdefault(user_id, {"changed": 0, "cancelled": 0})
            counts[row.effect] += 1
    return found


async def _send(
    session: AsyncSession,
    user_ids: Iterable[uuid.UUID],
    notification_type: str,
    params: BaseModel,
) -> None:
    for user_id in sorted(user_ids):
        await administration.create_notification(
            session,
            recipient_user_id=user_id,
            notification_type=notification_type,
            params=params,
        )


async def notify_session_rescheduled(
    session: AsyncSession,
    row: Affected,
    *,
    starts_at: datetime,
    actor_id: uuid.UUID | None,
    exclude_ids: Iterable[uuid.UUID] = (),
) -> None:
    """D1: one ``scheduling.session_rescheduled`` per recipient of ``row``
    (never ``exclude_ids``: #62's requester of an approved reschedule)."""
    found = await recipients(session, [row], actor_id=actor_id, exclude_ids=exclude_ids)
    await _send(
        session,
        found,
        SESSION_RESCHEDULED,
        SessionRescheduledParams(
            class_session_id=str(row.class_session_id), starts_at=rfc3339(starts_at)
        ),
    )


async def notify_series_changed(
    session: AsyncSession,
    rows: Sequence[Affected],
    *,
    series_id: uuid.UUID,
    from_date: date,
    actor_id: uuid.UUID | None,
) -> None:
    """D1: one ``scheduling.series_changed`` per recipient, with their own
    counts."""
    found = await recipients(session, rows, actor_id=actor_id)
    for user_id in sorted(found):
        counts = found[user_id]
        await _send(
            session,
            [user_id],
            SERIES_CHANGED,
            SeriesChangedParams(
                series_id=str(series_id),
                from_date=from_date.isoformat(),
                changed=counts["changed"],
                cancelled=counts["cancelled"],
            ),
        )


async def notify_series_cancelled(
    session: AsyncSession,
    rows: Sequence[Affected],
    *,
    series_id: uuid.UUID,
    from_date: date,
    actor_id: uuid.UUID | None,
) -> None:
    """D1: one ``scheduling.series_cancelled`` per recipient."""
    found = await recipients(session, rows, actor_id=actor_id)
    await _send(
        session,
        found,
        SERIES_CANCELLED,
        SeriesCancelledParams(
            series_id=str(series_id), from_date=from_date.isoformat()
        ),
    )
