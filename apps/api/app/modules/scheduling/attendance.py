"""Attendance (#76; spec §2, §3 "Attendance, session completion and
changes", W3.1; architecture ISSUE-A3; ADR-0005 D2, D9).

After a class starts its **current** tutor marks each participant
``present``, ``late`` or ``absent``; holders of ``attendance.correct``
(Owner, Administrator, Academic Coordinator) correct a mark with a reason,
kept in the append-only ``class_session_attendance_corrections`` table and
the named audit event ``scheduling.attendance_corrected``. Marking or
correcting never changes the session's state, ``actual_minutes`` or
``first_confirmed_at`` (E2): closeout (#77) does that and reuses
:func:`record_attendance`. No notification is sent.

**Read** (:func:`get_session_attendance`, ``GET …/attendance``): the record
policy :func:`~app.modules.scheduling.attendance_rules.attendance_access`
(the current tutor, or a ``schedule.view`` holder; anyone else the ``404``
of an unknown id, in any state). At most 6 statements: the session, the
participants with their attendance, the students, the corrections, the
markers' and correctors' accounts, the subject.

**Mark** (:func:`record_attendance`, ``PUT …/attendance``; the route adds
``401`` and the body's ``422`` first):

1. the session (unlocked) → ``404`` unknown or hidden;
2. ``403 attendance_tutor_only`` for a visible caller who isn't the current
   tutor;
3. the session ``FOR UPDATE`` (the only lock attendance takes: no advisory
   lock, so no deadlock with the scheduling writers, which take the session
   row last); the policy is checked again on the locked row;
4. ``409 version_conflict``;
5. ``409 attendance_not_recordable`` / ``attendance_not_started`` /
   ``attendance_locked`` (``by_tutor=True``; on a ``completed`` session the
   registered #78 unlock predicate runs first, after the lock: the tutor
   marks again while the closeout is ``changes_requested``);
6. ``422 validation_error`` with ``student_not_participant`` at
   ``marks[i].student_id`` (the same for an unknown student and another
   session's: no existence oracle);
7. ``409 attendance_corrected`` when a mark would change a corrected
   student's status (a staff correction wins);
8. the writes: a new mark inserts, a changed one updates ``status``,
   ``marked_by_user_id`` and ``marked_at``, an identical one is a no-op;
   when anything changed the session's ``version`` goes up by exactly one
   (the rows' automatic audit events record the actor). A fully no-op
   ``PUT`` writes nothing.

**Correct** (:func:`correct_attendance`, ``POST …/attendance/corrections``;
the route adds ``401``, ``403 permission_denied`` and the body's ``422``):
the session ``FOR UPDATE`` (``404`` unknown), ``409 version_conflict``, the
block with ``by_tutor=False`` (a ``completed`` session **is** correctable),
``422 student_not_participant`` (``student_id``), ``422
attendance_unchanged`` (``status``), then in one transaction: the attendance
row upserted (marked by the corrector now), one correction row, the named
event on the attendance row and the session's version bumped.

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from collections.abc import Awaitable, Callable, Collection, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Final, cast

from sqlalchemy import Date, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.audit import record_audit_event
from app.core.authz import Principal, RecordNotFoundError
from app.core.errors import FieldError
from app.core.models import ensure_version
from app.modules.catalog import service as catalog
from app.modules.families import service as families
from app.modules.identity import service as identity
from app.modules.scheduling.attendance_rules import (
    LOCKED,
    NOT_RECORDABLE,
    NOT_STARTED,
    AttendanceAccess,
    AttendanceBlock,
    AttendanceStatus,
    attendance_access,
    attendance_block,
    can_correct,
    is_current_tutor,
)
from app.modules.scheduling.attendance_schemas import (
    AttendanceCorrectionCreate,
    AttendanceCorrectionItem,
    AttendanceItem,
    AttendanceMark,
    AttendancePerson,
    AttendanceStudent,
    SessionAttendance,
)
from app.modules.scheduling.errors import (
    AttendanceCorrectedError,
    AttendanceLockedError,
    AttendanceNotRecordableError,
    AttendanceNotStartedError,
    AttendanceTutorOnlyError,
    SessionValidationError,
)
from app.modules.scheduling.models import (
    ClassSession,
    ClassSessionAttendance,
    ClassSessionAttendanceCorrection,
    ClassSessionParticipant,
)
from app.modules.scheduling.offer_flow import lock_sessions
from app.modules.scheduling.schedule_schemas import ScheduleSubject
from app.modules.scheduling.schemas import SessionKindCode, SessionStateCode
from app.modules.scheduling.states import SessionState

CORRECTED_EVENT: Final = "scheduling.attendance_corrected"
ATTENDANCE_RECORD_TYPE: Final = "class_session_attendance"

_NOT_PARTICIPANT_MESSAGE: Final = "Not a student of this session."
_UNCHANGED_MESSAGE: Final = "The student already has this status."


type CompletedAttendanceUnlock = Callable[[AsyncSession, uuid.UUID], Awaitable[bool]]
"""``(session, class_session_id) -> bool``: whether the tutor may mark a
``completed`` session again (#78 E6)."""

_unlocks: list[CompletedAttendanceUnlock] = []


def register_completed_attendance_unlock(
    predicate: CompletedAttendanceUnlock | None,
) -> None:
    """Register the predicate (again: replaces it; ``None`` removes it,
    tests only). ``app.wiring`` registers
    ``academics.service.closeout_awaits_tutor``; scheduling never imports
    academics (ADR-0002). Without one, a ``completed`` session stays locked
    for the tutor (#76)."""
    _unlocks[:] = [] if predicate is None else [predicate]


async def _tutor_unlocked(session: AsyncSession, row: ClassSession) -> bool:
    """The registered predicate for a ``completed`` session (one statement
    then), else ``False`` without a statement. Called after the caller has
    read (or locked) the session row."""
    if row.state != SessionState.COMPLETED or not _unlocks:
        return False
    return await _unlocks[0](session, row.id)


def _person(name: str | None) -> AttendancePerson | None:
    return None if name is None else AttendancePerson(display_name=name)


def raise_block(block: AttendanceBlock, state: str) -> None:
    """Raise the ``409`` of a block code (A3)."""
    if block == NOT_RECORDABLE:
        raise AttendanceNotRecordableError(state)
    if block == NOT_STARTED:
        raise AttendanceNotStartedError()
    if block == LOCKED:
        raise AttendanceLockedError()


async def _session_row(
    session: AsyncSession, class_session_id: uuid.UUID
) -> ClassSession | None:
    return cast(
        ClassSession | None,
        await session.scalar(
            select(ClassSession).where(ClassSession.id == class_session_id)
        ),
    )


async def _attendance_view(
    session: AsyncSession,
    principal: Principal,
    row: ClassSession,
    *,
    tutor_unlocked: bool | None = None,
) -> SessionAttendance:
    """The B2 body of ``row`` for ``principal`` (at most 5 statements; one
    more for the tutor of a ``completed`` session when ``tutor_unlocked``
    isn't given: the #78 unlock predicate, AM1)."""
    pairs = list(
        await session.execute(
            select(ClassSessionParticipant.student_id, ClassSessionAttendance)
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
            .where(ClassSessionParticipant.class_session_id == row.id)
        )
    )
    students = await families.student_references(
        session, [student_id for student_id, _ in pairs]
    )
    marks = {student_id: mark for student_id, mark in pairs if mark is not None}
    corrections: dict[uuid.UUID, list[ClassSessionAttendanceCorrection]] = {}
    if marks:
        for correction in await session.scalars(
            select(ClassSessionAttendanceCorrection)
            .where(
                ClassSessionAttendanceCorrection.attendance_id.in_(
                    [mark.id for mark in marks.values()]
                )
            )
            .order_by(
                ClassSessionAttendanceCorrection.created_at,
                ClassSessionAttendanceCorrection.id,
            )
        ):
            corrections.setdefault(correction.attendance_id, []).append(correction)
    accounts = await identity.get_account_references(
        session,
        {mark.marked_by_user_id for mark in marks.values()}
        | {
            item.corrected_by_user_id
            for items in corrections.values()
            for item in items
        },
    )
    subject_ref = (await catalog.subject_references(session, [row.subject_id]))[
        row.subject_id
    ]

    def name_of(user_id: uuid.UUID) -> str | None:
        account = accounts.get(user_id)
        return None if account is None else account.display_name

    items = []
    for student_id, _ in pairs:
        student = students.get(student_id)
        if student is None:  # pragma: no cover - the key guarantees the row
            continue
        mark = marks.get(student_id)
        history = corrections.get(mark.id, []) if mark is not None else []
        items.append(
            AttendanceItem(
                student=AttendanceStudent(
                    id=student.id, display_name=student.display_name
                ),
                status=None if mark is None else cast(AttendanceStatus, mark.status),
                marked_by=None
                if mark is None
                else _person(name_of(mark.marked_by_user_id)),
                marked_at=None if mark is None else mark.marked_at.astimezone(UTC),
                corrected=bool(history),
                corrections=[
                    AttendanceCorrectionItem(
                        id=item.id,
                        previous_status=cast(
                            AttendanceStatus | None, item.previous_status
                        ),
                        new_status=cast(AttendanceStatus, item.new_status),
                        reason=item.reason,
                        corrected_by=AttendancePerson(
                            display_name=name_of(item.corrected_by_user_id) or ""
                        ),
                        created_at=item.created_at.astimezone(UTC),
                    )
                    for item in history
                ],
            )
        )
    items.sort(
        key=lambda item: (item.student.display_name.casefold(), str(item.student.id))
    )

    now = clock.now()
    is_tutor = is_current_tutor(principal, row.tutor_user_id)
    if is_tutor and tutor_unlocked is None:
        tutor_unlocked = await _tutor_unlocked(session, row)
    tutor_block = attendance_block(
        row.state,
        row.starts_at,
        now,
        by_tutor=True,
        tutor_unlocked=bool(tutor_unlocked),
    )
    staff_block = attendance_block(row.state, row.starts_at, now, by_tutor=False)
    return SessionAttendance(
        class_session_id=row.id,
        version=row.version,
        kind=cast(SessionKindCode, row.kind),
        state=cast(SessionStateCode, row.state),
        starts_at=row.starts_at.astimezone(UTC),
        ends_at=row.ends_at.astimezone(UTC),
        subject=ScheduleSubject(
            id=subject_ref.id,
            name=subject_ref.name,
            education_level_name=subject_ref.education_level_name,
        ),
        can_mark=is_tutor and tutor_block is None,
        can_correct=can_correct(principal) and staff_block is None,
        block=tutor_block if is_tutor else staff_block,
        items=items,
    )


async def get_session_attendance(
    session: AsyncSession, principal: Principal, class_session_id: uuid.UUID
) -> SessionAttendance:
    """``GET /api/v1/class-sessions/{id}/attendance`` (B1, B2)."""
    row = await _session_row(session, class_session_id)
    if row is None or attendance_access(principal, row.tutor_user_id) is (
        AttendanceAccess.HIDDEN
    ):
        raise RecordNotFoundError()
    return await _attendance_view(session, principal, row)


def _ensure_tutor(principal: Principal, row: ClassSession | None) -> None:
    """``404`` (unknown or hidden) or ``403 attendance_tutor_only``."""
    if row is None:
        raise RecordNotFoundError()
    access = attendance_access(principal, row.tutor_user_id)
    if access is AttendanceAccess.HIDDEN:
        raise RecordNotFoundError()
    if access is AttendanceAccess.STAFF:
        raise AttendanceTutorOnlyError()


async def _participants(
    session: AsyncSession, class_session_id: uuid.UUID
) -> set[uuid.UUID]:
    return set(
        await session.scalars(
            select(ClassSessionParticipant.student_id).where(
                ClassSessionParticipant.class_session_id == class_session_id
            )
        )
    )


async def _marks_of(
    session: AsyncSession, class_session_id: uuid.UUID
) -> dict[uuid.UUID, ClassSessionAttendance]:
    rows = await session.scalars(
        select(ClassSessionAttendance).where(
            ClassSessionAttendance.class_session_id == class_session_id
        )
    )
    return {row.student_id: row for row in rows}


async def _corrected_ids(
    session: AsyncSession, attendance_ids: Collection[uuid.UUID]
) -> set[uuid.UUID]:
    """The attendance rows (of ``attendance_ids``) with a correction."""
    if not attendance_ids:
        return set()
    return set(
        await session.scalars(
            select(ClassSessionAttendanceCorrection.attendance_id)
            .where(ClassSessionAttendanceCorrection.attendance_id.in_(attendance_ids))
            .distinct()
        )
    )


async def _bump(session: AsyncSession, row: ClassSession) -> None:
    """One versioned ``UPDATE`` of the session (only ``updated_at`` and
    ``version`` change, which the audit leaves out): its version covers its
    attendance."""
    row.updated_at = func.now()
    await session.flush()
    await session.refresh(row, ["updated_at"])


def _not_participant(path: list[str | int]) -> FieldError:
    return {
        "path": path,
        "code": "student_not_participant",
        "message": _NOT_PARTICIPANT_MESSAGE,
    }


async def record_attendance(
    session: AsyncSession,
    *,
    actor: Principal,
    class_session_id: uuid.UUID,
    version: int,
    marks: Sequence[AttendanceMark],
) -> SessionAttendance:
    """The tutor's marks (C2, C3; the order is in the module docstring).
    Returns the session's attendance as :func:`get_session_attendance`
    shows it to ``actor``. #77 reuses it."""
    _ensure_tutor(actor, await _session_row(session, class_session_id))
    [row] = await lock_sessions(session, [class_session_id])
    _ensure_tutor(actor, row)
    ensure_version(row, version)
    now = clock.now()
    unlocked = await _tutor_unlocked(session, row)
    block = attendance_block(
        row.state, row.starts_at, now, by_tutor=True, tutor_unlocked=unlocked
    )
    if block is not None:
        raise_block(block, row.state)

    participants = await _participants(session, row.id)
    unknown = [
        _not_participant(["body", "marks", index, "student_id"])
        for index, mark in enumerate(marks)
        if mark.student_id not in participants
    ]
    if unknown:
        raise SessionValidationError(unknown)

    existing = await _marks_of(session, row.id)
    changing = [
        existing[mark.student_id]
        for mark in marks
        if mark.student_id in existing
        and existing[mark.student_id].status != mark.status
    ]
    corrected = await _corrected_ids(session, [item.id for item in changing])
    for changing_row in changing:
        if changing_row.id in corrected:
            raise AttendanceCorrectedError(str(changing_row.student_id))

    changed = False
    for mark in marks:
        current = existing.get(mark.student_id)
        if current is None:
            session.add(
                ClassSessionAttendance(
                    class_session_id=row.id,
                    student_id=mark.student_id,
                    status=mark.status,
                    marked_by_user_id=actor.user_id,
                    marked_at=now,
                )
            )
            changed = True
        elif current.status != mark.status:
            current.status = mark.status
            current.marked_by_user_id = actor.user_id
            current.marked_at = now
            changed = True
    if changed:
        await session.flush()
        await _bump(session, row)
    return await _attendance_view(session, actor, row, tutor_unlocked=unlocked)


async def correct_attendance(
    session: AsyncSession,
    *,
    actor: Principal,
    class_session_id: uuid.UUID,
    body: AttendanceCorrectionCreate,
) -> SessionAttendance:
    """A staff correction (D2, D3; the order is in the module docstring).
    The route has checked ``attendance.correct``."""
    locked = await lock_sessions(session, [class_session_id])
    if not locked:
        raise RecordNotFoundError()
    [row] = locked
    ensure_version(row, body.version)
    now = clock.now()
    block = attendance_block(row.state, row.starts_at, now, by_tutor=False)
    if block is not None:
        raise_block(block, row.state)
    if body.student_id not in await _participants(session, row.id):
        raise SessionValidationError([_not_participant(["body", "student_id"])])
    current = (await _marks_of(session, row.id)).get(body.student_id)
    if current is not None and current.status == body.status:
        raise SessionValidationError(
            [
                {
                    "path": ["body", "status"],
                    "code": "attendance_unchanged",
                    "message": _UNCHANGED_MESSAGE,
                }
            ]
        )

    previous = None if current is None else current.status
    if current is None:
        current = ClassSessionAttendance(
            class_session_id=row.id,
            student_id=body.student_id,
            status=body.status,
            marked_by_user_id=actor.user_id,
            marked_at=now,
        )
        session.add(current)
    else:
        current.status = body.status
        current.marked_by_user_id = actor.user_id
        current.marked_at = now
    await session.flush()
    correction = ClassSessionAttendanceCorrection(
        attendance_id=current.id,
        previous_status=previous,
        new_status=body.status,
        reason=body.reason,
        corrected_by_user_id=actor.user_id,
    )
    session.add(correction)
    await session.flush()
    record_audit_event(
        session,
        action=CORRECTED_EVENT,
        record_type=ATTENDANCE_RECORD_TYPE,
        record_id=current.id,
        details={
            "correction_id": correction.id,
            "class_session_id": row.id,
            "student_id": body.student_id,
            "previous_status": previous,
            "new_status": body.status,
            "reason": body.reason,
        },
    )
    await _bump(session, row)
    return await _attendance_view(session, actor, row)


# --- Seams for #77, #84, #85 and the family schedule ------------------------------


async def attendance_statuses(
    session: AsyncSession, class_session_ids: Iterable[uuid.UUID]
) -> dict[tuple[uuid.UUID, uuid.UUID], AttendanceStatus]:
    """The marked statuses of these sessions by ``(class_session_id,
    student_id)`` (unmarked participants are left out). One statement, none
    for an empty collection."""
    ids = set(class_session_ids)
    if not ids:
        return {}
    rows = await session.execute(
        select(
            ClassSessionAttendance.class_session_id,
            ClassSessionAttendance.student_id,
            ClassSessionAttendance.status,
        ).where(ClassSessionAttendance.class_session_id.in_(ids))
    )
    return {
        (class_session_id, student_id): cast(AttendanceStatus, status)
        for class_session_id, student_id, status in rows
    }


@dataclass(frozen=True)
class AttendanceSummary:
    """One student's attendance counts over a period (#83 B2)."""

    sessions: int
    present: int
    late: int
    absent: int
    not_recorded: int


SUMMARY_STATES: Final = (SessionState.CONFIRMED.value, SessionState.COMPLETED.value)
"""The states a progress report counts (cancelled and skipped never)."""


async def student_attendance_summary(
    session: AsyncSession,
    student_id: uuid.UUID,
    start: date,
    end: date,
    *,
    subject_id: uuid.UUID | None,
    time_zone: str,
    now: datetime,
) -> AttendanceSummary:
    """#83 B2: the student's ``confirmed`` or ``completed`` sessions that have
    started (``starts_at <= now``) and whose local start date (in
    ``time_zone``) is in ``[start, end]`` (only ``subject_id``'s when given),
    counted by the student's mark: ``present``, ``late``, ``absent`` or not
    recorded. One statement."""
    local_date = cast(Any, func.timezone(time_zone, ClassSession.starts_at)).cast(Date)
    status = ClassSessionAttendance.status
    query = (
        select(
            func.count(),
            func.count().filter(status == "present"),
            func.count().filter(status == "late"),
            func.count().filter(status == "absent"),
            func.count().filter(status.is_(None)),
        )
        .select_from(ClassSessionParticipant)
        .join(ClassSession, ClassSession.id == ClassSessionParticipant.class_session_id)
        .outerjoin(
            ClassSessionAttendance,
            and_(
                ClassSessionAttendance.class_session_id
                == ClassSessionParticipant.class_session_id,
                ClassSessionAttendance.student_id == ClassSessionParticipant.student_id,
            ),
        )
        .where(
            ClassSessionParticipant.student_id == student_id,
            ClassSession.state.in_(SUMMARY_STATES),
            ClassSession.starts_at <= now,
            local_date >= start,
            local_date <= end,
        )
    )
    if subject_id is not None:
        query = query.where(ClassSession.subject_id == subject_id)
    sessions, present, late, absent, not_recorded = (await session.execute(query)).one()
    return AttendanceSummary(
        sessions=sessions,
        present=present,
        late=late,
        absent=absent,
        not_recorded=not_recorded,
    )


async def student_attendance(
    session: AsyncSession,
    student_id: uuid.UUID,
    class_session_ids: Iterable[uuid.UUID],
) -> dict[uuid.UUID, AttendanceStatus]:
    """One student's marked status in these sessions, by session id (#76
    E4: the family schedule shows only the path's student). One statement,
    none for an empty collection."""
    ids = set(class_session_ids)
    if not ids:
        return {}
    rows = await session.execute(
        select(ClassSessionAttendance.class_session_id, ClassSessionAttendance.status)
        .where(ClassSessionAttendance.student_id == student_id)
        .where(ClassSessionAttendance.class_session_id.in_(ids))
    )
    return {
        class_session_id: cast(AttendanceStatus, status)
        for class_session_id, status in rows
    }
