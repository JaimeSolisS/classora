"""A tutor's current student assignments (#67 D1, E5; the #33 F3 seam).

The students a tutor is assigned to are the participants of the sessions
whose **current** tutor they are (``tutor_user_id``), in ``offered``
(the tutor judges the offer), ``confirmed`` or ``completed``, that ended less
than :data:`TUTOR_ASSIGNMENT_LOOKBACK_DAYS` days ago or haven't ended.
``unassigned``, ``cancelled``, ``skipped_closure`` and ``conflict`` sessions
don't count, and a substitution or a decline (which change or clear
``tutor_user_id``) ends the link at once.
:func:`subject_ids_for_tutor_student` gives the subjects of the same
sessions for one student (#81 E4), and
:func:`current_tutor_subjects_for_student` the reverse: a student's current
tutors with those subjects (#316 B1). Former students' read-only history is a
separate fact set (M5).

``families`` never imports ``scheduling`` (ADR-0002): ``app/wiring.py``
registers :data:`TUTOR_ASSIGNMENT_SOURCE` with
``families.assignments.register_tutor_assignment_source``, so the student
record policy sees these students at the ``limited`` level (#67 D3).
"""

import uuid
from datetime import datetime, timedelta
from typing import Any, Final

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.modules.scheduling.models import ClassSession, ClassSessionParticipant
from app.modules.scheduling.states import TUTOR_OCCUPYING_STATES

TUTOR_ASSIGNMENT_LOOKBACK_DAYS: Final = 30
"""How long after a tutor's last such session ends they still see the
student (follow-up after the last class)."""

ASSIGNMENT_STATES: Final = TUTOR_OCCUPYING_STATES
"""``offered``, ``confirmed`` and ``completed`` (#67 E5)."""


def _assignments(*columns: Any, now: datetime) -> Select[Any]:
    """The participants of every current assignment session at ``now`` (one
    definition for :func:`student_ids_for_tutor`,
    :func:`subject_ids_for_tutor_student` and
    :func:`current_tutor_subjects_for_student`, so their states and window
    never drift apart)."""
    since = now - timedelta(days=TUTOR_ASSIGNMENT_LOOKBACK_DAYS)
    return (
        select(*columns)
        .select_from(ClassSessionParticipant)
        .join(ClassSession, ClassSession.id == ClassSessionParticipant.class_session_id)
        .where(
            ClassSession.tutor_user_id.is_not(None),
            ClassSession.state.in_(sorted(state.value for state in ASSIGNMENT_STATES)),
            ClassSession.ends_at > since,
        )
        .distinct()
    )


def _current_assignments(
    columns: Any, tutor_user_id: uuid.UUID, now: datetime
) -> Select[Any]:
    """The participants of ``tutor_user_id``'s current assignment sessions
    at ``now``."""
    return _assignments(columns, now=now).where(
        ClassSession.tutor_user_id == tutor_user_id
    )


async def student_ids_for_tutor(
    session: AsyncSession, tutor_user_id: uuid.UUID, *, now: datetime
) -> frozenset[uuid.UUID]:
    """The students of ``tutor_user_id``'s current assignments at ``now``
    (see the module docstring). One statement."""
    found = await session.scalars(
        _current_assignments(ClassSessionParticipant.student_id, tutor_user_id, now)
    )
    return frozenset(found)


async def subject_ids_for_tutor_student(
    session: AsyncSession,
    tutor_user_id: uuid.UUID,
    student_id: uuid.UUID,
    *,
    now: datetime,
) -> frozenset[uuid.UUID]:
    """The subjects in which ``tutor_user_id`` currently teaches
    ``student_id`` at ``now`` (#81 E4): the subjects of the same sessions as
    :func:`student_ids_for_tutor`, for that student. Empty exactly when the
    student isn't one of the tutor's current assignments (a substituted-away
    original tutor gets an empty set). One statement."""
    found = await session.scalars(
        _current_assignments(ClassSession.subject_id, tutor_user_id, now).where(
            ClassSessionParticipant.student_id == student_id
        )
    )
    return frozenset(found)


async def current_tutor_subjects_for_student(
    session: AsyncSession, student_id: uuid.UUID, *, now: datetime
) -> dict[uuid.UUID, frozenset[uuid.UUID]]:
    """The tutors who currently teach ``student_id`` at ``now`` (those whose
    :func:`student_ids_for_tutor` holds the student), each with the subjects
    :func:`subject_ids_for_tutor_student` gives for them (#316 B1: the
    eligible authors of a report schedule). One statement."""
    rows = await session.execute(
        _assignments(
            ClassSession.tutor_user_id, ClassSession.subject_id, now=now
        ).where(ClassSessionParticipant.student_id == student_id)
    )
    found: dict[uuid.UUID, set[uuid.UUID]] = {}
    for tutor_user_id, subject_id in rows:
        found.setdefault(tutor_user_id, set()).add(subject_id)
    return {tutor: frozenset(subjects) for tutor, subjects in found.items()}


class SchedulingTutorAssignments:
    """The ``families.assignments.TutorAssignmentSource`` the app wires: the
    assignments at ``clock.now()``."""

    async def student_ids_for_tutor(
        self, session: AsyncSession, user_id: uuid.UUID
    ) -> frozenset[uuid.UUID]:
        return await student_ids_for_tutor(session, user_id, now=clock.now())


TUTOR_ASSIGNMENT_SOURCE: Final = SchedulingTutorAssignments()
