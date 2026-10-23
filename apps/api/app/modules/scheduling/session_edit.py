"""Moving one session: ``PATCH /api/v1/class-sessions/{id}`` (#54 C1, E3;
ADR-0005 D1, D2, D3, D4).

One generic route for one-off sessions and series occurrences; the
calendar's drag and drop and its Reschedule dialog (#58) use it too. It
changes the instants and, optionally, the room; the tutor, the subject and
the students never change here (#52, #55). After the route's ``401``/``403``
(``schedule.manage``), the body's ``422`` (``tutor_user_id`` is an unknown
member; ``override`` is the #49 type with its codes) and the route's ``403
permission_denied`` for an ``override`` without ``schedule.override`` (#58
A2 step 3: before anything is read or locked, even for a move without a
conflict):

1. ``404`` for an unknown id;
2. the locks (:mod:`~app.modules.scheduling.series_edit` order):
   ``lock_students`` (the session's students), the zone ``FOR SHARE``, for a
   group occurrence the group row ``FOR UPDATE`` (#56 F1), for an
   occurrence its series row ``FOR SHARE`` (its version is not bumped), then
   the row ``FOR UPDATE``;
3. ``409 version_conflict`` on the session ``version``;
4. ``409 session_not_editable``: started (``starts_at <= now``), or
   ``completed``, ``cancelled`` or ``skipped_closure`` (a skipped occurrence
   isn't moved; a makeup is an ordinary one-off session, D6);
5. ``422`` for the bounds (the #48 mapping on ``starts_at`` / ``ends_at``);
6. ``422`` for the references (the #48 codes: the students, the subject,
   the tutor the row keeps and the room, all in one response);
7. ``422 session_unchanged`` when the instants and the room are the same;
8. the #48 checks with the row's own tutor (a ``conflict`` row: the series'
   assigned tutor for its date), ``center_closed`` **on** (moving a session
   onto a closure date is a deliberate placement), the row itself excluded
   from the overlaps;
9. the override decision (#58 A2 step 10,
   :func:`~app.modules.scheduling.overrides.evaluate_override` over the
   conflicts recomputed here, under the locks): any hard conflict, or a soft
   one the ``override`` doesn't list, is ``409 scheduling_conflict`` with the
   complete list (the #48 body, never the override); listed codes that don't
   occur are ignored; no override carries forward from an earlier write
   (#58 A3): a later move needs its own;
10. the tutor's workload warnings at the new time (#58 C1, #50
    :func:`~app.modules.scheduling.workload_sessions.session_warnings`, the
    session's own row excluded; at most two statements; none without a
    tutor);
11. the update: an occurrence becomes an exception (``is_exception``), its
    ``original_start`` unchanged, so the nightly job never recreates its
    date; a ``conflict`` row moves to ``occurrence_initial_state`` and its
    inbox request is cancelled; ``offered`` / ``confirmed`` keep their state
    and tutor; an ``EXCLUDE`` race is the #48 single-conflict ``409``;
12. a pending offer of the row gets its ``respond_by`` capped at its first
    covered start (D3); a pending substitution of a ``confirmed`` row is
    withdrawn (#55 G3); ``scheduling.session_rescheduled`` to the tutor
    (``offered`` / ``confirmed``) and the family (``confirmed``);
13. with something overridden, the #49 override row (an occurrence's
    ``class_series_id`` is its own series, #236 D1) and its
    ``scheduling.session_override`` event, in the same transaction;
14. ``200`` with the session (its ``overrides`` included) and the
    ``warnings``.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.authz import RecordNotFoundError
from app.core.errors import FieldError
from app.core.models import ensure_version
from app.modules.administration import service as administration
from app.modules.scheduling.booking import get_class_session, validate_bounds
from app.modules.scheduling.checks import SlotCandidate, collect_conflicts
from app.modules.scheduling.conflicts import ROOM_OVERLAP, TUTOR_OVERLAP, Conflict
from app.modules.scheduling.errors import (
    SchedulingConflictError,
    SessionNotEditableError,
    SessionOverlapError,
    SessionValidationError,
)
from app.modules.scheduling.group_lock import lock_group
from app.modules.scheduling.locks import lock_students
from app.modules.scheduling.materialize import (
    assigned_tutor,
    occurrence_initial_state,
    occurrence_local_date,
    validate_slot_references,
)
from app.modules.scheduling.models import (
    ClassSeries,
    ClassSession,
    ClassSessionParticipant,
)
from app.modules.scheduling.notifications import (
    Affected,
    notify_session_rescheduled,
)
from app.modules.scheduling.offer_flow import (
    cap_offer_deadlines,
    withdraw_pending_substitutions,
)
from app.modules.scheduling.offer_records import attach_to_pending_series_offer
from app.modules.scheduling.overrides import evaluate_override, record_session_override
from app.modules.scheduling.schemas import ClassSessionUpdate, ClassSessionUpdated
from app.modules.scheduling.series_edit import cancel_conflict_request
from app.modules.scheduling.sessions import (
    flush_class_sessions,
    transition_class_session,
)
from app.modules.scheduling.states import SessionState
from app.modules.scheduling.times import scheduled_minutes
from app.modules.scheduling.workload_sessions import session_warnings

NOT_EDITABLE_STATES = frozenset(
    {SessionState.COMPLETED, SessionState.CANCELLED, SessionState.SKIPPED_CLOSURE}
)


def _race(code: str, classroom_id: uuid.UUID | None) -> Conflict:
    if code == ROOM_OVERLAP:
        return Conflict(
            ROOM_OVERLAP, {"classroom_id": str(classroom_id), "class_session_ids": []}
        )
    return Conflict(TUTOR_OVERLAP, {"class_session_ids": []})


async def update_class_session(
    session: AsyncSession,
    class_session_id: uuid.UUID,
    body: ClassSessionUpdate,
    *,
    actor_id: uuid.UUID,
    notice_reference_at: datetime | None = None,
    exclude_recipient_ids: Sequence[uuid.UUID] = (),
) -> ClassSessionUpdated:
    """``PATCH /api/v1/class-sessions/{id}``: see the module docstring.
    ``actor_id`` is the signed-in user (the override's actor); the route has
    already checked ``schedule.override`` for an ``override``.

    #62 (an approved reschedule request) also passes ``notice_reference_at``
    (the request's ``created_at``: the booking notice is measured from it,
    with the policy in force then; every other check stays live, and the
    "started" rule keeps using now) and ``exclude_recipient_ids`` (the
    requester, left out of ``scheduling.session_rescheduled``). ``PATCH``
    passes neither."""
    found = await session.get(ClassSession, class_session_id)
    if found is None:
        raise RecordNotFoundError()
    student_ids = sorted(
        await session.scalars(
            select(ClassSessionParticipant.student_id).where(
                ClassSessionParticipant.class_session_id == found.id
            )
        )
    )
    await lock_students(session, student_ids)
    zone = await administration.get_center_time_zone(session, for_share=True)
    if found.group_id is not None:
        # #56 F1: a group occurrence's room changes under the group lock.
        await lock_group(session, found.group_id)
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
    now = clock.now()
    if row.starts_at <= now:
        raise SessionNotEditableError(started=True)
    if row.state in NOT_EDITABLE_STATES:
        raise SessionNotEditableError(state=row.state)
    validate_bounds(body, zone)

    classroom_id = row.classroom_id
    if "classroom_id" in body.model_fields_set:
        classroom_id = body.classroom_id
    previous_state = row.state
    target = SessionState(row.state)
    tutor = row.tutor_user_id
    if previous_state == SessionState.CONFLICT and series is not None:
        local_date = occurrence_local_date(row, series.time_zone)
        tutor = assigned_tutor(series, local_date)
        target = occurrence_initial_state(series, local_date)
    await validate_slot_references(
        session,
        student_ids,
        subject_id=row.subject_id,
        tutor_user_id=tutor,
        classroom_id=classroom_id,
    )
    if (body.starts_at, body.ends_at, classroom_id) == (
        row.starts_at,
        row.ends_at,
        row.classroom_id,
    ):
        error: FieldError = {
            "path": ["body"],
            "code": "session_unchanged",
            "message": "The new time and room are the same as the session's.",
        }
        raise SessionValidationError([error])

    conflicts = await collect_conflicts(
        session,
        SlotCandidate(
            kind=row.kind,
            student_ids=tuple(student_ids),
            subject_id=row.subject_id,
            tutor_user_id=tutor,
            classroom_id=classroom_id,
            starts_at=body.starts_at,
            ends_at=body.ends_at,
        ),
        zone=zone,
        now=now if notice_reference_at is None else notice_reference_at,
        check_center_closed=True,
        exclude_session_ids=frozenset({row.id}),
    )
    decision = evaluate_override(conflicts, body.override)
    if not decision.allowed:
        raise SchedulingConflictError([conflict.as_wire() for conflict in conflicts])
    warnings = (
        []
        if tutor is None
        else await session_warnings(
            session,
            tutor,
            body.starts_at,
            body.ends_at,
            zone,
            exclude_session_id=row.id,
        )
    )

    affected = Affected(row.id, previous_state, row.tutor_user_id, "changed")
    row.starts_at = body.starts_at
    row.ends_at = body.ends_at
    row.scheduled_minutes = scheduled_minutes(body.starts_at, body.ends_at)
    row.classroom_id = classroom_id
    if row.series_id is not None:
        row.is_exception = True
    if previous_state == SessionState.CONFLICT:
        row.tutor_user_id = tutor
    try:
        # Flush before a transition (#266): its version check reads the
        # stored version.
        await flush_class_sessions(session)
        if previous_state == SessionState.CONFLICT:
            await transition_class_session(
                session,
                row.id,
                to_state=target,
                expected_version=row.version,
                tutor_user_id=tutor if target == SessionState.OFFERED else None,
            )
    except SessionOverlapError as exc:
        raise SchedulingConflictError(
            [_race(exc.code, classroom_id).as_wire()]
        ) from None
    if previous_state == SessionState.CONFLICT:
        if series is not None and target == SessionState.OFFERED:
            await attach_to_pending_series_offer(session, series, [row])
        await cancel_conflict_request(session, row.id)
    if row.state == SessionState.OFFERED:
        await cap_offer_deadlines(session, [row.id])
    if previous_state == SessionState.CONFIRMED:
        # #55 G3: the pending substitution's snapshot no longer matches.
        await withdraw_pending_substitutions(session, [row.id])
    await notify_session_rescheduled(
        session,
        affected,
        starts_at=row.starts_at,
        actor_id=actor_id,
        exclude_ids=exclude_recipient_ids,
    )
    if decision.overridden:
        # Only an allowed override reaches here, so body.override is set.
        assert body.override is not None  # noqa: S101
        await record_session_override(
            session,
            row.id,
            decision.overridden,
            body.override.reason,
            actor_id=actor_id,
            class_series_id=row.series_id,
        )
    await session.flush()
    detail = await get_class_session(session, row.id)
    return ClassSessionUpdated(**detail.model_dump(), warnings=warnings)
