"""Creating and reading one-off individual sessions (#48 C, D, E; spec W1.1,
W1.2; ADR-0005 D1, D2, D7, D9).

**Create** (:func:`create_class_session`), in this order, after the route's
``401``/``403`` and the body's ``422``:

1. the per-student advisory lock (:func:`~app.modules.scheduling.locks.lock_students`);
2. the center zone ``FOR SHARE`` (``administration.service.get_center_time_zone``),
   so a zone change waits for this transaction;
3. ``422`` for the bounds (``validate_session_bounds``): the reason is a
   field error on ``ends_at``, except ``naive_datetime`` and
   ``not_whole_minutes``, which go on the field at fault;
4. ``422`` for the references, collected in one response:
   ``student_unknown`` / ``student_inactive`` (``families.service.student_references``),
   ``subject_unknown`` / ``subject_inactive`` (the subject or its level;
   ``catalog.service.get_subject_for_reference`` ``FOR SHARE``),
   ``tutor_unknown`` (no such user, or not a tutor) / ``tutor_inactive``
   (any state but ``active``, a pending invitee included;
   ``identity.service.get_role_member``) and ``classroom_unknown`` /
   ``classroom_inactive`` (``catalog.service.classroom_references``);
5. every conflict check (:mod:`app.modules.scheduling.conflicts` lists the
   codes and their data sources); the tutor checks run only with a tutor, the
   room checks only with a room. Local values are taken in the center zone;
   a local end at the next midnight is ``time.max`` for the window checks;
6. the override decision (#49,
   :func:`~app.modules.scheduling.overrides.evaluate_override`) on the
   conflicts just computed: ``409 scheduling_conflict`` with **every**
   conflict, sorted (:func:`~app.modules.scheduling.conflicts.sort_conflicts`),
   unless there is none, or the body's ``override`` lists every one and all
   are soft;
7. the tutor's workload warnings (#50 A7,
   :func:`~app.modules.scheduling.workload_sessions.session_warnings`): at
   most two statements with a tutor, none without one; they never block and
   come only with the ``201`` (an overridden create carries them too);
8. the session (``offered`` with the tutor, or ``unassigned`` without one,
   D2 / E1) and its one participant, flushed through
   ``flush_class_sessions``: an ``EXCLUDE`` race becomes ``409
   scheduling_conflict`` with that one hard conflict (its
   ``class_session_ids`` is ``[]``: the other row is not known);
9. with overridden conflicts, the override row and its
   ``scheduling.session_override`` event
   (:func:`~app.modules.scheduling.overrides.record_session_override`).

The ``403`` for an ``override`` without ``schedule.override`` comes before
step 1 (the route). No offer row, notification or inbox item is created
(#52). Audit: the automatic ``create`` events of both tables, plus the
override event when something was overridden.

**Read** (:func:`get_class_session`): every holder of ``schedule.view``
sees every session and its overrides (no record policy for staff, D9); at
most seven statements; ``404`` for an unknown id.

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, time
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.core import clock
from app.core.authz import RecordNotFoundError
from app.core.errors import FieldError
from app.modules.administration import service as administration
from app.modules.catalog import service as catalog
from app.modules.families import service as families
from app.modules.identity import service as identity
from app.modules.scheduling.capacity import capacity_violations
from app.modules.scheduling.conflicts import (
    CENTER_CLOSED,
    NOTICE_WINDOW,
    OUTSIDE_OPERATING_HOURS,
    ROOM_OVERLAP,
    ROOM_UNAVAILABLE,
    STUDENT_OVERLAP,
    TUTOR_NOT_QUALIFIED,
    TUTOR_OVERLAP,
    TUTOR_UNAVAILABLE,
    Conflict,
    LocalSlot,
    covers_operating_hours,
    local_slot,
    sort_conflicts,
    tutor_unavailable_reasons,
)
from app.modules.scheduling.errors import (
    InvalidSessionTimesError,
    SchedulingConflictError,
    SessionOverlapError,
    SessionValidationError,
)
from app.modules.scheduling.locks import lock_students
from app.modules.scheduling.models import (
    ClassSession,
    ClassSessionOverride,
    ClassSessionParticipant,
)
from app.modules.scheduling.overrides import evaluate_override, record_session_override
from app.modules.scheduling.schemas import (
    ClassSessionCreate,
    ClassSessionCreated,
    ClassSessionDetail,
    ClassSessionSlot,
    OverrideActor,
    SessionClassroom,
    SessionOverrideItem,
    SessionParticipant,
    SessionSubject,
    SessionTutor,
)
from app.modules.scheduling.sessions import flush_class_sessions
from app.modules.scheduling.states import (
    ROOM_OCCUPYING_STATES,
    STUDENT_OCCUPYING_STATES,
    TUTOR_OCCUPYING_STATES,
    SessionKind,
    SessionState,
)
from app.modules.scheduling.times import scheduled_minutes, validate_session_bounds
from app.modules.scheduling.workload_sessions import session_warnings
from app.modules.staff import service as staff

TUTOR_ROLE: Final = "tutor"
ACTIVE: Final = "active"
OVERLAP_IDS_LIMIT: Final = 10
"""At most this many overlapping session ids in a conflict's details."""

_REFERENCE_MESSAGES: Final = {
    "student_unknown": "Choose an existing student.",
    "student_inactive": "The student is inactive.",
    "subject_unknown": "Choose an existing subject.",
    "subject_inactive": "The subject or its education level is inactive.",
    "tutor_unknown": "Choose an existing tutor.",
    "tutor_inactive": "The tutor's account is not active.",
    "classroom_unknown": "Choose an existing classroom.",
    "classroom_inactive": "The classroom is inactive.",
}
_TIME_MESSAGES: Final = {
    "naive_datetime": "Give the time with its UTC offset.",
    "not_whole_minutes": "Use whole minutes.",
    "ends_before_start": "The end must be after the start.",
    "duration_out_of_range": "A session lasts 15 to 480 minutes.",
    "crosses_local_date": "A session starts and ends on the same local date.",
}


def _field_error(field: str, code: str, message: str) -> FieldError:
    return {"path": ["body", field], "code": code, "message": message}


def _is_naive(value: datetime) -> bool:
    return value.tzinfo is None or value.utcoffset() is None


def _bounds_field(reason: str, starts_at: datetime) -> str:
    if reason == "naive_datetime":
        return "starts_at" if _is_naive(starts_at) else "ends_at"
    if reason == "not_whole_minutes":
        return "starts_at" if starts_at.second or starts_at.microsecond else "ends_at"
    return "ends_at"


def validate_bounds(body: ClassSessionSlot, zone: str) -> None:
    """``422`` for the bounds (step 3; also the slot suggestions, #50)."""
    try:
        validate_session_bounds(body.starts_at, body.ends_at, zone)
    except InvalidSessionTimesError as exc:
        field = _bounds_field(exc.reason, body.starts_at)
        raise SessionValidationError(
            [_field_error(field, exc.reason, _TIME_MESSAGES[exc.reason])]
        ) from None


@dataclass(frozen=True)
class _References:
    student: SessionParticipant
    subject: SessionSubject
    tutor: SessionTutor | None
    classroom: SessionClassroom | None


async def references(
    session: AsyncSession, body: ClassSessionSlot, *, lock: bool = True
) -> _References:
    """Every reference of the body, or one ``422`` with every invalid one
    (step 4; also the slot suggestions, #50, with ``lock=False``: the
    subject isn't read ``FOR SHARE`` when nothing is written)."""
    errors: list[FieldError] = []

    def reject(field: str, code: str) -> None:
        errors.append(_field_error(field, code, _REFERENCE_MESSAGES[code]))

    student = (await families.student_references(session, [body.student_id])).get(
        body.student_id
    )
    if student is None:
        reject("student_id", "student_unknown")
    elif not student.is_active:
        reject("student_id", "student_inactive")

    subject = await catalog.get_subject_for_reference(
        session, body.subject_id, lock=lock
    )
    if subject is None:
        reject("subject_id", "subject_unknown")
    elif not (subject.is_active and subject.education_level_is_active):
        reject("subject_id", "subject_inactive")

    tutor = None
    if body.tutor_user_id is not None:
        tutor = await identity.get_role_member(session, TUTOR_ROLE, body.tutor_user_id)
        if tutor is None:
            reject("tutor_user_id", "tutor_unknown")
        elif tutor.state != ACTIVE:
            reject("tutor_user_id", "tutor_inactive")

    classroom = None
    if body.classroom_id is not None:
        classroom = (
            await catalog.classroom_references(session, [body.classroom_id])
        ).get(body.classroom_id)
        if classroom is None:
            reject("classroom_id", "classroom_unknown")
        elif not classroom.is_active:
            reject("classroom_id", "classroom_inactive")

    if errors:
        raise SessionValidationError(errors)
    # Narrowing only: every missing reference was rejected above.
    assert student is not None and subject is not None  # noqa: S101
    return _References(
        student=SessionParticipant(
            student_id=student.id,
            display_name=student.display_name,
            is_active=student.is_active,
        ),
        subject=SessionSubject(
            id=subject.id,
            name=subject.name,
            education_level_name=subject.education_level_name,
            is_active=subject.is_active and subject.education_level_is_active,
        ),
        tutor=(
            None
            if tutor is None
            else SessionTutor(id=tutor.id, display_name=tutor.display_name)
        ),
        classroom=(
            None
            if classroom is None
            else SessionClassroom(
                id=classroom.id, name=classroom.name, is_active=classroom.is_active
            )
        ),
    )


# --- Conflict checks (D) -------------------------------------------------------------


def _overlapping(slot: LocalSlot) -> Any:
    return (ClassSession.starts_at < slot.ends_at) & (
        ClassSession.ends_at > slot.starts_at
    )


async def _overlap_ids(
    session: AsyncSession,
    slot: LocalSlot,
    states: frozenset[SessionState],
    column: InstrumentedAttribute[Any],
    value: uuid.UUID,
) -> list[str]:
    query = select(ClassSession.id).where(
        column == value,
        ClassSession.state.in_(sorted(state.value for state in states)),
        _overlapping(slot),
    )
    if column is ClassSessionParticipant.student_id:
        query = query.join(
            ClassSessionParticipant,
            ClassSessionParticipant.class_session_id == ClassSession.id,
        )
    rows = await session.scalars(
        query.order_by(ClassSession.starts_at, ClassSession.id).limit(OVERLAP_IDS_LIMIT)
    )
    return [str(row) for row in rows]


async def _overlap_conflicts(
    session: AsyncSession, body: ClassSessionSlot, slot: LocalSlot
) -> list[Conflict]:
    conflicts: list[Conflict] = []
    students = await _overlap_ids(
        session,
        slot,
        STUDENT_OCCUPYING_STATES,
        ClassSessionParticipant.student_id,
        body.student_id,
    )
    if students:
        conflicts.append(
            Conflict(
                STUDENT_OVERLAP,
                {"student_id": str(body.student_id), "class_session_ids": students},
            )
        )
    if body.tutor_user_id is not None:
        tutors = await _overlap_ids(
            session,
            slot,
            TUTOR_OCCUPYING_STATES,
            ClassSession.tutor_user_id,
            body.tutor_user_id,
        )
        if tutors:
            conflicts.append(Conflict(TUTOR_OVERLAP, {"class_session_ids": tutors}))
    if body.classroom_id is not None:
        rooms = await _overlap_ids(
            session,
            slot,
            ROOM_OCCUPYING_STATES,
            ClassSession.classroom_id,
            body.classroom_id,
        )
        if rooms:
            conflicts.append(
                Conflict(
                    ROOM_OVERLAP,
                    {
                        "classroom_id": str(body.classroom_id),
                        "class_session_ids": rooms,
                    },
                )
            )
    return conflicts


async def _capacity_conflicts(
    session: AsyncSession, body: ClassSessionSlot
) -> list[Conflict]:
    """D4 / ADR-0005 D7: one path for every kind. An individual session never
    exceeds a room (a room holds at least 1, #39), so nothing comes back
    here; #56 / #65 make it reachable."""
    room_capacity = None
    if body.classroom_id is not None:
        room_capacity = (
            await catalog.get_classroom(session, body.classroom_id)
        ).capacity
    return [
        Conflict(code, {"participants": 1, "room_capacity": room_capacity})
        for code in capacity_violations(
            body.kind, 1, group_max=None, room_capacity=room_capacity
        )
    ]


async def _tutor_conflicts(
    session: AsyncSession, body: ClassSessionSlot, slot: LocalSlot, tutor: uuid.UUID
) -> list[Conflict]:
    conflicts: list[Conflict] = []
    availability = await staff.is_tutor_available_at(
        session, tutor, slot.starts_at, slot.ends_at
    )
    reasons = tutor_unavailable_reasons(availability.reasons)
    if reasons:
        conflicts.append(Conflict(TUTOR_UNAVAILABLE, {"reasons": reasons}))
    if not await staff.is_qualified(session, tutor, body.subject_id):
        conflicts.append(
            Conflict(TUTOR_NOT_QUALIFIED, {"subject_id": str(body.subject_id)})
        )
    return conflicts


def _hours_of(item: Any) -> tuple[int, time, time]:
    return (
        item.weekday,
        time.fromisoformat(item.opens_at),
        time.fromisoformat(item.closes_at),
    )


async def _center_conflicts(session: AsyncSession, slot: LocalSlot) -> list[Conflict]:
    conflicts: list[Conflict] = []
    if await catalog.is_center_closed_on(session, slot.local_date):
        conflicts.append(Conflict(CENTER_CLOSED, {"date": slot.local_date.isoformat()}))
    settings = await administration.get_business_settings(session)
    hours = settings.operating_hours
    if not covers_operating_hours(
        [_hours_of(item) for item in hours], slot.weekday, slot.start, slot.end
    ):
        day = next((item for item in hours if item.weekday == slot.weekday), None)
        conflicts.append(
            Conflict(
                OUTSIDE_OPERATING_HOURS,
                {
                    "date": slot.local_date.isoformat(),
                    "opens_at": None if day is None else day.opens_at,
                    "closes_at": None if day is None else day.closes_at,
                },
            )
        )
    return conflicts


async def _notice_conflicts(session: AsyncSession, slot: LocalSlot) -> list[Conflict]:
    """D10: the booking notice of the policy in force now (#61); a start in
    the past breaks it too, and exactly the notice is fine."""
    now = clock.now()
    policy = await administration.get_scheduling_policy(session, now)
    if not administration.violates_booking_notice(
        policy, starts_at=slot.starts_at, at=now
    ):
        return []
    return [
        Conflict(NOTICE_WINDOW, {"booking_notice_hours": policy.booking_notice_hours})
    ]


async def find_conflicts(
    session: AsyncSession, body: ClassSessionSlot, zone: str
) -> list[Conflict]:
    """Every hard and soft conflict of the body, sorted. The caller holds the
    student locks and the zone ``FOR SHARE``, and has validated the bounds
    and the references."""
    slot = local_slot(body.starts_at, body.ends_at, zone)
    conflicts = await _overlap_conflicts(session, body, slot)
    conflicts += await _capacity_conflicts(session, body)
    if body.tutor_user_id is not None:
        conflicts += await _tutor_conflicts(session, body, slot, body.tutor_user_id)
    if body.classroom_id is not None and not await catalog.is_classroom_available(
        session, body.classroom_id, slot.weekday, slot.start, slot.end
    ):
        conflicts.append(
            Conflict(ROOM_UNAVAILABLE, {"classroom_id": str(body.classroom_id)})
        )
    conflicts += await _center_conflicts(session, slot)
    conflicts += await _notice_conflicts(session, slot)
    return sort_conflicts(conflicts)


# --- Create (C) and read (E) ---------------------------------------------------------


def _race_conflict(code: str, body: ClassSessionSlot) -> Conflict:
    if code == ROOM_OVERLAP:
        return Conflict(
            ROOM_OVERLAP,
            {"classroom_id": str(body.classroom_id), "class_session_ids": []},
        )
    return Conflict(TUTOR_OVERLAP, {"class_session_ids": []})


def _detail(
    row: ClassSession,
    *,
    subject: SessionSubject,
    tutor: SessionTutor | None,
    classroom: SessionClassroom | None,
    participants: list[SessionParticipant],
    overrides: list[SessionOverrideItem],
) -> ClassSessionDetail:
    return ClassSessionDetail(
        id=row.id,
        kind=SessionKind(row.kind).value,
        state=SessionState(row.state).value,
        subject=subject,
        tutor=tutor,
        classroom=classroom,
        participants=sorted(
            participants, key=lambda p: (p.display_name.casefold(), str(p.student_id))
        ),
        # As stored: the request's offset is not kept (ADR-0004).
        starts_at=row.starts_at.astimezone(UTC),
        ends_at=row.ends_at.astimezone(UTC),
        scheduled_minutes=row.scheduled_minutes,
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
        overrides=overrides,
    )


def _override_item(
    row: ClassSessionOverride, actors: dict[uuid.UUID, Any]
) -> SessionOverrideItem:
    actor = actors[row.actor_user_id]
    return SessionOverrideItem(
        id=row.id,
        codes=list(row.codes),
        reason=row.reason,
        actor=OverrideActor(id=actor.id, display_name=actor.display_name),
        created_at=row.created_at,
    )


async def create_class_session(
    session: AsyncSession, body: ClassSessionCreate, *, actor_id: uuid.UUID
) -> ClassSessionCreated:
    """``POST /api/v1/class-sessions``: see the module docstring for the
    order. ``actor_id`` is the signed-in user (the override's actor); the
    route has already checked ``schedule.override`` for an ``override``."""
    await lock_students(session, [body.student_id])
    zone = await administration.get_center_time_zone(session, for_share=True)
    validate_bounds(body, zone)
    refs = await references(session, body)
    conflicts = await find_conflicts(session, body, zone)
    decision = evaluate_override(conflicts, body.override)
    if not decision.allowed:
        raise SchedulingConflictError([conflict.as_wire() for conflict in conflicts])
    warnings = (
        []
        if body.tutor_user_id is None
        else await session_warnings(
            session, body.tutor_user_id, body.starts_at, body.ends_at, zone
        )
    )

    state = (
        SessionState.UNASSIGNED if body.tutor_user_id is None else SessionState.OFFERED
    )
    row = ClassSession(
        kind=SessionKind.INDIVIDUAL.value,
        state=state.value,
        subject_id=body.subject_id,
        tutor_user_id=body.tutor_user_id,
        classroom_id=body.classroom_id,
        starts_at=body.starts_at,
        ends_at=body.ends_at,
        scheduled_minutes=scheduled_minutes(body.starts_at, body.ends_at),
    )
    session.add(row)
    try:
        await flush_class_sessions(session)
    except SessionOverlapError as exc:
        raise SchedulingConflictError(
            [_race_conflict(exc.code, body).as_wire()]
        ) from None
    session.add(
        ClassSessionParticipant(class_session_id=row.id, student_id=body.student_id)
    )
    await flush_class_sessions(session)
    overrides: list[SessionOverrideItem] = []
    if decision.overridden:
        # Only an allowed override reaches here, so body.override is set.
        assert body.override is not None  # noqa: S101
        stored = await record_session_override(
            session,
            row.id,
            decision.overridden,
            body.override.reason,
            actor_id=actor_id,
        )
        actors = await identity.get_account_references(session, [actor_id])
        overrides.append(_override_item(stored, actors))
    detail = _detail(
        row,
        subject=refs.subject,
        tutor=refs.tutor,
        classroom=refs.classroom,
        participants=[refs.student],
        overrides=overrides,
    )
    return ClassSessionCreated(**detail.model_dump(), warnings=warnings)


async def get_class_session(
    session: AsyncSession, class_session_id: uuid.UUID
) -> ClassSessionDetail:
    """``GET /api/v1/class-sessions/{id}``: at most seven statements (the
    tutor and the override actors are read together)."""
    row = await session.get(ClassSession, class_session_id)
    if row is None:
        raise RecordNotFoundError()
    student_ids = list(
        await session.scalars(
            select(ClassSessionParticipant.student_id).where(
                ClassSessionParticipant.class_session_id == row.id
            )
        )
    )
    students = await families.student_references(session, student_ids)
    subject = (await catalog.subject_references(session, [row.subject_id]))[
        row.subject_id
    ]
    override_rows = list(
        await session.scalars(
            select(ClassSessionOverride)
            .where(ClassSessionOverride.class_session_id == row.id)
            .order_by(ClassSessionOverride.created_at, ClassSessionOverride.id)
        )
    )
    account_ids = {override.actor_user_id for override in override_rows}
    if row.tutor_user_id is not None:
        account_ids.add(row.tutor_user_id)
    accounts = await identity.get_account_references(session, account_ids)
    tutor = None
    if row.tutor_user_id is not None:
        account = accounts[row.tutor_user_id]
        tutor = SessionTutor(id=account.id, display_name=account.display_name)
    classroom = None
    if row.classroom_id is not None:
        room = (await catalog.classroom_references(session, [row.classroom_id]))[
            row.classroom_id
        ]
        classroom = SessionClassroom(
            id=room.id, name=room.name, is_active=room.is_active
        )
    return _detail(
        row,
        subject=SessionSubject(
            id=subject.id,
            name=subject.name,
            education_level_name=subject.education_level_name,
            is_active=subject.is_active and subject.education_level_is_active,
        ),
        tutor=tutor,
        classroom=classroom,
        participants=[
            SessionParticipant(
                student_id=student.id,
                display_name=student.display_name,
                is_active=student.is_active,
            )
            for student in students.values()
        ],
        overrides=[_override_item(override, accounts) for override in override_rows],
    )
