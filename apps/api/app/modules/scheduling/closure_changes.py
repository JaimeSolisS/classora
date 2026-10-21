"""Closure changes on series occurrences that already exist (#232; ADR-0005
D2, D4, D6; spec §3, W1.2).

:mod:`~app.modules.scheduling.materialize` skips a closure date only when it
**creates** an occurrence. :func:`reconcile_series_closures` brings a
series' **future** rows (``starts_at > now``) in line with the closures as
they are now. The nightly job (:mod:`~app.modules.scheduling.tasks`) runs it
for every unfinished series, right before the extension and in the same
transaction; a closure ``POST`` or ``PATCH`` (dates or ``is_active``) queues
that job after its commit (``app.core.jobs.defer_series_materialization``).

**Lock order** (the #53 one, then the rows): ``lock_students`` → the zone
``FOR SHARE`` → the series row ``FOR UPDATE`` → the series' future rows
``FOR UPDATE`` by start → (session-scope offers, through
``withdraw_pending_offers``). One ``catalog.service.closures_between`` call
gives the closed local dates of those rows (the date the row is held on, in
the series zone).

**Newly closed** (a closure added, extended or reactivated): a row in
``unassigned``, ``offered`` or ``confirmed`` (exceptions included) on a
closed date → ``skipped_closure``. It keeps its tutor, room and
participants and holds nothing (D2). ``completed``, ``cancelled``,
``conflict`` (its inbox item still decides it) and ``skipped_closure`` rows
are never touched. A pending **session-scope** offer of a skipped
``offered`` row is withdrawn (``offer_flow.withdraw_pending_offers``). A
pending **series** offer is left alone: it covers the series from its
``from_date``, so a skipped row keeps its covered-session row and an accept
or decline simply passes over it (only ``offered`` rows move). Notifications
``scheduling.occurrence_skipped_closure`` (:func:`skip_audience`, from the
state before the skip): the tutor of an ``offered`` or ``confirmed`` row;
for a ``confirmed`` row also the student's own login and its guardians'
(families only ever see ``confirmed`` sessions). One per recipient per
occurrence; params ``{class_session_id, series_id, starts_at}``.

**Reopened** (a closure deactivated or shrunk): a ``skipped_closure`` row on
a date that is no longer closed runs the #53 checks again (the references,
``validate_session_bounds``, ``collect_conflicts`` with ``now`` and
``check_center_closed=False``) with the tutor
:func:`~app.modules.scheduling.materialize.assigned_tutor` gives for its
rule date:

- clean → :func:`~app.modules.scheduling.materialize.
  occurrence_initial_state` (``unassigned``, ``offered``, then attached to
  the pending series offer, or ``confirmed``);
- any code → ``conflict`` with ``conflict_codes`` and one
  ``scheduling.occurrence_conflict`` inbox item (requester ``NULL``);
- an ``EXCLUDE`` race on the move (a session booked between the checks and
  the write) → ``conflict`` with that one code: the move runs in a
  savepoint, so the rest of the series still goes through.

A restore sends no notification (the row returns to a state families never
saw; the inbox item covers a conflict).

**Idempotent:** a second run finds nothing to change and writes no audit
event, no notification and no inbox item. Every function runs in the
caller's Unit of Work and never commits.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.administration import service as administration
from app.modules.catalog import service as catalog
from app.modules.families import service as families
from app.modules.scheduling.checks import SlotCandidate, collect_conflicts
from app.modules.scheduling.errors import (
    InvalidSessionTimesError,
    SessionOverlapError,
)
from app.modules.scheduling.locks import lock_students
from app.modules.scheduling.materialize import (
    OCCURRENCE_CONFLICT_TYPE,
    assigned_tutor,
    closed_dates,
    occurrence_initial_state,
    occurrence_local_date,
    revalidate_references,
    series_participant_ids,
)
from app.modules.scheduling.models import (
    ClassSeries,
    ClassSession,
    ClassSessionParticipant,
)
from app.modules.scheduling.offer_flow import lock_series, withdraw_pending_offers
from app.modules.scheduling.offer_records import (
    attach_to_pending_series_offer,
    rfc3339,
)
from app.modules.scheduling.sessions import transition_class_session
from app.modules.scheduling.states import SessionState
from app.modules.scheduling.times import validate_session_bounds

OCCURRENCE_SKIPPED_CLOSURE: Final = "scheduling.occurrence_skipped_closure"
"""The notification type of an occurrence skipped for a closure (#232 C1)."""

SKIPPABLE_STATES: Final = frozenset(
    {SessionState.UNASSIGNED, SessionState.OFFERED, SessionState.CONFIRMED}
)
"""The states a newly closed date moves to ``skipped_closure`` (A2)."""

_RECONCILED_STATES: Final = sorted(
    state.value for state in SKIPPABLE_STATES | {SessionState.SKIPPED_CLOSURE}
)

Audience = Literal["tutor", "family"]


class OccurrenceSkippedClosureParams(BaseModel):
    """The params of ``scheduling.occurrence_skipped_closure``: ids and an
    instant only (never the closure's name, which is staff free text)."""

    class_session_id: str
    series_id: str
    starts_at: str


def register_closure_notifications() -> None:
    """Register the notification type (again: a no-op)."""
    administration.register_notification_type(
        OCCURRENCE_SKIPPED_CLOSURE, OccurrenceSkippedClosureParams
    )


@dataclass(frozen=True)
class ReconcileCounts:
    """What one :func:`reconcile_series_closures` call changed."""

    skipped: int = 0
    restored: int = 0
    conflicts: int = 0


def skip_audience(previous_state: str) -> frozenset[Audience]:
    """Who is told about a skip, from the row's state **before** it (C2):
    the tutor once the row was ``offered`` or ``confirmed``; the family
    (the student's login and its guardians) only for ``confirmed`` (ADR-0005
    D2 family visibility). An ``unassigned`` row tells nobody."""
    if previous_state == SessionState.CONFIRMED:
        return frozenset({"tutor", "family"})
    if previous_state == SessionState.OFFERED:
        return frozenset({"tutor"})
    return frozenset()


async def _future_rows(
    session: AsyncSession, series_id: uuid.UUID, now: datetime
) -> list[ClassSession]:
    rows = await session.scalars(
        select(ClassSession)
        .where(
            ClassSession.series_id == series_id,
            ClassSession.starts_at > now,
            ClassSession.state.in_(_RECONCILED_STATES),
        )
        .order_by(ClassSession.starts_at, ClassSession.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return list(rows)


async def reconcile_series_closures(
    session: AsyncSession, series_id: uuid.UUID, *, now: datetime
) -> ReconcileCounts:
    """Skip the series' future rows on closed dates and re-check the skipped
    ones whose date reopened; see the module docstring."""
    student_ids = await series_participant_ids(session, series_id)
    await lock_students(session, student_ids)
    zone = await administration.get_center_time_zone(session, for_share=True)
    series = await lock_series(session, series_id)
    rows = await _future_rows(session, series_id, now)
    if not rows:
        return ReconcileCounts()
    series_zone = ZoneInfo(series.time_zone)
    held_on = {row.id: row.starts_at.astimezone(series_zone).date() for row in rows}
    closed = closed_dates(
        await catalog.closures_between(
            session, min(held_on.values()), max(held_on.values())
        )
    )
    to_skip = [
        row
        for row in rows
        if row.state in SKIPPABLE_STATES and held_on[row.id] in closed
    ]
    to_restore = [
        row
        for row in rows
        if row.state == SessionState.SKIPPED_CLOSURE and held_on[row.id] not in closed
    ]
    await _skip(session, series, to_skip)
    restored, conflicts = await _restore(
        session, series, to_restore, student_ids, zone=zone, now=now
    )
    return ReconcileCounts(skipped=len(to_skip), restored=restored, conflicts=conflicts)


async def _skip(
    session: AsyncSession, series: ClassSeries, rows: Sequence[ClassSession]
) -> None:
    if not rows:
        return
    previous = {row.id: (row.state, row.tutor_user_id) for row in rows}
    await withdraw_pending_offers(
        session,
        [row.id for row in rows if row.state == SessionState.OFFERED],
        by_user_id=None,
    )
    for row in rows:
        await transition_class_session(
            session,
            row.id,
            to_state=SessionState.SKIPPED_CLOSURE,
            expected_version=row.version,
        )
    await _notify_skipped(session, series, rows, previous)


async def _notify_skipped(
    session: AsyncSession,
    series: ClassSeries,
    rows: Sequence[ClassSession],
    previous: dict[uuid.UUID, tuple[str, uuid.UUID | None]],
) -> None:
    family_rows = [
        row.id for row in rows if "family" in skip_audience(previous[row.id][0])
    ]
    students: dict[uuid.UUID, list[uuid.UUID]] = {}
    if family_rows:
        for class_session_id, student_id in await session.execute(
            select(
                ClassSessionParticipant.class_session_id,
                ClassSessionParticipant.student_id,
            ).where(ClassSessionParticipant.class_session_id.in_(family_rows))
        ):
            students.setdefault(class_session_id, []).append(student_id)
    family_users = await families.student_notification_recipients(
        session, {student for ids in students.values() for student in ids}
    )
    for row in rows:
        state, tutor = previous[row.id]
        audience = skip_audience(state)
        recipients: set[uuid.UUID] = set()
        if "tutor" in audience and tutor is not None:
            recipients.add(tutor)
        if "family" in audience:
            for student_id in students.get(row.id, []):
                recipients.update(family_users.get(student_id, []))
        params = OccurrenceSkippedClosureParams(
            class_session_id=str(row.id),
            series_id=str(series.id),
            starts_at=rfc3339(row.starts_at),
        )
        for user_id in sorted(recipients):
            await administration.create_notification(
                session,
                recipient_user_id=user_id,
                notification_type=OCCURRENCE_SKIPPED_CLOSURE,
                params=params,
            )


async def _restore(
    session: AsyncSession,
    series: ClassSeries,
    rows: Sequence[ClassSession],
    student_ids: list[uuid.UUID],
    *,
    zone: str,
    now: datetime,
) -> tuple[int, int]:
    """``(restored, conflicts)``."""
    if not rows:
        return 0, 0
    reference_codes = await revalidate_references(session, series, student_ids)
    restored = conflicts = 0
    offered: list[ClassSession] = []
    for row in rows:
        local_date = occurrence_local_date(row, series.time_zone)
        tutor = assigned_tutor(series, local_date)
        codes = list(reference_codes)
        try:
            validate_session_bounds(row.starts_at, row.ends_at, zone)
        except InvalidSessionTimesError as exc:
            codes.append(exc.reason)
        else:
            found = await collect_conflicts(
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
                now=now,
                check_center_closed=False,
            )
            codes += [c.code for c in found if c.code not in codes]
        if not codes:
            target = occurrence_initial_state(series, local_date)
            try:
                async with session.begin_nested():
                    if target == SessionState.CONFIRMED:
                        # Confirmed needs the series' current tutor first
                        # (flushed, so the version below is the stored one).
                        row.tutor_user_id = tutor
                        await session.flush()
                    moved = await transition_class_session(
                        session,
                        row.id,
                        to_state=target,
                        expected_version=row.version,
                        tutor_user_id=tutor if target == SessionState.OFFERED else None,
                    )
            except SessionOverlapError as exc:
                # A4: the savepoint rolled back (and expired the row); the
                # race makes it a conflict.
                codes = [exc.code]
                await session.refresh(row)
            else:
                restored += 1
                if target == SessionState.OFFERED:
                    offered.append(moved)
                continue
        row.tutor_user_id = tutor
        row.conflict_codes = codes
        await session.flush()
        await transition_class_session(
            session,
            row.id,
            to_state=SessionState.CONFLICT,
            expected_version=row.version,
        )
        await administration.create_approval_request(
            session,
            request_type=OCCURRENCE_CONFLICT_TYPE,
            subject_id=row.id,
            requester_user_id=None,
        )
        conflicts += 1
    # #233 B2: restored offered rows join the pending series offer.
    await attach_to_pending_series_offer(session, series, offered)
    return restored, conflicts
