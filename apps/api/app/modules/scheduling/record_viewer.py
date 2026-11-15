"""Loading the group and cohort record policy (#246 A1-A3; ADR-0005 D2, D9;
ADR-0007 D17). The rules are pure, in :mod:`~app.modules.scheduling.permissions`;
this module loads their facts and builds their SQL forms.

:func:`record_viewer` runs no statement for staff (the read permission) or
for a caller with neither family role; one for the family facts
(``families.service.family_schedule_facts``, whatever the roles) and one
more, the center zone, only when a linked student has not given the
guardian the academic consent (whether they are a minor decides). The facts
are read on every request: an unlink, a deactivated guardian profile or a
revoked consent applies at once.

:func:`taught_series_condition` is A2 in SQL: a producing series (no last
occurrence, or one on or after its local today) whose **current** tutor is
the caller, with a ``pending`` or ``accepted`` assignment (the same set as
``permissions.TAUGHT_ASSIGNMENT_STATUSES``).
"""

import uuid
from datetime import date, datetime
from typing import Final

from sqlalchemy import ColumnElement, Date, and_, cast, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.authz import Principal
from app.modules.administration import service as administration
from app.modules.families import service as families
from app.modules.scheduling.models import ClassSeries, ClassSession
from app.modules.scheduling.permissions import (
    STAFF_VIEWER,
    TAUGHT_ASSIGNMENT_STATUSES,
    RecordViewer,
    ages_needed,
    teaching_tutor_id,
    visible_family_students,
)
from app.modules.scheduling.states import SessionState


async def record_viewer(
    session: AsyncSession, principal: Principal, *, staff_permission: str
) -> RecordViewer:
    """The A1-A3 facts of ``principal``: staff when they hold
    ``staff_permission``; otherwise their visible students and, with the
    ``tutor`` role, their user id. See the module docstring for the
    statements."""
    if principal.has(staff_permission):
        return STAFF_VIEWER
    loaded = await families.family_schedule_facts(session, principal)
    needed = ages_needed(principal, loaded.facts)
    minors: frozenset[uuid.UUID] = frozenset()
    if needed:
        today = clock.today_in(await administration.get_center_time_zone(session))
        minors = frozenset(
            student_id
            for student_id in needed
            if families.is_minor(loaded.birthdates[student_id], today)
        )
    return RecordViewer(
        staff=False,
        students=visible_family_students(principal, loaded.facts, minors=minors),
        tutor_id=teaching_tutor_id(principal),
    )


NOT_DONE: Final = ClassSession.state != SessionState.COMPLETED.value
"""An upcoming occurrence (``next_occurrences``) is never a completed one,
whatever the visibility rule allows (#246 A4)."""


def series_local_today(now: datetime) -> ColumnElement[date]:
    """The series' local today (its ``time_zone`` is the center zone), from
    the application clock."""
    return cast(func.timezone(ClassSeries.time_zone, literal(now)), Date)


def producing_series(now: datetime) -> ColumnElement[bool]:
    """The series still produces occurrences (#56 H1)."""
    return or_(
        ClassSeries.last_occurrence_on.is_(None),
        ClassSeries.last_occurrence_on >= series_local_today(now),
    )


def taught_series_condition(tutor_id: uuid.UUID, now: datetime) -> ColumnElement[bool]:
    """A2 on ``class_series``: see the module docstring."""
    return and_(
        ClassSeries.tutor_user_id == tutor_id,
        ClassSeries.assignment_status.in_(sorted(TAUGHT_ASSIGNMENT_STATUSES)),
        producing_series(now),
    )


async def taught_cohort_ids(
    session: AsyncSession,
    tutor_id: uuid.UUID,
    *,
    now: datetime,
    cohort_id: uuid.UUID | None = None,
) -> frozenset[uuid.UUID]:
    """The cohorts with a producing ``course`` series taught by ``tutor_id``
    (A2; ``cohort_id``: only that one). One statement."""
    query = select(ClassSeries.cohort_id).where(
        ClassSeries.cohort_id.is_not(None), taught_series_condition(tutor_id, now)
    )
    if cohort_id is not None:
        query = query.where(ClassSeries.cohort_id == cohort_id)
    return frozenset(
        found for found in await session.scalars(query.distinct()) if found
    )
