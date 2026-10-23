"""The per-student and per-tutor scheduling locks (ADR-0005 D1; #48 C4;
M4-18 #63 D5).

Student overlap has no database constraint (a session has many students),
so it is checked by the application under a transaction advisory lock per
student: ``pg_advisory_xact_lock(STUDENT_LOCK_NAMESPACE,
hashtext(student_id::text))``, released when the transaction ends
(compatible with PgBouncer transaction pooling, ADR-0003).

**Rule: every writer that adds a participant to a session, or moves a
session in time, takes this lock for each of the session's students first,
before it reads anything** (#48 create; #56 groups, #58 reschedule, #62
cancel-and-restore, #65 courses). Locks are taken in ascending
``student_id`` order, so two writers never deadlock. Two students whose ids
hash alike only serialize more; an overlap never gets through.

**Tutor lock** (:func:`lock_tutor`, M4-18 #63 D5): ``pg_advisory_xact_lock(
TUTOR_LOCK_NAMESPACE, hashtext(tutor_user_id::text))``, the same pattern. It
serializes a change of a tutor's availability with the bookings that check
that availability, so a session cannot be booked into time that is being
removed and escape both the booking's ``tutor_unavailable`` check and the
change's impact check. It is taken, **before the availability is read**, by:
the impact source of a direct change (``scheduling.reassignment``, after
the weekly ``PUT``'s settings row), the approval of a change request
(``app.workflows.availability_change``, after the request and the settings
row), the #48 create with a tutor (after the students and the zone), the
#52 re-offer, the #55 substitution (for the substitute) and the #233
series offer (after their session or series locks and before the zone).
Nothing that holds it waits for a student lock, so the two never deadlock.

**Lock order** for every writer that changes a group's or a cohort's
sessions or participants (#56; #65 G1, also in ``_docs/api.md``):

    students (this module) → zone ``FOR SHARE`` → course template
    ``FOR SHARE`` (only in the cohort create) → group **or cohort**
    ``FOR UPDATE`` → series ``FOR UPDATE`` → sessions by id → offer

A group series takes its group row (:mod:`.group_lock`); a course series
takes its cohort row instead (``courses.service.lock_cohort``: the nightly
extension, the #233 series offer, and #66 enrollment after the students
and the zone). The ``courses`` module never takes a scheduling lock. Any
step a writer does not need is skipped, never reordered.
"""

import uuid
from collections.abc import Iterable
from typing import Final

from sqlalchemy import cast, func, select
from sqlalchemy.dialects.postgresql import TEXT
from sqlalchemy.ext.asyncio import AsyncSession

STUDENT_LOCK_NAMESPACE: Final = 480_301
"""The first key of the two-key advisory lock: a fixed int4 that names
"scheduling student" (M4-03), so it never collides with another lock kind."""

TUTOR_LOCK_NAMESPACE: Final = 480_302
"""The first key of the tutor lock ("scheduling tutor", M4-18)."""


async def lock_students(
    session: AsyncSession, student_ids: Iterable[uuid.UUID]
) -> None:
    """Take the transaction advisory lock of each student, in ascending id
    order (one statement per student; waits while another transaction holds
    one)."""
    for student_id in sorted(set(student_ids)):
        await session.execute(
            select(
                func.pg_advisory_xact_lock(
                    STUDENT_LOCK_NAMESPACE,
                    func.hashtext(cast(str(student_id), TEXT)),
                )
            )
        )


async def lock_tutor(session: AsyncSession, tutor_user_id: uuid.UUID) -> None:
    """Take the transaction advisory lock of the tutor (one statement; waits
    while another transaction holds it; taking it again in the same
    transaction returns at once)."""
    await session.execute(
        select(
            func.pg_advisory_xact_lock(
                TUTOR_LOCK_NAMESPACE, func.hashtext(cast(str(tutor_user_id), TEXT))
            )
        )
    )
