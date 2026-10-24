"""A tutor's current student assignments (#67 D1, E5; the #33 F3 seam).

The students a tutor is assigned to are the participants of the sessions
whose **current** tutor they are (``tutor_user_id``), in ``offered``
(the tutor judges the offer), ``confirmed`` or ``completed``, that ended less
than :data:`TUTOR_ASSIGNMENT_LOOKBACK_DAYS` days ago or haven't ended.
``unassigned``, ``cancelled``, ``skipped_closure`` and ``conflict`` sessions
don't count, and a substitution or a decline (which change or clear
``tutor_user_id``) ends the link at once. Former students' read-only
history is a separate fact set (M5).

``families`` never imports ``scheduling`` (ADR-0002): ``app/wiring.py``
registers :data:`TUTOR_ASSIGNMENT_SOURCE` with
``families.assignments.register_tutor_assignment_source``, so the student
record policy sees these students at the ``limited`` level (#67 D3).
"""

import uuid
from datetime import datetime, timedelta
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.modules.scheduling.models import ClassSession, ClassSessionParticipant
from app.modules.scheduling.states import TUTOR_OCCUPYING_STATES

TUTOR_ASSIGNMENT_LOOKBACK_DAYS: Final = 30
"""How long after a tutor's last such session ends they still see the
student (follow-up after the last class)."""

ASSIGNMENT_STATES: Final = TUTOR_OCCUPYING_STATES
"""``offered``, ``confirmed`` and ``completed`` (#67 E5)."""


async def student_ids_for_tutor(
    session: AsyncSession, tutor_user_id: uuid.UUID, *, now: datetime
) -> frozenset[uuid.UUID]:
    """The students of ``tutor_user_id``'s current assignments at ``now``
    (see the module docstring). One statement."""
    since = now - timedelta(days=TUTOR_ASSIGNMENT_LOOKBACK_DAYS)
    found = await session.scalars(
        select(ClassSessionParticipant.student_id)
        .join(ClassSession, ClassSession.id == ClassSessionParticipant.class_session_id)
        .where(
            ClassSession.tutor_user_id == tutor_user_id,
            ClassSession.state.in_(sorted(state.value for state in ASSIGNMENT_STATES)),
            ClassSession.ends_at > since,
        )
        .distinct()
    )
    return frozenset(found)


class SchedulingTutorAssignments:
    """The ``families.assignments.TutorAssignmentSource`` the app wires: the
    assignments at ``clock.now()``."""

    async def student_ids_for_tutor(
        self, session: AsyncSession, user_id: uuid.UUID
    ) -> frozenset[uuid.UUID]:
        return await student_ids_for_tutor(session, user_id, now=clock.now())


TUTOR_ASSIGNMENT_SOURCE: Final = SchedulingTutorAssignments()
