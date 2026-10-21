"""The one conflict collector shared by every write path (#48 D, #53 D1;
ADR-0005 D1, D6, D7).

:func:`collect_conflicts` runs exactly the #48 checks for one
:class:`SlotCandidate` (the students, subject, tutor, classroom and the
interval): the overlaps, the capacity, the tutor's availability and
qualification, the room's windows, the center's closures and operating
hours, and the booking notice. :mod:`~app.modules.scheduling.conflicts`
lists each check's data source. The one-off create
(:func:`~app.modules.scheduling.booking.find_conflicts`) and the series
occurrences (:mod:`~app.modules.scheduling.materialize`, the
``scheduling.occurrence_conflict`` approve) call it.

- ``check_center_closed=False`` is for **series occurrences**: an occurrence
  on a closure date never gets here (it is stored as ``skipped_closure``,
  ADR-0005 D6 "``center_closed``: one-off sessions only"). The tutor's
  ``center_closed`` availability reason is dropped either way (#48 D5).
- ``now`` is the instant the booking notice is measured from (``clock.now()``
  for a request; the job's run time for the nightly materialization).

The caller holds the student locks and the zone ``FOR SHARE`` and has
validated the bounds and the references. Runs in the caller's Unit of Work,
reads only.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.modules.administration import service as administration
from app.modules.catalog import service as catalog
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
from app.modules.scheduling.models import ClassSession, ClassSessionParticipant
from app.modules.scheduling.states import (
    ROOM_OCCUPYING_STATES,
    STUDENT_OCCUPYING_STATES,
    TUTOR_OCCUPYING_STATES,
    SessionState,
)
from app.modules.staff import service as staff

OVERLAP_IDS_LIMIT: Final = 10
"""At most this many overlapping session ids in a conflict's details."""


@dataclass(frozen=True)
class SlotCandidate:
    """What a session to be written is made of, for the checks."""

    kind: str
    student_ids: tuple[uuid.UUID, ...]
    subject_id: uuid.UUID
    tutor_user_id: uuid.UUID | None
    classroom_id: uuid.UUID | None
    starts_at: datetime
    ends_at: datetime


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
    session: AsyncSession, candidate: SlotCandidate, slot: LocalSlot
) -> list[Conflict]:
    conflicts: list[Conflict] = []
    for student_id in candidate.student_ids:
        students = await _overlap_ids(
            session,
            slot,
            STUDENT_OCCUPYING_STATES,
            ClassSessionParticipant.student_id,
            student_id,
        )
        if students:
            conflicts.append(
                Conflict(
                    STUDENT_OVERLAP,
                    {"student_id": str(student_id), "class_session_ids": students},
                )
            )
    if candidate.tutor_user_id is not None:
        tutors = await _overlap_ids(
            session,
            slot,
            TUTOR_OCCUPYING_STATES,
            ClassSession.tutor_user_id,
            candidate.tutor_user_id,
        )
        if tutors:
            conflicts.append(Conflict(TUTOR_OVERLAP, {"class_session_ids": tutors}))
    if candidate.classroom_id is not None:
        rooms = await _overlap_ids(
            session,
            slot,
            ROOM_OCCUPYING_STATES,
            ClassSession.classroom_id,
            candidate.classroom_id,
        )
        if rooms:
            conflicts.append(
                Conflict(
                    ROOM_OVERLAP,
                    {
                        "classroom_id": str(candidate.classroom_id),
                        "class_session_ids": rooms,
                    },
                )
            )
    return conflicts


async def _capacity_conflicts(
    session: AsyncSession, candidate: SlotCandidate
) -> list[Conflict]:
    """D4 / ADR-0005 D7: one path for every kind. An individual session never
    exceeds a room (a room holds at least 1, #39), so nothing comes back
    here; #56 / #65 make it reachable."""
    participants = len(candidate.student_ids)
    room_capacity = None
    if candidate.classroom_id is not None:
        room_capacity = (
            await catalog.get_classroom(session, candidate.classroom_id)
        ).capacity
    return [
        Conflict(code, {"participants": participants, "room_capacity": room_capacity})
        for code in capacity_violations(
            candidate.kind, participants, group_max=None, room_capacity=room_capacity
        )
    ]


async def _tutor_conflicts(
    session: AsyncSession, candidate: SlotCandidate, slot: LocalSlot, tutor: uuid.UUID
) -> list[Conflict]:
    conflicts: list[Conflict] = []
    availability = await staff.is_tutor_available_at(
        session, tutor, slot.starts_at, slot.ends_at
    )
    reasons = tutor_unavailable_reasons(availability.reasons)
    if reasons:
        conflicts.append(Conflict(TUTOR_UNAVAILABLE, {"reasons": reasons}))
    if not await staff.is_qualified(session, tutor, candidate.subject_id):
        conflicts.append(
            Conflict(TUTOR_NOT_QUALIFIED, {"subject_id": str(candidate.subject_id)})
        )
    return conflicts


def _hours_of(item: Any) -> tuple[int, time, time]:
    return (
        item.weekday,
        time.fromisoformat(item.opens_at),
        time.fromisoformat(item.closes_at),
    )


async def _center_conflicts(
    session: AsyncSession, slot: LocalSlot, *, check_center_closed: bool
) -> list[Conflict]:
    conflicts: list[Conflict] = []
    if check_center_closed and await catalog.is_center_closed_on(
        session, slot.local_date
    ):
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


async def _notice_conflicts(
    session: AsyncSession, slot: LocalSlot, now: datetime
) -> list[Conflict]:
    """D10: the booking notice of the policy in force at ``now`` (#61); a
    start before ``now`` breaks it too, and exactly the notice is fine."""
    policy = await administration.get_scheduling_policy(session, now)
    if not administration.violates_booking_notice(
        policy, starts_at=slot.starts_at, at=now
    ):
        return []
    return [
        Conflict(NOTICE_WINDOW, {"booking_notice_hours": policy.booking_notice_hours})
    ]


async def collect_conflicts(
    session: AsyncSession,
    candidate: SlotCandidate,
    *,
    zone: str,
    now: datetime,
    check_center_closed: bool,
) -> list[Conflict]:
    """Every hard and soft conflict of ``candidate``, sorted (see the module
    docstring). The tutor checks run only with a tutor, the room checks only
    with a room."""
    slot = local_slot(candidate.starts_at, candidate.ends_at, zone)
    conflicts = await _overlap_conflicts(session, candidate, slot)
    conflicts += await _capacity_conflicts(session, candidate)
    if candidate.tutor_user_id is not None:
        conflicts += await _tutor_conflicts(
            session, candidate, slot, candidate.tutor_user_id
        )
    if candidate.classroom_id is not None and not await catalog.is_classroom_available(
        session, candidate.classroom_id, slot.weekday, slot.start, slot.end
    ):
        conflicts.append(
            Conflict(ROOM_UNAVAILABLE, {"classroom_id": str(candidate.classroom_id)})
        )
    conflicts += await _center_conflicts(
        session, slot, check_center_closed=check_center_closed
    )
    conflicts += await _notice_conflicts(session, slot, now)
    return sort_conflicts(conflicts)
