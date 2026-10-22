"""Creating and reading recurring individual series (#53 E, I; spec §1, §3;
ADR-0005 D4, D6, D9).

**Create** (:func:`create_class_series`), after the route's ``401``/``403``
and the body's ``422``:

1. the per-student advisory lock (``lock_students``);
2. the center zone ``FOR SHARE``;
3. ``422`` for the recurrence
   (:func:`~app.modules.scheduling.recurrence.validate_recurrence`, then
   ``no_occurrences`` from
   :func:`~app.modules.scheduling.recurrence.last_occurrence_on`), on
   ``recurrence.weekdays`` / ``.interval`` / ``.until`` / ``.count`` or
   ``recurrence``;
4. ``422 starts_on_in_past`` when ``starts_on`` is before the center's local
   today;
5. ``422 crosses_local_date`` on ``start_time`` when ``start_time +
   duration`` is after 24:00 (an end at exactly midnight is allowed);
6. ``422`` for the references (the #48 codes, all in one response);
7. the series row and its participant, then
   :func:`~app.modules.scheduling.materialize.materialize_series` in
   ``create`` mode over ``[now, horizon_end)``: closure dates become
   ``skipped_closure``, every other occurrence passes the #48 checks
   (``center_closed`` off), and any conflict is one ``409
   scheduling_conflict`` listing every conflict of every occurrence. A
   ``409`` or ``422`` rolls everything back, so nothing is created. With an
   ``override`` (#236 E1; the route checked ``schedule.override`` right
   after the body) the conflicts go through
   :func:`~app.modules.scheduling.overrides.evaluate_series_override`: a
   hard conflict or an unlisted soft pair is the same ``409``;
8. with something overridden, the override rows and the
   ``scheduling.series_override`` event
   (:func:`~app.modules.scheduling.overrides.record_series_overrides`,
   ``write = "create"``);
9. ``201`` with the series and its occurrences.

A series that starts after the horizon is created with no occurrence row:
the nightly job creates and checks them later (D4). Audit: the automatic
``create`` events of the series, its participant and each session and
session participant.

**With a tutor** (#233 C1): the series is stored with ``assignment_status =
'pending'`` from ``starts_on``, so its clean occurrences are ``offered``;
after the expansion the same transaction makes the pending series offer
over every ``offered`` row (:func:`~app.modules.scheduling.series_offers.
offer_new_series`) and notifies the tutor. ``respond_by`` (optional, only
with a tutor: ``422 respond_by_requires_tutor``) is checked after the
references, before the expansion, against the rule's next occurrence
(``422 respond_by_invalid``); a series with no occurrence still to come is
``409 series_not_offerable`` (``ended``). A series with no row inside the
horizon still gets its offer, with no covered session.

**Read** (:func:`get_class_series`): every holder of ``schedule.view``;
at most seven statements; ``404`` for an unknown id.
"""

import uuid
from datetime import UTC, date
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.authz import RecordNotFoundError
from app.core.errors import FieldError
from app.modules.administration import service as administration
from app.modules.catalog import service as catalog
from app.modules.families import service as families
from app.modules.identity import service as identity
from app.modules.scheduling.booking import references
from app.modules.scheduling.errors import SessionValidationError
from app.modules.scheduling.locks import lock_students
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
    ClassSessionOverride,
)
from app.modules.scheduling.overrides import record_series_overrides
from app.modules.scheduling.recurrence import (
    InvalidRecurrenceError,
    RecurrenceFields,
    build_rrule,
    last_occurrence_on,
    validate_recurrence,
)
from app.modules.scheduling.schemas import (
    ClassSeriesCreate,
    ClassSeriesCreated,
    ClassSeriesDetail,
    SeriesAssignment,
    SeriesOccurrence,
    SeriesRecurrenceDetail,
    SessionClassroom,
    SessionParticipant,
    SessionSubject,
    SessionTutor,
)
from app.modules.scheduling.series_offers import (
    offer_new_series,
    validate_create_respond_by,
)
from app.modules.scheduling.states import SessionKind, SessionState

SERIES_TIME_ZONE_DEPENDENCY: Final = "scheduling.class_series"
"""The name of the class-series check in the time-zone registry (#37, #53
B6)."""

MINUTES_PER_DAY: Final = 24 * 60

RECURRENCE_MESSAGES: Final = {
    "weekdays_empty": "Choose at least one weekday.",
    "weekday_out_of_range": "Weekdays are 1 (Monday) to 7 (Sunday).",
    "weekdays_duplicate": "List each weekday only once.",
    "interval_out_of_range": "Repeat every 1 to 4 weeks.",
    "until_and_count": "Give an end date or a number of sessions, not both.",
    "count_out_of_range": "A series has 1 to 200 sessions.",
    "until_before_start": "The end date must not be before the start date.",
    "no_occurrences": "The rule gives no session.",
}


def _error(path: list[str | int], code: str, message: str) -> SessionValidationError:
    error: FieldError = {"path": path, "code": code, "message": message}
    return SessionValidationError([error])


def _fields(body: ClassSeriesCreate) -> RecurrenceFields:
    rule = body.recurrence
    return RecurrenceFields(
        weekdays=tuple(rule.weekdays),
        interval=rule.interval,
        until_on=rule.until,
        count=rule.count,
    )


def _check_recurrence(fields: RecurrenceFields, body: ClassSeriesCreate) -> None:
    try:
        validate_recurrence(fields, body.starts_on)
        last_occurrence_on(fields, body.starts_on)
    except InvalidRecurrenceError as exc:
        path: list[str | int] = ["body", "recurrence"]
        if exc.field != "recurrence":
            path.append(exc.field)
        raise _error(path, exc.reason, RECURRENCE_MESSAGES[exc.reason]) from None


def _check_times(body: ClassSeriesCreate, zone: str) -> None:
    if body.starts_on < clock.today_in(zone):
        raise _error(
            ["body", "starts_on"],
            "starts_on_in_past",
            "The series can't start before today.",
        )
    start = body.start_time.hour * 60 + body.start_time.minute
    if start + body.duration_minutes > MINUTES_PER_DAY:
        raise _error(
            ["body", "start_time"],
            "crosses_local_date",
            "A session starts and ends on the same local date.",
        )


def _respond_by_without_tutor() -> SessionValidationError:
    return _error(
        ["body", "respond_by"],
        "respond_by_requires_tutor",
        "A deadline needs a tutor to answer it.",
    )


async def create_class_series(
    session: AsyncSession,
    body: ClassSeriesCreate,
    *,
    horizon_weeks: int,
    actor_id: uuid.UUID,
) -> ClassSeriesCreated:
    """``POST /api/v1/class-series``: see the module docstring."""
    await lock_students(session, [body.student_id])
    zone = await administration.get_center_time_zone(session, for_share=True)
    fields = _fields(body)
    _check_recurrence(fields, body)
    _check_times(body, zone)
    if body.tutor_user_id is None and body.respond_by is not None:
        raise _respond_by_without_tutor()
    await references(session, body)
    offered_at = clock.now()

    series = ClassSeries(
        kind=SessionKind.INDIVIDUAL.value,
        subject_id=body.subject_id,
        tutor_user_id=body.tutor_user_id,
        classroom_id=body.classroom_id,
        starts_on=body.starts_on,
        start_time=body.start_time,
        duration_minutes=body.duration_minutes,
        time_zone=zone,
        weekdays=sorted(fields.weekdays),
        interval=fields.interval,
        until_on=fields.until_on,
        count=fields.count,
        rrule=build_rrule(fields),
        last_occurrence_on=last_occurrence_on(fields, body.starts_on),
        # #233 C1: a tutor is offered the whole series from its start.
        assignment_status=(
            None if body.tutor_user_id is None else SERIES_ASSIGNMENT_PENDING
        ),
        assignment_from=None if body.tutor_user_id is None else body.starts_on,
    )
    if body.tutor_user_id is not None:
        await validate_create_respond_by(
            session, series, offered_at=offered_at, requested=body.respond_by
        )
    session.add(series)
    await session.flush()
    session.add(
        ClassSeriesParticipant(class_series_id=series.id, student_id=body.student_id)
    )
    await session.flush()
    counts = await materialize_series(
        session,
        series.id,
        now=offered_at,
        horizon_weeks=horizon_weeks,
        mode="create",
        override=body.override,
    )
    if body.override is not None and counts.override is not None:
        await record_series_overrides(
            session,
            class_series_id=series.id,
            decision=counts.override,
            reason=body.override.reason,
            write="create",
            actor_id=actor_id,
        )
    if body.tutor_user_id is not None:
        await offer_new_series(
            session,
            series,
            offered_at=offered_at,
            requested_respond_by=body.respond_by,
            actor_id=actor_id,
        )
    detail = await get_class_series(session, series.id)
    return ClassSeriesCreated(**detail.model_dump(), warnings=[])


def _occurrence(row: ClassSession, overridden: bool) -> SeriesOccurrence:
    # Every row of a series has an original start (a CHECK).
    assert row.original_start is not None  # noqa: S101
    return SeriesOccurrence(
        id=row.id,
        state=SessionState(row.state).value,
        starts_at=row.starts_at.astimezone(UTC),
        ends_at=row.ends_at.astimezone(UTC),
        original_start=row.original_start.astimezone(UTC),
        is_exception=row.is_exception,
        overridden=overridden,
        conflict_codes=list(row.conflict_codes),
        version=row.version,
    )


def is_ended(series: ClassSeries, today: date) -> bool:
    """#54 A3: the series has a last occurrence date and it is before the
    local ``today``."""
    return series.last_occurrence_on is not None and series.last_occurrence_on < today


async def get_class_series(
    session: AsyncSession, class_series_id: uuid.UUID
) -> ClassSeriesDetail:
    """``GET /api/v1/class-series/{id}``: the series, its references, its
    tutor assignment (#233 F2), its lineage (#54 A2) and its occurrence rows
    starting now or later. At most nine statements."""
    series = await session.get(ClassSeries, class_series_id)
    if series is None:
        raise RecordNotFoundError()
    student_ids = list(
        await session.scalars(
            select(ClassSeriesParticipant.student_id).where(
                ClassSeriesParticipant.class_series_id == series.id
            )
        )
    )
    students = await families.student_references(session, student_ids)
    subject = (await catalog.subject_references(session, [series.subject_id]))[
        series.subject_id
    ]
    tutor = None
    if series.tutor_user_id is not None:
        account = (
            await identity.get_account_references(session, [series.tutor_user_id])
        )[series.tutor_user_id]
        tutor = SessionTutor(id=account.id, display_name=account.display_name)
    classroom = None
    if series.classroom_id is not None:
        room = (await catalog.classroom_references(session, [series.classroom_id]))[
            series.classroom_id
        ]
        classroom = SessionClassroom(
            id=room.id, name=room.name, is_active=room.is_active
        )
    # #236 G4: whether an override names each row, in the same statement.
    overridden = (
        select(ClassSessionOverride.id)
        .where(ClassSessionOverride.class_session_id == ClassSession.id)
        .exists()
        .label("overridden")
    )
    rows = list(
        await session.execute(
            select(ClassSession, overridden)
            .where(
                ClassSession.series_id == series.id,
                ClassSession.starts_at >= clock.now(),
            )
            .order_by(ClassSession.starts_at, ClassSession.id)
        )
    )
    participants = sorted(
        (
            SessionParticipant(
                student_id=student.id,
                display_name=student.display_name,
                is_active=student.is_active,
            )
            for student in students.values()
        ),
        key=lambda p: (p.display_name.casefold(), str(p.student_id)),
    )
    split_into = list(
        await session.scalars(
            select(ClassSeries.id)
            .where(ClassSeries.split_from_series_id == series.id)
            .order_by(ClassSeries.created_at, ClassSeries.id)
        )
    )
    assignment = None
    if tutor is not None and series.assignment_status is not None:
        assert series.assignment_from is not None  # noqa: S101 - a CHECK
        offer = await session.scalar(
            select(AssignmentOffer)
            .where(
                AssignmentOffer.class_series_id == series.id,
                AssignmentOffer.scope == OFFER_SCOPE_SERIES,
                AssignmentOffer.status.in_((OFFER_PENDING, OFFER_ACCEPTED)),
                AssignmentOffer.tutor_user_id == series.tutor_user_id,
            )
            .order_by(AssignmentOffer.offered_at.desc(), AssignmentOffer.id.desc())
            .limit(1)
        )
        assignment = SeriesAssignment(
            status=series.assignment_status,  # type: ignore[arg-type]
            tutor=tutor,
            from_date=series.assignment_from,
            offer_id=None if offer is None else offer.id,
            respond_by=(
                offer.respond_by.astimezone(UTC)
                if offer is not None and offer.status == OFFER_PENDING
                else None
            ),
        )
    return ClassSeriesDetail(
        id=series.id,
        kind=SessionKind(series.kind).value,
        subject=SessionSubject(
            id=subject.id,
            name=subject.name,
            education_level_name=subject.education_level_name,
            is_active=subject.is_active and subject.education_level_is_active,
        ),
        tutor=tutor,
        classroom=classroom,
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
        split_into_series_ids=split_into,
        assignment=assignment,
        version=series.version,
        created_at=series.created_at,
        updated_at=series.updated_at,
        occurrences=[_occurrence(row, flag) for row, flag in rows],
    )


async def has_class_series(session: AsyncSession) -> bool:
    """Whether any class series exists (#37 E1 (a), #53 B6): a series stores
    a local time in the center zone, so even one with no occurrence row yet
    (it starts after the horizon) blocks a time-zone change."""
    return bool(await session.scalar(select(select(ClassSeries.id).exists())))
