"""Sessions an availability change affects, and their reassignment flags
(M4-18 #63 B3, B4, D3 step 6, E, F2; spec §3; ADR-0005 D2, D3).

**Impact** (:func:`affected_tutor_sessions`): the tutor's future
``offered`` / ``confirmed`` sessions that the current rules cover and the
proposed rules don't (the pure rule
``staff.service.is_affected``), ordered by ``starts_at`` then ``id``, with
their subject names. Three statements whatever the number of sessions: the
zone, the sessions (bounded to the dates an exception change touches, with
the 480-minute maximum duration as margin) and the subjects.
:data:`AVAILABILITY_IMPACT_SOURCE` is the ``staff`` seam's implementation
(``app.wiring`` registers it): it takes :func:`~app.modules.scheduling.locks.
lock_tutor` first (four statements in all). Materialized series
occurrences are sessions like any other; occurrences beyond the horizon
don't exist yet, and the nightly ``materialize_series`` checks the tutor's
availability when it creates them (#53 D4).

**Flags** (:func:`flag_sessions_for_reassignment`, the approval's step 6):
the sessions ``FOR UPDATE`` in ascending id, each still the tutor's,
``offered`` or ``confirmed`` and not started (else skipped); every
``offered`` one has its pending session-scope offers withdrawn (#52 E4,
``withdraw_pending_offers``: the offers are locked after the sessions, #52
A4) and goes ``offered → unassigned`` (tutor cleared); a ``confirmed`` one
keeps its state and tutor (E1). One flag row per session. A row covered by
a pending **series** offer stays covered (the #55 cancellation precedent;
#233 B6). Flags are open by the read-time rule of
:mod:`~app.modules.scheduling.reassignment_rules`
(:func:`open_reassignment_flags`; the session detail's ``reassignment``
and the calendar's ``reassignment_needed`` marker are staff-only).

**F2** (:func:`notify_reassignment_needed`): one
``scheduling.reassignment_needed`` notification per approval that flagged
sessions, to every active holder of ``schedule.assign``.

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from collections.abc import Collection, Sequence
from datetime import date, datetime, time, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.modules.administration import service as administration
from app.modules.catalog import service as catalog
from app.modules.identity import service as identity
from app.modules.scheduling.locks import lock_tutor
from app.modules.scheduling.models import (
    REASSIGNMENT_AVAILABILITY_CHANGE,
    ClassSession,
    ClassSessionReassignmentFlag,
)
from app.modules.scheduling.offer_flow import lock_sessions, withdraw_pending_offers
from app.modules.scheduling.offer_records import SCHEDULE_ASSIGN, rfc3339
from app.modules.scheduling.reassignment_rules import open_flag_condition
from app.modules.scheduling.sessions import transition_class_session
from app.modules.scheduling.states import SessionState
from app.modules.scheduling.times import MAX_DURATION_MINUTES
from app.modules.staff import service as staff

REASSIGNMENT_NEEDED_NOTIFICATION: Final = "scheduling.reassignment_needed"

IMPACT_STATES: Final = (SessionState.OFFERED.value, SessionState.CONFIRMED.value)


# --- Impact (B3, B4) -----------------------------------------------------------------


def _bounds(dates: tuple[date, date], zone: str) -> tuple[datetime, datetime]:
    """The ``starts_at`` range of sessions that can touch the inclusive
    local ``dates``: from the first date's midnight less the longest
    session, to the midnight after the last date."""
    tz = ZoneInfo(zone)
    first, last = dates
    low = datetime.combine(first, time(0), tzinfo=tz) - timedelta(
        minutes=MAX_DURATION_MINUTES
    )
    high = datetime.combine(last + timedelta(days=1), time(0), tzinfo=tz)
    return low, high


async def affected_tutor_sessions(
    session: AsyncSession,
    tutor_user_id: uuid.UUID,
    *,
    current: staff.TutorAvailabilityRules,
    proposed: staff.TutorAvailabilityRules,
    now: datetime,
    dates: tuple[date, date] | None = None,
) -> list[staff.AffectedSession]:
    """See the module docstring (no lock; three statements)."""
    zone = await administration.get_center_time_zone(session)
    query = (
        select(ClassSession)
        .where(ClassSession.tutor_user_id == tutor_user_id)
        .where(ClassSession.state.in_(IMPACT_STATES))
        .where(ClassSession.starts_at > now)
        .order_by(ClassSession.starts_at, ClassSession.id)
    )
    if dates is not None:
        low, high = _bounds(dates, zone)
        query = query.where(ClassSession.starts_at >= low).where(
            ClassSession.starts_at < high
        )
    rows = [
        row
        for row in await session.scalars(query)
        if staff.is_affected(
            state=row.state,
            starts_at=row.starts_at,
            ends_at=row.ends_at,
            now=now,
            zone=zone,
            current=current,
            proposed=proposed,
        )
    ]
    subjects = await catalog.subject_references(
        session, {row.subject_id for row in rows}
    )
    return [
        staff.AffectedSession(
            id=row.id,
            state=row.state,
            starts_at=row.starts_at,
            ends_at=row.ends_at,
            subject_name=subjects[row.subject_id].name,
            series_id=row.series_id,
        )
        for row in rows
    ]


class SchedulingImpactSource:
    """The ``staff`` impact seam (``staff.service.AvailabilityImpactSource``):
    the tutor lock, then :func:`affected_tutor_sessions` at now."""

    async def affected_sessions(
        self,
        session: AsyncSession,
        tutor_user_id: uuid.UUID,
        *,
        current: staff.TutorAvailabilityRules,
        proposed: staff.TutorAvailabilityRules,
        dates: tuple[date, date] | None,
    ) -> list[staff.AffectedSession]:
        await lock_tutor(session, tutor_user_id)
        return await affected_tutor_sessions(
            session,
            tutor_user_id,
            current=current,
            proposed=proposed,
            now=clock.now(),
            dates=dates,
        )


AVAILABILITY_IMPACT_SOURCE: Final = SchedulingImpactSource()


# --- Flags (D3 step 6, E) ------------------------------------------------------------


async def flag_sessions_for_reassignment(
    session: AsyncSession,
    *,
    tutor_user_id: uuid.UUID,
    class_session_ids: Sequence[uuid.UUID],
    source_id: uuid.UUID,
    actor_id: uuid.UUID,
) -> list[ClassSession]:
    """Flag the affected sessions (see the module docstring); returns the
    flagged rows by ``starts_at``. The caller holds the tutor lock."""
    now = clock.now()
    rows = [
        row
        for row in await lock_sessions(session, class_session_ids)
        if row.tutor_user_id == tutor_user_id
        and row.state in IMPACT_STATES
        and row.starts_at > now
    ]
    offered = [row for row in rows if row.state == SessionState.OFFERED]
    await withdraw_pending_offers(
        session, [row.id for row in offered], by_user_id=actor_id
    )
    for row in offered:
        await transition_class_session(
            session,
            row.id,
            to_state=SessionState.UNASSIGNED,
            expected_version=row.version,
        )
    session.add_all(
        ClassSessionReassignmentFlag(
            class_session_id=row.id,
            tutor_user_id=tutor_user_id,
            reason=REASSIGNMENT_AVAILABILITY_CHANGE,
            source_id=source_id,
            created_by_user_id=actor_id,
        )
        for row in rows
    )
    await session.flush()
    return sorted(rows, key=lambda row: (row.starts_at, row.id))


async def open_reassignment_flags(
    session: AsyncSession, class_session_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, ClassSessionReassignmentFlag]:
    """The newest open flag of each of these sessions that has one (one
    statement, none for an empty collection). Staff-only data: family
    views (#67) never show it."""
    if not class_session_ids:
        return {}
    flags = await session.scalars(
        select(ClassSessionReassignmentFlag)
        .join(
            ClassSession,
            ClassSession.id == ClassSessionReassignmentFlag.class_session_id,
        )
        .where(ClassSession.id.in_(set(class_session_ids)))
        .where(open_flag_condition(clock.now()))
        .order_by(
            ClassSessionReassignmentFlag.created_at,
            ClassSessionReassignmentFlag.id,
        )
    )
    return {flag.class_session_id: flag for flag in flags}


# --- F2 ------------------------------------------------------------------------------


class ReassignmentNeededParams(BaseModel):
    """The params of ``scheduling.reassignment_needed``."""

    tutor_user_id: str
    tutor_name: str
    session_count: int
    first_starts_at: str


def register_reassignment_notifications() -> None:
    """Register ``scheduling.reassignment_needed`` (again: a no-op)."""
    administration.register_notification_type(
        REASSIGNMENT_NEEDED_NOTIFICATION, ReassignmentNeededParams
    )


async def notify_reassignment_needed(
    session: AsyncSession,
    *,
    tutor_user_id: uuid.UUID,
    flagged: Sequence[ClassSession],
) -> None:
    """F2: one notification to every active holder of ``schedule.assign``
    (none when nothing was flagged)."""
    if not flagged:
        return
    accounts = await identity.get_account_references(session, [tutor_user_id])
    account = accounts.get(tutor_user_id)
    params = ReassignmentNeededParams(
        tutor_user_id=str(tutor_user_id),
        tutor_name=account.display_name if account is not None else "",
        session_count=len(flagged),
        first_starts_at=rfc3339(min(row.starts_at for row in flagged)),
    )
    for user_id in await identity.active_user_ids_with_permission(
        session, SCHEDULE_ASSIGN
    ):
        await administration.create_notification(
            session,
            recipient_user_id=user_id,
            notification_type=REASSIGNMENT_NEEDED_NOTIFICATION,
            params=params,
        )
