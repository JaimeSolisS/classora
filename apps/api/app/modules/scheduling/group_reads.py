"""Reading tutoring groups, with the record policy (#56 G, H; #246 A;
ADR-0005 D2 family visibility, D9 record policies; spec §1, §2).

**Who sees what** (:class:`~app.modules.scheduling.permissions.RecordViewer`,
loaded by :func:`~app.modules.scheduling.record_viewer.record_viewer`):

- a holder of ``schedule.view`` (Owner, Administrator, Academic
  Coordinator, Finance) sees every group, its roster and every session;
- a **student or a guardian** sees the groups where a visible student (their
  own profile; a linked child who is a minor, or an adult who gave that
  guardian the academic consent, #246 E1) has an **active** enrollment,
  without the roster or the enrolled count, the tutor's name only (once
  they accepted), and of the group's sessions only the family-visible ones
  (``states.is_family_visible`` with ``first_confirmed_at``, #67 A3) where a
  visible student is a participant, each with ``students`` (only the
  caller's own);
- a **tutor** sees the groups with a producing series they currently teach
  (``pending`` or ``accepted``, #246 A2), with ``tutor_roster`` (the active
  enrollments' students, E2) instead of the roster, and of the sessions the
  tutor-visible ones whose current tutor they are, with ``participants``;
- anyone else sees nothing: an empty list, ``404 group_not_found`` for a
  detail or the sessions (never ``403``).

With several roles a caller sees the union; a group they teach is shown in
the tutor shape, any other in the family shape. A hidden group and an
unknown one give the same ``404``. The detail runs at most nine statements,
the sessions read at most seven (#246 A6: the #56 budgets plus the consent
facts).
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import ColumnElement, Select, false, func, or_, select, tuple_
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
    SERIES_ASSIGNMENT_ACCEPTED,
    ClassGroup,
    ClassSeries,
    ClassSession,
    ClassSessionOverride,
    ClassSessionParticipant,
    GroupEnrollment,
)
from app.modules.scheduling.permissions import RecordViewer
from app.modules.scheduling.record_viewer import (
    NOT_DONE,
    producing_series,
    record_viewer,
    taught_series_condition,
)
from app.modules.scheduling.role_schedules import (
    family_visible_condition,
    tutor_visible_condition,
)
from app.modules.scheduling.schemas import (
    CohortSessionStudent,
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
    RosterStudent,
    SeriesRecurrenceDetail,
    SessionClassroom,
    SessionParticipantName,
    SessionTutor,
    TutorName,
    TutorRosterItem,
)
from app.modules.scheduling.states import FAMILY_VISIBLE_STATES, SessionState

SCHEDULE_VIEW: Final = "schedule.view"
NEXT_OCCURRENCES: Final = 10
UPCOMING_STATES: Final = tuple(sorted(AFFECTED_STATES - {SessionState.SKIPPED_CLOSURE}))
"""The upcoming occurrences staff see (not cancelled, skipped or done)."""
STUDENT_STATES: Final = tuple(sorted(FAMILY_VISIBLE_STATES))
"""``confirmed`` and ``completed``: the family's next start in a list."""
TUTOR_NEXT_STATES: Final = (SessionState.CONFIRMED.value, SessionState.OFFERED.value)
"""The tutor's next start in a list: an offered or confirmed session."""


async def viewer_of(session: AsyncSession, principal: Principal) -> RecordViewer:
    """The record-policy facts of ``principal`` for groups (see
    :func:`~app.modules.scheduling.record_viewer.record_viewer`)."""
    return await record_viewer(session, principal, staff_permission=SCHEDULE_VIEW)


def _enrolled(student_ids: frozenset[uuid.UUID]) -> ColumnElement[bool]:
    return (
        select(GroupEnrollment.id)
        .where(
            GroupEnrollment.group_id == ClassGroup.id,
            GroupEnrollment.student_id.in_(student_ids),
            GroupEnrollment.withdrawn_at.is_(None),
        )
        .exists()
    )


def _taught(viewer: RecordViewer, now: datetime) -> ColumnElement[bool]:
    """A2: a producing series of the group taught by the caller."""
    if viewer.tutor_id is None:
        return false()
    return (
        select(ClassSeries.id)
        .where(
            ClassSeries.group_id == ClassGroup.id,
            taught_series_condition(viewer.tutor_id, now),
        )
        .exists()
    )


def _visible(viewer: RecordViewer, now: datetime) -> ColumnElement[bool]:
    """A3 for a caller without ``schedule.view``: a visible student's active
    enrollment, or a taught producing series."""
    parts: list[ColumnElement[bool]] = []
    if viewer.students:
        parts.append(_enrolled(viewer.students))
    if viewer.tutor_id is not None:
        parts.append(_taught(viewer, now))
    return or_(*parts) if parts else false()


def _has_visible_participant(student_ids: frozenset[uuid.UUID]) -> ColumnElement[bool]:
    return (
        select(ClassSessionParticipant.class_session_id)
        .where(
            ClassSessionParticipant.class_session_id == ClassSession.id,
            ClassSessionParticipant.student_id.in_(student_ids),
        )
        .exists()
    )


def _family_rows(viewer: RecordViewer) -> list[ColumnElement[bool]]:
    """A5: the family-visible sessions a visible student takes part in."""
    return [_has_visible_participant(viewer.students), family_visible_condition()]


def _tutor_rows(viewer: RecordViewer) -> list[ColumnElement[bool]]:
    """A5: the tutor-visible sessions whose current tutor is the caller."""
    return [ClassSession.tutor_user_id == viewer.tutor_id, tutor_visible_condition()]


@dataclass(frozen=True)
class _Visible:
    group: ClassGroup
    taught: bool
    active_student_ids: tuple[uuid.UUID, ...]


async def _visible_group(
    session: AsyncSession, viewer: RecordViewer, group_id: uuid.UUID, now: datetime
) -> _Visible:
    """The group when ``viewer`` may see it, with whether they teach it and
    (for a tutor) its active enrollments' students; one statement, none
    when they see nothing. ``404 group_not_found`` otherwise."""
    if viewer.sees_nothing:
        raise GroupNotFoundError()
    if viewer.staff:
        group = await session.scalar(
            select(ClassGroup).where(ClassGroup.id == group_id)
        )
        if group is None:
            raise GroupNotFoundError()
        return _Visible(group=group, taught=False, active_student_ids=())
    active = (
        select(func.array_agg(GroupEnrollment.student_id))
        .where(
            GroupEnrollment.group_id == ClassGroup.id,
            GroupEnrollment.withdrawn_at.is_(None),
        )
        .correlate(ClassGroup)
        .scalar_subquery()
    )
    row = (
        await session.execute(
            select(ClassGroup, _taught(viewer, now).label("taught"), active).where(
                ClassGroup.id == group_id, _visible(viewer, now)
            )
        )
    ).first()
    if row is None:
        raise GroupNotFoundError()
    group, taught, students = row
    return _Visible(
        group=group,
        taught=bool(taught),
        active_student_ids=tuple(students or ()) if taught else (),
    )


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


def group_name_columns() -> Select[uuid.UUID, str]:
    """``SELECT id, name FROM class_groups``, **not executed** (#89 E7):
    the price catalog joins it as a subquery (the #80 F2 pattern)."""
    return select(ClassGroup.id, ClassGroup.name)


async def group_subject_id(
    session: AsyncSession, group_id: uuid.UUID
) -> uuid.UUID | None:
    """The subject of a group, or ``None`` for an unknown group (#89 B3:
    a group-scoped price must name the group's subject). No record policy:
    the caller holds a staff permission. One statement."""
    found: uuid.UUID | None = await session.scalar(
        select(ClassGroup.subject_id).where(ClassGroup.id == group_id)
    )
    return found


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
    """``GET /api/v1/groups/{id}`` (G1, H2; #246 A4); at most eight
    statements for staff, nine with the family facts."""
    viewer = await viewer_of(session, principal)
    now = clock.now()
    visible = await _visible_group(session, viewer, group_id, now)
    return await _detail(session, viewer, visible, now)


async def _detail(
    session: AsyncSession, viewer: RecordViewer, visible: _Visible, now: datetime
) -> GroupDetail:
    group = visible.group
    subject = (await catalog.subject_references(session, [group.subject_id]))[
        group.subject_id
    ]
    series = await session.scalar(
        select(ClassSeries)
        .where(ClassSeries.group_id == group.id, producing_series(now))
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
    elif visible.taught:
        upcoming = upcoming.where(*_tutor_rows(viewer), NOT_DONE)
    else:
        upcoming = upcoming.where(*_family_rows(viewer), NOT_DONE)
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
    elif visible.active_student_ids:
        students = await families.student_references(
            session, visible.active_student_ids
        )
    shown_tutor = (
        series is not None
        and series.tutor_user_id is not None
        and (
            viewer.staff
            or visible.taught
            or series.assignment_status == SERIES_ASSIGNMENT_ACCEPTED
        )
    )
    user_ids: set[uuid.UUID] = set()
    if shown_tutor and series is not None and series.tutor_user_id is not None:
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
    tutor_roster = None
    if visible.taught:
        tutor_roster = [
            TutorRosterItem(
                student=RosterStudent(id=ref.id, display_name=ref.display_name)
            )
            for ref in sorted(
                (
                    students[sid]
                    for sid in set(visible.active_student_ids)
                    if sid in students
                ),
                key=lambda ref: (ref.display_name.casefold(), str(ref.id)),
            )
        ]

    current = None
    if series is not None:
        tutor: SessionTutor | TutorName | None = None
        if shown_tutor and series.tutor_user_id is not None:
            account = accounts.get(series.tutor_user_id)
            name = "" if account is None else account.display_name
            tutor = (
                SessionTutor(id=series.tutor_user_id, display_name=name)
                if viewer.staff or visible.taught
                else TutorName(display_name=name)
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
        tutor_roster=tutor_roster,
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
    query = select(ClassGroup, _taught(viewer, now).label("taught"))
    if not viewer.staff:
        query = query.where(_visible(viewer, now))
    if q:
        query = query.where(sort_name.contains(q.casefold(), autoescape=True))
    if subject_id is not None:
        query = query.where(ClassGroup.subject_id == subject_id)
    if status != "all":
        producing = (
            select(ClassSeries.id)
            .where(ClassSeries.group_id == ClassGroup.id, producing_series(now))
            .exists()
        )
        query = query.where(producing if status == "active" else ~producing)
    if cursor is not None:
        name, after_id = _cursor_position(cursor)
        query = query.where(tuple_(sort_name, ClassGroup.id) > tuple_(name, after_id))
    rows = (
        await session.execute(query.order_by(sort_name, ClassGroup.id).limit(limit + 1))
    ).all()
    page = [row[0] for row in rows[:limit]]
    more = len(rows) > limit
    taught = {row[0].id for row in rows[:limit] if row[1]}
    items = await _list_items(session, viewer, page, taught, now)
    next_cursor = (
        encode_cursor([page[-1].name.lower(), str(page[-1].id)])
        if more and page
        else None
    )
    return GroupPage(items=items, next_cursor=next_cursor)


async def _list_items(
    session: AsyncSession,
    viewer: RecordViewer,
    groups: Sequence[ClassGroup],
    taught: set[uuid.UUID],
    now: datetime,
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
    for group_id, tutor_id, status in await session.execute(
        select(
            ClassSeries.group_id,
            ClassSeries.tutor_user_id,
            ClassSeries.assignment_status,
        )
        .where(ClassSeries.group_id.in_(ids), producing_series(now))
        .order_by(ClassSeries.group_id, ClassSeries.starts_on, ClassSeries.id)
    ):
        if group_id is not None:
            # The latest start wins; a family caller sees the tutor only
            # once they accepted (a pending offer is assignment data).
            shown = (
                viewer.staff
                or group_id in taught
                or status == SERIES_ASSIGNMENT_ACCEPTED
            )
            tutors[group_id] = tutor_id if shown else None
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
        parts: list[ColumnElement[bool]] = []
        if viewer.students:
            parts.append(
                ClassSession.state.in_(STUDENT_STATES)
                & _has_visible_participant(viewer.students)
            )
        if taught:
            parts.append(
                ClassSession.group_id.in_(taught)
                & (ClassSession.tutor_user_id == viewer.tutor_id)
                & ClassSession.state.in_(TUTOR_NEXT_STATES)
            )
        upcoming = upcoming.where(or_(*parts) if parts else false())
    next_starts = {
        group_id: starts_at for group_id, starts_at in await session.execute(upcoming)
    }
    items: list[GroupListItem] = []
    for group in groups:
        tutor_id = tutors.get(group.id)
        account = None if tutor_id is None else accounts.get(tutor_id)
        starts = next_starts.get(group.id)
        name = "" if account is None else account.display_name
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
                    else SessionTutor(id=tutor_id, display_name=name)
                    if viewer.staff or group.id in taught
                    else TutorName(display_name=name)
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
    """``GET /api/v1/groups/{id}/sessions`` (H3; #246 A5): the #57 range
    rules, then the group's sessions overlapping ``[from, to)`` by start
    (see the module docstring for who sees which). At most six statements
    for staff, seven with the family facts."""
    validate_range(query.from_, query.to)
    viewer = await viewer_of(session, principal)
    now = clock.now()
    visible = await _visible_group(session, viewer, group_id, now)
    in_range = [
        ClassSession.group_id == visible.group.id,
        ClassSession.starts_at < query.to,
        ClassSession.ends_at > query.from_,
    ]
    takers: dict[uuid.UUID, list[uuid.UUID]] = {}
    if viewer.staff:
        rows = list(
            await session.scalars(
                select(ClassSession)
                .where(*in_range)
                .order_by(ClassSession.starts_at, ClassSession.id)
            )
        )
    else:
        scope = (
            [ClassSessionParticipant.student_id.in_(viewer.students)]
            if not visible.taught
            else []
        )
        students = (
            select(func.array_agg(ClassSessionParticipant.student_id))
            .where(ClassSessionParticipant.class_session_id == ClassSession.id, *scope)
            .correlate(ClassSession)
            .scalar_subquery()
        )
        conditions = _tutor_rows(viewer) if visible.taught else _family_rows(viewer)
        found = (
            await session.execute(
                select(ClassSession, students)
                .where(*in_range, *conditions)
                .order_by(ClassSession.starts_at, ClassSession.id)
            )
        ).all()
        rows = [row[0] for row in found]
        takers = {row[0].id: list(row[1] or ()) for row in found}
    names = await families.student_references(
        session, {sid for ids in takers.values() for sid in ids}
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
        name = "" if account is None else account.display_name
        refs = sorted(
            (names[sid] for sid in takers.get(row.id, ()) if sid in names),
            key=lambda ref: (ref.display_name.casefold(), str(ref.id)),
        )
        family = not viewer.staff and not visible.taught
        items.append(
            GroupSessionItem(
                id=row.id,
                state=SessionState(row.state).value,
                starts_at=row.starts_at.astimezone(UTC),
                ends_at=row.ends_at.astimezone(UTC),
                tutor=(
                    None
                    if row.tutor_user_id is None
                    else TutorName(display_name=name)
                    if family
                    else SessionTutor(id=row.tutor_user_id, display_name=name)
                ),
                classroom=(
                    None
                    if room is None
                    else GroupSessionClassroom(id=room.id, name=room.name)
                ),
                students=(
                    [
                        CohortSessionStudent(id=r.id, display_name=r.display_name)
                        for r in refs
                    ]
                    if family
                    else None
                ),
                participants=(
                    [
                        SessionParticipantName(
                            student_id=r.id, display_name=r.display_name
                        )
                        for r in refs
                    ]
                    if visible.taught
                    else None
                ),
            )
        )
    return GroupSessions(items=items)
