"""The scheduling seam of evaluations (#80 B2, D2 step 7, AM1).

``app.workflows.evaluations`` composes it with ``academics`` (the
evaluation rows); scheduling never imports ``academics``.

:func:`evaluation_session` gives what an evaluation's session link and the
``evaluation-context`` route need of a class session: its kind, state,
start, current tutor, subject, cohort and participants, in **one**
statement (the session left-joined with its participants). With ``share``
the session row is taken ``FOR SHARE`` (D2 step 7), so a create can't
interleave with a state transition or a substitution (which lock it ``FOR
UPDATE``).
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.scheduling.models import ClassSession, ClassSessionParticipant


@dataclass(frozen=True)
class EvaluationSession:
    """What an evaluation needs of its session (B2)."""

    id: uuid.UUID
    kind: str
    state: str
    starts_at: datetime
    tutor_user_id: uuid.UUID | None
    subject_id: uuid.UUID
    cohort_id: uuid.UUID | None
    participant_ids: tuple[uuid.UUID, ...]


async def evaluation_session(
    session: AsyncSession, class_session_id: uuid.UUID, *, share: bool = False
) -> EvaluationSession | None:
    """The session with its participants (``None`` when unknown), unlocked
    or with the session row ``FOR SHARE`` (re-read). One statement."""
    query = (
        select(ClassSession, ClassSessionParticipant.student_id)
        .outerjoin(
            ClassSessionParticipant,
            ClassSessionParticipant.class_session_id == ClassSession.id,
        )
        .where(ClassSession.id == class_session_id)
        .order_by(ClassSessionParticipant.student_id)
    )
    if share:
        query = query.with_for_update(read=True, of=ClassSession).execution_options(
            populate_existing=True
        )
    rows = (await session.execute(query)).all()
    if not rows:
        return None
    row: ClassSession = rows[0][0]
    return EvaluationSession(
        id=row.id,
        kind=row.kind,
        state=row.state,
        starts_at=row.starts_at.astimezone(UTC),
        tutor_user_id=row.tutor_user_id,
        subject_id=row.subject_id,
        cohort_id=row.cohort_id,
        participant_ids=tuple(
            student_id for _, student_id in rows if student_id is not None
        ),
    )
