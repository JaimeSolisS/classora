"""Where tutors' current student assignments come from (#33 F3, #67 D2).

``families`` never imports ``scheduling`` (ADR-0002): the composition roots
(``app/wiring.py``, used by the API app factory, ``app.cli`` and
``app.worker``) register the scheduling source with
:func:`register_tutor_assignment_source` (the students of the tutor's recent
and upcoming sessions, ``scheduling.service.student_ids_for_tutor``).
Without a registration, :func:`get_tutor_assignment_source` answers
:class:`NoTutorAssignments` (always "none"). Tests inject a fixed mapping
by overriding the one dependency :func:`get_tutor_assignment_source`
through ``app.dependency_overrides``.
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
    """The default source: no tutor has any assignment. Used until a source
    is registered (#67 D2), and by the family routes that never use
    assignments. It sends no SQL."""

    async def student_ids_for_tutor(
        self, session: AsyncSession, user_id: uuid.UUID
    ) -> frozenset[uuid.UUID]:
        return frozenset()


_NO_ASSIGNMENTS = NoTutorAssignments()
_registered: list[TutorAssignmentSource] = []


def register_tutor_assignment_source(source: TutorAssignmentSource) -> None:
    """Make ``source`` the tutor assignment source (``app/wiring.py``).
    Registering again replaces it."""
    _registered[:] = [source]


def get_tutor_assignment_source() -> TutorAssignmentSource:
    """The FastAPI dependency the student routes use (override it in tests):
    the registered source, or :class:`NoTutorAssignments`."""
    return _registered[0] if _registered else _NO_ASSIGNMENTS
