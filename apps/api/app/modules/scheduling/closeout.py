"""The scheduling seam of session closeout (#77 B1, B2, E1; ADR-0005 D2;
architecture ISSUE-F2).

``app.workflows.session_closeout`` composes these with ``academics`` (the
closeout row) and ``administration`` (the inbox item); scheduling never
imports ``academics`` (E2).

- :func:`closeout_session_context`: the read-only bundle of ``GET
  …/closeout`` (the session, its subject, current tutor, participants and
  their attendance statuses) in three statements.
- :func:`lock_closeout_session`: the session row ``FOR UPDATE`` (the only
  lock of the closeout, as #76's attendance), as a frozen snapshot for the
  workflow's policy and state checks.
- :func:`complete_class_session`: the row ``FOR UPDATE`` →
  ``ensure_version`` → ``confirmed`` only → every participant marked (#76
  ``attendance``) → ``confirmed → completed`` through
  :func:`~app.modules.scheduling.sessions.transition_class_session` with
  ``actual_minutes`` in the same versioned update (the version goes up by
  exactly one; the row's audit update records the state and the minutes).
  It never commits and takes only the session row lock: the scheduling
  writers lock the session row last and refuse started sessions anyway, so
  there is no deadlock.
- #78: :func:`closeout_attendance` (participants and statuses, for the
  approval snapshot and the resubmission) and :func:`set_actual_minutes`
  (a closeout correction on the ``completed`` session).
- :func:`closeout_session_summaries`: the inbox summaries of closeouts, by
  closeout id, joining the caller's ``(subject_id, class_session_id)``
  select (``academics.service.closeout_session_keys``) into the session
  read: three statements per page.

``first_confirmed_at`` never changes. The lock order of the closeout paths
is closeout → session → approval request (#78 note); #77's submit creates
the closeout and the request after the session lock, and neither exists
before it.
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import RecordNotFoundError
from app.core.models import ensure_version
from app.modules.catalog import service as catalog
from app.modules.identity import service as identity
from app.modules.scheduling.attendance_rules import AttendanceStatus
from app.modules.scheduling.errors import (
    AttendanceIncompleteError,
    SessionNotCompletableError,
)
from app.modules.scheduling.models import (
    ClassSession,
    ClassSessionAttendance,
    ClassSessionParticipant,
)
from app.modules.scheduling.offer_flow import lock_sessions
from app.modules.scheduling.offer_records import rfc3339
from app.modules.scheduling.sessions import transition_class_session
from app.modules.scheduling.states import SessionState


@dataclass(frozen=True)
class CloseoutSubject:
    id: uuid.UUID
    name: str
    education_level_name: str


@dataclass(frozen=True)
class CloseoutSessionContext:
    """What ``GET …/closeout`` needs of the session (B2)."""

    id: uuid.UUID
    version: int
    kind: str
    state: str
    starts_at: datetime
    ends_at: datetime
    scheduled_minutes: int
    actual_minutes: int | None
    tutor_user_id: uuid.UUID | None
    subject: CloseoutSubject
    participant_ids: tuple[uuid.UUID, ...]
    """Sorted by id."""
    statuses: Mapping[uuid.UUID, AttendanceStatus]
    """The marked participants' statuses by student id."""

    @property
    def missing_student_ids(self) -> list[uuid.UUID]:
        """The unmarked participants, sorted (as text)."""
        return sorted(
            (i for i in self.participant_ids if i not in self.statuses), key=str
        )


@dataclass(frozen=True)
class ClosingSession:
    """The locked session row as the workflow checks it."""

    id: uuid.UUID
    version: int
    state: str
    starts_at: datetime
    tutor_user_id: uuid.UUID | None
    kind: str
    scheduled_minutes: int
    actual_minutes: int | None
    subject_id: uuid.UUID


@dataclass(frozen=True)
class CompletedSession:
    """A session :func:`complete_class_session` has just completed (B1)."""

    id: uuid.UUID
    version: int
    kind: str
    starts_at: datetime
    ends_at: datetime
    scheduled_minutes: int
    actual_minutes: int
    tutor_user_id: uuid.UUID
    participant_ids: tuple[uuid.UUID, ...]
    statuses: Mapping[uuid.UUID, AttendanceStatus]


async def _participants_and_statuses(
    session: AsyncSession, class_session_id: uuid.UUID
) -> tuple[tuple[uuid.UUID, ...], dict[uuid.UUID, AttendanceStatus]]:
    """The participants with their marks (one statement)."""
    rows = list(
        await session.execute(
            select(ClassSessionParticipant.student_id, ClassSessionAttendance.status)
            .outerjoin(
                ClassSessionAttendance,
                (
                    ClassSessionAttendance.class_session_id
                    == ClassSessionParticipant.class_session_id
                )
                & (
                    ClassSessionAttendance.student_id
                    == ClassSessionParticipant.student_id
                ),
            )
            .where(ClassSessionParticipant.class_session_id == class_session_id)
            .order_by(ClassSessionParticipant.student_id)
        )
    )
    participants = tuple(student_id for student_id, _ in rows)
    statuses = {
        student_id: cast(AttendanceStatus, status)
        for student_id, status in rows
        if status is not None
    }
    return participants, statuses


async def closeout_session_context(
    session: AsyncSession, class_session_id: uuid.UUID
) -> CloseoutSessionContext | None:
    """B2: the session (``None`` when unknown), its participants with their
    marks and its subject, in three statements; no lock."""
    row = cast(
        ClassSession | None,
        await session.scalar(
            select(ClassSession).where(ClassSession.id == class_session_id)
        ),
    )
    if row is None:
        return None
    participants, statuses = await _participants_and_statuses(session, row.id)
    subject = (await catalog.subject_references(session, [row.subject_id]))[
        row.subject_id
    ]
    return CloseoutSessionContext(
        id=row.id,
        version=row.version,
        kind=row.kind,
        state=row.state,
        starts_at=row.starts_at.astimezone(UTC),
        ends_at=row.ends_at.astimezone(UTC),
        scheduled_minutes=row.scheduled_minutes,
        actual_minutes=row.actual_minutes,
        tutor_user_id=row.tutor_user_id,
        subject=CloseoutSubject(
            id=subject.id,
            name=subject.name,
            education_level_name=subject.education_level_name,
        ),
        participant_ids=participants,
        statuses=statuses,
    )


async def lock_closeout_session(
    session: AsyncSession, class_session_id: uuid.UUID, *, share: bool = False
) -> ClosingSession:
    """The session row ``FOR UPDATE`` (``FOR SHARE`` with ``share``: #78's
    approve and request changes read it without writing it), re-read;
    ``RecordNotFoundError`` when it is gone."""
    if share:
        row = cast(
            ClassSession | None,
            await session.scalar(
                select(ClassSession)
                .where(ClassSession.id == class_session_id)
                .with_for_update(read=True)
                .execution_options(populate_existing=True)
            ),
        )
    else:
        locked = await lock_sessions(session, [class_session_id])
        row = locked[0] if locked else None
    if row is None:
        raise RecordNotFoundError()
    return ClosingSession(
        id=row.id,
        version=row.version,
        state=row.state,
        starts_at=row.starts_at.astimezone(UTC),
        tutor_user_id=row.tutor_user_id,
        kind=row.kind,
        scheduled_minutes=row.scheduled_minutes,
        actual_minutes=row.actual_minutes,
        subject_id=row.subject_id,
    )


async def closeout_attendance(
    session: AsyncSession, class_session_id: uuid.UUID
) -> tuple[tuple[uuid.UUID, ...], dict[uuid.UUID, AttendanceStatus]]:
    """The session's participants (sorted by id) and the marked ones'
    statuses (#78: the approval snapshot, the resubmission's completeness
    check). One statement, no lock (the caller holds the session row)."""
    return await _participants_and_statuses(session, class_session_id)


async def set_actual_minutes(
    session: AsyncSession, *, class_session_id: uuid.UUID, actual_minutes: int
) -> int:
    """#78 B1: correct a ``completed`` session's ``actual_minutes`` (a
    closeout correction or resubmission). The row ``FOR UPDATE`` (re-read);
    its version goes up by exactly one only when the value changes (the
    row's audit update records it). Returns the session's version.
    ``RecordNotFoundError`` when it is gone; a session that isn't
    ``completed`` is :class:`SessionNotCompletableError` (the closeout
    paths only reach completed sessions)."""
    locked = await lock_sessions(session, [class_session_id])
    if not locked:
        raise RecordNotFoundError()
    [row] = locked
    if row.state != SessionState.COMPLETED:
        raise SessionNotCompletableError(row.state)
    if row.actual_minutes != actual_minutes:
        row.actual_minutes = actual_minutes
        await session.flush()
    return row.version


async def complete_class_session(
    session: AsyncSession,
    *,
    class_session_id: uuid.UUID,
    expected_version: int,
    actual_minutes: int,
) -> CompletedSession:
    """B1 (the order is in the module docstring). Raises
    ``RecordNotFoundError``, ``StaleVersionError``,
    :class:`SessionNotCompletableError` (not ``confirmed``) or
    :class:`AttendanceIncompleteError` before anything changes."""
    locked = await lock_sessions(session, [class_session_id])
    if not locked:
        raise RecordNotFoundError()
    [row] = locked
    ensure_version(row, expected_version)
    if row.state != SessionState.CONFIRMED:
        raise SessionNotCompletableError(row.state)
    participants, statuses = await _participants_and_statuses(session, row.id)
    missing = [student_id for student_id in participants if student_id not in statuses]
    if missing:
        raise AttendanceIncompleteError(missing)
    row = await transition_class_session(
        session,
        row.id,
        to_state=SessionState.COMPLETED,
        expected_version=expected_version,
        actual_minutes=actual_minutes,
    )
    assert row.tutor_user_id is not None  # noqa: S101 (a completed row has one)
    return CompletedSession(
        id=row.id,
        version=row.version,
        kind=row.kind,
        starts_at=row.starts_at.astimezone(UTC),
        ends_at=row.ends_at.astimezone(UTC),
        scheduled_minutes=row.scheduled_minutes,
        actual_minutes=actual_minutes,
        tutor_user_id=row.tutor_user_id,
        participant_ids=participants,
        statuses=statuses,
    )


async def closeout_session_summaries(
    session: AsyncSession, keys: Select[*tuple[Any, ...]]
) -> dict[uuid.UUID, dict[str, Any]]:
    """The approval inbox summary of each closeout (#77 E1), by the
    ``subject_id`` column of ``keys`` (a select of ``subject_id``,
    ``class_session_id`` and any further columns, which are passed through
    into the summary as they are: #78's ``kind_of_review`` and
    ``approved_minutes``): ``{class_session_id, starts_at, subject_name,
    tutor_display_name, kind, scheduled_minutes, actual_minutes,
    participant_count, …}``. No summary text, student names or attendance.
    Three statements: the sessions with their participant counts, the
    subjects, the tutors' names."""
    keyed = keys.subquery()
    extra = [
        column
        for column in keyed.c
        if column.name not in ("subject_id", "class_session_id")
    ]
    participant_count = (
        select(func.count())
        .where(ClassSessionParticipant.class_session_id == ClassSession.id)
        .correlate(ClassSession)
        .scalar_subquery()
    )
    rows = list(
        await session.execute(
            select(
                keyed.c.subject_id,
                ClassSession,
                participant_count.label("participant_count"),
                *extra,
            ).join(ClassSession, ClassSession.id == keyed.c.class_session_id)
        )
    )
    if not rows:
        return {}
    subjects = await catalog.subject_references(
        session, {found[1].subject_id for found in rows}
    )
    tutors = await identity.get_account_references(
        session,
        {
            found[1].tutor_user_id
            for found in rows
            if found[1].tutor_user_id is not None
        },
    )
    summaries: dict[uuid.UUID, dict[str, Any]] = {}
    for found in rows:
        subject_id, row, count = found[0], found[1], found[2]
        subject = subjects.get(row.subject_id)
        tutor = None if row.tutor_user_id is None else tutors.get(row.tutor_user_id)
        summaries[subject_id] = {
            "class_session_id": str(row.id),
            "starts_at": rfc3339(row.starts_at),
            "subject_name": "" if subject is None else subject.name,
            "tutor_display_name": "" if tutor is None else tutor.display_name,
            "kind": row.kind,
            "scheduled_minutes": row.scheduled_minutes,
            "actual_minutes": row.actual_minutes,
            "participant_count": int(count),
            **{column.name: found[3 + i] for i, column in enumerate(extra)},
        }
    return summaries
