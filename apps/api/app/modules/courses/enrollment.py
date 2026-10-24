"""Cohort enrollment periods: the ``courses`` half of cohort enrollment (#66
A2, C, D, E2; spec §1, §3, §6, §12 ``CohortEnrollment``; architecture §4.1
"courses: cohort enrollment").

``cohort_enrollments`` holds one row per enrollment **period** of a student
in a cohort: inserted at the enrollment instant, closed once by the
withdrawal (``withdrawn_at``, ``withdrawn_by_user_id``, the optional
reason) and never deleted (a database trigger). Re-enrolling after a
withdrawal is a new row, so M6-07 (#92) bills each period. The participant
rows of the cohort's series and sessions stay in ``scheduling``.

``app.workflows.cohort_enrollment`` composes these functions with
``scheduling.service`` in one Unit of Work and maps the plain outcomes
(:class:`~app.modules.courses.errors.CohortEnrollmentActiveExists`,
:class:`~app.modules.courses.errors.CohortEnrollmentNotActive`, ``None`` for
an unknown enrollment) to the shared #56 codes ``already_enrolled``,
``enrollment_not_active`` and ``enrollment_not_found``. This module never
imports ``scheduling`` and takes no lock but its own rows: the workflow
takes ``scheduling.service.lock_students``, the zone and
:func:`~app.modules.courses.service.lock_cohort` first (#66 H1).

The reads for the M6-07 sweeper (:func:`list_cohort_enrollment_periods`),
the roster (:func:`cohort_enrollment_rows`) and the record policy
(:func:`active_cohort_ids_of_students`) are here too.

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import ensure_version
from app.modules.courses.errors import (
    CohortEnrollmentActiveExists,
    CohortEnrollmentNotActive,
)
from app.modules.courses.models import (
    COHORT_ENROLLMENT_ACTIVE_UNIQUE,
    CohortEnrollment,
    CourseCohort,
)


@dataclass(frozen=True)
class CohortEnrollmentRow:
    """One enrollment period as stored."""

    id: uuid.UUID
    cohort_id: uuid.UUID
    student_id: uuid.UUID
    enrolled_at: datetime
    enrolled_by_user_id: uuid.UUID
    withdrawn_at: datetime | None
    withdrawn_by_user_id: uuid.UUID | None
    withdrawal_reason: str | None
    version: int


@dataclass(frozen=True)
class CohortEnrollmentPeriod:
    """One enrollment period for the M6-07 sweeper (#66 D3). ``changed_at``
    is its latest change (``withdrawn_at``, else ``enrolled_at``)."""

    enrollment_id: uuid.UUID
    cohort_id: uuid.UUID
    student_id: uuid.UUID
    enrolled_at: datetime
    enrolled_by_user_id: uuid.UUID
    withdrawn_at: datetime | None
    withdrawn_by_user_id: uuid.UUID | None
    changed_at: datetime


def _row(enrollment: CohortEnrollment) -> CohortEnrollmentRow:
    return CohortEnrollmentRow(
        id=enrollment.id,
        cohort_id=enrollment.cohort_id,
        student_id=enrollment.student_id,
        enrolled_at=enrollment.enrolled_at,
        enrolled_by_user_id=enrollment.enrolled_by_user_id,
        withdrawn_at=enrollment.withdrawn_at,
        withdrawn_by_user_id=enrollment.withdrawn_by_user_id,
        withdrawal_reason=enrollment.withdrawal_reason,
        version=enrollment.version,
    )


async def active_cohort_enrollments(
    session: AsyncSession, cohort_id: uuid.UUID
) -> dict[uuid.UUID, uuid.UUID]:
    """The cohort's active enrollments, ``{student_id: enrollment_id}`` (one
    statement): the count is the E1 capacity base, a withdrawal frees a
    seat."""
    rows = await session.execute(
        select(CohortEnrollment.student_id, CohortEnrollment.id).where(
            CohortEnrollment.cohort_id == cohort_id,
            CohortEnrollment.withdrawn_at.is_(None),
        )
    )
    return {student_id: enrollment_id for student_id, enrollment_id in rows}


async def active_cohort_enrollment_count(
    session: AsyncSession, cohort_id: uuid.UUID
) -> int:
    """The number of active enrollments of the cohort (one statement)."""
    count = await session.scalar(
        select(func.count()).where(
            CohortEnrollment.cohort_id == cohort_id,
            CohortEnrollment.withdrawn_at.is_(None),
        )
    )
    return int(count or 0)


async def active_cohort_enrollment_counts(
    session: AsyncSession, cohort_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, int]:
    """The active enrollment count of each cohort (one statement, none for
    an empty collection); cohorts without one are left out."""
    if not cohort_ids:
        return {}
    rows = await session.execute(
        select(CohortEnrollment.cohort_id, func.count())
        .where(
            CohortEnrollment.cohort_id.in_(set(cohort_ids)),
            CohortEnrollment.withdrawn_at.is_(None),
        )
        .group_by(CohortEnrollment.cohort_id)
    )
    return {cohort_id: int(count) for cohort_id, count in rows}


async def insert_cohort_enrollment(
    session: AsyncSession,
    *,
    cohort_id: uuid.UUID,
    student_id: uuid.UUID,
    enrolled_at: datetime,
    actor_id: uuid.UUID,
) -> uuid.UUID:
    """Insert an active enrollment period (flush, audited ``create``).
    :class:`CohortEnrollmentActiveExists` when ``uq_cohort_enrollments_active``
    refuses it (a race the cohort lock prevents)."""
    enrollment = CohortEnrollment(
        id=uuid.uuid7(),
        cohort_id=cohort_id,
        student_id=student_id,
        enrolled_at=enrolled_at,
        enrolled_by_user_id=actor_id,
    )
    session.add(enrollment)
    try:
        await session.flush()
    except IntegrityError as exc:
        if COHORT_ENROLLMENT_ACTIVE_UNIQUE in str(exc.orig):
            raise CohortEnrollmentActiveExists() from None
        raise
    return enrollment.id


async def find_cohort_enrollment(
    session: AsyncSession, enrollment_id: uuid.UUID
) -> CohortEnrollmentRow | None:
    """The enrollment, unlocked (the withdrawal reads its student before the
    locks), or ``None``."""
    found = await session.get(CohortEnrollment, enrollment_id)
    return None if found is None else _row(found)


async def withdraw_cohort_enrollment(
    session: AsyncSession,
    enrollment_id: uuid.UUID,
    *,
    version: int,
    withdrawn_at: datetime,
    actor_id: uuid.UUID,
    reason: str | None,
) -> CohortEnrollmentRow:
    """Close the period once (#66 D1 steps 4-5), on a row the caller locked
    with :func:`lock_cohort_enrollment` (and checked it is the cohort's):
    ``409 version_conflict`` → :class:`CohortEnrollmentNotActive` when
    already withdrawn → the withdrawal columns (flush, audited
    ``update``)."""
    enrollment = await session.get(CohortEnrollment, enrollment_id)
    if enrollment is None:  # pragma: no cover - the caller locked it
        raise LookupError(f"no cohort enrollment {enrollment_id}")
    ensure_version(enrollment, version)
    if enrollment.withdrawn_at is not None:
        raise CohortEnrollmentNotActive()
    enrollment.withdrawn_at = withdrawn_at
    enrollment.withdrawn_by_user_id = actor_id
    enrollment.withdrawal_reason = reason
    await session.flush()
    return _row(enrollment)


async def _lock(
    session: AsyncSession, enrollment_id: uuid.UUID
) -> CohortEnrollment | None:
    found: CohortEnrollment | None = await session.scalar(
        select(CohortEnrollment)
        .where(CohortEnrollment.id == enrollment_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return found


async def lock_cohort_enrollment(
    session: AsyncSession, enrollment_id: uuid.UUID
) -> CohortEnrollmentRow | None:
    """The enrollment re-read ``FOR UPDATE``, or ``None`` when unknown."""
    found = await _lock(session, enrollment_id)
    return None if found is None else _row(found)


async def cohort_enrollment_rows(
    session: AsyncSession,
    *,
    cohort_id: uuid.UUID,
    enrollment_ids: Collection[uuid.UUID] | None = None,
) -> list[CohortEnrollmentRow]:
    """The cohort's enrollment periods (``enrollment_ids``: only these), in
    no particular order (one statement; re-read)."""
    query = select(CohortEnrollment).where(CohortEnrollment.cohort_id == cohort_id)
    if enrollment_ids is not None:
        query = query.where(CohortEnrollment.id.in_(set(enrollment_ids)))
    rows = await session.scalars(query.execution_options(populate_existing=True))
    return [_row(row) for row in rows]


def has_active_enrollment(student_ids: Collection[uuid.UUID]) -> Exists:
    """An ``EXISTS`` on an active enrollment of one of ``student_ids`` in the
    cohort row of the enclosing query (the G2 record policy)."""
    return (
        select(CohortEnrollment.id)
        .where(
            CohortEnrollment.cohort_id == CourseCohort.id,
            CohortEnrollment.student_id.in_(set(student_ids)),
            CohortEnrollment.withdrawn_at.is_(None),
        )
        .exists()
    )


async def cohort_visible_to(
    session: AsyncSession, cohort_id: uuid.UUID, student_ids: Collection[uuid.UUID]
) -> bool:
    """Whether one of ``student_ids`` has an active enrollment in the cohort
    (one statement; none and ``False`` without students)."""
    if not student_ids:
        return False
    found = await session.scalar(
        select(CohortEnrollment.id)
        .where(
            CohortEnrollment.cohort_id == cohort_id,
            CohortEnrollment.student_id.in_(set(student_ids)),
            CohortEnrollment.withdrawn_at.is_(None),
        )
        .limit(1)
    )
    return found is not None


async def list_cohort_enrollment_periods(
    session: AsyncSession, *, changed_since: datetime, limit: int
) -> list[CohortEnrollmentPeriod]:
    """The enrollment periods whose ``enrolled_at`` or ``withdrawn_at`` is at
    or after ``changed_since``, by their latest change then id, at most
    ``limit`` (one statement): the M6-07 (#92) sweeper's input (#66 D3)."""
    changed_at = func.coalesce(
        CohortEnrollment.withdrawn_at, CohortEnrollment.enrolled_at
    )
    rows = await session.execute(
        select(CohortEnrollment, changed_at.label("changed_at"))
        .where(
            or_(
                CohortEnrollment.enrolled_at >= changed_since,
                CohortEnrollment.withdrawn_at >= changed_since,
            )
        )
        .order_by(changed_at, CohortEnrollment.id)
        .limit(limit)
    )
    return [
        CohortEnrollmentPeriod(
            enrollment_id=row.id,
            cohort_id=row.cohort_id,
            student_id=row.student_id,
            enrolled_at=row.enrolled_at,
            enrolled_by_user_id=row.enrolled_by_user_id,
            withdrawn_at=row.withdrawn_at,
            withdrawn_by_user_id=row.withdrawn_by_user_id,
            changed_at=at,
        )
        for row, at in rows
    ]
