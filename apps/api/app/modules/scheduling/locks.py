"""The per-student scheduling lock (ADR-0005 D1; #48 C4).

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
