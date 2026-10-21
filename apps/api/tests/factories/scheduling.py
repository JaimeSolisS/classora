"""Scheduling factories (#47 D8): class sessions and their participants.

For #47 and the M4 issues that follow (#48 onward). A session's references
(subject, tutor, classroom) are rows of their own: :func:`persist_class_session`
creates a subject (with its level) and a tutor user when they aren't given,
and never a classroom (``NULL`` = no room) unless ``classroom=True``.

Times are instants (ADR-0004): the default slot is a whole-minute 60-minute
session starting on the hour, 14:00-22:00 UTC, in the fixed week of
2026-03-02 (Monday) to 2026-03-06, never relative to today.
``scheduled_minutes`` always follows ``starts_at`` / ``ends_at``.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import factory
import factory.random
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalog.models import Classroom, EducationLevel, Subject
from app.modules.scheduling.models import ClassSession, ClassSessionParticipant
from app.modules.scheduling.service import SessionKind, SessionState
from tests.factories.base import ClassoraFactory, persist
from tests.factories.families import persist_student
from tests.factories.identity import UserFactory

WEEK_START = datetime(2026, 3, 2, tzinfo=UTC)
"""Monday of the fixed default week (Mexico City is UTC-6 then: 08:00-16:00
local for the default 14:00-22:00 UTC starts)."""


def _default_start() -> datetime:
    day = factory.random.randgen.randint(0, 4)
    hour = factory.random.randgen.randint(14, 21)
    return WEEK_START + timedelta(days=day, hours=hour)


def _minutes(starts_at: datetime, ends_at: datetime) -> int:
    return int((ends_at - starts_at).total_seconds() // 60)


class ClassSessionFactory(ClassoraFactory):
    """A ``confirmed`` ``individual`` session, 60 minutes. ``subject_id`` and
    (for a tutor-occupying state) ``tutor_user_id`` must be passed; use
    :func:`persist_class_session` to have them created."""

    class Meta:
        model = ClassSession

    kind = SessionKind.INDIVIDUAL.value
    state = SessionState.CONFIRMED.value
    classroom_id = None
    starts_at = factory.LazyFunction(_default_start)
    ends_at = factory.LazyAttribute(lambda o: o.starts_at + timedelta(minutes=60))
    scheduled_minutes = factory.LazyAttribute(
        lambda o: _minutes(o.starts_at, o.ends_at)
    )
    actual_minutes = None
    cancelled_at = None
    cancellation_reason = None


async def persist_subject(session: AsyncSession) -> Subject:
    """A new active level and one active subject in it (unique names)."""
    suffix = uuid.uuid4().hex[:8]
    level = EducationLevel(name=f"Nivel {suffix}", sort_order=10)
    session.add(level)
    await session.flush()
    subject = Subject(education_level_id=level.id, name=f"Materia {suffix}")
    session.add(subject)
    await session.flush()
    return subject


async def persist_classroom(session: AsyncSession, capacity: int = 10) -> Classroom:
    """An active classroom without windows (no time restriction)."""
    room = Classroom(name=f"Aula {uuid.uuid4().hex[:8]}", capacity=capacity)
    session.add(room)
    await session.flush()
    return room


async def persist_tutor_user(session: AsyncSession) -> uuid.UUID:
    """A user to reference as ``tutor_user_id`` (no role: the table only
    references ``users``; role checks are #48's)."""
    user = await persist(session, UserFactory)
    return user.id


async def persist_class_session(
    session: AsyncSession,
    *,
    classroom: bool = False,
    tutor: bool | None = None,
    **overrides: Any,
) -> ClassSession:
    """Persist a :class:`ClassSessionFactory` session (flush, never commit).

    Creates the subject unless ``subject_id`` is given, a tutor unless
    ``tutor_user_id`` is given (by default only for a tutor-occupying
    state; ``tutor=True``/``False`` forces it) and a classroom when
    ``classroom=True`` and no ``classroom_id`` is given.
    """
    if "subject_id" not in overrides:
        overrides["subject_id"] = (await persist_subject(session)).id
    state = overrides.get("state", ClassSessionFactory.state)
    wants_tutor = (
        tutor
        if tutor is not None
        else state
        in {
            SessionState.OFFERED,
            SessionState.CONFIRMED,
            SessionState.COMPLETED,
        }
    )
    if "tutor_user_id" not in overrides and wants_tutor:
        overrides["tutor_user_id"] = await persist_tutor_user(session)
    if classroom and "classroom_id" not in overrides:
        overrides["classroom_id"] = (await persist_classroom(session)).id
    if state == SessionState.CANCELLED:
        overrides.setdefault("cancelled_at", WEEK_START - timedelta(days=7))
        overrides.setdefault("cancellation_reason", "Cancelada por el centro")
    instance: ClassSession = await persist(session, ClassSessionFactory, **overrides)
    return instance


async def add_participant(
    session: AsyncSession,
    class_session: ClassSession,
    student_id: uuid.UUID | None = None,
) -> ClassSessionParticipant:
    """Add a student (a new one unless ``student_id`` is given) to the
    session. Flushes, never commits."""
    if student_id is None:
        student_id = (await persist_student(session)).id
    participant = ClassSessionParticipant(
        class_session_id=class_session.id, student_id=student_id
    )
    session.add(participant)
    await session.flush()
    return participant
