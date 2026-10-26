"""The scheduling reads of the progress dashboard (#84 M5-17 D1).

Every read is over the student's **in-scope** sessions
(:func:`~app.modules.scheduling.attendance.in_scope_participations`: a
participant, ``confirmed`` or ``completed``, started), the same set as
:func:`~app.modules.scheduling.attendance.student_attendance_summary`, so the
dashboard and the progress reports (#83) never disagree:

- :func:`student_attendance_by_month`: the counts per center-local month and
  subject (one statement);
- :func:`student_session_log`: the cursor-paged log with the student's own
  mark, the subject's and the current tutor's names (one statement);
- :func:`tutor_taught_student`: whether a tutor taught an in-scope session of
  the student (the former-tutor rule, one statement).

"Taught" is the session's current tutor **or** a session in
``taught_sessions``: an unexecuted select of class session ids the caller
builds elsewhere (the workflow passes ``academics``' closeouts of that tutor,
so ``scheduling`` never imports ``academics``). The subjects' and people's
names come the same way (``catalog.subject_name_columns``,
``identity.display_name_columns``). Every function runs in the caller's Unit
of Work and never commits.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, cast

from sqlalchemy import Select, func, or_, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import InvalidCursorError, decode_cursor, encode_cursor
from app.modules.scheduling.attendance import (
    attendance_counts,
    in_scope_participations,
    local_start_date,
)
from app.modules.scheduling.attendance_rules import AttendanceStatus
from app.modules.scheduling.models import ClassSession, ClassSessionAttendance


@dataclass(frozen=True)
class MonthAttendance:
    """One (center-local month, subject) of a student's in-scope sessions."""

    month: str
    """``YYYY-MM``."""
    subject_id: uuid.UUID
    subject_name: str
    sessions: int
    present: int
    late: int
    absent: int
    not_recorded: int


@dataclass(frozen=True)
class SessionLogRow:
    """One in-scope session of the student (C2)."""

    class_session_id: uuid.UUID
    starts_at: datetime
    subject_id: uuid.UUID
    subject_name: str
    kind: str
    tutor_user_id: uuid.UUID | None
    tutor_display_name: str | None
    attendance: AttendanceStatus | None
    """The student's own mark (``None``: not recorded)."""


@dataclass(frozen=True)
class SessionLogPage:
    rows: list[SessionLogRow]
    next_cursor: str | None


def _taught(user_id: uuid.UUID, taught_sessions: Select[*tuple[Any, ...]]) -> Any:
    return or_(
        ClassSession.tutor_user_id == user_id,
        ClassSession.id.in_(taught_sessions),
    )


async def student_attendance_by_month(
    session: AsyncSession,
    student_id: uuid.UUID,
    start: date,
    end: date,
    *,
    time_zone: str,
    now: datetime,
    subjects: Select[*tuple[Any, ...]],
) -> list[MonthAttendance]:
    """The student's in-scope sessions whose local start date (in
    ``time_zone``) is in ``[start, end]``, counted by mark per local month
    and subject (every subject: the caller filters), only the pairs with a
    session (``subjects``: an unexecuted ``(id, name)`` select). One
    statement."""
    local_date = local_start_date(time_zone)
    month = func.to_char(local_date, "YYYY-MM")
    joined = subjects.subquery()
    query = (
        in_scope_participations(
            month,
            ClassSession.subject_id,
            joined.c.name,
            *attendance_counts(),
            student_id=student_id,
            now=now,
        )
        .outerjoin(joined, joined.c.id == ClassSession.subject_id)
        .where(local_date >= start, local_date <= end)
        .group_by(month, ClassSession.subject_id, joined.c.name)
    )
    return [
        MonthAttendance(
            month=str(row[0]),
            subject_id=row[1],
            subject_name="" if row[2] is None else str(row[2]),
            sessions=row[3],
            present=row[4],
            late=row[5],
            absent=row[6],
            not_recorded=row[7],
        )
        for row in await session.execute(query)
    ]


def _position(cursor: str) -> tuple[datetime, uuid.UUID]:
    starts_at, session_id = decode_cursor(cursor, types=(str, str))
    try:
        when = datetime.fromisoformat(str(starts_at))
        identifier = uuid.UUID(str(session_id))
    except ValueError:
        raise InvalidCursorError() from None
    if when.tzinfo is None:
        raise InvalidCursorError()
    return when, identifier


async def student_session_log(
    session: AsyncSession,
    student_id: uuid.UUID,
    *,
    from_: date | None,
    to: date | None,
    subject_id: uuid.UUID | None,
    taught_by: tuple[uuid.UUID, Select[*tuple[Any, ...]]] | None,
    cursor: str | None,
    limit: int,
    time_zone: str | None,
    now: datetime,
    subjects: Select[*tuple[Any, ...]],
    people: Select[*tuple[Any, ...]],
) -> SessionLogPage:
    """C1: the student's in-scope sessions, newest first by ``(starts_at,
    id)`` after the cursor, at most ``limit``; only those whose local start
    date is in ``[from_, to]`` (each bound optional) and of ``subject_id``
    when given; with ``taught_by = (user_id, taught_sessions)`` only the
    sessions that tutor taught (``time_zone``: the center's, needed only
    with a bound). ``subjects`` and ``people``: unexecuted
    ``(id, name)`` selects for the subject's and the current tutor's names.
    ``400 invalid_cursor`` for a cursor it didn't make. One statement."""
    subject = subjects.subquery()
    tutor = people.subquery()
    query = in_scope_participations(
        ClassSession.id,
        ClassSession.starts_at,
        ClassSession.subject_id,
        subject.c.name,
        ClassSession.kind,
        ClassSession.tutor_user_id,
        tutor.c.display_name,
        ClassSessionAttendance.status,
        student_id=student_id,
        now=now,
    )
    query = query.outerjoin(subject, subject.c.id == ClassSession.subject_id)
    query = query.outerjoin(tutor, tutor.c.id == ClassSession.tutor_user_id)
    if from_ is not None or to is not None:
        assert time_zone is not None  # noqa: S101 (a bound needs the zone)
        local_date = local_start_date(time_zone)
        if from_ is not None:
            query = query.where(local_date >= from_)
        if to is not None:
            query = query.where(local_date <= to)
    if subject_id is not None:
        query = query.where(ClassSession.subject_id == subject_id)
    if taught_by is not None:
        query = query.where(_taught(*taught_by))
    if cursor is not None:
        when, identifier = _position(cursor)
        query = query.where(
            tuple_(ClassSession.starts_at, ClassSession.id) < tuple_(when, identifier)
        )
    result = await session.execute(
        query.order_by(ClassSession.starts_at.desc(), ClassSession.id.desc()).limit(
            limit + 1
        )
    )
    rows = [
        SessionLogRow(
            class_session_id=row[0],
            starts_at=row[1],
            subject_id=row[2],
            subject_name="" if row[3] is None else str(row[3]),
            kind=str(row[4]),
            tutor_user_id=row[5],
            tutor_display_name=None if row[6] is None else str(row[6]),
            attendance=cast(AttendanceStatus | None, row[7]),
        )
        for row in result
    ]
    items = rows[:limit]
    last = items[-1] if items else None
    next_cursor = (
        encode_cursor([last.starts_at.isoformat(), str(last.class_session_id)])
        if len(rows) > limit and last is not None
        else None
    )
    return SessionLogPage(rows=items, next_cursor=next_cursor)


async def tutor_taught_student(
    session: AsyncSession,
    user_id: uuid.UUID,
    student_id: uuid.UUID,
    *,
    now: datetime,
    taught_sessions: Select[*tuple[Any, ...]],
) -> bool:
    """Whether ``user_id`` taught at least one in-scope session of the
    student: its current tutor, or one of ``taught_sessions`` (#84 E4, the
    former-tutor rule). One statement."""
    found = await session.scalar(
        in_scope_participations(ClassSession.id, student_id=student_id, now=now)
        .where(_taught(user_id, taught_sessions))
        .limit(1)
    )
    return found is not None
