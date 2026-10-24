"""Course series: the scheduling half of cohorts (#65 C, F; spec §3;
ADR-0005 D1, D4, D6, D7).

A cohort (``courses``) has its sessions as the occurrences of one weekly
``course`` series (``class_series.cohort_id``; every occurrence copies it).
``app.workflows.cohorts`` calls these functions through
``scheduling.service``; ``courses`` never imports this module.

**Create** (:func:`create_course_series`): the #53 E2 create, steps 9-14,
for ``kind = 'course'``: the series (``COUNT`` derived by the workflow so
the template's sessions avoid the closures known at creation, #65 E3; the
duration is the snapshot's session length), then
:func:`~app.modules.scheduling.materialize.materialize_series` in
``create`` mode over the horizon with the cohort maximum (closure dates
``skipped_closure``; any conflict not covered by the #236 ``override`` is
one ``409 scheduling_conflict`` per occurrence and nothing is written),
the override rows, and with a tutor the pending series offer (#233 C1).
The caller holds the zone ``FOR SHARE`` (there are no students to lock)
and has validated the fields (:func:`check_course_series`).

**Reads** (#65 F): :func:`cohort_schedules` (the cohort list: every
cohort's series facts and next start, one statement),
:func:`cohort_series_read` (the detail: the current series as
:class:`~app.modules.scheduling.schemas.ClassSeriesSummary`, the room
capacity and the next occurrences; five statements, six with
participants) and :func:`list_cohort_sessions` (the range read; three
statements).

**Lock order (#65 G1):** students → zone → template ``FOR SHARE`` (only in
the cohort create) → group or cohort ``FOR UPDATE`` → series ``FOR UPDATE``
→ sessions by id → offer.

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.errors import FieldError
from app.modules.catalog import service as catalog
from app.modules.catalog.schemas import ClassroomCapacity, SubjectReference
from app.modules.families import service as families
from app.modules.identity import service as identity
from app.modules.scheduling.booking import REFERENCE_MESSAGES
from app.modules.scheduling.calendar_read import validate_range
from app.modules.scheduling.groups_rules import AFFECTED_STATES
from app.modules.scheduling.materialize import materialize_series
from app.modules.scheduling.models import (
    OFFER_ACCEPTED,
    OFFER_PENDING,
    OFFER_SCOPE_SERIES,
    SERIES_ASSIGNMENT_PENDING,
    AssignmentOffer,
    ClassSeries,
    ClassSeriesParticipant,
    ClassSession,
)
from app.modules.scheduling.overrides import record_series_overrides
from app.modules.scheduling.recurrence import (
    InvalidRecurrenceError,
    RecurrenceFields,
    build_rrule,
    iter_local_starts,
    last_occurrence_on,
    to_instant,
    validate_recurrence,
)
from app.modules.scheduling.schemas import (
    ClassSeriesSummary,
    CohortSessionItem,
    GroupSessionClassroom,
    SeriesAssignment,
    SeriesOverride,
    SeriesRecurrenceDetail,
    SessionClassroom,
    SessionParticipant,
    SessionSubject,
    SessionTutor,
)
from app.modules.scheduling.series import (
    MINUTES_PER_DAY,
    RECURRENCE_MESSAGES,
    is_ended,
)
from app.modules.scheduling.series_offers import (
    offer_new_series,
    validate_create_respond_by,
)
from app.modules.scheduling.states import SessionKind, SessionState

TUTOR_ROLE = "tutor"
ACTIVE = "active"
NEXT_OCCURRENCES = 10
UPCOMING_STATES = tuple(sorted(AFFECTED_STATES - {SessionState.SKIPPED_CLOSURE}))
"""The upcoming occurrences staff see (not cancelled, skipped or done)."""
RULE_DATES_MAX = 200
"""ADR-0005 D4: a series has at most 200 occurrences."""


def _error(path: list[str | int], code: str, message: str) -> FieldError:
    return {"path": path, "code": code, "message": message}


# --- The rule ------------------------------------------------------------------------


def course_rule_dates(
    *, starts_on: date, weekdays: Sequence[int], interval: int
) -> list[date]:
    """The local dates of the weekly rule's first 200 occurrences (#65 D2
    step 7: no ``UNTIL`` or ``COUNT``), in order. The fields must be
    valid."""
    fields = RecurrenceFields(
        weekdays=tuple(weekdays), interval=interval, until_on=None, count=None
    )
    found: list[date] = []
    for local in iter_local_starts(
        build_rrule(fields), starts_on=starts_on, start_time=time()
    ):
        found.append(local.date())
        if len(found) == RULE_DATES_MAX:
            break
    return found


async def check_course_series(
    session: AsyncSession,
    *,
    subject_id: uuid.UUID | None,
    tutor_user_id: uuid.UUID | None,
    classroom_id: uuid.UUID | None,
    starts_on: date,
    start_time: time,
    duration_minutes: int,
    weekdays: Sequence[int],
    interval: int,
    respond_by: datetime | None,
    today: date,
) -> list[FieldError]:
    """#65 D2 step 5, the scheduling fields: every error at once (empty:
    valid). ``subject_id`` (``None`` when the workflow already refused it,
    ``subject_not_in_template``) is read ``FOR SHARE`` (``subject_unknown``,
    ``subject_inactive``); then the recurrence (the #53 C1 codes on
    ``recurrence.weekdays`` / ``.interval``), ``starts_on_in_past``,
    ``crosses_local_date`` on ``start_time`` (``start_time`` plus the
    snapshot's session length after 24:00), ``respond_by_requires_tutor``
    and the tutor and classroom references (the #48 codes)."""
    errors: list[FieldError] = []

    def reference(field: str, code: str) -> None:
        errors.append(_error(["body", field], code, REFERENCE_MESSAGES[code]))

    if subject_id is not None:
        subject = await catalog.get_subject_for_reference(
            session, subject_id, lock=True
        )
        if subject is None:
            reference("subject_id", "subject_unknown")
        elif not (subject.is_active and subject.education_level_is_active):
            reference("subject_id", "subject_inactive")
    try:
        validate_recurrence(
            RecurrenceFields(
                weekdays=tuple(weekdays), interval=interval, until_on=None, count=None
            ),
            starts_on,
        )
    except InvalidRecurrenceError as exc:
        errors.append(
            _error(
                ["body", "recurrence", exc.field],
                exc.reason,
                RECURRENCE_MESSAGES[exc.reason],
            )
        )
    if starts_on < today:
        errors.append(
            _error(
                ["body", "starts_on"],
                "starts_on_in_past",
                "The cohort can't start before today.",
            )
        )
    if start_time.hour * 60 + start_time.minute + duration_minutes > MINUTES_PER_DAY:
        errors.append(
            _error(
                ["body", "start_time"],
                "crosses_local_date",
                "A session starts and ends on the same local date.",
            )
        )
    if respond_by is not None and tutor_user_id is None:
        errors.append(
            _error(
                ["body", "respond_by"],
                "respond_by_requires_tutor",
                "A deadline needs a tutor to answer it.",
            )
        )
    if tutor_user_id is not None:
        tutor = await identity.get_role_member(session, TUTOR_ROLE, tutor_user_id)
        if tutor is None:
            reference("tutor_user_id", "tutor_unknown")
        elif tutor.state != ACTIVE:
            reference("tutor_user_id", "tutor_inactive")
    if classroom_id is not None:
        room = (await catalog.classroom_references(session, [classroom_id])).get(
            classroom_id
        )
        if room is None:
            reference("classroom_id", "classroom_unknown")
        elif not room.is_active:
            reference("classroom_id", "classroom_inactive")
    return errors


# --- Create --------------------------------------------------------------------------


async def create_course_series(
    session: AsyncSession,
    *,
    cohort_id: uuid.UUID,
    max_students: int,
    subject_id: uuid.UUID,
    tutor_user_id: uuid.UUID | None,
    classroom_id: uuid.UUID | None,
    starts_on: date,
    start_time: time,
    duration_minutes: int,
    weekdays: Sequence[int],
    interval: int,
    count: int,
    zone: str,
    now: datetime,
    respond_by: datetime | None,
    override: SeriesOverride | None,
    horizon_weeks: int,
    actor_id: uuid.UUID,
) -> uuid.UUID:
    """The cohort's ``course`` series and its horizon occurrences (#65 C2;
    see the module docstring). Raises the #53 route's plain exceptions
    (:class:`~app.modules.scheduling.errors.SchedulingConflictError`,
    :class:`~app.modules.scheduling.errors.SessionValidationError` for
    ``respond_by_invalid`` or a DST ``crosses_local_date``,
    :class:`~app.modules.scheduling.errors.SeriesNotOfferableError`).
    Returns the series id."""
    fields = RecurrenceFields(
        weekdays=tuple(weekdays), interval=interval, until_on=None, count=count
    )
    series = ClassSeries(
        kind=SessionKind.COURSE.value,
        cohort_id=cohort_id,
        subject_id=subject_id,
        tutor_user_id=tutor_user_id,
        classroom_id=classroom_id,
        starts_on=starts_on,
        start_time=start_time,
        duration_minutes=duration_minutes,
        time_zone=zone,
        weekdays=sorted(fields.weekdays),
        interval=fields.interval,
        until_on=None,
        count=count,
        rrule=build_rrule(fields),
        last_occurrence_on=last_occurrence_on(fields, starts_on),
        # #233 C1: a tutor is offered the whole series from its start.
        assignment_status=None if tutor_user_id is None else SERIES_ASSIGNMENT_PENDING,
        assignment_from=None if tutor_user_id is None else starts_on,
    )
    if tutor_user_id is not None:
        await validate_create_respond_by(
            session, series, offered_at=now, requested=respond_by
        )
    session.add(series)
    await session.flush()
    counts = await materialize_series(
        session,
        series.id,
        now=now,
        horizon_weeks=horizon_weeks,
        mode="create",
        override=override,
        group_max=max_students,
    )
    if override is not None and counts.override is not None:
        await record_series_overrides(
            session,
            class_series_id=series.id,
            decision=counts.override,
            reason=override.reason,
            write="create",
            actor_id=actor_id,
        )
    if tutor_user_id is not None:
        await offer_new_series(
            session,
            series,
            offered_at=now,
            requested_respond_by=respond_by,
            actor_id=actor_id,
        )
    return series.id


# --- Reads ---------------------------------------------------------------------------


def _first_date(series: ClassSeries) -> date | None:
    for local in iter_local_starts(
        series.rrule, starts_on=series.starts_on, start_time=series.start_time
    ):
        return local.date()
    return None


@dataclass(frozen=True)
class CohortSchedule:
    """A cohort's dates from its series' rules (#65 B2, F): valid beyond the
    horizon. ``current`` is the series with the latest ``starts_on`` (then
    id); the first occurrence is the earliest series' first rule date, the
    last the latest ``last_occurrence_on``."""

    cohort_id: uuid.UUID
    current_series_id: uuid.UUID
    tutor_user_id: uuid.UUID | None
    assignment_status: str | None
    classroom_id: uuid.UUID | None
    first_session_on: date | None
    last_session_on: date | None
    first_start: datetime | None
    last_start: datetime | None
    last_minutes: int
    next_starts_at: datetime | None


def _schedule(
    cohort_id: uuid.UUID,
    rows: Sequence[tuple[ClassSeries, datetime | None]],
) -> CohortSchedule:
    ordered = sorted(rows, key=lambda row: (row[0].starts_on, row[0].id))
    current = ordered[-1][0]
    firsts = [
        (day, series)
        for series, _ in ordered
        if (day := _first_date(series)) is not None
    ]
    first_on, first_series = min(firsts, key=lambda item: item[0], default=(None, None))
    lasts = [
        (series.last_occurrence_on, series)
        for series, _ in ordered
        if series.last_occurrence_on is not None
    ]
    last_on, last_series = max(lasts, key=lambda item: item[0], default=(None, None))
    nexts = [starts for _, starts in ordered if starts is not None]
    return CohortSchedule(
        cohort_id=cohort_id,
        current_series_id=current.id,
        tutor_user_id=current.tutor_user_id,
        assignment_status=current.assignment_status,
        classroom_id=current.classroom_id,
        first_session_on=first_on,
        last_session_on=last_on,
        first_start=(
            None
            if first_on is None or first_series is None
            else to_instant(
                datetime.combine(first_on, first_series.start_time),
                first_series.time_zone,
            )
        ),
        last_start=(
            None
            if last_on is None or last_series is None
            else to_instant(
                datetime.combine(last_on, last_series.start_time),
                last_series.time_zone,
            )
        ),
        last_minutes=(
            current.duration_minutes
            if last_series is None
            else last_series.duration_minutes
        ),
        next_starts_at=min(nexts).astimezone(UTC) if nexts else None,
    )


async def cohort_schedules(
    session: AsyncSession, cohort_ids: Collection[uuid.UUID] | None, *, now: datetime
) -> dict[uuid.UUID, CohortSchedule]:
    """The :class:`CohortSchedule` of these cohorts (``None``: every cohort
    with a series), by cohort id. One statement: the series with each
    one's next upcoming occurrence (``unassigned``, ``offered``,
    ``confirmed`` or ``conflict``, starting after ``now``)."""
    if cohort_ids is not None and not cohort_ids:
        return {}
    next_start = (
        select(func.min(ClassSession.starts_at))
        .where(
            ClassSession.series_id == ClassSeries.id,
            ClassSession.starts_at > now,
            ClassSession.state.in_(UPCOMING_STATES),
        )
        .correlate(ClassSeries)
        .scalar_subquery()
    )
    query = select(ClassSeries, next_start).where(ClassSeries.cohort_id.is_not(None))
    if cohort_ids is not None:
        query = query.where(ClassSeries.cohort_id.in_(set(cohort_ids)))
    grouped: dict[uuid.UUID, list[tuple[ClassSeries, datetime | None]]] = {}
    for series, starts in await session.execute(query):
        assert series.cohort_id is not None  # noqa: S101 - the filter above
        grouped.setdefault(series.cohort_id, []).append((series, starts))
    return {
        cohort_id: _schedule(cohort_id, rows) for cohort_id, rows in grouped.items()
    }


@dataclass(frozen=True)
class CohortOccurrence:
    """An upcoming occurrence of a cohort."""

    id: uuid.UUID
    state: str
    starts_at: datetime
    ends_at: datetime


@dataclass(frozen=True)
class CohortSeriesRead:
    """The scheduling half of a cohort detail (#65 F1)."""

    schedule: CohortSchedule
    series: ClassSeriesSummary
    subject: SubjectReference
    room: ClassroomCapacity | None
    next_occurrences: list[CohortOccurrence]


async def cohort_series_read(
    session: AsyncSession, cohort_id: uuid.UUID, *, now: datetime
) -> CohortSeriesRead | None:
    """The cohort's current series (all the ``ClassSeriesDetail`` fields but
    its occurrences, with ``assignment`` and ``cohort_id``), its room with
    the capacity, its dates and its next 10 upcoming occurrences. ``None``
    when the cohort has no series. Five statements (the series with their
    participants, lineage, offer and next start; the next occurrences; the
    subject; the tutor account; the room), six with participants (#66)."""
    next_start = (
        select(func.min(ClassSession.starts_at))
        .where(
            ClassSession.series_id == ClassSeries.id,
            ClassSession.starts_at > now,
            ClassSession.state.in_(UPCOMING_STATES),
        )
        .correlate(ClassSeries)
        .scalar_subquery()
    )
    students = (
        select(func.array_agg(ClassSeriesParticipant.student_id))
        .where(ClassSeriesParticipant.class_series_id == ClassSeries.id)
        .correlate(ClassSeries)
        .scalar_subquery()
    )
    split = ClassSeries.__table__.alias("split")
    split_into = (
        select(
            func.array_agg(
                aggregate_order_by(split.c.id, split.c.created_at, split.c.id)
            )
        )
        .where(split.c.split_from_series_id == ClassSeries.id)
        .correlate(ClassSeries)
        .scalar_subquery()
    )
    offer = (
        select(AssignmentOffer.id)
        .where(
            AssignmentOffer.class_series_id == ClassSeries.id,
            AssignmentOffer.scope == OFFER_SCOPE_SERIES,
            AssignmentOffer.status.in_((OFFER_PENDING, OFFER_ACCEPTED)),
            AssignmentOffer.tutor_user_id == ClassSeries.tutor_user_id,
        )
        .order_by(AssignmentOffer.offered_at.desc(), AssignmentOffer.id.desc())
        .limit(1)
        .correlate(ClassSeries)
        .scalar_subquery()
    )
    offer_row = AssignmentOffer.__table__.alias("current_offer")
    rows = (
        await session.execute(
            select(
                ClassSeries,
                next_start,
                students,
                split_into,
                offer_row.c.id,
                offer_row.c.status,
                offer_row.c.respond_by,
            )
            .outerjoin(offer_row, offer_row.c.id == offer)
            .where(ClassSeries.cohort_id == cohort_id)
        )
    ).all()
    if not rows:
        return None
    schedule = _schedule(cohort_id, [(row[0], row[1]) for row in rows])
    current_row = next(row for row in rows if row[0].id == schedule.current_series_id)
    series: ClassSeries = current_row[0]
    student_ids: list[uuid.UUID] = list(current_row[2] or [])
    split_ids: list[uuid.UUID] = list(current_row[3] or [])
    next_rows = list(
        await session.scalars(
            select(ClassSession)
            .where(
                ClassSession.cohort_id == cohort_id,
                ClassSession.starts_at > now,
                ClassSession.state.in_(UPCOMING_STATES),
            )
            .order_by(ClassSession.starts_at, ClassSession.id)
            .limit(NEXT_OCCURRENCES)
        )
    )
    student_refs = await families.student_references(session, student_ids)
    subject = (await catalog.subject_references(session, [series.subject_id]))[
        series.subject_id
    ]
    tutor = None
    if series.tutor_user_id is not None:
        account = (
            await identity.get_account_references(session, [series.tutor_user_id])
        ).get(series.tutor_user_id)
        tutor = SessionTutor(
            id=series.tutor_user_id,
            display_name="" if account is None else account.display_name,
        )
    room = None
    if series.classroom_id is not None:
        room = (await catalog.classroom_capacities(session, [series.classroom_id])).get(
            series.classroom_id
        )
    assignment = None
    if tutor is not None and series.assignment_status is not None:
        assert series.assignment_from is not None  # noqa: S101 - a CHECK
        offer_id, offer_status, respond_by = current_row[4:7]
        assignment = SeriesAssignment(
            status=series.assignment_status,  # type: ignore[arg-type]
            tutor=tutor,
            from_date=series.assignment_from,
            offer_id=offer_id,
            respond_by=(
                respond_by.astimezone(UTC)
                if respond_by is not None and offer_status == OFFER_PENDING
                else None
            ),
        )
    participants = sorted(
        (
            SessionParticipant(
                student_id=student.id,
                display_name=student.display_name,
                is_active=student.is_active,
            )
            for student in student_refs.values()
        ),
        key=lambda p: (p.display_name.casefold(), str(p.student_id)),
    )
    summary = ClassSeriesSummary(
        id=series.id,
        kind=SessionKind(series.kind).value,
        subject=SessionSubject(
            id=subject.id,
            name=subject.name,
            education_level_name=subject.education_level_name,
            is_active=subject.is_active and subject.education_level_is_active,
        ),
        tutor=tutor,
        classroom=(
            None
            if room is None
            else SessionClassroom(id=room.id, name=room.name, is_active=room.is_active)
        ),
        participants=participants,
        starts_on=series.starts_on,
        start_time=series.start_time.strftime("%H:%M"),
        duration_minutes=series.duration_minutes,
        time_zone=series.time_zone,
        recurrence=SeriesRecurrenceDetail(
            weekdays=list(series.weekdays),
            interval=series.interval,
            until=series.until_on,
            count=series.count,
        ),
        rrule=series.rrule,
        last_occurrence_on=series.last_occurrence_on,
        ended=is_ended(series, clock.today_in(series.time_zone)),
        split_from_series_id=series.split_from_series_id,
        split_into_series_ids=split_ids,
        assignment=assignment,
        group_id=series.group_id,
        cohort_id=series.cohort_id,
        version=series.version,
        created_at=series.created_at,
        updated_at=series.updated_at,
    )
    return CohortSeriesRead(
        schedule=schedule,
        series=summary,
        subject=subject,
        room=room,
        next_occurrences=[
            CohortOccurrence(
                id=row.id,
                state=SessionState(row.state).value,
                starts_at=row.starts_at.astimezone(UTC),
                ends_at=row.ends_at.astimezone(UTC),
            )
            for row in next_rows
        ],
    )


async def list_cohort_sessions(
    session: AsyncSession, cohort_id: uuid.UUID, start: datetime, end: datetime
) -> list[CohortSessionItem]:
    """The cohort's sessions overlapping ``[start, end)`` by start, in every
    state (#65 F4, staff). The #57 range rules (``422 range_invalid``,
    ``range_too_long``) are checked first. Three statements (the rows, the
    tutors, the rooms)."""
    validate_range(start, end)
    rows = list(
        await session.scalars(
            select(ClassSession)
            .where(
                ClassSession.cohort_id == cohort_id,
                ClassSession.starts_at < end,
                ClassSession.ends_at > start,
            )
            .order_by(ClassSession.starts_at, ClassSession.id)
        )
    )
    accounts = await identity.get_account_references(
        session, {row.tutor_user_id for row in rows if row.tutor_user_id is not None}
    )
    rooms = await catalog.classroom_references(
        session, {row.classroom_id for row in rows if row.classroom_id is not None}
    )
    items: list[CohortSessionItem] = []
    for row in rows:
        account = None if row.tutor_user_id is None else accounts.get(row.tutor_user_id)
        room = None if row.classroom_id is None else rooms.get(row.classroom_id)
        assert row.original_start is not None  # noqa: S101 - every row has a series
        items.append(
            CohortSessionItem(
                id=row.id,
                state=SessionState(row.state).value,
                starts_at=row.starts_at.astimezone(UTC),
                ends_at=row.ends_at.astimezone(UTC),
                original_start=row.original_start.astimezone(UTC),
                is_exception=row.is_exception,
                tutor=(
                    None
                    if row.tutor_user_id is None
                    else SessionTutor(
                        id=row.tutor_user_id,
                        display_name="" if account is None else account.display_name,
                    )
                ),
                classroom=(
                    None
                    if room is None
                    else GroupSessionClassroom(id=room.id, name=room.name)
                ),
            )
        )
    return items
