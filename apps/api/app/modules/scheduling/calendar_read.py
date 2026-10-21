"""The calendar's range read and its tutor filter options (#57 A, B; spec
§3; ADR-0005 D6, D9).

**Range read** (:func:`list_calendar_sessions`, ``GET /api/v1/class-sessions``):

- ``to`` after ``from`` (``range_invalid``) and at most
  :data:`MAX_RANGE` later (``range_too_long``); a naive instant is
  ``naive_datetime`` on its field. All are ``422 validation_error`` field
  errors under ``query``.
- A session matches when it overlaps the half-open range: ``starts_at < to
  AND ends_at > from``. ``starts_at > from - 480 minutes`` (the ADR-0005
  maximum duration) is added so ``ix_class_sessions_starts_at`` bounds the
  scan. Filters combine with AND; an unknown filter id matches nothing.
- Every state except ``cancelled`` unless ``include_cancelled``.
- At most :data:`ITEM_LIMIT` items, by ``starts_at`` then ``id``;
  ``truncated`` tells that more matched.
- Markers (:mod:`app.modules.scheduling.markers`) are computed here, with one
  ``closures_between`` over the items' local dates and one
  ``staff.service.qualified_pairs``.
- At most 10 statements whatever the item count: sessions, participants,
  students, subjects, tutors, classrooms, the zone, the closures and the
  qualifications.

Every holder of ``schedule.view`` sees every session (no staff record
policy, D9). Read-only: no lock, no audit event.

**Tutor options** (:func:`list_tutor_options`): the users holding the
``tutor`` role, matching ``q`` like the #31 user search and in its order,
inactive ones included (past sessions keep their tutor). No contact data.
Three statements (``identity.service.list_role_members``).
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.errors import FieldError
from app.core.pagination import OffsetParams
from app.modules.administration import service as administration
from app.modules.catalog import service as catalog
from app.modules.families import service as families
from app.modules.identity import service as identity
from app.modules.scheduling.errors import SessionValidationError
from app.modules.scheduling.markers import (
    CLOSURE_MARKED_STATES,
    gets_closure_marker,
    markers_of,
    needs_qualification_check,
)
from app.modules.scheduling.models import ClassSession, ClassSessionParticipant
from app.modules.scheduling.schemas import (
    CalendarClassroom,
    CalendarParticipant,
    CalendarSubject,
    ClassSessionCalendar,
    ClassSessionCalendarItem,
    ClassSessionRangeQuery,
    SessionTutor,
    TutorOption,
    TutorOptions,
)
from app.modules.scheduling.states import SessionKind, SessionState
from app.modules.staff import service as staff

MAX_RANGE: Final = timedelta(days=45)
"""The longest range one read covers (a month view shows at most 42 days)."""
MAX_SESSION: Final = timedelta(minutes=480)
"""The ADR-0005 maximum session duration: bounds the ``starts_at`` scan."""
ITEM_LIMIT: Final = 1000
"""The most items one read returns."""
TUTOR_ROLE: Final = "tutor"

_MESSAGES: Final = {
    "naive_datetime": "Give the time with its UTC offset.",
    "range_invalid": "`to` must be after `from`.",
    "range_too_long": "The range is at most 45 days.",
}


def _query_error(field: str, code: str) -> FieldError:
    return {"path": ["query", field], "code": code, "message": _MESSAGES[code]}


def _is_naive(value: datetime) -> bool:
    return value.tzinfo is None or value.utcoffset() is None


def validate_range(start: datetime, end: datetime) -> None:
    """``422`` unless ``[start, end)`` is a valid range (#57 A1, A2)."""
    naive = [
        _query_error(field, "naive_datetime")
        for field, value in (("from", start), ("to", end))
        if _is_naive(value)
    ]
    if naive:
        raise SessionValidationError(naive)
    if end <= start:
        raise SessionValidationError([_query_error("to", "range_invalid")])
    if end - start > MAX_RANGE:
        raise SessionValidationError([_query_error("to", "range_too_long")])


def _sessions_query(query: ClassSessionRangeQuery) -> Select[ClassSession]:
    start, end = query.from_, query.to
    statement = select(ClassSession).where(
        ClassSession.starts_at < end,
        ClassSession.ends_at > start,
        ClassSession.starts_at > start - MAX_SESSION,
    )
    if not query.include_cancelled:
        statement = statement.where(ClassSession.state != SessionState.CANCELLED.value)
    if query.tutor_user_id is not None:
        statement = statement.where(ClassSession.tutor_user_id == query.tutor_user_id)
    if query.classroom_id is not None:
        statement = statement.where(ClassSession.classroom_id == query.classroom_id)
    if query.kind is not None:
        statement = statement.where(ClassSession.kind == query.kind)
    if query.student_id is not None:
        statement = statement.where(
            select(ClassSessionParticipant.id)
            .where(ClassSessionParticipant.class_session_id == ClassSession.id)
            .where(ClassSessionParticipant.student_id == query.student_id)
            .exists()
        )
    return statement.order_by(ClassSession.starts_at, ClassSession.id).limit(
        ITEM_LIMIT + 1
    )


async def _closed_dates(
    session: AsyncSession, local_dates: list[date]
) -> frozenset[date]:
    """The closed local dates among ``local_dates`` (one statement, none for
    an empty list)."""
    if not local_dates:
        return frozenset()
    first, last = min(local_dates), max(local_dates)
    closed: set[date] = set()
    for closure in await catalog.closures_between(session, first, last):
        day = max(closure.starts_on, first)
        while day <= min(closure.ends_on, last):
            closed.add(day)
            day += timedelta(days=1)
    return frozenset(closed)


async def list_calendar_sessions(
    session: AsyncSession, query: ClassSessionRangeQuery
) -> ClassSessionCalendar:
    """``GET /api/v1/class-sessions``: see the module docstring."""
    validate_range(query.from_, query.to)
    rows = list(await session.scalars(_sessions_query(query)))
    truncated = len(rows) > ITEM_LIMIT
    rows = rows[:ITEM_LIMIT]
    if not rows:
        return ClassSessionCalendar(items=[], truncated=False)

    participant_rows = await session.execute(
        select(
            ClassSessionParticipant.class_session_id,
            ClassSessionParticipant.student_id,
        ).where(ClassSessionParticipant.class_session_id.in_([row.id for row in rows]))
    )
    students_of: dict[uuid.UUID, list[uuid.UUID]] = {}
    for class_session_id, student_id in participant_rows:
        students_of.setdefault(class_session_id, []).append(student_id)

    students = await families.student_references(
        session, {sid for ids in students_of.values() for sid in ids}
    )
    subjects = await catalog.subject_references(
        session, {row.subject_id for row in rows}
    )
    tutor_ids = {row.tutor_user_id for row in rows if row.tutor_user_id is not None}
    tutors = await identity.get_account_references(session, tutor_ids)
    rooms = await catalog.classroom_references(
        session, {row.classroom_id for row in rows if row.classroom_id is not None}
    )

    zone = ZoneInfo(await administration.get_center_time_zone(session))
    local_dates = {row.id: row.starts_at.astimezone(zone).date() for row in rows}
    closed = await _closed_dates(
        session,
        [local_dates[row.id] for row in rows if row.state in CLOSURE_MARKED_STATES],
    )
    now = clock.now()
    checked = [
        row
        for row in rows
        if needs_qualification_check(
            row.state, row.tutor_user_id is not None, row.starts_at, now
        )
    ]
    qualified = await staff.qualified_pairs(
        session,
        [(row.tutor_user_id, row.subject_id) for row in checked if row.tutor_user_id],
    )
    unqualified = {
        row.id
        for row in checked
        if (row.tutor_user_id, row.subject_id) not in qualified
    }

    items = []
    for row in rows:
        subject = subjects[row.subject_id]
        tutor = tutors.get(row.tutor_user_id) if row.tutor_user_id else None
        room = rooms.get(row.classroom_id) if row.classroom_id else None
        participants = sorted(
            (
                CalendarParticipant(
                    student_id=student.id, display_name=student.display_name
                )
                for student in (
                    students[sid]
                    for sid in students_of.get(row.id, [])
                    if sid in students
                )
            ),
            key=lambda p: (p.display_name.casefold(), str(p.student_id)),
        )
        items.append(
            ClassSessionCalendarItem(
                id=row.id,
                kind=SessionKind(row.kind).value,
                state=SessionState(row.state).value,
                starts_at=row.starts_at.astimezone(UTC),
                ends_at=row.ends_at.astimezone(UTC),
                subject=CalendarSubject(
                    id=subject.id,
                    name=subject.name,
                    education_level_name=subject.education_level_name,
                ),
                tutor=(
                    None
                    if tutor is None
                    else SessionTutor(id=tutor.id, display_name=tutor.display_name)
                ),
                classroom=(
                    None
                    if room is None
                    else CalendarClassroom(id=room.id, name=room.name)
                ),
                participants=participants,
                markers=markers_of(
                    closed=gets_closure_marker(row.state, local_dates[row.id], closed),
                    not_qualified=row.id in unqualified,
                ),
            )
        )
    return ClassSessionCalendar(items=items, truncated=truncated)


async def list_tutor_options(
    session: AsyncSession, *, q: str | None, limit: int
) -> TutorOptions:
    """``GET /api/v1/schedule/tutor-options``: see the module docstring."""
    page = await identity.list_role_members(
        session, TUTOR_ROLE, q=q, state=None, page=OffsetParams(limit=limit, offset=0)
    )
    return TutorOptions(
        items=[
            TutorOption(
                id=member.id, display_name=member.display_name, state=member.state
            )
            for member in page.items
        ]
    )
