"""The tutor workload, with the database (#50 A6, A7).

The counted sessions of a tutor (:func:`tutor_workload_sessions`) are read
in **one statement**, from the Monday of the first ISO week through the
Sunday of the last, widened by ``BREAK_MAX`` minutes at both ends; the rules
are the pure :mod:`~app.modules.scheduling.workload`. The create path (#48
C2) computes the warnings (:func:`session_warnings`) after the conflict
checks and before the insert: at most two statements with a tutor (the
limits, then the sessions, only when a limit is set), none without one. Not
locked (#50 A8). #51 and #58 reuse both.

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from collections.abc import Iterable
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.administration import service as administration
from app.modules.scheduling.models import ClassSession
from app.modules.scheduling.schemas import SchedulingWarning
from app.modules.scheduling.states import TUTOR_OCCUPYING_STATES
from app.modules.scheduling.workload import (
    WorkloadSession,
    WorkloadWarning,
    workload_range,
    workload_warnings,
)
from app.modules.staff import service as staff


async def tutor_workload_sessions(
    session: AsyncSession,
    tutor_user_id: uuid.UUID,
    first_local_date: date,
    last_local_date: date,
) -> tuple[WorkloadSession, ...]:
    """The tutor's sessions in ``TUTOR_OCCUPYING_STATES`` overlapping
    :func:`~app.modules.scheduling.workload.workload_range` of the local
    dates (the ISO weeks holding them, widened by ``BREAK_MAX``), by start.
    One statement (the center zone comes from the Unit of Work's cache when
    it was read before)."""
    zone = await administration.get_center_time_zone(session)
    low, high = workload_range(first_local_date, last_local_date, zone)
    rows = await session.execute(
        select(
            ClassSession.id,
            ClassSession.starts_at,
            ClassSession.ends_at,
            ClassSession.scheduled_minutes,
        )
        .where(
            ClassSession.tutor_user_id == tutor_user_id,
            ClassSession.state.in_(sorted(s.value for s in TUTOR_OCCUPYING_STATES)),
            ClassSession.starts_at < high,
            ClassSession.ends_at > low,
        )
        .order_by(ClassSession.starts_at, ClassSession.id)
    )
    return tuple(WorkloadSession(*row) for row in rows)


def _has_limits(limits: staff.TeachingLimits) -> bool:
    return any(
        value is not None
        for value in (
            limits.daily_limit_minutes,
            limits.weekly_limit_minutes,
            limits.required_break_minutes,
        )
    )


def wire_warnings(warnings: Iterable[WorkloadWarning]) -> list[SchedulingWarning]:
    """The ``warnings[]`` of a ``2xx`` body."""
    return [SchedulingWarning.model_validate(w.as_wire()) for w in warnings]


async def session_warnings(
    session: AsyncSession,
    tutor_user_id: uuid.UUID,
    starts_at: datetime,
    ends_at: datetime,
    zone: str,
) -> list[SchedulingWarning]:
    """The workload warnings of a new session of the tutor (#50 A7): the
    limits, then (only when one is set) the counted sessions of the
    session's ISO week. At most two statements."""
    limits = await staff.teaching_limits(session, tutor_user_id)
    if not _has_limits(limits):
        return []
    local_date = starts_at.astimezone(ZoneInfo(zone)).date()
    sessions = await tutor_workload_sessions(
        session, tutor_user_id, local_date, local_date
    )
    return wire_warnings(
        workload_warnings(limits, sessions, (starts_at, ends_at), zone)
    )
