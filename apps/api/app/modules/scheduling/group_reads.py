"""Reading tutoring groups, with the student record policy (#56 G, H; ADR-0005
D2 family visibility; spec §1).

**Who sees what (H, E4):**

- a holder of ``schedule.view`` (Owner, Administrator, Academic
  Coordinator, Finance) sees every group, its roster and every session;
- a **student** (no ``schedule.view``) sees only the groups where their own
  student profile (``families.service.load_access_facts(...).
  own_student_id``) has an **active** enrollment, without the roster or the
  enrolled count (other students are minors' personal data), and of the
  group's sessions only those where their profile is a participant in
  ``confirmed`` or ``completed`` (families never see ``unassigned``,
  ``offered`` or ``conflict``; sessions before their enrollment or after
  their withdrawal are therefore left out);
- anyone else (tutors, guardians, a student account without a profile) sees
  nothing: an empty list, ``404 group_not_found`` for a detail or the
  sessions (never ``403``). #67 extends this to linked guardians and
  assigned tutors.

A hidden group and an unknown one give the same ``404``. The detail runs at
most eight statements, the sessions read at most six.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import Date, cast, func, literal, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.authz import Principal
from app.core.pagination import InvalidCursorError, decode_cursor, encode_cursor
from app.modules.catalog import service as catalog
from app.modules.catalog.schemas import SubjectReference
from app.modules.families import service as families
from app.modules.identity import service as identity
from app.modules.scheduling.calendar_read import validate_range
from app.modules.scheduling.errors import GroupNotFoundError
from app.modules.scheduling.groups_rules import AFFECTED_STATES
from app.modules.scheduling.models import (
    ClassGroup,
    ClassSeries,
    ClassSession,
    ClassSessionOverride,
    ClassSessionParticipant,
    GroupEnrollment,
)
from app.modules.scheduling.schemas import (
    EnrollmentActor,
    EnrollmentStudent,
    GroupDetail,
    GroupEnrollmentItem,
    GroupLevel,
    GroupListItem,
    GroupOccurrenceItem,
    GroupPage,
    GroupSeries,
    GroupSessionClassroom,
    GroupSessionItem,
    GroupSessions,
    GroupSessionsQuery,
    GroupStatus,
    GroupSubject,
    SeriesRecurrenceDetail,
    SessionClassroom,
    SessionTutor,
)
from app.modules.scheduling.states import FAMILY_VISIBLE_STATES, SessionState

SCHEDULE_VIEW: Final = "schedule.view"
STUDENT_ROLE: Final = "student"
NEXT_OCCURRENCES: Final = 10
UPCOMING_STATES: Final = tuple(sorted(AFFECTED_STATES - {SessionState.SKIPPED_CLOSURE}))
"""The upcoming occurrences staff see (not cancelled, skipped or done)."""
STUDENT_STATES: Final = tuple(sorted(FAMILY_VISIBLE_STATES))
"""``confirmed`` and ``completed`` (ADR-0005 D2 family visibility)."""


class _NoAssignments:
    """Group reads never use tutor assignments (#67 adds tutors)."""

    async def student_ids_for_tutor(
        self, session: AsyncSession, user_id: uuid.UUID
    ) -> frozenset[uuid.UUID]:
        return frozenset()


_NO_ASSIGNMENTS: Final = _NoAssignments()


@dataclass(frozen=True)
class Viewer:
    """Who is reading: staff (``schedule.view``), a student with their own
    profile, or nobody who may see a group."""

    staff: bool
    student_id: uuid.UUID | None

    @property
    def sees_nothing(self) -> bool:
        return not self.staff and self.student_id is None


async def viewer_of(session: AsyncSession, principal: Principal) -> Viewer:
    """The H record-policy facts of ``principal`` (no statement for staff or
    for a caller without the student role)."""
    if principal.has(SCHEDULE_VIEW):
        return Viewer(staff=True, student_id=None)
    if STUDENT_ROLE not in principal.roles:
        return Viewer(staff=False, student_id=None)
    facts = await families.load_access_facts(session, principal, _NO_ASSIGNMENTS)
    return Viewer(staff=False, student_id=facts.own_student_id)


def _enrolled(student_id: uuid.UUID) -> Any:
    return (
        select(GroupEnrollment.id)
        .where(
            GroupEnrollment.group_id == ClassGroup.id,
            GroupEnrollment.student_id == student_id,
            GroupEnrollment.withdrawn_at.is_(None),
        )
        .exists()
    )


def _local_today(now: datetime) -> Any:
    """The series' local today (its ``time_zone`` is the center zone), from
    the application clock."""
    return cast(func.timezone(ClassSeries.time_zone, literal(now)), Date)


def _producing(now: datetime) -> Any:
    return or_(
        ClassSeries.last_occurrence_on.is_(None),
        ClassSeries.last_occurrence_on >= _local_today(now),
    )


async def _visible_group(
    session: AsyncSession, viewer: Viewer, group_id: uuid.UUID
) -> ClassGroup:
    if viewer.sees_nothing:
        raise GroupNotFoundError()
    query = select(ClassGroup).where(ClassGroup.id == group_id)
    if not viewer.staff:
        assert viewer.student_id is not None  # noqa: S101 - sees_nothing above
        query = query.where(_enrolled(viewer.student_id))
    group = await session.scalar(query)
    if group is None:
        raise GroupNotFoundError()
    return group


def _subject(reference: SubjectReference) -> GroupSubject:
    return GroupSubject(
        id=reference.id,
        name=reference.name,
        level=GroupLevel(
            id=reference.education_level_id, name=reference.education_level_name
        ),
    )


def _actor(accounts: dict[uuid.UUID, Any], user_id: uuid.UUID) -> EnrollmentActor:
    account = accounts.get(user_id)
    return EnrollmentActor(
        id=user_id, display_name="" if account is None else account.display_name
    )


def _enrollment_item(
    enrollment: GroupEnrollment,
    overridden: bool,
    students: dict[uuid.UUID, Any],
    accounts: dict[uuid.UUID, Any],
) -> GroupEnrollmentItem:
    student = students.get(enrollment.student_id)
    return GroupEnrollmentItem(
        id=enrollment.id,
        student=EnrollmentStudent(
            id=enrollment.student_id,
            display_name="" if student is None else student.display_name,
            is_active=False if student is None else student.is_active,
        ),
        enrolled_at=enrollment.enrolled_at.astimezone(UTC),
        enrolled_by=_actor(accounts, enrollment.enrolled_by_user_id),
        withdrawn_at=(
            None
            if enrollment.withdrawn_at is None
            else enrollment.withdrawn_at.astimezone(UTC)
        ),
        withdrawn_by=(
            None
            if enrollment.withdrawn_by_user_id is None
            else _actor(accounts, enrollment.withdrawn_by_user_id)
        ),
        withdrawal_reason=enrollment.withdrawal_reason,
        overridden=overridden,
        version=enrollment.version,
    )


def _overridden() -> Any:
    return (
        select(ClassSessionOverride.id)
        .where(ClassSessionOverride.group_enrollment_id == GroupEnrollment.id)
        .exists()
        .label("overridden")
    )


async def get_group_enrollment(
    session: AsyncSession, group_id: uuid.UUID, enrollment_id: uuid.UUID
) -> GroupEnrollmentItem:
    """One enrollment of a group (the enroll and withdraw responses; staff
    only). Three statements."""
    row = (
        await session.execute(
            select(GroupEnrollment, _overridden())
            .where(
                GroupEnrollment.id == enrollment_id,
                GroupEnrollment.group_id == group_id,
            )
            .execution_options(populate_existing=True)
        )
    ).first()
    if row is None:
        raise GroupNotFoundError()
    enrollment, overridden = row
    students = await families.student_references(session, [enrollment.student_id])
    actors = {enrollment.enrolled_by_user_id}
    if enrollment.withdrawn_by_user_id is not None:
        actors.add(enrollment.withdrawn_by_user_id)
    accounts = await identity.get_account_references(session, actors)
    return _enrollment_item(enrollment, bool(overridden), students, accounts)


async def get_group(
    session: AsyncSession, principal: Principal, group_id: uuid.UUID
) -> GroupDetail:
    """``GET /api/v1/groups/{id}`` (G1, H2); at most eight statements."""
    viewer = await viewer_of(session, principal)
    group = await _visible_group(session, viewer, group_id)
    return await _detail(session, viewer, group)


async def _detail(
    session: AsyncSession, viewer: Viewer, group: ClassGroup
) -> GroupDetail:
    now = clock.now()
    subject = (await catalog.subject_references(session, [group.subject_id]))[
        group.subject_id
    ]
    series = await session.scalar(
        select(ClassSeries)
        .where(ClassSeries.group_id == group.id, _producing(now))
        .order_by(ClassSeries.starts_on.desc(), ClassSeries.id.desc())
        .limit(1)
    )
    room = None
    if series is not None and series.classroom_id is not None:
        room = (await catalog.classroom_capacities(session, [series.classroom_id])).get(
            series.classroom_id
        )
    upcoming = (
        select(ClassSession)
        .where(ClassSession.group_id == group.id, ClassSession.starts_at > now)
        .order_by(ClassSession.starts_at, ClassSession.id)
        .limit(NEXT_OCCURRENCES)
    )
    if viewer.staff:
        upcoming = upcoming.where(ClassSession.state.in_(UPCOMING_STATES))
    else:
        assert viewer.student_id is not None  # noqa: S101 - checked visible
        upcoming = upcoming.join(
            ClassSessionParticipant,
            ClassSessionParticipant.class_session_id == ClassSession.id,
        ).where(
            ClassSessionParticipant.student_id == viewer.student_id,
            ClassSession.state.in_(STUDENT_STATES),
        )
    next_rows = list(await session.scalars(upcoming))

    enrollments: list[tuple[GroupEnrollment, bool]] = []
    students: dict[uuid.UUID, Any] = {}
    if viewer.staff:
        enrollments = [
            (enrollment, bool(flag))
            for enrollment, flag in await session.execute(
                select(GroupEnrollment, _overridden()).where(
                    GroupEnrollment.group_id == group.id
                )
            )
        ]
        students = await families.student_references(
            session, {enrollment.student_id for enrollment, _ in enrollments}
        )
    user_ids: set[uuid.UUID] = set()
    if series is not None and series.tutor_user_id is not None:
        user_ids.add(series.tutor_user_id)
    for enrollment, _ in enrollments:
        user_ids.add(enrollment.enrolled_by_user_id)
        if enrollment.withdrawn_by_user_id is not None:
            user_ids.add(enrollment.withdrawn_by_user_id)
    accounts = await identity.get_account_references(session, user_ids)

    roster = None
    enrolled_count = None
    if viewer.staff:
        items = [
            _enrollment_item(enrollment, flag, students, accounts)
            for enrollment, flag in enrollments
        ]
        active = sorted(
            (item for item in items if item.withdrawn_at is None),
            key=lambda item: (item.student.display_name.casefold(), str(item.id)),
        )
        withdrawn = sorted(
            (item for item in items if item.withdrawn_at is not None),
            key=lambda item: (item.withdrawn_at, str(item.id)),
            reverse=True,
        )
        roster = active + withdrawn
        enrolled_count = len(active)

    current = None
    if series is not None:
        tutor = None
        if series.tutor_user_id is not None:
            account = accounts.get(series.tutor_user_id)
            tutor = SessionTutor(
                id=series.tutor_user_id,
                display_name="" if account is None else account.display_name,
            )
        current = GroupSeries(
            id=series.id,
            tutor=tutor,
            classroom=(
                None
                if room is None
                else SessionClassroom(
                    id=room.id, name=room.name, is_active=room.is_active
                )
            ),
            starts_on=series.starts_on,
            start_time=series.start_time.strftime("%H:%M"),
            duration_minutes=series.duration_minutes,
            time_zone=series.time_zone,
            recurrence=SeriesRecurrenceDetail(
                weekdays=list(series.weekdays),
                interval=series.interval,
                until=series.until_on,
                count=series.count,
            ),
            last_occurrence_on=series.last_occurrence_on,
            version=series.version,
        )
    return GroupDetail(
        id=group.id,
        name=group.name,
        subject=_subject(subject),
        max_students=group.max_students,
        enrolled_count=enrolled_count,
        effective_capacity=(
            group.max_students
            if room is None
            else min(group.max_students, room.capacity)
        ),
        current_series=current,
        next_occurrences=[
            GroupOccurrenceItem(
                id=row.id,
                state=SessionState(row.state).value,
                starts_at=row.starts_at.astimezone(UTC),
                ends_at=row.ends_at.astimezone(UTC),
            )
            for row in next_rows
        ],
        version=group.version,
        created_at=group.created_at,
        updated_at=group.updated_at,
        roster=roster,
    )


def _cursor_position(cursor: str) -> tuple[str, uuid.UUID]:
    name, group_id = decode_cursor(cursor, types=(str, str))
    try:
        return str(name), uuid.UUID(str(group_id))
    except ValueError:
        raise InvalidCursorError() from None


async def list_groups(
    session: AsyncSession,
    principal: Principal,
    *,
    q: str | None,
    subject_id: uuid.UUID | None,
    status: GroupStatus,
    limit: int,
    cursor: str | None,
) -> GroupPage:
    """``GET /api/v1/groups`` (H1): the visible groups by name (case-folded)
    then id, cursor-paginated. ``status``: ``active`` (a producing series
    exists, the default), ``ended`` or ``all``."""
    viewer = await viewer_of(session, principal)
    if viewer.sees_nothing:
        return GroupPage(items=[], next_cursor=None)
    now = clock.now()
    sort_name = func.lower(ClassGroup.name)
    query = select(ClassGroup)
    if not viewer.staff:
        assert viewer.student_id is not None  # noqa: S101 - sees_nothing above
        query = query.where(_enrolled(viewer.student_id))
    if q:
        query = query.where(sort_name.contains(q.casefold(), autoescape=True))
    if subject_id is not None:
        query = query.where(ClassGroup.subject_id == subject_id)
    if status != "all":
        producing = (
            select(ClassSeries.id)
            .where(ClassSeries.group_id == ClassGroup.id, _producing(now))
            .exists()
        )
        query = query.where(producing if status == "active" else ~producing)
    if cursor is not None:
        name, after_id = _cursor_position(cursor)
        query = query.where(tuple_(sort_name, ClassGroup.id) > tuple_(name, after_id))
    groups = list(
        await session.scalars(query.order_by(sort_name, ClassGroup.id).limit(limit + 1))
    )
    page, more = groups[:limit], len(groups) > limit
    items = await _list_items(session, viewer, page, now)
    next_cursor = (
        encode_cursor([page[-1].name.lower(), str(page[-1].id)])
        if more and page
        else None
    )
    return GroupPage(items=items, next_cursor=next_cursor)


async def _list_items(
    session: AsyncSession, viewer: Viewer, groups: Sequence[ClassGroup], now: datetime
) -> list[GroupListItem]:
    if not groups:
        return []
    ids = [group.id for group in groups]
    subjects = await catalog.subject_references(
        session, {group.subject_id for group in groups}
    )
    counts: dict[uuid.UUID, int] = {}
    if viewer.staff:
        counts = {
            group_id: int(count)
            for group_id, count in await session.execute(
                select(GroupEnrollment.group_id, func.count())
                .where(
                    GroupEnrollment.group_id.in_(ids),
                    GroupEnrollment.withdrawn_at.is_(None),
                )
                .group_by(GroupEnrollment.group_id)
            )
        }
    tutors: dict[uuid.UUID, uuid.UUID | None] = {}
    for group_id, tutor_id in await session.execute(
        select(ClassSeries.group_id, ClassSeries.tutor_user_id)
        .where(ClassSeries.group_id.in_(ids), _producing(now))
        .order_by(ClassSeries.group_id, ClassSeries.starts_on, ClassSeries.id)
    ):
        if group_id is not None:
            tutors[group_id] = tutor_id  # the latest start wins
    accounts = await identity.get_account_references(
        session, {tutor for tutor in tutors.values() if tutor is not None}
    )
    upcoming = (
        select(ClassSession.group_id, func.min(ClassSession.starts_at))
        .where(ClassSession.group_id.in_(ids), ClassSession.starts_at > now)
        .group_by(ClassSession.group_id)
    )
    if viewer.staff:
        upcoming = upcoming.where(ClassSession.state.in_(UPCOMING_STATES))
    else:
        upcoming = upcoming.join(
            ClassSessionParticipant,
            ClassSessionParticipant.class_session_id == ClassSession.id,
        ).where(
            ClassSessionParticipant.student_id == viewer.student_id,
            ClassSession.state.in_(STUDENT_STATES),
        )
    next_starts = {
        group_id: starts_at for group_id, starts_at in await session.execute(upcoming)
    }
    items: list[GroupListItem] = []
    for group in groups:
        tutor_id = tutors.get(group.id)
        account = None if tutor_id is None else accounts.get(tutor_id)
        starts = next_starts.get(group.id)
        items.append(
            GroupListItem(
                id=group.id,
                name=group.name,
                subject=_subject(subjects[group.subject_id]),
                max_students=group.max_students,
                enrolled_count=counts.get(group.id, 0) if viewer.staff else None,
                tutor=(
                    None
                    if tutor_id is None
                    else SessionTutor(
                        id=tutor_id,
                        display_name="" if account is None else account.display_name,
                    )
                ),
                next_starts_at=None if starts is None else starts.astimezone(UTC),
            )
        )
    return items


async def list_group_sessions(
    session: AsyncSession,
    principal: Principal,
    group_id: uuid.UUID,
    query: GroupSessionsQuery,
) -> GroupSessions:
    """``GET /api/v1/groups/{id}/sessions`` (H3): the #57 range rules, then
    the group's sessions overlapping ``[from, to)`` by start (a student: only
    their own ``confirmed`` / ``completed`` ones). At most six statements."""
    validate_range(query.from_, query.to)
    viewer = await viewer_of(session, principal)
    group = await _visible_group(session, viewer, group_id)
    statement = select(ClassSession).where(
        ClassSession.group_id == group.id,
        ClassSession.starts_at < query.to,
        ClassSession.ends_at > query.from_,
    )
    if not viewer.staff:
        statement = statement.join(
            ClassSessionParticipant,
            ClassSessionParticipant.class_session_id == ClassSession.id,
        ).where(
            ClassSessionParticipant.student_id == viewer.student_id,
            ClassSession.state.in_(STUDENT_STATES),
        )
    rows = list(
        await session.scalars(
            statement.order_by(ClassSession.starts_at, ClassSession.id)
        )
    )
    accounts = await identity.get_account_references(
        session, {row.tutor_user_id for row in rows if row.tutor_user_id is not None}
    )
    rooms = await catalog.classroom_references(
        session, {row.classroom_id for row in rows if row.classroom_id is not None}
    )
    items: list[GroupSessionItem] = []
    for row in rows:
        account = None if row.tutor_user_id is None else accounts.get(row.tutor_user_id)
        room = None if row.classroom_id is None else rooms.get(row.classroom_id)
        items.append(
            GroupSessionItem(
                id=row.id,
                state=SessionState(row.state).value,
                starts_at=row.starts_at.astimezone(UTC),
                ends_at=row.ends_at.astimezone(UTC),
                tutor=(
                    None
                    if row.tutor_user_id is None
                    else SessionTutor(
                        id=row.tutor_user_id,
                        display_name="" if account is None else account.display_name,
                    )
                ),
                classroom=(
                    None
                    if room is None
                    else GroupSessionClassroom(id=room.id, name=room.name)
                ),
            )
        )
    return GroupSessions(items=items)
