"""Cohort enrollment periods: the ``courses`` half of cohort enrollment (#66
A2, C, D, E2; spec §1, §3, §6, §12 ``CohortEnrollment``; architecture §4.1
"courses: cohort enrollment").

``cohort_enrollments`` holds one row per enrollment **period** of a student
in a cohort: inserted at the enrollment instant, closed once by the
withdrawal (``withdrawn_at``, ``withdrawn_by_user_id``, the optional
reason) and never deleted (a database trigger). Re-enrolling after a
withdrawal is a new row; M6-07 (#92) charges a period's fee only while no
other period of the student in the cohort has a fee with a positive net
(J5). The participant
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

The billing reads (#92: :func:`cohort_enrollment_columns`,
:func:`cohort_enrollment_period`, :func:`lock_cohort_for_billing`,
:func:`cohort_session_count`), the roster (:func:`cohort_enrollment_rows`)
and the record policy (:func:`active_cohort_ids_of_students`) are here
too.

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Exists, Select, func, select
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
    CourseTemplateSnapshot,
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


# --- Billing reads (#92) -------------------------------------------------------------


def cohort_enrollment_columns() -> Select[
    uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID, datetime, datetime | None
]:
    """``SELECT enrollment_id, owner_id (the cohort), student_id, subject_id
    (the cohort's), enrolled_at, withdrawn_at`` of every cohort enrollment
    period, **not executed** (the #92 enrollment sweeper's candidates and
    the J5 "other periods" read)."""
    return select(
        CohortEnrollment.id.label("enrollment_id"),
        CohortEnrollment.cohort_id.label("owner_id"),
        CohortEnrollment.student_id,
        CourseCohort.subject_id,
        CohortEnrollment.enrolled_at,
        CohortEnrollment.withdrawn_at,
    ).join(CourseCohort, CourseCohort.id == CohortEnrollment.cohort_id)


@dataclass(frozen=True)
class CohortPeriod:
    """One cohort enrollment period with the cohort's subject."""

    enrollment_id: uuid.UUID
    cohort_id: uuid.UUID
    student_id: uuid.UUID
    subject_id: uuid.UUID
    enrolled_at: datetime
    withdrawn_at: datetime | None


async def cohort_enrollment_period(
    session: AsyncSession, enrollment_id: uuid.UUID
) -> CohortPeriod | None:
    """One period (re-read), or ``None`` when unknown. One statement."""
    row = (
        await session.execute(
            cohort_enrollment_columns()
            .where(CohortEnrollment.id == enrollment_id)
            .execution_options(populate_existing=True)
        )
    ).first()
    if row is None:
        return None
    return CohortPeriod(
        enrollment_id=row.enrollment_id,
        cohort_id=row.owner_id,
        student_id=row.student_id,
        subject_id=row.subject_id,
        enrolled_at=row.enrolled_at.astimezone(UTC),
        withdrawn_at=(
            None if row.withdrawn_at is None else row.withdrawn_at.astimezone(UTC)
        ),
    )


async def lock_cohort_for_billing(
    session: AsyncSession, cohort_id: uuid.UUID
) -> uuid.UUID | None:
    """The cohort row ``FOR SHARE`` (#92 D4), its id or ``None`` when
    unknown. The #66 enrollment and withdrawal take it ``FOR UPDATE``, so an
    enrollment sweeper step and an enrollment change serialize. One
    statement."""
    found: uuid.UUID | None = await session.scalar(
        select(CourseCohort.id)
        .where(CourseCohort.id == cohort_id)
        .with_for_update(read=True)
    )
    return found


async def cohort_session_count(session: AsyncSession, cohort_id: uuid.UUID) -> int:
    """The cohort snapshot's ``session_count`` (the advertised number of
    sessions, fixed at creation): the denominator of a late enrollment's
    proration (#92 B2, J3). An unknown cohort is a :class:`ValueError`. One
    statement."""
    count = await session.scalar(
        select(CourseTemplateSnapshot.content["session_count"].as_integer())
        .join(
            CourseCohort,
            CourseCohort.template_snapshot_id == CourseTemplateSnapshot.id,
        )
        .where(CourseCohort.id == cohort_id)
    )
    if count is None:
        raise ValueError("unknown cohort")
    return int(count)
