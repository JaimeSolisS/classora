"""Where tutors' current student assignments come from (#33 F3).

Assignments arrive with scheduling (M4). Until then the app wires
:class:`NoTutorAssignments`, which always answers "none", so a tutor sees no
student. Tests inject a fixed mapping by overriding the one dependency
:func:`get_tutor_assignment_source` through ``app.dependency_overrides``.
"""

import uuid
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession


class TutorAssignmentSource(Protocol):
    """The students a tutor is currently assigned to."""

    async def student_ids_for_tutor(
        self, session: AsyncSession, user_id: uuid.UUID
    ) -> frozenset[uuid.UUID]: ...


class NoTutorAssignments:
    """The stub source: no tutor has any assignment yet.

    M4 replaces it with a call to ``scheduling.service`` that returns the
    student ids of the tutor's current assignments (former students need a
    separate fact set, see the #33 PM notes). It sends no SQL.
    """

    async def student_ids_for_tutor(
        self, session: AsyncSession, user_id: uuid.UUID
    ) -> frozenset[uuid.UUID]:
        return frozenset()


_NO_ASSIGNMENTS = NoTutorAssignments()


def get_tutor_assignment_source() -> TutorAssignmentSource:
    """The FastAPI dependency the student routes use (override it in tests)."""
    return _NO_ASSIGNMENTS
