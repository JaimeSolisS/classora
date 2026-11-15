"""Which scheduled classes an availability change affects: the seam between
``staff`` and ``scheduling`` (#45 D; M4-18 #63 B3).

``staff`` never imports ``scheduling`` (ADR-0002), so the availability
routes ask an injected :class:`AvailabilityImpactSource`. The composition
roots register the scheduling implementation
(``scheduling.service.AVAILABILITY_IMPACT_SOURCE``, wired in
``app/wiring.py``) with :func:`register_availability_impact_source`; the
routes read it through the FastAPI dependency
:func:`get_availability_impact_source` (tests may override it through
``app.dependency_overrides``). Without a registered source the dependency
fails closed (:class:`ImpactSourceMissingError`): a change is never applied
unchecked.

The source takes the scheduling tutor lock (``scheduling.locks.lock_tutor``)
before it reads, so a direct change and its check serialize with a booking
of the same tutor (#63 D5). It answers the tutor's **affected** sessions
(the pure rule :func:`~app.modules.staff.availability_impact.is_affected`)
ordered by ``starts_at``, then ``id``.

The weekly ``PUT`` asks only when it takes weekly time away
(:func:`~app.modules.staff.availability.uncovered_ranges`); a new
``unavailable`` exception and a deleted ``available`` one always ask. A
non-empty answer stores the change as a pending request and answers ``202``
(``staff/availability_change_requests.py``); nothing else changes.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.staff.availability import TutorAvailabilityRules


@dataclass(frozen=True, slots=True)
class AffectedSession:
    """One affected session, with what a change request shows of it."""

    id: uuid.UUID
    state: str
    starts_at: datetime
    ends_at: datetime
    subject_name: str
    series_id: uuid.UUID | None


class AvailabilityImpactSource(Protocol):
    """The tutor's scheduled sessions that a change would leave outside
    their availability."""

    async def affected_sessions(
        self,
        session: AsyncSession,
        tutor_user_id: uuid.UUID,
        *,
        current: TutorAvailabilityRules,
        proposed: TutorAvailabilityRules,
        dates: tuple[date, date] | None,
    ) -> list[AffectedSession]:
        """``current``: the tutor's rules now; ``proposed``: the same rules
        after the change. ``dates``: the inclusive local dates an exception
        change touches (the read is bounded to them), ``None`` for a weekly
        change (every future session)."""
        ...


class ImpactSourceMissingError(RuntimeError):
    """No impact source is registered (a composition root forgot
    ``app.wiring``)."""


_registered: list[AvailabilityImpactSource] = []


def register_availability_impact_source(source: AvailabilityImpactSource) -> None:
    """Register the app's source (again: replaces it; the composition roots
    register the same object)."""
    _registered[:] = [source]


def registered_availability_impact_source() -> AvailabilityImpactSource:
    """The registered source; :class:`ImpactSourceMissingError` without
    one."""
    if not _registered:
        raise ImpactSourceMissingError("no availability impact source registered")
    return _registered[0]


def get_availability_impact_source() -> AvailabilityImpactSource:
    """The FastAPI dependency the availability routes use (override it in
    tests)."""
    return registered_availability_impact_source()
