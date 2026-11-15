"""Overriding the soft conflicts of one ``conflict`` occurrence (#236 G3,
E3; ADR-0005 D4; spec §3, W1.6).

The nightly job (:mod:`~app.modules.scheduling.materialize`) never carries
an override forward: an occurrence that fails a soft check is stored as
``conflict`` with an inbox item. ``POST /api/v1/class-sessions/{id}/
conflict-overrides`` lets a holder of ``schedule.override`` override that
one row's soft conflicts (the #49 ``{codes, reason}`` semantics) and
schedule it. The inbox item itself is unchanged: approving it still only
re-checks (#53 G2).

**Order** (:func:`override_conflict_occurrence`), after the route's
``401``/``403`` (``schedule.manage``) and the body's ``422``:

1. ``404`` for an unknown id (before the permission check, so Owners and
   Administrators both see "not denied");
2. ``403 permission_denied`` without ``schedule.override``;
3. the locks, in the approve order: ``lock_students`` (the row's
   students), the zone ``FOR SHARE``, the series ``FOR SHARE`` (as
   ``PATCH``: the series doesn't change), then the row ``FOR UPDATE``;
4. ``409 version_conflict``; ``409 session_not_in_conflict`` unless the
   row is ``conflict``; ``409 session_not_editable`` (``details.reason =
   started``) when ``starts_at <= now``;
5. ``422`` for the references with the #48 codes (``tutor_inactive`` …):
   data validity is never overridable; ``422`` for the bounds;
6. the #53 checks with ``now`` and the series' assigned tutor for the
   row's date (``center_closed`` off);
7. :func:`~app.modules.scheduling.overrides.evaluate_override`: a hard
   conflict or an unlisted soft one is ``409 scheduling_conflict`` with
   the #48 single-session body (no ``occurrence_start``);
8. the approve's transition
   (:func:`~app.modules.scheduling.occurrence_approval.schedule_conflict_row`:
   ``occurrence_initial_state``, the series offer for ``offered``);
   ``conflict_codes`` are kept;
9. with something overridden, the #49 row (``class_series_id`` = the row's
   series) and its ``scheduling.session_override`` event; with no conflict
   left nothing is recorded (#49 E1) and the row is still scheduled;
10. its open ``scheduling.occurrence_conflict`` request is cancelled by the
    system;
11. ``200`` with the session (and its ``overrides``).

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.authz import Principal, RecordNotFoundError, ensure_permission
from app.core.errors import FieldError
from app.core.models import ensure_version
from app.modules.administration import service as administration
from app.modules.scheduling.booking import get_class_session
from app.modules.scheduling.checks import SlotCandidate, collect_conflicts
from app.modules.scheduling.errors import (
    InvalidSessionTimesError,
    SchedulingConflictError,
    SessionNotEditableError,
    SessionNotInConflictError,
    SessionValidationError,
)
from app.modules.scheduling.locks import lock_students
from app.modules.scheduling.materialize import (
    assigned_tutor,
    occurrence_local_date,
    validate_series_references,
)
from app.modules.scheduling.models import (
    ClassSeries,
    ClassSession,
    ClassSessionParticipant,
)
from app.modules.scheduling.occurrence_approval import schedule_conflict_row
from app.modules.scheduling.overrides import evaluate_override, record_session_override
from app.modules.scheduling.schemas import ClassSessionDetail, ConflictOverrideCreate
from app.modules.scheduling.series_edit import cancel_conflict_request
from app.modules.scheduling.states import SessionState
from app.modules.scheduling.times import validate_session_bounds

SCHEDULE_OVERRIDE: Final = "schedule.override"
"""The identity catalog's permission (repeated here: ADR-0002)."""


async def override_conflict_occurrence(
    session: AsyncSession,
    class_session_id: uuid.UUID,
    body: ConflictOverrideCreate,
    *,
    principal: Principal,
) -> ClassSessionDetail:
    """``POST /api/v1/class-sessions/{id}/conflict-overrides``: see the module
    docstring."""
    found = await session.get(ClassSession, class_session_id)
    if found is None:
        raise RecordNotFoundError()
    ensure_permission(principal, SCHEDULE_OVERRIDE)
    student_ids = sorted(
        await session.scalars(
            select(ClassSessionParticipant.student_id).where(
                ClassSessionParticipant.class_session_id == found.id
            )
        )
    )
    await lock_students(session, student_ids)
    zone = await administration.get_center_time_zone(session, for_share=True)
    series = None
    if found.series_id is not None:
        series = await session.scalar(
            select(ClassSeries)
            .where(ClassSeries.id == found.series_id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
    row = await session.scalar(
        select(ClassSession)
        .where(ClassSession.id == found.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:  # pragma: no cover - sessions are never deleted
        raise RecordNotFoundError()
    ensure_version(row, body.version)
    if row.state != SessionState.CONFLICT or series is None:
        raise SessionNotInConflictError(row.state)
    now = clock.now()
    if row.starts_at <= now:
        raise SessionNotEditableError(started=True)

    # Data validity is never a conflict (and never overridable): 422.
    await validate_series_references(session, series, student_ids)
    try:
        validate_session_bounds(row.starts_at, row.ends_at, zone)
    except InvalidSessionTimesError as exc:
        error: FieldError = {
            "path": ["body"],
            "code": exc.reason,
            "message": "The occurrence's time is not valid in the center zone.",
        }
        raise SessionValidationError([error]) from None
    conflicts = await collect_conflicts(
        session,
        SlotCandidate(
            kind=row.kind,
            student_ids=tuple(student_ids),
            subject_id=row.subject_id,
            tutor_user_id=assigned_tutor(
                series, occurrence_local_date(row, series.time_zone)
            ),
            classroom_id=row.classroom_id,
            starts_at=row.starts_at,
            ends_at=row.ends_at,
        ),
        zone=zone,
        now=now,
        check_center_closed=False,
    )
    decision = evaluate_override(conflicts, body.override)
    if not decision.allowed:
        raise SchedulingConflictError([c.as_wire() for c in conflicts])

    await schedule_conflict_row(session, row, series)
    if decision.overridden:
        await record_session_override(
            session,
            row.id,
            decision.overridden,
            body.override.reason,
            actor_id=principal.user_id,
            class_series_id=series.id,
        )
    await cancel_conflict_request(session, row.id)
    await session.flush()
    return await get_class_session(session, row.id)
