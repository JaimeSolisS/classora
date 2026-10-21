"""The ``scheduling.occurrence_conflict`` approval type (#53 G; ADR-0005 D4).

The nightly materialization (:mod:`~app.modules.scheduling.materialize`)
stores an occurrence that fails a check as ``conflict`` and raises one
request for it (the subject is the session; the requester is ``NULL``, the
system). :data:`OCCURRENCE_CONFLICT_HANDLER` plugs these requests into the
approval inbox: reviewers hold ``schedule.manage`` (Owners and
Administrators); requests can't be sent back for changes, and a rejection
needs a reason.

- **Approve = re-check and schedule.** ``lock_students`` (the session's
  students), the center zone ``FOR SHARE``, the series row ``FOR UPDATE``
  (#233 B3), then the session ``FOR UPDATE``. A session no longer in
  ``conflict`` is ``409 approval_transition_invalid``. The references are
  re-validated (``422`` with the #48 codes) and the #48 checks run again with ``now``
  (``center_closed`` off, as for every series occurrence): still failing is
  ``409 scheduling_conflict`` with the conflicts, and the rollback leaves the
  request ``pending``; clean moves the session to
  :func:`~app.modules.scheduling.materialize.occurrence_initial_state` for
  its local date (#233 B1: ``unassigned`` without a tutor or before
  ``assignment_from``, ``offered`` while the series offer is pending, then
  attached to it, ``confirmed`` once it is accepted). The checks run with
  that tutor (the series' current one, or none). A start already in
  the past fails ``notice_window``, so the reviewer rejects it instead.
- **Reject = cancel the occurrence** with the reviewer's reason.

Fixing the time or the tutor is #54/#55's (those issues cancel the request
themselves). ``summaries`` reads the display data in five statements.
"""

import uuid
from collections.abc import Iterable, Mapping
from datetime import UTC
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.authz import Principal
from app.core.errors import FieldError
from app.modules.administration import service as administration
from app.modules.catalog import service as catalog
from app.modules.families import service as families
from app.modules.identity import service as identity
from app.modules.scheduling.checks import SlotCandidate, collect_conflicts
from app.modules.scheduling.conflicts import ROOM_OVERLAP, TUTOR_OVERLAP, Conflict
from app.modules.scheduling.errors import (
    InvalidSessionTimesError,
    SchedulingConflictError,
    SessionOverlapError,
    SessionValidationError,
)
from app.modules.scheduling.locks import lock_students
from app.modules.scheduling.materialize import (
    OCCURRENCE_CONFLICT_TYPE,
    assigned_tutor,
    occurrence_initial_state,
    occurrence_local_date,
    validate_series_references,
)
from app.modules.scheduling.models import (
    ClassSeries,
    ClassSession,
    ClassSessionParticipant,
)
from app.modules.scheduling.offer_records import attach_to_pending_series_offer
from app.modules.scheduling.sessions import transition_class_session
from app.modules.scheduling.states import SessionState
from app.modules.scheduling.times import validate_session_bounds

SCHEDULE_MANAGE: Final = "schedule.manage"
"""The identity catalog's permission (repeated here: ADR-0002)."""


async def _participants(
    session: AsyncSession, class_session_id: uuid.UUID
) -> list[uuid.UUID]:
    return sorted(
        await session.scalars(
            select(ClassSessionParticipant.student_id).where(
                ClassSessionParticipant.class_session_id == class_session_id
            )
        )
    )


async def _locked_conflict(
    session: AsyncSession, class_session_id: uuid.UUID
) -> ClassSession:
    row = await session.scalar(
        select(ClassSession)
        .where(ClassSession.id == class_session_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None or row.state != SessionState.CONFLICT:
        raise administration.ApprovalTransitionInvalidError()
    return row


async def _locked_series(session: AsyncSession, series_id: uuid.UUID) -> ClassSeries:
    series = await session.scalar(
        select(ClassSeries)
        .where(ClassSeries.id == series_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if series is None:  # pragma: no cover - occurrences keep their series
        raise administration.ApprovalTransitionInvalidError()
    return series


def _race_conflict(code: str, row: ClassSession) -> Conflict:
    if code == ROOM_OVERLAP:
        return Conflict(
            ROOM_OVERLAP,
            {"classroom_id": str(row.classroom_id), "class_session_ids": []},
        )
    return Conflict(TUTOR_OVERLAP, {"class_session_ids": []})


class OccurrenceConflictApprovalHandler:
    """The :class:`~app.modules.administration.service.ApprovalHandler` of
    ``conflict`` series occurrences; the subject is the class session."""

    request_type: Final = OCCURRENCE_CONFLICT_TYPE
    review_permission: Final = SCHEDULE_MANAGE
    supports_request_changes: Final = False
    reason_required_on_reject: Final = True

    async def approve(
        self,
        session: AsyncSession,
        request: administration.ApprovalRequestView,
        principal: Principal,
    ) -> None:
        student_ids = await _participants(session, request.subject_id)
        await lock_students(session, student_ids)
        zone = await administration.get_center_time_zone(session, for_share=True)
        series_id = await session.scalar(
            select(ClassSession.series_id).where(ClassSession.id == request.subject_id)
        )
        if series_id is None:  # pragma: no cover - a conflict row is an occurrence
            raise administration.ApprovalTransitionInvalidError()
        # #233 B3: the series row before the session (its assignment decides
        # the target state and must not change under us).
        series = await _locked_series(session, series_id)
        row = await _locked_conflict(session, request.subject_id)
        local_date = occurrence_local_date(row, series.time_zone)
        tutor = assigned_tutor(series, local_date)
        # Data validity is never a conflict: 422, as the create answers.
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
                tutor_user_id=tutor,
                classroom_id=row.classroom_id,
                starts_at=row.starts_at,
                ends_at=row.ends_at,
            ),
            zone=zone,
            now=clock.now(),
            check_center_closed=False,
        )
        if conflicts:
            raise SchedulingConflictError([c.as_wire() for c in conflicts])
        target = occurrence_initial_state(series, local_date)
        if target == SessionState.CONFIRMED:
            # The row holds nothing in ``conflict``: it takes the series'
            # current tutor before it is confirmed.
            row.tutor_user_id = tutor
        try:
            moved = await transition_class_session(
                session,
                row.id,
                to_state=target,
                expected_version=row.version,
                tutor_user_id=tutor if target == SessionState.OFFERED else None,
            )
        except SessionOverlapError as exc:
            raise SchedulingConflictError(
                [_race_conflict(exc.code, row).as_wire()]
            ) from None
        if target == SessionState.OFFERED:
            await attach_to_pending_series_offer(session, series, [moved])

    async def reject(
        self,
        session: AsyncSession,
        request: administration.ApprovalRequestView,
        principal: Principal,
        reason: str | None,
    ) -> None:
        # The core requires the reason (reason_required_on_reject).
        row = await _locked_conflict(session, request.subject_id)
        await transition_class_session(
            session,
            row.id,
            to_state=SessionState.CANCELLED,
            expected_version=row.version,
            reason=reason,
        )

    async def request_changes(
        self,
        session: AsyncSession,
        request: administration.ApprovalRequestView,
        principal: Principal,
        reason: str,
    ) -> None:
        # Never called: supports_request_changes is False.
        raise NotImplementedError

    async def summaries(
        self, session: AsyncSession, subject_ids: Iterable[uuid.UUID]
    ) -> Mapping[uuid.UUID, Mapping[str, Any]]:
        """``{class_session_id, series_id, starts_at, subject_name,
        student_display_names, tutor_display_name, conflict_codes}`` by
        session id; five statements (none without ids)."""
        ids = set(subject_ids)
        if not ids:
            return {}
        rows = list(
            await session.scalars(select(ClassSession).where(ClassSession.id.in_(ids)))
        )
        pairs = list(
            await session.execute(
                select(
                    ClassSessionParticipant.class_session_id,
                    ClassSessionParticipant.student_id,
                ).where(ClassSessionParticipant.class_session_id.in_(ids))
            )
        )
        students = await families.student_references(
            session, {student_id for _, student_id in pairs}
        )
        subjects = await catalog.subject_references(
            session, {row.subject_id for row in rows}
        )
        accounts = await identity.get_account_references(
            session, {row.tutor_user_id for row in rows if row.tutor_user_id}
        )
        names: dict[uuid.UUID, list[str]] = {}
        for session_id, student_id in pairs:
            student = students.get(student_id)
            if student is not None:
                names.setdefault(session_id, []).append(student.display_name)
        summaries: dict[uuid.UUID, Mapping[str, Any]] = {}
        for row in rows:
            subject = subjects.get(row.subject_id)
            tutor = accounts.get(row.tutor_user_id) if row.tutor_user_id else None
            summaries[row.id] = {
                "class_session_id": str(row.id),
                "series_id": None if row.series_id is None else str(row.series_id),
                "starts_at": row.starts_at.astimezone(UTC)
                .isoformat()
                .replace("+00:00", "Z"),
                "subject_name": None if subject is None else subject.name,
                "student_display_names": sorted(
                    names.get(row.id, []), key=str.casefold
                ),
                "tutor_display_name": None if tutor is None else tutor.display_name,
                "conflict_codes": list(row.conflict_codes),
            }
        return summaries


OCCURRENCE_CONFLICT_HANDLER: Final = OccurrenceConflictApprovalHandler()
