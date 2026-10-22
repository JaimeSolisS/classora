"""Editing and cancelling recurring series from a date on (#54 B, C2-C5;
spec §3, W1.5; ADR-0005 D2, D3, D4, D6; architecture ISSUE-SC2).

**Lock order** (B1), the #53 / #233 B3 one, so a write here and the nightly
job (:mod:`~app.modules.scheduling.materialize`, which takes the same order)
serialize on the series row and can't deadlock:

1. ``lock_students`` for the series' participants (ascending ids);
2. the center zone ``FOR SHARE``;
3. the **series row ``FOR UPDATE``** (re-read);
4. the series' rows on or after the edit date ``FOR UPDATE``, by id
   (re-read);
5. the #52 offer rows ``FOR UPDATE`` (``withdraw_pending_offers``,
   ``withdraw_series_offer``, ``cap_offer_deadlines``).

``PATCH /api/v1/class-sessions/{id}`` (:mod:`~app.modules.scheduling.
session_edit`) takes steps 1-2, the series row ``FOR SHARE`` (C1: it never
changes the series) and then its one row.

**"Started" (B3).** A row is *started* when ``starts_at <= now``
(``app.core.clock.now()``). Started, ``completed`` and ``cancelled`` rows
are **never updated** here: an edit or cancellation from a date ``D`` on
which the series has a started row is ``409 occurrence_started``, and the
rows before ``D`` are never read for writing. The one exception is the E6
**re-point** below, which changes ``series_id`` and ``original_start`` of a
future ``cancelled`` row and nothing else.

**The split algorithm** (:func:`classify`, pure; C2). The **in-scope** rows
are the old series' rows whose ``original_start`` falls on a local date on
or after ``D`` and that haven't started. The new rule is expanded from
``starts_on = D`` over ``[now, horizon_end)``, and in-scope rows are matched
to new occurrences **by local date** (a series has one row per date):

=====================================  =======================================
matched ``unassigned`` / ``offered`` /  **moved**: same id, new ``series_id``,
``confirmed`` / ``conflict``            ``original_start``, times and room; the
                                        tutor, students and state are kept (a
                                        ``conflict`` row that passes takes
                                        ``occurrence_initial_state``); the
                                        exception flag is reset; on a closed
                                        date → ``skipped_closure``
matched ``skipped_closure``             moved, its times updated (#232
                                        restores it when the date reopens)
matched ``cancelled``                   **re-pointed** (E6)
unmatched ``unassigned`` / ``offered``  ``cancelled``, reason
/ ``confirmed`` / ``conflict``          ``series_changed``
unmatched ``skipped_closure`` /         untouched, left on the old series
``cancelled``                           (``skipped_closure → cancelled`` is not
                                        a D2 move)
a new date with no old row              inserted as the nightly job would
=====================================  =======================================

**Re-point rationale (E6).** A future ``cancelled`` occurrence (for example
cancelled by #55) on a date the new rule also produces would otherwise be
recreated by the split and by the nightly job, because the new series has
no row on that date (``UNIQUE (series_id, original_start)`` plus the job's
pre-filter only see the new series' rows). Re-pointing keeps its content
(state, times, reason, tutor, students) and makes the date taken, so the
class doesn't come back.

**Checks (B4).** Every inserted row and every moved row whose slot (start,
end or room) changes, or that is in ``conflict``, runs the #53 checks:
``validate_session_bounds`` and ``collect_conflicts`` with the row's own
tutor (a ``conflict`` row: the series' assigned tutor for its date) and
``check_center_closed=False`` (a closed date gives ``skipped_closure``).
The ids of every row the edit moves, re-points or cancels are **excluded**
from the overlap checks (self-exclusion), so an edit never conflicts with
itself. The references (students, subject, the series' tutor and the new
room) are ``422`` first. Any conflict refuses the whole write with one
``409 scheduling_conflict`` in the #53 per-occurrence format
(``details.occurrence_start`` = the **new** instant, ``details.date``); a
moved row whose slot doesn't change is not re-checked (nothing about it
changes).

**Overrides (#236 F).** With an ``override`` (pairs; the route checked
``schedule.override`` before the ``404``) the conflicts go through
:func:`~app.modules.scheduling.overrides.evaluate_series_override`
instead of the plain refusal: pairs name the **new** instant, a hard
conflict or an unlisted soft pair is the same ``409``, and after the
writes :func:`~app.modules.scheduling.overrides.record_series_overrides`
stores one row per overridden occurrence with ``class_series_id`` = the
resulting series (the new one after a split) and one
``scheduling.series_override`` event (``write = "edit"``). An edit needs
its own override: a row overridden earlier and re-checked here must be
listed again if its soft conflict still occurs; its older rows stay as
history and are never consulted. ``series_unchanged`` stays ``422`` with an
override (re-checking an unchanged row is the conflict-override route's
job).

**In place or split (C3).** With no old-series row before ``D`` the series
itself takes the new rule (``starts_on = D``; no lineage). Otherwise the old
series ends (``until_on = D - 1``, ``count`` cleared, the rule rebuilt,
``last_occurrence_on`` = its last rule date before ``D``) and a **new**
series starts on ``D`` with the same kind, subject, tutor assignment
(``assignment_from`` = the later of the old one and ``D``, #233 E4),
students and zone, and ``split_from_series_id`` = the old one.

**Side effects (B6)**, in the same transaction: the open
``scheduling.occurrence_conflict`` request of a row that leaves
``conflict`` is cancelled by the system; a cancelled or skipped ``offered``
row's pending session offer is ``withdrawn``; a moved ``offered`` row's
offer gets its ``respond_by`` capped at its first covered start (D3); the
notifications of :mod:`~app.modules.scheduling.notifications`; the
automatic audit events plus one named event per write
(``scheduling.series_changed`` / ``scheduling.series_cancelled``).

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Final, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.audit import record_audit_event
from app.core.authz import RecordNotFoundError
from app.core.errors import FieldError
from app.core.models import ensure_version
from app.modules.administration import service as administration
from app.modules.catalog import service as catalog
from app.modules.scheduling.checks import SlotCandidate, collect_conflicts
from app.modules.scheduling.conflicts import ROOM_OVERLAP, TUTOR_OVERLAP, Conflict
from app.modules.scheduling.errors import (
    InvalidSessionTimesError,
    OccurrenceStartedError,
    SchedulingConflictError,
    SeriesEndedError,
    SeriesOfferPendingError,
    SessionOverlapError,
    SessionValidationError,
)
from app.modules.scheduling.locks import lock_students
from app.modules.scheduling.materialize import (
    OCCURRENCE_CONFLICT_TYPE,
    PlannedOccurrence,
    assigned_tutor,
    closed_dates,
    insert_occurrences,
    occurrence_bounds_error,
    occurrence_initial_state,
    occurrence_local_date,
    occurrence_wire,
    series_participant_ids,
    validate_slot_references,
)
from app.modules.scheduling.models import (
    SERIES_ASSIGNMENT_PENDING,
    ClassSeries,
    ClassSeriesParticipant,
    ClassSession,
)
from app.modules.scheduling.notifications import (
    Affected,
    notify_series_cancelled,
    notify_series_changed,
)
from app.modules.scheduling.offer_flow import (
    cap_offer_deadlines,
    lock_series,
    lock_sessions,
    withdraw_pending_offers,
    withdraw_pending_substitutions,
    withdraw_series_offer,
)
from app.modules.scheduling.offer_records import attach_to_pending_series_offer
from app.modules.scheduling.overrides import (
    OccurrenceConflict,
    evaluate_series_override,
    record_series_overrides,
)
from app.modules.scheduling.recurrence import (
    InvalidRecurrenceError,
    Occurrence,
    RecurrenceFields,
    build_rrule,
    expand,
    horizon_end,
    iter_local_starts,
    last_occurrence_on,
    validate_recurrence,
)
from app.modules.scheduling.schemas import (
    ClassSeriesCancel,
    ClassSeriesCancelled,
    ClassSeriesEdit,
    ClassSeriesEdited,
    SeriesCancelSummary,
    SeriesEditSummary,
)
from app.modules.scheduling.series import (
    MINUTES_PER_DAY,
    RECURRENCE_MESSAGES,
    get_class_series,
)
from app.modules.scheduling.sessions import (
    flush_class_sessions,
    transition_class_session,
)
from app.modules.scheduling.states import SessionState
from app.modules.scheduling.times import validate_session_bounds

SERIES_CHANGED_EVENT: Final = "scheduling.series_changed"
SERIES_CANCELLED_EVENT: Final = "scheduling.series_cancelled"
SERIES_CHANGED_REASON: Final = "series_changed"
"""The system cancellation reason of a row whose date left the rule (D4)."""

MOVABLE_STATES: Final = frozenset(
    {
        SessionState.UNASSIGNED,
        SessionState.OFFERED,
        SessionState.CONFIRMED,
        SessionState.CONFLICT,
    }
)
"""The states an edit moves (matched) or cancels (unmatched)."""

Mode = Literal["following", "all"]


# --- The pure classification (C2 steps 1 and 3) ------------------------------------


@dataclass(frozen=True)
class ExistingRow:
    """An old-series row as the classification sees it: ``local_date`` is
    its rule date (``original_start`` in the series zone)."""

    id: uuid.UUID
    state: str
    local_date: date
    started: bool


@dataclass(frozen=True)
class EditPlan:
    """What :func:`classify` decided, each tuple by date. ``moved`` and
    ``repointed`` pair a row id with the date it is matched on."""

    moved: tuple[tuple[uuid.UUID, date], ...] = ()
    repointed: tuple[tuple[uuid.UUID, date], ...] = ()
    cancelled: tuple[uuid.UUID, ...] = ()
    untouched: tuple[uuid.UUID, ...] = ()
    new_dates: tuple[date, ...] = ()


def in_scope(rows: Iterable[ExistingRow], from_date: date) -> list[ExistingRow]:
    """The rows an edit or cancellation from ``from_date`` may change: rule
    date on or after it, not started (B3), by date."""
    return sorted(
        (row for row in rows if row.local_date >= from_date and not row.started),
        key=lambda row: (row.local_date, row.id),
    )


def classify(rows: Sequence[ExistingRow], new_dates: Iterable[date]) -> EditPlan:
    """Match the in-scope ``rows`` to the new rule's local dates (the table
    of the module docstring). A ``completed`` row (never in scope in
    practice: it has started) is untouched and keeps its date taken."""
    wanted = set(new_dates)
    taken: set[date] = set()
    moved: list[tuple[uuid.UUID, date]] = []
    repointed: list[tuple[uuid.UUID, date]] = []
    cancelled: list[uuid.UUID] = []
    untouched: list[uuid.UUID] = []
    for row in sorted(rows, key=lambda r: (r.local_date, r.id)):
        state = SessionState(row.state)
        if row.local_date in wanted and row.local_date not in taken:
            taken.add(row.local_date)
            if state in MOVABLE_STATES or state == SessionState.SKIPPED_CLOSURE:
                moved.append((row.id, row.local_date))
            elif state == SessionState.CANCELLED:
                repointed.append((row.id, row.local_date))
            else:
                untouched.append(row.id)
        elif state in MOVABLE_STATES:
            cancelled.append(row.id)
        else:
            untouched.append(row.id)
    return EditPlan(
        moved=tuple(moved),
        repointed=tuple(repointed),
        cancelled=tuple(cancelled),
        untouched=tuple(untouched),
        new_dates=tuple(sorted(wanted - taken)),
    )


def last_rule_date_before(
    series_rule: str, *, starts_on: date, before: date
) -> date | None:
    """The last local date of the rule strictly before ``before`` (``None``:
    none)."""
    last = None
    for local in iter_local_starts(series_rule, starts_on=starts_on, start_time=time()):
        if local.date() >= before:
            break
        last = local.date()
    return last


# --- Shared steps ------------------------------------------------------------


def _error(path: list[str | int], code: str, message: str) -> SessionValidationError:
    error: FieldError = {"path": path, "code": code, "message": message}
    return SessionValidationError([error])


@dataclass
class _Locked:
    series: ClassSeries
    zone: str
    student_ids: list[uuid.UUID]
    rows: list[ClassSession]
    """Every row of the series (unlocked), by original start."""
    now: datetime
    today: date


async def _lock(
    session: AsyncSession, class_series_id: uuid.UUID, version: int
) -> _Locked:
    """``404`` → steps 1-3 of the lock order → ``409 version_conflict``."""
    found = await session.get(ClassSeries, class_series_id)
    if found is None:
        raise RecordNotFoundError()
    student_ids = await series_participant_ids(session, found.id)
    await lock_students(session, student_ids)
    zone = await administration.get_center_time_zone(session, for_share=True)
    series = await lock_series(session, found.id)
    ensure_version(series, version)
    rows = list(
        await session.scalars(
            select(ClassSession)
            .where(ClassSession.series_id == series.id)
            .order_by(ClassSession.original_start, ClassSession.id)
        )
    )
    return _Locked(
        series=series,
        zone=zone,
        student_ids=student_ids,
        rows=rows,
        now=clock.now(),
        today=clock.today_in(zone),
    )


def _existing(row: ClassSession, zone: str, now: datetime) -> ExistingRow:
    return ExistingRow(
        id=row.id,
        state=row.state,
        local_date=occurrence_local_date(row, zone),
        started=row.starts_at <= now,
    )


def _start_date(ctx: _Locked, mode: Mode, from_date: date | None) -> date:
    """C3 steps 7-9: ``D``, then ``422 from_date_in_past``, ``409
    series_ended`` and ``409 occurrence_started``."""
    series = ctx.series
    if mode == "following":
        assert from_date is not None  # noqa: S101 - the schema requires it
        start = from_date
        if start < ctx.today:
            raise _error(
                ["body", "from_date"],
                "from_date_in_past",
                "The change can't start before today.",
            )
    else:
        upcoming = [
            occurrence_local_date(row, series.time_zone)
            for row in ctx.rows
            if row.starts_at > ctx.now
        ]
        start = (
            max(min(upcoming), ctx.today)
            if upcoming
            else max(series.starts_on, ctx.today)
        )
    if series.last_occurrence_on is not None and start > series.last_occurrence_on:
        raise SeriesEndedError()
    if any(
        row.starts_at <= ctx.now
        and occurrence_local_date(row, series.time_zone) == start
        for row in ctx.rows
    ):
        raise OccurrenceStartedError()
    return start


async def _lock_from(
    session: AsyncSession, ctx: _Locked, start: date
) -> list[ClassSession]:
    """Step 4: the series' rows with a rule date on or after ``start``,
    ``FOR UPDATE`` by id and re-read; returned by original start."""
    ids = [
        row.id
        for row in ctx.rows
        if occurrence_local_date(row, ctx.series.time_zone) >= start
    ]
    locked = await lock_sessions(session, ids)
    return sorted(locked, key=lambda row: (row.original_start, row.id))


async def cancel_conflict_request(
    session: AsyncSession, class_session_id: uuid.UUID
) -> None:
    """B6: the row left ``conflict``; its open inbox request is cancelled by
    the system. A request that isn't open is left alone."""
    request_id = await administration.approval_request_id_for_subject(
        session, request_type=OCCURRENCE_CONFLICT_TYPE, subject_id=class_session_id
    )
    if request_id is None:
        return
    try:
        await administration.cancel_approval_request(
            session, request_id=request_id, by_user_id=None
        )
    except administration.ApprovalTransitionInvalidError:
        # Already decided: nothing is open. The check runs before any write.
        return


async def _cancel_rows(
    session: AsyncSession,
    rows: Sequence[ClassSession],
    *,
    reason: str,
    actor_id: uuid.UUID,
) -> list[Affected]:
    """Cancel ``rows`` (movable states) with ``reason``: their pending
    session offers are withdrawn first and their conflict requests are
    cancelled. Returns them as they were."""
    affected = [
        Affected(row.id, row.state, row.tutor_user_id, "cancelled") for row in rows
    ]
    # #55 G2/G3: the session-scope offers of either kind (an offered row's
    # assignment, a confirmed row's pending substitution).
    await withdraw_pending_offers(
        session,
        [row.id for row in rows],
        by_user_id=actor_id,
    )
    for row in rows:
        was_conflict = row.state == SessionState.CONFLICT
        await transition_class_session(
            session,
            row.id,
            to_state=SessionState.CANCELLED,
            expected_version=row.version,
            reason=reason,
        )
        if was_conflict:
            await cancel_conflict_request(session, row.id)
    return affected


# --- C3: edits ---------------------------------------------------------------


def _fields(body: ClassSeriesEdit) -> RecurrenceFields:
    rule = body.recurrence
    return RecurrenceFields(
        weekdays=tuple(rule.weekdays),
        interval=rule.interval,
        until_on=rule.until,
        count=rule.count,
    )


def _check_rule(fields: RecurrenceFields, start: date, body: ClassSeriesEdit) -> None:
    """C3 steps 10-11: the #53 recurrence ``422`` codes (``until`` against
    ``D``), then ``crosses_local_date``."""
    try:
        validate_recurrence(fields, start)
        last_occurrence_on(fields, start)
    except InvalidRecurrenceError as exc:
        path: list[str | int] = ["body", "recurrence"]
        if exc.field != "recurrence":
            path.append(exc.field)
        raise _error(path, exc.reason, RECURRENCE_MESSAGES[exc.reason]) from None
    minutes = body.start_time.hour * 60 + body.start_time.minute
    if minutes + body.duration_minutes > MINUTES_PER_DAY:
        raise _error(
            ["body", "start_time"],
            "crosses_local_date",
            "A session starts and ends on the same local date.",
        )


def _unchanged(
    series: ClassSeries, body: ClassSeriesEdit, fields: RecurrenceFields
) -> bool:
    return (
        series.start_time == body.start_time
        and series.duration_minutes == body.duration_minutes
        and series.classroom_id == body.classroom_id
        and sorted(series.weekdays) == sorted(fields.weekdays)
        and series.interval == fields.interval
        and series.until_on == fields.until_on
        and series.count == fields.count
    )


@dataclass
class _Move:
    row: ClassSession
    occurrence: Occurrence
    closed: bool
    previous_state: str
    previous_tutor: uuid.UUID | None
    was_exception: bool
    slot_changed: bool
    target: SessionState | None = None
    """The state after a passing check of a ``conflict`` row."""
    tutor: uuid.UUID | None = None


def _race(code: str, classroom_id: uuid.UUID | None) -> Conflict:
    if code == ROOM_OVERLAP:
        return Conflict(
            ROOM_OVERLAP, {"classroom_id": str(classroom_id), "class_session_ids": []}
        )
    return Conflict(TUTOR_OVERLAP, {"class_session_ids": []})


async def _check(
    session: AsyncSession,
    ctx: _Locked,
    occurrence: Occurrence,
    *,
    tutor: uuid.UUID | None,
    classroom_id: uuid.UUID | None,
    exclude: frozenset[uuid.UUID],
) -> list[Conflict]:
    try:
        validate_session_bounds(occurrence.starts_at, occurrence.ends_at, ctx.zone)
    except InvalidSessionTimesError:
        raise occurrence_bounds_error(occurrence) from None
    conflicts = await collect_conflicts(
        session,
        SlotCandidate(
            kind=ctx.series.kind,
            student_ids=tuple(ctx.student_ids),
            subject_id=ctx.series.subject_id,
            tutor_user_id=tutor,
            classroom_id=classroom_id,
            starts_at=occurrence.starts_at,
            ends_at=occurrence.ends_at,
        ),
        zone=ctx.zone,
        now=ctx.now,
        check_center_closed=False,
        exclude_session_ids=exclude,
    )
    return conflicts


def _new_series(old: ClassSeries, body: ClassSeriesEdit, start: date) -> ClassSeries:
    """The series a split creates (not yet added to the session)."""
    fields = _fields(body)
    assignment_from = old.assignment_from
    if assignment_from is not None and assignment_from < start:
        assignment_from = start
    return ClassSeries(
        id=uuid.uuid7(),
        kind=old.kind,
        subject_id=old.subject_id,
        tutor_user_id=old.tutor_user_id,
        assignment_status=old.assignment_status,
        assignment_from=assignment_from,
        classroom_id=body.classroom_id,
        starts_on=start,
        start_time=body.start_time,
        duration_minutes=body.duration_minutes,
        time_zone=old.time_zone,
        weekdays=sorted(fields.weekdays),
        interval=fields.interval,
        until_on=fields.until_on,
        count=fields.count,
        rrule=build_rrule(fields),
        last_occurrence_on=last_occurrence_on(fields, start),
        split_from_series_id=old.id,
    )


def _apply_in_place(series: ClassSeries, body: ClassSeriesEdit, start: date) -> None:
    fields = _fields(body)
    series.classroom_id = body.classroom_id
    series.starts_on = start
    series.start_time = body.start_time
    series.duration_minutes = body.duration_minutes
    series.weekdays = sorted(fields.weekdays)
    series.interval = fields.interval
    series.until_on = fields.until_on
    series.count = fields.count
    series.rrule = build_rrule(fields)
    series.last_occurrence_on = last_occurrence_on(fields, start)


def _end_before(series: ClassSeries, start: date) -> None:
    """The old series of a split ends the day before ``start`` (C3): a
    ``COUNT`` series becomes an ``UNTIL`` series."""
    series.last_occurrence_on = last_rule_date_before(
        series.rrule, starts_on=series.starts_on, before=start
    ) or start - timedelta(days=1)
    series.until_on = start - timedelta(days=1)
    series.count = None
    series.rrule = build_rrule(
        RecurrenceFields(
            weekdays=tuple(series.weekdays),
            interval=series.interval,
            until_on=series.until_on,
            count=None,
        )
    )


async def edit_class_series(
    session: AsyncSession,
    class_series_id: uuid.UUID,
    body: ClassSeriesEdit,
    *,
    horizon_weeks: int,
    actor_id: uuid.UUID,
) -> ClassSeriesEdited:
    """``POST /api/v1/class-series/{id}/edits`` (C3): see the module
    docstring and the route for the order."""
    ctx = await _lock(session, class_series_id, body.version)
    old = ctx.series
    if old.assignment_status == SERIES_ASSIGNMENT_PENDING:
        raise SeriesOfferPendingError()
    start = _start_date(ctx, body.mode, body.from_date)
    fields = _fields(body)
    _check_rule(fields, start, body)
    await validate_slot_references(
        session,
        ctx.student_ids,
        subject_id=old.subject_id,
        tutor_user_id=old.tutor_user_id,
        classroom_id=body.classroom_id,
    )
    rows = await _lock_from(session, ctx, start)
    existing = {row.id: _existing(row, old.time_zone, ctx.now) for row in rows}
    scope = in_scope(existing.values(), start)
    by_id = {row.id: row for row in rows}
    if _unchanged(old, body, fields) and not any(
        by_id[row.id].is_exception for row in scope
    ):
        raise _error(
            ["body"], "series_unchanged", "The new rule is the same as the series'."
        )
    split = any(occurrence_local_date(row, old.time_zone) < start for row in ctx.rows)
    target = _new_series(old, body, start) if split else old
    occurrences = expand(
        build_rrule(fields),
        starts_on=start,
        start_time=body.start_time,
        duration_minutes=body.duration_minutes,
        zone=old.time_zone,
        window_start=ctx.now,
        window_end=horizon_end(ctx.now, horizon_weeks),
    )
    by_date = {o.local_date: o for o in occurrences}
    plan = classify(scope, by_date)
    closed = (
        closed_dates(
            await catalog.closures_between(
                session, occurrences[0].local_date, occurrences[-1].local_date
            )
        )
        if occurrences
        else set()
    )
    exclude = frozenset(
        [row_id for row_id, _ in plan.moved]
        + [row_id for row_id, _ in plan.repointed]
        + list(plan.cancelled)
    )

    # C2 step 4: check every changed row and every new one, by new instant.
    moves: list[_Move] = []
    for row_id, day in plan.moved:
        row = by_id[row_id]
        occurrence = by_date[day]
        moves.append(
            _Move(
                row=row,
                occurrence=occurrence,
                closed=day in closed,
                previous_state=row.state,
                previous_tutor=row.tutor_user_id,
                was_exception=row.is_exception,
                slot_changed=(row.starts_at, row.ends_at, row.classroom_id)
                != (occurrence.starts_at, occurrence.ends_at, body.classroom_id),
            )
        )
    planned: list[PlannedOccurrence] = []
    checks: list[tuple[Occurrence, list[Conflict]]] = []
    for move in moves:
        state = SessionState(move.row.state)
        if state == SessionState.SKIPPED_CLOSURE or move.closed:
            continue
        if state == SessionState.CONFLICT:
            move.tutor = assigned_tutor(target, move.occurrence.local_date)
            move.target = occurrence_initial_state(target, move.occurrence.local_date)
        elif not move.slot_changed:
            continue
        else:
            move.tutor = move.row.tutor_user_id
        checks.append(
            (
                move.occurrence,
                await _check(
                    session,
                    ctx,
                    move.occurrence,
                    tutor=move.tutor,
                    classroom_id=body.classroom_id,
                    exclude=exclude,
                ),
            )
        )
    for day in plan.new_dates:
        occurrence = by_date[day]
        if day in closed:
            planned.append(PlannedOccurrence(occurrence, SessionState.SKIPPED_CLOSURE))
            continue
        checks.append(
            (
                occurrence,
                await _check(
                    session,
                    ctx,
                    occurrence,
                    tutor=assigned_tutor(target, day),
                    classroom_id=body.classroom_id,
                    exclude=exclude,
                ),
            )
        )
        planned.append(
            PlannedOccurrence(occurrence, occurrence_initial_state(target, day))
        )
    checks.sort(key=lambda item: item[0].starts_at)
    refused = [
        occurrence_wire(conflict, occurrence)
        for occurrence, found in checks
        for conflict in found
    ]
    # #236 F1 step 5: pairs name the new instants (details.occurrence_start).
    decision = evaluate_series_override(
        [
            OccurrenceConflict(occurrence.original_start, conflict)
            for occurrence, found in checks
            for conflict in found
        ],
        body.override,
    )
    if not decision.allowed:
        raise SchedulingConflictError(refused)

    # C2 step 5: write. The series first (a split's new series must exist
    # before rows move to it).
    if split:
        _end_before(old, start)
        session.add(target)
        await session.flush()
        session.add_all(
            ClassSeriesParticipant(class_series_id=target.id, student_id=student_id)
            for student_id in ctx.student_ids
        )
    else:
        _apply_in_place(old, body, start)
    await session.flush()

    affected = await _cancel_rows(
        session,
        [by_id[row_id] for row_id in plan.cancelled],
        reason=SERIES_CHANGED_REASON,
        actor_id=actor_id,
    )
    await _apply_moves(session, target, moves, body.classroom_id, actor_id=actor_id)
    for row_id, day in plan.repointed:
        row = by_id[row_id]
        row.series_id = target.id
        row.original_start = by_date[day].original_start
    await flush_class_sessions(session)
    # "create" mode: an EXCLUDE race is one per-occurrence 409.
    inserted = await insert_occurrences(
        session, target, planned, ctx.student_ids, mode="create"
    )
    await attach_to_pending_series_offer(
        session, target, [row for row in inserted if row.state == SessionState.OFFERED]
    )
    await cap_offer_deadlines(
        session,
        [m.row.id for m in moves if m.row.state == SessionState.OFFERED],
    )
    if body.override is not None:
        await record_series_overrides(
            session,
            class_series_id=target.id,
            decision=decision,
            reason=body.override.reason,
            write="edit",
            actor_id=actor_id,
        )

    summary = SeriesEditSummary(
        moved=len(moves),
        created=len(inserted),
        cancelled=len(plan.cancelled),
        skipped_closure=sum(
            1 for m in moves if m.row.state == SessionState.SKIPPED_CLOSURE
        )
        + sum(1 for row in inserted if row.state == SessionState.SKIPPED_CLOSURE),
        exceptions_reset=sum(1 for m in moves if m.was_exception),
    )
    affected += [
        Affected(m.row.id, m.previous_state, m.previous_tutor, "changed")
        for m in moves
        if m.slot_changed or m.closed
    ]
    await notify_series_changed(
        session, affected, series_id=target.id, from_date=start, actor_id=actor_id
    )
    record_audit_event(
        session,
        action=SERIES_CHANGED_EVENT,
        record_type="class_series",
        record_id=old.id,
        details={
            "series_id": str(old.id),
            "new_series_id": str(target.id) if split else None,
            "mode": body.mode,
            "from_date": start.isoformat(),
            "moved": summary.moved,
            "created": summary.created,
            "cancelled": summary.cancelled,
            "exceptions_reset": summary.exceptions_reset,
        },
    )
    await session.flush()
    return ClassSeriesEdited(
        series=await get_class_series(session, target.id),
        previous_series_id=old.id if split else None,
        split=split,
        summary=summary,
        warnings=[],
    )


async def _apply_moves(
    session: AsyncSession,
    target: ClassSeries,
    moves: Sequence[_Move],
    classroom_id: uuid.UUID | None,
    *,
    actor_id: uuid.UUID,
) -> None:
    """C2 step 5 (updates): each matched row takes the new series, rule
    instant, times and room; see the module docstring for the states."""
    to_skip = [
        m
        for m in moves
        if m.closed
        and m.row.state
        in (SessionState.UNASSIGNED, SessionState.OFFERED, SessionState.CONFIRMED)
    ]
    # #55 G3: skipped rows lose their session-scope offers of either kind;
    # a confirmed row moved to a new slot or room loses its pending
    # substitution (its snapshot no longer matches).
    await withdraw_pending_offers(
        session, [m.row.id for m in to_skip], by_user_id=actor_id
    )
    await withdraw_pending_substitutions(
        session,
        [
            m.row.id
            for m in moves
            if not m.closed and m.slot_changed and m.row.state == SessionState.CONFIRMED
        ],
    )
    for move in to_skip:
        # Skip first, at the old times: the row then holds nothing (D2).
        await transition_class_session(
            session,
            move.row.id,
            to_state=SessionState.SKIPPED_CLOSURE,
            expected_version=move.row.version,
        )
    for move in moves:
        row, occurrence = move.row, move.occurrence
        was_conflict = row.state == SessionState.CONFLICT
        row.series_id = target.id
        row.original_start = occurrence.original_start
        row.starts_at = occurrence.starts_at
        row.ends_at = occurrence.ends_at
        row.scheduled_minutes = int(
            (occurrence.ends_at - occurrence.starts_at).total_seconds() // 60
        )
        row.classroom_id = classroom_id
        row.is_exception = False
        if was_conflict and move.closed:
            # A conflict row holds nothing, as a skipped one; conflict ->
            # skipped_closure is not a D2 move, so the system sets it (the
            # row is never offered or confirmed on the way).
            row.state = SessionState.SKIPPED_CLOSURE.value
        elif was_conflict:
            row.tutor_user_id = move.tutor
        try:
            # Flush before a transition (#266): its version check reads the
            # stored version.
            await flush_class_sessions(session)
            if was_conflict and not move.closed:
                assert move.target is not None  # noqa: S101 - set by the check
                await transition_class_session(
                    session,
                    row.id,
                    to_state=move.target,
                    expected_version=row.version,
                    tutor_user_id=(
                        move.tutor if move.target == SessionState.OFFERED else None
                    ),
                )
        except SessionOverlapError as exc:
            raise SchedulingConflictError(
                [occurrence_wire(_race(exc.code, classroom_id), occurrence)]
            ) from None
        if was_conflict:
            await cancel_conflict_request(session, row.id)
    offered = [
        m.row
        for m in moves
        if m.previous_state == SessionState.CONFLICT
        and m.row.state == SessionState.OFFERED
    ]
    await attach_to_pending_series_offer(session, target, offered)


# --- C5: cancellations -------------------------------------------------------------


async def cancel_class_series(
    session: AsyncSession,
    class_series_id: uuid.UUID,
    body: ClassSeriesCancel,
    *,
    actor_id: uuid.UUID,
) -> ClassSeriesCancelled:
    """``POST /api/v1/class-series/{id}/cancellations`` (C5): the C3 steps
    1-9 (no ``series_offer_pending``: a pending series offer is withdrawn),
    then every in-scope ``unassigned`` / ``offered`` / ``confirmed`` /
    ``conflict`` row is cancelled with the reason, ``skipped_closure`` and
    ``cancelled`` rows are kept, and the series ends the day before ``D``
    (``until_on = last_occurrence_on = D - 1``)."""
    ctx = await _lock(session, class_series_id, body.version)
    series = ctx.series
    start = _start_date(ctx, body.mode, body.from_date)
    rows = await _lock_from(session, ctx, start)
    scope = in_scope((_existing(row, series.time_zone, ctx.now) for row in rows), start)
    by_id = {row.id: row for row in rows}
    to_cancel = [
        by_id[row.id] for row in scope if SessionState(row.state) in MOVABLE_STATES
    ]
    before = {
        row.id: Affected(row.id, row.state, row.tutor_user_id, "cancelled")
        for row in to_cancel
    }
    kept = sum(1 for row in scope if row.state == SessionState.SKIPPED_CLOSURE)
    if series.assignment_status == SERIES_ASSIGNMENT_PENDING:
        # #233 B6: the offer covers the series from its from_date; it is
        # withdrawn as a whole (its offered rows go back to unassigned).
        await withdraw_series_offer(session, series.id, by_user_id=actor_id)
    await _cancel_rows(session, to_cancel, reason=body.reason, actor_id=actor_id)
    series.until_on = start - timedelta(days=1)
    series.last_occurrence_on = series.until_on
    series.count = None
    series.rrule = build_rrule(
        RecurrenceFields(
            weekdays=tuple(series.weekdays),
            interval=series.interval,
            until_on=series.until_on,
            count=None,
        )
    )
    await session.flush()
    await notify_series_cancelled(
        session,
        list(before.values()),
        series_id=series.id,
        from_date=start,
        actor_id=actor_id,
    )
    record_audit_event(
        session,
        action=SERIES_CANCELLED_EVENT,
        record_type="class_series",
        record_id=series.id,
        details={
            "series_id": str(series.id),
            "from_date": start.isoformat(),
            "cancelled": len(to_cancel),
        },
    )
    await session.flush()
    return ClassSeriesCancelled(
        series=await get_class_series(session, series.id),
        summary=SeriesCancelSummary(
            cancelled=len(to_cancel), skipped_closure_kept=kept
        ),
    )
