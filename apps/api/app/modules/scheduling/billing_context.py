"""The scheduling seam of session charges (#91 C4, C5; ADR-0006 E1).

``app.workflows.session_charges`` composes it with ``billing`` and
``families``; scheduling never imports ``billing``.

- :data:`BILLABLE_STATES`: ``confirmed`` and ``completed`` (ADR-0006 E1).
  ``unassigned``, ``offered``, ``conflict``, ``cancelled`` and
  ``skipped_closure`` are never charged by the session sweeper.
- :func:`billable_participant_columns`: the participants of the billable
  sessions of some kinds that start at or after an instant, as an
  **unexecuted** ``SELECT`` the workflow joins (ADR-0002: cross-module joins
  only in ``app/workflows/``).
- :func:`lock_session_for_billing`: one session with its participants, the
  session row ``FOR SHARE`` (re-read). Every scheduling writer that changes
  a session's state, time, duration or participants takes that row ``FOR
  UPDATE``, so a charge waits for it, or it waits for the charge's
  transaction; either way the charge is posted from committed, current
  data.
"""

import uuid
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.scheduling.models import ClassSession, ClassSessionParticipant
from app.modules.scheduling.states import SessionState

BILLABLE_STATES: Final = frozenset({SessionState.CONFIRMED, SessionState.COMPLETED})
"""The states in which a session's participants are charged (ADR-0006 E1)."""


@dataclass(frozen=True)
class BillableSession:
    """What a session charge needs of its session (#91 C5 step 1)."""

    id: uuid.UUID
    kind: str
    state: str
    subject_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    scheduled_minutes: int
    participant_ids: tuple[uuid.UUID, ...]


def billable_participant_columns(
    *,
    kinds: Collection[str],
    starts_from: datetime,
    student_id: uuid.UUID | None = None,
) -> Select[uuid.UUID, uuid.UUID, uuid.UUID, datetime, datetime, str, int]:
    """``SELECT class_session_id, student_id, subject_id, starts_at,
    ends_at, state, scheduled_minutes`` of the participants of the sessions
    of ``kinds`` in a :data:`BILLABLE_STATES` state that start at or after
    ``starts_from`` (optionally one student's), **not executed**."""
    query = (
        select(
            ClassSession.id.label("class_session_id"),
            ClassSessionParticipant.student_id,
            ClassSession.subject_id,
            ClassSession.starts_at,
            ClassSession.ends_at,
            ClassSession.state,
            ClassSession.scheduled_minutes,
        )
        .join(
            ClassSessionParticipant,
            ClassSessionParticipant.class_session_id == ClassSession.id,
        )
        .where(
            ClassSession.kind.in_(sorted(kinds)),
            ClassSession.state.in_(sorted(state.value for state in BILLABLE_STATES)),
            ClassSession.starts_at >= starts_from,
        )
    )
    if student_id is not None:
        query = query.where(ClassSessionParticipant.student_id == student_id)
    return query


async def lock_session_for_billing(
    session: AsyncSession, class_session_id: uuid.UUID
) -> BillableSession | None:
    """The session with its participants (``None`` when unknown), the
    session row ``FOR SHARE`` (re-read, never from the identity map). One
    statement."""
    rows = (
        await session.execute(
            select(ClassSession, ClassSessionParticipant.student_id)
            .outerjoin(
                ClassSessionParticipant,
                ClassSessionParticipant.class_session_id == ClassSession.id,
            )
            .where(ClassSession.id == class_session_id)
            .order_by(ClassSessionParticipant.student_id)
            .with_for_update(read=True, of=ClassSession)
            .execution_options(populate_existing=True)
        )
    ).all()
    if not rows:
        return None
    row: ClassSession = rows[0][0]
    return BillableSession(
        id=row.id,
        kind=row.kind,
        state=row.state,
        subject_id=row.subject_id,
        starts_at=row.starts_at.astimezone(UTC),
        ends_at=row.ends_at.astimezone(UTC),
        scheduled_minutes=row.scheduled_minutes,
        participant_ids=tuple(
            student_id for _, student_id in rows if student_id is not None
        ),
    )
