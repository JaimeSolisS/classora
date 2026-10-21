"""Which scheduled classes an availability change affects (#45 D; the seam
for M4-18 #63).

Assignments arrive with scheduling (M4). Until then the app wires
:class:`NoAssignmentImpact`, which always answers "none", so every
availability change applies directly. The weekly ``PUT``, an
``unavailable`` exception ``POST`` and an ``available`` exception
``DELETE`` ask the source; a non-empty answer is ``409
availability_change_requires_approval`` and nothing changes. Tests inject a
source by overriding the one dependency :func:`get_availability_impact_source`
through ``app.dependency_overrides`` (like
``families.assignments.get_tutor_assignment_source``, #33).
"""

import uuid
from collections.abc import Sequence
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.staff.availability import DatedException, WeeklyWindow


class AvailabilityImpactSource(Protocol):
    """The tutor's scheduled sessions that a change would leave outside
    their availability."""

    async def affected_session_ids(
        self,
        session: AsyncSession,
        tutor_user_id: uuid.UUID,
        *,
        removed_windows: Sequence[WeeklyWindow],
        added_unavailability: Sequence[DatedException],
    ) -> list[uuid.UUID]:
        """``removed_windows``: the weekly local time the change takes away
        (``availability.uncovered_ranges``: what the old windows covered and
        the new ones don't). ``added_unavailability``: the dated time that
        stops being available: a new ``unavailable`` exception, or a deleted
        ``available`` one (passed as is, with its ``kind``)."""
        ...


class NoAssignmentImpact:
    """The stub source: no change affects any session yet. It sends no SQL.

    M4-18 (#63) replaces it with a call to ``scheduling.service`` returning
    the tutor's affected future sessions, and turns the ``409`` into an
    approval request (``staff.availability_change``).
    """

    async def affected_session_ids(
        self,
        session: AsyncSession,
        tutor_user_id: uuid.UUID,
        *,
        removed_windows: Sequence[WeeklyWindow],
        added_unavailability: Sequence[DatedException],
    ) -> list[uuid.UUID]:
        return []


_NO_IMPACT = NoAssignmentImpact()


def get_availability_impact_source() -> AvailabilityImpactSource:
    """The FastAPI dependency the availability routes use (override it in
    tests)."""
    return _NO_IMPACT
