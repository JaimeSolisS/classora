"""Rolling materialization of class series occurrences (#53 F; ADR-0005 D4,
D6; spec §3, W1.2).

:func:`materialize_series` is the **one** function that turns a series rule
into ``class_sessions`` rows, for the series create (``mode="create"``) and
the nightly job (``mode="extend"``, :mod:`~app.modules.scheduling.tasks`).
Both share the expansion (:func:`~app.modules.scheduling.recurrence.expand`,
with its DST rule: naive local expansion, ``fold=0``, the end from elapsed
minutes), the closure skip and the checks.

**Window:** ``[now, horizon_end(now, horizon_weeks))`` on instants; it never
reaches the past.

**Lock order** (fixed; deadlock-free together with #48 and the
``scheduling.occurrence_conflict`` approve):

1. ``lock_students`` for the series' participants (ascending ids);
2. the center zone ``FOR SHARE``;
3. for a ``group`` series, the **group row ``FOR UPDATE``** (#56 C2, F1:
   :mod:`~app.modules.scheduling.group_lock`); for a ``course`` series, the
   **cohort row ``FOR UPDATE``** (``courses.service.lock_cohort``, #65 C3,
   G1: "group" reads "group or cohort");
4. the **series row ``FOR UPDATE``**.

The create takes steps 1 and 2 itself before inserting the series (so in
``create`` mode they are already held); the extend mode takes all four
(:func:`lock_series_for_write`). **Group series (#56 C2):** after the series
lock the participants are read again; when they differ from the locked set
(an enrollment or withdrawal committed in between),
:class:`~app.modules.scheduling.group_lock.SeriesParticipantsChangedError`
is raised: the nightly job rolls that series' unit back and retries it (at
most 3 times, then counts it as failed and the next night retries); the
same holds for a ``course`` series under its cohort lock (#65 C3;
participants change only from #66 on). A group occurrence copies
``group_id`` (a course occurrence ``cohort_id``) from its series and takes
one participant per series participant. The job never re-checks the group
maximum (the enrollment enforced or overrode it), only each occurrence's
room capacity (``room_capacity_exceeded`` → a ``conflict`` row).

**Each occurrence**, in order:

- on a **closed local date** (one ``catalog.service.closures_between`` call
  over the window's dates) → ``skipped_closure``, with no checks. It keeps
  the tutor, room and participant (so #232 can restore it) and holds
  nothing (ADR-0005 D2, D6);
- otherwise ``validate_session_bounds`` and
  :func:`~app.modules.scheduling.checks.collect_conflicts` with
  ``check_center_closed=False`` (a closure date never gets here). In extend
  mode the references are re-validated too (:func:`revalidate_references`:
  a tutor, student, subject or room deactivated since the create).

**Create mode:** any conflict refuses the whole create: ``409
scheduling_conflict`` listing every conflict of every occurrence, each with
``details.occurrence_start`` and ``details.date``, by occurrence then rule
order. Nothing is stored (the Unit of Work rolls the series back). A bounds
failure (only where a DST change falls at local midnight) is ``422
crosses_local_date`` on ``start_time``. With a series ``override`` (#236)
the conflicts go through :func:`~app.modules.scheduling.overrides.
evaluate_series_override` instead: allowed, the overridden occurrences are
created in their initial state (no ``conflict_codes``) and the decision is
returned for the caller to record; refused, the same ``409``.

**Overrides are never read here (#236 G1).** The extend mode (and #232's
closure restore) never consult ``class_session_overrides``: a new
occurrence that fails a soft check is a ``conflict`` row with an inbox item,
whatever earlier writes overrode. An override covers only the occurrences
the write that carried it checked, because each override is a fresh
authorization against conflicts recomputed then (W1.6); for example, a
qualification revoked later must not stay overridden.

**Extend mode:** a failing occurrence is stored as **``conflict``** with its
``conflict_codes``, the series' tutor and room and its participant; it
holds nothing (D2), and one ``scheduling.occurrence_conflict`` approval
request (requester ``NULL``, the system) is raised for it. Nothing is
created silently and nothing is dropped (D4, W1.2).

**Idempotency (#53 E3):** the series row lock plus a **pre-filter** (the
``original_start`` values the series already has in the window are left
out) mean a rerun inserts nothing. The rows are inserted through the ORM so
they are audited; ``INSERT … ON CONFLICT DO NOTHING`` is not used because
the #22 audit guard refuses Core inserts into audited tables. The unique
constraint ``uq_class_sessions_series_original_start`` is the backstop: a
violation (which the lock rules out) rolls back that series' transaction and
the next run retries it.

**Initial state (#233 B1, B2).** A clean occurrence takes
:func:`occurrence_initial_state` for its local date: ``unassigned`` (no
tutor) when the series has no tutor or the date is before
``assignment_from``; ``offered`` while the series offer is ``pending``;
``confirmed`` once it is ``accepted`` (ADR-0005 D3: no further offer). Every
row (``skipped_closure`` and ``conflict`` ones too) is created and checked
with :func:`assigned_tutor` for its date. In extend mode the new ``offered``
rows join the pending series offer (``offer_records.
attach_to_pending_series_offer``), whose ``respond_by`` never moves; in
create mode the series create makes the offer over them right after
(``series_offers.offer_new_series``).

**Existing rows are never updated** here, whatever their state
(``conflict``, ``skipped_closure``, exceptions, past, ``completed``,
``cancelled``). Closures added, changed or removed after the rows exist are
handled by :func:`~app.modules.scheduling.closure_changes.
reconcile_series_closures` (#232), which the nightly job runs for each
series right before this extension, in the same transaction.

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Final, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import FieldError, SchedulingConflict
from app.modules.administration import service as administration
from app.modules.catalog import service as catalog
from app.modules.catalog.schemas import ClosureDetail
from app.modules.courses import service as courses
from app.modules.scheduling.booking import references
from app.modules.scheduling.checks import SlotCandidate, collect_conflicts
from app.modules.scheduling.conflicts import ROOM_OVERLAP, TUTOR_OVERLAP, Conflict
from app.modules.scheduling.errors import (
    InvalidSessionTimesError,
    SchedulingConflictError,
    SessionOverlapError,
    SessionValidationError,
)
from app.modules.scheduling.group_lock import (
    SeriesParticipantsChangedError,
    lock_group,
)
from app.modules.scheduling.locks import lock_students
from app.modules.scheduling.models import (
    SERIES_ASSIGNMENT_ACCEPTED,
    ClassSeries,
    ClassSeriesParticipant,
    ClassSession,
    ClassSessionParticipant,
)
from app.modules.scheduling.offer_records import attach_to_pending_series_offer
from app.modules.scheduling.overrides import (
    OccurrenceConflict,
    OverrideDecision,
    SeriesOverrideDecision,
    evaluate_listed,
    evaluate_series_override,
)
from app.modules.scheduling.recurrence import Occurrence, expand, horizon_end
from app.modules.scheduling.schemas import GroupOverride, SeriesOverride
from app.modules.scheduling.sessions import flush_class_sessions
from app.modules.scheduling.states import SessionState
from app.modules.scheduling.times import validate_session_bounds

OCCURRENCE_CONFLICT_TYPE: Final = "scheduling.occurrence_conflict"
"""The approval type raised for each ``conflict`` occurrence (#53 G)."""

Mode = Literal["create", "extend"]


@dataclass(frozen=True)
class MaterializeCounts:
    """What one run of :func:`materialize_series` inserted."""

    created: int = 0
    skipped_closure: int = 0
    conflicts: int = 0
    override: SeriesOverrideDecision | None = None
    """Create mode: the allowed override decision (#236), for the caller to
    record; ``None`` otherwise."""
    group_override: OverrideDecision | None = None
    """Create mode of a group (#56 D2): the allowed decision on the
    group-level conflicts; ``None`` otherwise."""


@dataclass(frozen=True)
class _SeriesRefs:
    student_id: uuid.UUID
    subject_id: uuid.UUID
    tutor_user_id: uuid.UUID | None
    classroom_id: uuid.UUID | None


@dataclass
class PlannedOccurrence:
    occurrence: Occurrence
    state: SessionState
    codes: list[str] = field(default_factory=list)


def assignment_applies(series: ClassSeries, local_date: date) -> bool:
    """Whether the series' tutor assignment covers an occurrence on
    ``local_date``: the series has a tutor and ``local_date`` is on or after
    ``assignment_from`` (the same date is covered)."""
    return (
        series.tutor_user_id is not None
        and series.assignment_from is not None
        and local_date >= series.assignment_from
    )


def occurrence_initial_state(series: ClassSeries, local_date: date) -> SessionState:
    """The state a clean occurrence of ``series`` on ``local_date`` is
    created in (#233 B1; ADR-0005 D3):

    - no tutor, or ``local_date`` before ``assignment_from``: ``unassigned``;
    - ``assignment_status = 'pending'``: ``offered`` (the caller attaches the
      row to the pending series offer, :func:`~app.modules.scheduling.
      offer_records.attach_to_pending_series_offer`);
    - ``assignment_status = 'accepted'``: ``confirmed``, with no offer.
    """
    if not assignment_applies(series, local_date):
        return SessionState.UNASSIGNED
    if series.assignment_status == SERIES_ASSIGNMENT_ACCEPTED:
        return SessionState.CONFIRMED
    return SessionState.OFFERED


def assigned_tutor(series: ClassSeries, local_date: date) -> uuid.UUID | None:
    """The tutor an occurrence on ``local_date`` is created (and checked)
    with: the series' tutor when its assignment covers the date, else
    none."""
    return series.tutor_user_id if assignment_applies(series, local_date) else None


def occurrence_local_date(row: ClassSession, zone: str) -> date:
    """The local date of an occurrence row's rule instant
    (``original_start``) in the series zone."""
    # Every row of a series has an original start (a CHECK).
    assert row.original_start is not None  # noqa: S101
    return row.original_start.astimezone(ZoneInfo(zone)).date()


async def series_participant_ids(
    session: AsyncSession, series_id: uuid.UUID
) -> list[uuid.UUID]:
    """The series' students, by id (one statement)."""
    return sorted(
        await session.scalars(
            select(ClassSeriesParticipant.student_id).where(
                ClassSeriesParticipant.class_series_id == series_id
            )
        )
    )


async def validate_series_references(
    session: AsyncSession, series: ClassSeries, student_ids: list[uuid.UUID]
) -> None:
    """``422`` (:class:`SessionValidationError`) with the #48 C3 field errors
    of the series' references that are no longer valid (a student, the
    subject or its level, the tutor or the room deactivated since the
    create), each once. The subject is read ``FOR SHARE``, as at create."""
    await validate_slot_references(
        session,
        student_ids,
        subject_id=series.subject_id,
        tutor_user_id=series.tutor_user_id,
        classroom_id=series.classroom_id,
    )


async def validate_slot_references(
    session: AsyncSession,
    student_ids: list[uuid.UUID],
    *,
    subject_id: uuid.UUID,
    tutor_user_id: uuid.UUID | None,
    classroom_id: uuid.UUID | None,
) -> None:
    """``422`` (:class:`SessionValidationError`) with the #48 C3 field errors
    of these references (the students, the subject or its level, the tutor
    and the room), each once; also the #54 edits'."""
    errors: list[FieldError] = []
    for student_id in student_ids:
        try:
            await references(
                session,
                _SeriesRefs(
                    student_id=student_id,
                    subject_id=subject_id,
                    tutor_user_id=tutor_user_id,
                    classroom_id=classroom_id,
                ),
            )
        except SessionValidationError as exc:
            errors += [error for error in exc.errors or [] if error not in errors]
    if errors:
        raise SessionValidationError(errors)


async def revalidate_references(
    session: AsyncSession, series: ClassSeries, student_ids: list[uuid.UUID]
) -> list[str]:
    """The codes of :func:`validate_series_references` (``student_inactive``,
    ``subject_inactive``, ``tutor_inactive``, ``classroom_inactive`` or an
    ``…_unknown``), each once in check order; empty when all are valid. The
    nightly job stores them in ``conflict_codes``."""
    try:
        await validate_series_references(session, series, student_ids)
    except SessionValidationError as exc:
        return list(dict.fromkeys(error["code"] for error in exc.errors or []))
    return []


def closed_dates(closures: list[ClosureDetail]) -> set[date]:
    """Every local date the closures contain (inclusive ranges)."""
    closed: set[date] = set()
    for closure in closures:
        day = closure.starts_on
        while day <= closure.ends_on:
            closed.add(day)
            day += timedelta(days=1)
    return closed


def occurrence_wire(conflict: Conflict, occurrence: Occurrence) -> SchedulingConflict:
    wire = conflict.as_wire()
    wire["details"] = {
        **wire["details"],
        "occurrence_start": occurrence.original_start.isoformat().replace(
            "+00:00", "Z"
        ),
        "date": occurrence.local_date.isoformat(),
    }
    return wire


def occurrence_bounds_error(occurrence: Occurrence) -> SessionValidationError:
    error: FieldError = {
        "path": ["body", "start_time"],
        "code": "crosses_local_date",
        "message": (
            "The occurrence on"
            f" {occurrence.local_date.isoformat()} would end on another local"
            " date."
        ),
    }
    return SessionValidationError([error])


def _race_conflict(code: str, series: ClassSeries) -> Conflict:
    if code == ROOM_OVERLAP:
        return Conflict(
            ROOM_OVERLAP,
            {"classroom_id": str(series.classroom_id), "class_session_ids": []},
        )
    return Conflict(TUTOR_OVERLAP, {"class_session_ids": []})


async def _locked_series(session: AsyncSession, series_id: uuid.UUID) -> ClassSeries:
    series = await session.scalar(
        select(ClassSeries)
        .where(ClassSeries.id == series_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if series is None:
        raise LookupError(f"no class series {series_id}")
    return series


@dataclass(frozen=True)
class LockedSeries:
    """A series under the materialization lock order."""

    series: ClassSeries
    zone: str
    student_ids: list[uuid.UUID]


async def lock_series_for_write(
    session: AsyncSession, series_id: uuid.UUID
) -> LockedSeries:
    """Steps 1-4 of the lock order for a series write that reads the
    participants first (the nightly extension, #232's reconcile). For a
    group (or course) series the participants are re-read under the series
    lock; a change raises :class:`SeriesParticipantsChangedError` (#56 C2,
    #65 C3)."""
    found = await session.get(ClassSeries, series_id)
    if found is None:
        raise LookupError(f"no class series {series_id}")
    # Neither changes for a series.
    group_id, cohort_id = found.group_id, found.cohort_id
    student_ids = await series_participant_ids(session, series_id)
    await lock_students(session, student_ids)
    zone = await administration.get_center_time_zone(session, for_share=True)
    if group_id is not None:
        await lock_group(session, group_id)
    elif cohort_id is not None:
        await courses.lock_cohort(session, cohort_id)
    series = await _locked_series(session, series_id)
    if (group_id is not None or cohort_id is not None) and (
        await series_participant_ids(session, series_id) != student_ids
    ):
        raise SeriesParticipantsChangedError(str(series_id))
    return LockedSeries(series=series, zone=zone, student_ids=student_ids)


async def _existing_starts(
    session: AsyncSession, series_id: uuid.UUID, window_start: datetime
) -> set[datetime]:
    rows = await session.scalars(
        select(ClassSession.original_start).where(
            ClassSession.series_id == series_id,
            ClassSession.original_start >= window_start,
        )
    )
    return {row for row in rows if row is not None}


async def materialize_series(
    session: AsyncSession,
    series_id: uuid.UUID,
    *,
    now: datetime,
    horizon_weeks: int,
    mode: Mode,
    override: SeriesOverride | GroupOverride | None = None,
    group_conflicts: Sequence[Conflict] = (),
    group_codes: Collection[str] | None = None,
    group_max: int | None = None,
) -> MaterializeCounts:
    """Create the series' missing occurrences in ``[now, horizon_end)``; see
    the module docstring. Returns the inserted counts. In ``create`` mode a
    conflict that ``override`` doesn't cover raises
    :class:`SchedulingConflictError` (and a bounds failure
    :class:`SessionValidationError`); the caller holds the student locks
    and the zone ``FOR SHARE``. ``override`` is for ``create`` mode only.

    A group create (#56 D2) passes its group-level conflicts
    (``group_capacity_exceeded``) and the codes its override lists for them
    (``group_codes``; ``None``: no override): they are decided with the
    #49 rule and refused together with the occurrences' conflicts, group
    level first, in one ``409``.

    A course create (#65 C1, C2) passes the cohort maximum as ``group_max``:
    each occurrence's participants are checked against it
    (``group_capacity_exceeded``, soft) besides the room capacity; with no
    participants (every cohort until #66) no capacity code can occur."""
    if (
        override is not None or group_conflicts or group_max is not None
    ) and mode != "create":
        raise ValueError("only a create carries an override")
    if mode == "extend":
        locked = await lock_series_for_write(session, series_id)
        student_ids, series = locked.student_ids, locked.series
    else:
        student_ids = await series_participant_ids(session, series_id)
        series = await _locked_series(session, series_id)
    zone = series.time_zone

    window_start = now
    occurrences = expand(
        series.rrule,
        starts_on=series.starts_on,
        start_time=series.start_time,
        duration_minutes=series.duration_minutes,
        zone=zone,
        window_start=window_start,
        window_end=horizon_end(now, horizon_weeks),
    )
    if mode == "extend":
        existing = await _existing_starts(session, series.id, window_start)
        occurrences = [o for o in occurrences if o.original_start not in existing]
    group_decision = evaluate_listed(group_conflicts, group_codes)
    if not occurrences:
        if not group_decision.allowed:
            raise SchedulingConflictError([c.as_wire() for c in group_conflicts])
        return MaterializeCounts(
            group_override=group_decision if mode == "create" else None
        )

    closed = closed_dates(
        await catalog.closures_between(
            session, occurrences[0].local_date, occurrences[-1].local_date
        )
    )
    reference_codes = (
        await revalidate_references(session, series, student_ids)
        if mode == "extend"
        else []
    )
    planned: list[PlannedOccurrence] = []
    refused: list[SchedulingConflict] = []
    found: list[OccurrenceConflict] = []
    for occurrence in occurrences:
        if occurrence.local_date in closed:
            planned.append(PlannedOccurrence(occurrence, SessionState.SKIPPED_CLOSURE))
            continue
        codes = list(reference_codes)
        try:
            validate_session_bounds(occurrence.starts_at, occurrence.ends_at, zone)
        except InvalidSessionTimesError as exc:
            if mode == "create":
                raise occurrence_bounds_error(occurrence) from None
            codes.append(exc.reason)
        else:
            conflicts = await collect_conflicts(
                session,
                SlotCandidate(
                    kind=series.kind,
                    student_ids=tuple(student_ids),
                    subject_id=series.subject_id,
                    tutor_user_id=assigned_tutor(series, occurrence.local_date),
                    classroom_id=series.classroom_id,
                    starts_at=occurrence.starts_at,
                    ends_at=occurrence.ends_at,
                    group_max=group_max,
                ),
                zone=zone,
                now=now,
                check_center_closed=False,
            )
            if mode == "create":
                # Every conflict is refused or overridden (decided below).
                refused += [occurrence_wire(c, occurrence) for c in conflicts]
                found += [
                    OccurrenceConflict(occurrence.original_start, c) for c in conflicts
                ]
                conflicts = []
            codes += [c.code for c in conflicts if c.code not in codes]
        if codes:
            planned.append(PlannedOccurrence(occurrence, SessionState.CONFLICT, codes))
        else:
            planned.append(
                PlannedOccurrence(
                    occurrence, occurrence_initial_state(series, occurrence.local_date)
                )
            )
    decision = None
    if mode == "create":
        decision = evaluate_series_override(found, override)
        if not (decision.allowed and group_decision.allowed):
            raise SchedulingConflictError(
                [c.as_wire() for c in group_conflicts] + refused
            )

    rows = await insert_occurrences(session, series, planned, student_ids, mode=mode)
    if mode == "extend":
        # B2: new offered rows join the pending series offer (in create
        # mode the series offer is made after this, with every row).
        await attach_to_pending_series_offer(
            session,
            series,
            [row for row in rows if row.state == SessionState.OFFERED],
        )
    for row in rows:
        if row.state == SessionState.CONFLICT:
            await administration.create_approval_request(
                session,
                request_type=OCCURRENCE_CONFLICT_TYPE,
                subject_id=row.id,
                requester_user_id=None,
            )
    states = [row.state for row in rows]
    skipped = states.count(SessionState.SKIPPED_CLOSURE)
    conflicted = states.count(SessionState.CONFLICT)
    return MaterializeCounts(
        created=len(rows) - skipped - conflicted,
        skipped_closure=skipped,
        conflicts=conflicted,
        override=decision,
        group_override=group_decision if mode == "create" else None,
    )


async def insert_occurrences(
    session: AsyncSession,
    series: ClassSeries,
    planned: list[PlannedOccurrence],
    student_ids: list[uuid.UUID],
    *,
    mode: Mode,
) -> list[ClassSession]:
    """ORM inserts (audited), one flush per row so an ``EXCLUDE`` race is
    attributed to its occurrence; then the participants."""
    rows: list[ClassSession] = []
    for item in planned:
        occurrence = item.occurrence
        tutor = assigned_tutor(series, occurrence.local_date)
        row = ClassSession(
            kind=series.kind,
            state=item.state.value,
            subject_id=series.subject_id,
            tutor_user_id=tutor,
            classroom_id=series.classroom_id,
            starts_at=occurrence.starts_at,
            ends_at=occurrence.ends_at,
            scheduled_minutes=series.duration_minutes,
            series_id=series.id,
            original_start=occurrence.original_start,
            is_exception=False,
            conflict_codes=item.codes,
            group_id=series.group_id,
            cohort_id=series.cohort_id,
        )
        session.add(row)
        try:
            await flush_class_sessions(session)
        except SessionOverlapError as exc:
            if mode == "create":
                raise SchedulingConflictError(
                    [occurrence_wire(_race_conflict(exc.code, series), occurrence)]
                ) from None
            raise
        rows.append(row)
    session.add_all(
        ClassSessionParticipant(class_session_id=row.id, student_id=student_id)
        for row in rows
        for student_id in student_ids
    )
    await flush_class_sessions(session)
    return rows
