"""Slot suggestions, with the database (#50 B).

**Suggestions** (:func:`suggest_slots`, ``POST
/api/v1/class-sessions/suggestions``), in this order after the route's
``401``/``403`` and the body's ``422`` (``limit``, ``search_range_invalid``
and ``search_range_too_long`` when both dates are sent):

1. the center zone, a plain read (no ``FOR SHARE``, no advisory lock:
   nothing is written);
2. ``422`` for the bounds of the requested start and end, as the create;
3. the window: ``search_from`` defaults to the requested start's local date
   and ``search_to`` to ``search_from`` + 6 days; a defaulted window is
   checked like a sent one (``422`` on ``search_to``);
4. ``422`` for the references, as the create (codes and collector), without
   locking the subject;
5. the search: a tutor not qualified for the subject gives no slot without
   reading more; otherwise everything the in-memory check needs is loaded
   once (:func:`load_slot_context`) and the candidates are checked with
   :func:`~app.modules.scheduling.suggestions.slot_conflicts`;
6. ``200`` with the slots.

At most 20 statements, whatever the window's length: nothing is read per
candidate or per day. Nothing is inserted, updated, audited or notified.

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.core import clock
from app.core.errors import FieldError
from app.modules.administration import service as administration
from app.modules.catalog import service as catalog
from app.modules.scheduling.booking import references, validate_bounds
from app.modules.scheduling.errors import SessionValidationError
from app.modules.scheduling.models import ClassSession, ClassSessionParticipant
from app.modules.scheduling.schemas import (
    SEARCH_DEFAULT_DAYS,
    SlotSuggestionRequest,
    SlotSuggestions,
    SuggestedSlot,
    search_range_error,
)
from app.modules.scheduling.states import (
    ROOM_OCCUPYING_STATES,
    STUDENT_OCCUPYING_STATES,
    SessionState,
)
from app.modules.scheduling.suggestions import (
    Busy,
    OperatingDay,
    SlotContext,
    candidate_slots,
    local_dates,
    search_slots,
)
from app.modules.scheduling.times import scheduled_minutes
from app.modules.scheduling.workload import WorkloadSession
from app.modules.scheduling.workload_sessions import (
    tutor_workload_sessions,
    wire_warnings,
)
from app.modules.staff import service as staff


def _states(states: frozenset[SessionState]) -> list[str]:
    return sorted(state.value for state in states)


# --- Suggestions ---------------------------------------------------------------------


def _local_midnight(day: date, zone: ZoneInfo) -> datetime:
    return datetime.combine(day, time(0), tzinfo=zone).astimezone(UTC)


async def _busy(
    session: AsyncSession,
    column: InstrumentedAttribute[Any],
    value: uuid.UUID,
    states: frozenset[SessionState],
    low: datetime,
    high: datetime,
) -> tuple[Busy, ...]:
    query = select(ClassSession.id, ClassSession.starts_at, ClassSession.ends_at).where(
        column == value,
        ClassSession.state.in_(_states(states)),
        ClassSession.starts_at < high,
        ClassSession.ends_at > low,
    )
    if column is ClassSessionParticipant.student_id:
        query = query.join(
            ClassSessionParticipant,
            ClassSessionParticipant.class_session_id == ClassSession.id,
        )
    rows = await session.execute(
        query.order_by(ClassSession.starts_at, ClassSession.id)
    )
    return tuple(Busy(*row) for row in rows)


def _closed_dates(closures: Any, first: date, last: date) -> frozenset[date]:
    window = set(local_dates(first, last))
    closed: set[date] = set()
    for closure in closures:
        closed.update(
            day
            for day in local_dates(closure.starts_on, closure.ends_on)
            if day in window
        )
    return frozenset(closed)


async def load_slot_context(
    session: AsyncSession,
    body: SlotSuggestionRequest,
    zone: str,
    search_from: date,
    search_to: date,
    *,
    tutor_qualified: bool,
) -> SlotContext:
    """Everything the in-memory check needs for the window, once: the
    student's, tutor's and room's occupying sessions, the tutor's rules and
    limits, the room's windows and capacity, the closures, the operating
    hours and the booking-notice policy in force now."""
    tz = ZoneInfo(zone)
    low = _local_midnight(search_from, tz)
    high = _local_midnight(search_to + timedelta(days=1), tz)
    student_sessions = await _busy(
        session,
        ClassSessionParticipant.student_id,
        body.student_id,
        STUDENT_OCCUPYING_STATES,
        low,
        high,
    )
    room_sessions: tuple[Busy, ...] = ()
    room_windows: tuple[tuple[int, time, time], ...] = ()
    room_capacity = None
    if body.classroom_id is not None:
        room_sessions = await _busy(
            session,
            ClassSession.classroom_id,
            body.classroom_id,
            ROOM_OCCUPYING_STATES,
            low,
            high,
        )
        room_windows = await catalog.classroom_weekly_windows(
            session, body.classroom_id
        )
        room_capacity = (
            await catalog.get_classroom(session, body.classroom_id)
        ).capacity
    tutor_sessions: tuple[WorkloadSession, ...] = ()
    tutor_rules = None
    limits = staff.TeachingLimits()
    if body.tutor_user_id is not None:
        limits = await staff.teaching_limits(session, body.tutor_user_id)
        tutor_sessions = await tutor_workload_sessions(
            session, body.tutor_user_id, search_from, search_to
        )
        tutor_rules = await staff.tutor_availability_rules(
            session, body.tutor_user_id, search_from, search_to
        )
    closures = await catalog.closures_between(session, search_from, search_to)
    settings = await administration.get_business_settings(session)
    now = clock.now()
    policy = await administration.get_scheduling_policy(session, now)
    return SlotContext(
        zone=zone,
        kind=body.kind,
        student_id=body.student_id,
        subject_id=body.subject_id,
        tutor_user_id=body.tutor_user_id,
        classroom_id=body.classroom_id,
        student_sessions=student_sessions,
        tutor_sessions=tutor_sessions,
        room_sessions=room_sessions,
        tutor_rules=tutor_rules,
        tutor_qualified=tutor_qualified,
        limits=limits,
        room_windows=room_windows,
        room_capacity=room_capacity,
        closed_dates=_closed_dates(closures, search_from, search_to),
        operating_hours=tuple(
            OperatingDay(item.weekday, item.opens_at, item.closes_at)
            for item in settings.operating_hours
        ),
        policy=policy,
        now=now,
    )


def _window(body: SlotSuggestionRequest, zone: str) -> tuple[date, date]:
    """The search window (B1 defaults); a defaulted one that is out of range
    is ``422`` on ``search_to``."""
    search_from = body.search_from or body.starts_at.astimezone(ZoneInfo(zone)).date()
    search_to = body.search_to or search_from + timedelta(days=SEARCH_DEFAULT_DAYS - 1)
    error = search_range_error(search_from, search_to)
    if error is not None:
        field_error: FieldError = {
            "path": ["body", "search_to"],
            "code": error.type,
            "message": error.message(),
        }
        raise SessionValidationError([field_error])
    return search_from, search_to


async def suggest_slots(
    session: AsyncSession, body: SlotSuggestionRequest
) -> SlotSuggestions:
    """``POST /api/v1/class-sessions/suggestions``: see the module docstring."""
    zone = await administration.get_center_time_zone(session)
    validate_bounds(body, zone)
    search_from, search_to = _window(body, zone)
    await references(session, body, lock=False)
    slots: list[SuggestedSlot] = []
    qualified = body.tutor_user_id is None or await staff.is_qualified(
        session, body.tutor_user_id, body.subject_id
    )
    if qualified:
        context = await load_slot_context(
            session, body, zone, search_from, search_to, tutor_qualified=True
        )
        candidates = candidate_slots(
            search_from,
            search_to,
            zone,
            scheduled_minutes(body.starts_at, body.ends_at),
            body.starts_at,
        )
        slots = [
            SuggestedSlot(
                starts_at=found.starts_at,
                ends_at=found.ends_at,
                warnings=wire_warnings(found.warnings),
            )
            for found in search_slots(context, candidates, body.starts_at, body.limit)
        ]
    return SlotSuggestions(
        search_from=search_from, search_to=search_to, limit=body.limit, slots=slots
    )
