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
   ``409`` or ``422`` rolls everything back, so nothing is created;
8. ``201`` with the series and its occurrences.

A series that starts after the horizon is created with no occurrence row:
the nightly job creates and checks them later (D4). Audit: the automatic
``create`` events of the series, its participant and each session and
session participant. No offer, notification or inbox item (#233 adds the
series offer).

**Read** (:func:`get_class_series`): every holder of ``schedule.view``;
at most seven statements; ``404`` for an unknown id.
"""

import uuid
from datetime import UTC
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
    ClassSeries,
    ClassSeriesParticipant,
    ClassSession,
)
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
    SeriesOccurrence,
    SeriesRecurrenceDetail,
    SessionClassroom,
    SessionParticipant,
    SessionSubject,
    SessionTutor,
)
from app.modules.scheduling.states import SessionKind, SessionState

SERIES_TIME_ZONE_DEPENDENCY: Final = "scheduling.class_series"
"""The name of the class-series check in the time-zone registry (#37, #53
B6)."""

MINUTES_PER_DAY: Final = 24 * 60

_RECURRENCE_MESSAGES: Final = {
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
        raise _error(path, exc.reason, _RECURRENCE_MESSAGES[exc.reason]) from None


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


async def create_class_series(
    session: AsyncSession, body: ClassSeriesCreate, *, horizon_weeks: int
) -> ClassSeriesCreated:
    """``POST /api/v1/class-series``: see the module docstring."""
    await lock_students(session, [body.student_id])
    zone = await administration.get_center_time_zone(session, for_share=True)
    fields = _fields(body)
    _check_recurrence(fields, body)
    _check_times(body, zone)
    await references(session, body)

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
    )
    session.add(series)
    await session.flush()
    session.add(
        ClassSeriesParticipant(class_series_id=series.id, student_id=body.student_id)
    )
    await session.flush()
    await materialize_series(
        session,
        series.id,
        now=clock.now(),
        horizon_weeks=horizon_weeks,
        mode="create",
    )
    detail = await get_class_series(session, series.id)
    return ClassSeriesCreated(**detail.model_dump(), warnings=[])


def _occurrence(row: ClassSession) -> SeriesOccurrence:
    # Every row of a series has an original start (a CHECK).
    assert row.original_start is not None  # noqa: S101
    return SeriesOccurrence(
        id=row.id,
        state=SessionState(row.state).value,
        starts_at=row.starts_at.astimezone(UTC),
        ends_at=row.ends_at.astimezone(UTC),
        original_start=row.original_start.astimezone(UTC),
        is_exception=row.is_exception,
    )


async def get_class_series(
    session: AsyncSession, class_series_id: uuid.UUID
) -> ClassSeriesDetail:
    """``GET /api/v1/class-series/{id}``: the series, its references and its
    occurrence rows starting now or later. At most seven statements."""
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
    rows = await session.scalars(
        select(ClassSession)
        .where(
            ClassSession.series_id == series.id,
            ClassSession.starts_at >= clock.now(),
        )
        .order_by(ClassSession.starts_at, ClassSession.id)
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
        version=series.version,
        created_at=series.created_at,
        updated_at=series.updated_at,
        occurrences=[_occurrence(row) for row in rows],
    )


async def has_class_series(session: AsyncSession) -> bool:
    """Whether any class series exists (#37 E1 (a), #53 B6): a series stores
    a local time in the center zone, so even one with no occurrence row yet
    (it starts after the horizon) blocks a time-zone change."""
    return bool(await session.scalar(select(select(ClassSeries.id).exists())))
