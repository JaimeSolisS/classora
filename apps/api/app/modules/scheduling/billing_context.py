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
  session row ``FOR SHARE`` (re-read; the participants are read after the
  lock). Every scheduling writer that changes
  a session's state, time, duration or participants takes that row ``FOR
  UPDATE``, so a charge waits for it, or it waits for the charge's
  transaction; either way the charge is posted from committed, current
  data. Since #99 it also carries the ``version``, ``cohort_id``,
  ``first_confirmed_at`` and ``cancelled_at``.

Schedule reconciliation (#99 C; ``app.workflows.schedule_reconciliation``):

- :func:`reconciliation_session_columns`: the participants of the sessions
  of some kinds in a state the job looks at (``cancelled``,
  ``skipped_closure``, ``confirmed``, ``completed``), as an **unexecuted**
  ``SELECT``.
- :func:`cancellation_classification`: how a ``cancelled`` session's
  cancellation is classified for billing: an **approved** ``cancellation``
  change request's effective outcome (#62 E3; ``classified_at`` = the
  request's ``created_at``), otherwise a center cancellation (``excused``,
  makeup allowed, ``classified_at`` = ``cancelled_at``). One statement, no
  lock (the caller holds the session ``FOR SHARE``).
"""

import uuid
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from sqlalchemy import Select, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.scheduling.models import (
    CHANGE_REQUEST_APPROVED,
    CHANGE_REQUEST_CANCELLATION,
    ClassSession,
    ClassSessionParticipant,
    SessionChangeRequest,
)
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
    group_id: uuid.UUID | None = None
    """The group of a ``group`` session (#92 E1), else ``None``."""
    version: int = 0
    """The session row's ``version`` (#99: a repricing's natural key)."""
    cohort_id: uuid.UUID | None = None
    first_confirmed_at: datetime | None = None
    cancelled_at: datetime | None = None


def billable_participant_columns(
    *,
    kinds: Collection[str],
    starts_from: datetime,
    student_id: uuid.UUID | None = None,
) -> Select[
    uuid.UUID, uuid.UUID, uuid.UUID, datetime, datetime, str, int, str, uuid.UUID | None
]:
    """``SELECT class_session_id, student_id, subject_id, starts_at,
    ends_at, state, scheduled_minutes, kind, group_id`` of the participants
    of the sessions
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
            ClassSession.kind,
            ClassSession.group_id,
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
    session row ``FOR SHARE`` (re-read, never from the identity map). Two
    statements: the row lock first, then the participants, read **after**
    the lock (#92: a statement that waited on a row lock sees the locked row
    as committed but a joined table as of its own start, so a group
    withdrawal that removed the participant while the step waited would be
    missed)."""
    row: ClassSession | None = await session.scalar(
        select(ClassSession)
        .where(ClassSession.id == class_session_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if row is None:
        return None
    participant_ids = await session.scalars(
        select(ClassSessionParticipant.student_id)
        .where(ClassSessionParticipant.class_session_id == class_session_id)
        .order_by(ClassSessionParticipant.student_id)
    )
    return BillableSession(
        id=row.id,
        kind=row.kind,
        state=row.state,
        subject_id=row.subject_id,
        starts_at=row.starts_at.astimezone(UTC),
        ends_at=row.ends_at.astimezone(UTC),
        scheduled_minutes=row.scheduled_minutes,
        participant_ids=tuple(participant_ids),
        group_id=row.group_id,
        version=row.version,
        cohort_id=row.cohort_id,
        first_confirmed_at=row.first_confirmed_at,
        cancelled_at=row.cancelled_at,
    )


async def session_starts(
    session: AsyncSession, class_session_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, datetime]:
    """The ``starts_at`` (UTC) of these sessions (#94 D5: a package's
    consumptions show their session's start). One statement (none for no
    ids), no lock; unknown ids are left out."""
    if not class_session_ids:
        return {}
    rows = await session.execute(
        select(ClassSession.id, ClassSession.starts_at).where(
            ClassSession.id.in_(set(class_session_ids))
        )
    )
    return {row.id: row.starts_at.astimezone(UTC) for row in rows}


# --- Schedule reconciliation (#99 C) -------------------------------------------------

RECONCILED_STATES: Final = frozenset(
    {
        SessionState.CANCELLED,
        SessionState.SKIPPED_CLOSURE,
        SessionState.CONFIRMED,
        SessionState.COMPLETED,
    }
)
"""The states whose participants the reconciliation job looks at (#99
D2)."""


def reconciliation_session_columns(
    *, kinds: Collection[str]
) -> Select[uuid.UUID, uuid.UUID, str, str, datetime, int, int, datetime | None]:
    """C3: ``SELECT class_session_id, student_id, kind, state, starts_at,
    scheduled_minutes, version, first_confirmed_at`` of the participants of
    the sessions of ``kinds`` in a :data:`RECONCILED_STATES` state, **not
    executed**."""
    return (
        select(
            ClassSession.id.label("class_session_id"),
            ClassSessionParticipant.student_id,
            ClassSession.kind,
            ClassSession.state,
            ClassSession.starts_at,
            ClassSession.scheduled_minutes,
            ClassSession.version,
            ClassSession.first_confirmed_at,
        )
        .join(
            ClassSessionParticipant,
            ClassSessionParticipant.class_session_id == ClassSession.id,
        )
        .where(
            ClassSession.kind.in_(sorted(kinds)),
            ClassSession.state.in_(sorted(state.value for state in RECONCILED_STATES)),
        )
    )


@dataclass(frozen=True)
class CancellationFacts:
    """How a cancelled session's cancellation is classified for billing
    (C2)."""

    initiated_by: str
    """``center``, or the approved request's ``family`` / ``tutor``."""
    change_request_id: uuid.UUID | None
    classification: str
    makeup_allowed: bool
    classified_at: datetime
    """The request's ``created_at``, else the session's ``cancelled_at``."""


async def cancellation_classification(
    session: AsyncSession, class_session_id: uuid.UUID
) -> CancellationFacts | None:
    """C2 (module docstring): ``None`` unless the session is ``cancelled``
    (or unknown). One statement."""
    request = (
        select(
            SessionChangeRequest.id,
            SessionChangeRequest.initiated_by,
            SessionChangeRequest.final_classification,
            SessionChangeRequest.final_makeup_allowed,
            SessionChangeRequest.created_at,
        )
        .where(
            SessionChangeRequest.class_session_id == ClassSession.id,
            SessionChangeRequest.kind == CHANGE_REQUEST_CANCELLATION,
            SessionChangeRequest.status == CHANGE_REQUEST_APPROVED,
        )
        .order_by(SessionChangeRequest.resolved_at.desc(), SessionChangeRequest.id)
        .limit(1)
        .correlate(ClassSession)
        .lateral("request")
    )
    row = (
        await session.execute(
            select(
                ClassSession.state,
                ClassSession.cancelled_at,
                request.c.id.label("request_id"),
                request.c.initiated_by,
                request.c.final_classification,
                request.c.final_makeup_allowed,
                request.c.created_at.label("requested_at"),
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
    if row.request_id is not None and row.final_classification is not None:
        return CancellationFacts(
            initiated_by=row.initiated_by,
            change_request_id=row.request_id,
            classification=row.final_classification,
            makeup_allowed=bool(row.final_makeup_allowed),
            classified_at=row.requested_at.astimezone(UTC),
        )
    return CancellationFacts(
        initiated_by="center",
        change_request_id=None,
        classification="excused",
        makeup_allowed=True,
        classified_at=row.cancelled_at.astimezone(UTC),
    )
