"""Course cohorts: one Unit of Work over ``courses`` and ``scheduling``
(#65; spec §3 "each cohort has its own dates, tutors, enrolled students,
pricing and attendance"; architecture §4.1, §4.2; ADR-0002, ADR-0005).

**Where things live (E2).** The cohort row (``course_cohorts``, its
template snapshot, its one subject and its maximum) belongs to
``courses``; its sessions are the occurrences of one ``course`` series in
``scheduling`` (``class_series.cohort_id``), which keeps the tutor, the room
and the timing. The routes (:mod:`app.workflows.cohorts_router`) call these
functions, which compose ``courses.service`` and ``scheduling.service``;
``courses`` never imports ``scheduling``.

**Create** (:func:`create_cohort`, D2), after the route's ``401``/``403``,
the body's ``422`` and the ``override``'s ``403``:

1. the center zone ``FOR SHARE`` (no student lock: a new cohort has no
   students);
2. ``courses.service.snapshot_course_template``: the template ``FOR
   SHARE`` and the snapshot of its current version, reused or inserted
   (``422 template_unknown`` / ``template_inactive`` on ``template_id``);
3. every remaining ``422`` in one response: ``subject_not_in_template``
   (E4: the subject is one of the snapshot's), ``subject_inactive``, the
   recurrence, ``starts_on_in_past``, ``crosses_local_date`` (``start_time``
   plus the snapshot's session length), ``respond_by_requires_tutor`` and
   the tutor and classroom references;
4. ``409 cohort_name_taken`` (any case);
5. **``COUNT`` (E3):** the rule's first 200 dates, **one**
   ``catalog.service.closures_between`` call, then
   ``courses.service.derive_occurrence_count``: the template's sessions
   fall outside the closures known now (``422 too_many_occurrences`` on
   ``recurrence`` beyond D4's 200). A closure added later skips a session
   with no automatic makeup; a closure removed later can restore one
   (#232, ADR-0005 D6);
6. the cohort row, then ``scheduling.service.create_course_series``: the
   ``course`` series (the snapshot's session length, the derived
   ``COUNT``), its horizon occurrences checked by the #48/#53 rules
   (closure dates ``skipped_closure``; any conflict not overridden is ``409
   scheduling_conflict`` per occurrence) and, with a tutor, the pending
   series offer (E5). Any ``409``/``422`` rolls back everything, the cohort
   and a new snapshot included.

**Lock order (G1):** students → zone → template ``FOR SHARE`` (only here)
→ group or cohort ``FOR UPDATE`` → series ``FOR UPDATE`` → sessions by id
→ offer.

**Reads** (F): :func:`get_cohort_detail` (at most eight statements; six
today), :func:`list_cohorts` (at most five whatever the page size) and
:func:`list_cohort_sessions` (at most six). The status (B2) comes from the
series rule, so it holds beyond the materialization horizon.
"""

import uuid
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.errors import FieldError
from app.modules.administration import service as administration
from app.modules.catalog import service as catalog
from app.modules.catalog.schemas import SubjectReference
from app.modules.courses import service as courses
from app.modules.courses.schemas import (
    CohortRecord,
    CohortStatus,
    CohortStatusFilter,
    CohortTemplateName,
    CohortUpdate,
)
from app.modules.identity import service as identity
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.schemas import CohortSessionsQuery, SessionTutor
from app.workflows.cohort_schemas import (
    CohortCreate,
    CohortDetail,
    CohortLevel,
    CohortListItem,
    CohortOccurrenceItem,
    CohortPage,
    CohortSessions,
    CohortSubject,
)

STATUS_FILTERS: Final[dict[str, frozenset[str]]] = {
    "upcoming": frozenset({"upcoming"}),
    "in_progress": frozenset({"in_progress"}),
    "ended": frozenset({"ended"}),
    "current": frozenset({"upcoming", "in_progress"}),
}


def _error(path: list[str | int], code: str, message: str) -> FieldError:
    return {"path": path, "code": code, "message": message}


# --- Create and update ---------------------------------------------------------------


async def create_cohort(
    session: AsyncSession,
    body: CohortCreate,
    *,
    horizon_weeks: int,
    actor_id: uuid.UUID,
) -> uuid.UUID:
    """``POST /api/v1/cohorts``: see the module docstring. Returns the new
    cohort's id."""
    zone = await administration.get_center_time_zone(session, for_share=True)
    try:
        snapshot = await courses.snapshot_course_template(session, body.template_id)
    except courses.CourseTemplateNotFound:
        raise courses.CohortValidationError(
            [
                _error(
                    ["body", "template_id"],
                    "template_unknown",
                    "Choose an existing course template.",
                )
            ]
        ) from None
    except courses.CourseTemplateInactive:
        raise courses.CohortValidationError(
            [
                _error(
                    ["body", "template_id"],
                    "template_inactive",
                    "The course template is inactive. Choose an active one.",
                )
            ]
        ) from None
    content = snapshot.content
    session_count = int(content["session_count"])
    session_minutes = int(content["session_minutes"])

    errors: list[FieldError] = []
    in_template = str(body.subject_id) in {s["id"] for s in content["subjects"]}
    if not in_template:
        errors.append(
            _error(
                ["body", "subject_id"],
                "subject_not_in_template",
                "Choose one of the template's subjects.",
            )
        )
    errors += await scheduling.check_course_series(
        session,
        subject_id=body.subject_id if in_template else None,
        tutor_user_id=body.tutor_user_id,
        classroom_id=body.classroom_id,
        starts_on=body.starts_on,
        start_time=body.start_time,
        duration_minutes=session_minutes,
        weekdays=body.recurrence.weekdays,
        interval=body.recurrence.interval,
        respond_by=body.respond_by,
        today=clock.today_in(zone),
    )
    if errors:
        raise courses.CohortValidationError(errors)
    await courses.ensure_cohort_name_free(session, body.name)

    dates = scheduling.course_rule_dates(
        starts_on=body.starts_on,
        weekdays=body.recurrence.weekdays,
        interval=body.recurrence.interval,
    )
    closed = scheduling.closed_dates(
        await catalog.closures_between(session, dates[0], dates[-1])
    )
    try:
        count = courses.derive_occurrence_count(dates, closed, session_count)
    except courses.TooManyOccurrencesError:
        raise courses.CohortValidationError(
            [
                _error(
                    ["body", "recurrence"],
                    "too_many_occurrences",
                    "The rule can't fit the course's sessions in 200 occurrences"
                    " around the closures.",
                )
            ]
        ) from None

    cohort_id = await courses.insert_cohort(
        session,
        name=body.name,
        snapshot=snapshot,
        subject_id=body.subject_id,
        max_students=body.max_students,
    )
    await scheduling.create_course_series(
        session,
        cohort_id=cohort_id,
        max_students=body.max_students,
        subject_id=body.subject_id,
        tutor_user_id=body.tutor_user_id,
        classroom_id=body.classroom_id,
        starts_on=body.starts_on,
        start_time=body.start_time,
        duration_minutes=session_minutes,
        weekdays=body.recurrence.weekdays,
        interval=body.recurrence.interval,
        count=count,
        zone=zone,
        now=clock.now(),
        respond_by=body.respond_by,
        override=body.override,
        horizon_weeks=horizon_weeks,
        actor_id=actor_id,
    )
    return cohort_id


async def update_cohort(
    session: AsyncSession, cohort_id: uuid.UUID, body: CohortUpdate
) -> None:
    """``PATCH /api/v1/cohorts/{id}`` (E1): name and maximum only."""
    await courses.update_cohort(session, cohort_id, body)


# --- Reads ---------------------------------------------------------------------------


def _subject(reference: SubjectReference) -> CohortSubject:
    return CohortSubject(
        id=reference.id,
        name=reference.name,
        level=CohortLevel(
            id=reference.education_level_id, name=reference.education_level_name
        ),
    )


def _status(schedule: scheduling.CohortSchedule | None) -> CohortStatus:
    if schedule is None or schedule.first_start is None:
        return "upcoming"
    last_start = schedule.last_start or schedule.first_start
    return courses.cohort_status(
        schedule.first_start,
        last_start,
        clock.now(),
        session_minutes=schedule.last_minutes,
    )


async def get_cohort_detail(
    session: AsyncSession, cohort_id: uuid.UUID
) -> CohortDetail:
    """``GET /api/v1/cohorts/{id}`` (F1, F3): ``404 cohort_not_found`` when
    unknown. Six statements (the cohort with its snapshot; the series; the
    next occurrences; the subject; the tutor; the room), seven once the
    series has participants (#66); at most eight."""
    record = await courses.get_cohort(session, cohort_id)
    read = await scheduling.cohort_series_read(session, cohort_id, now=clock.now())
    if read is not None:
        subject = read.subject
    else:  # pragma: no cover - a cohort is always created with its series
        subject = (await catalog.subject_references(session, [record.subject_id]))[
            record.subject_id
        ]
    room = None if read is None else read.room
    assert record.syllabus is not None  # noqa: S101 - a detail read has it
    return CohortDetail(
        id=record.id,
        name=record.name,
        version=record.version,
        created_at=record.created_at,
        updated_at=record.updated_at,
        template=record.template,
        template_snapshot_id=record.template_snapshot_id,
        subject=_subject(subject),
        max_students=record.max_students,
        effective_capacity=(
            record.max_students
            if room is None
            else min(record.max_students, room.capacity)
        ),
        status=_status(None if read is None else read.schedule),
        planned_session_count=record.session_count,
        session_minutes=record.session_minutes,
        first_session_on=None if read is None else read.schedule.first_session_on,
        last_session_on=None if read is None else read.schedule.last_session_on,
        series=None if read is None else read.series,
        next_occurrences=(
            []
            if read is None
            else [
                CohortOccurrenceItem(
                    id=item.id,
                    state=item.state,  # type: ignore[arg-type]
                    starts_at=item.starts_at,
                    ends_at=item.ends_at,
                )
                for item in read.next_occurrences
            ]
        ),
        syllabus=record.syllabus,
    )


async def list_cohorts(
    session: AsyncSession,
    *,
    q: str | None,
    template_id: uuid.UUID | None,
    subject_id: uuid.UUID | None,
    status: CohortStatusFilter,
    limit: int,
    cursor: str | None,
) -> CohortPage:
    """``GET /api/v1/cohorts`` (F2): a cursor page by name, then id (the
    #56 groups keyset; ``400 invalid_cursor`` for a bad cursor). ``status``
    ``current`` (the default) is upcoming plus in progress. At most four
    statements whatever the page size (every cohort's series facts; the
    page; the subjects; the tutors)."""
    schedules = await scheduling.cohort_schedules(session, None, now=clock.now())
    ids = None
    if status != "all":
        wanted = STATUS_FILTERS[status]
        ids = {
            cohort_id
            for cohort_id, schedule in schedules.items()
            if _status(schedule) in wanted
        }
    records, next_cursor = await courses.list_cohorts(
        session,
        q=q,
        template_id=template_id,
        subject_id=subject_id,
        ids=ids,
        limit=limit,
        cursor=cursor,
    )
    subjects = await catalog.subject_references(
        session, {record.subject_id for record in records}
    )
    tutors = {
        schedule.tutor_user_id
        for record in records
        if (schedule := schedules.get(record.id)) is not None
        and schedule.tutor_user_id is not None
    }
    accounts = await identity.get_account_references(session, tutors)
    return CohortPage(
        items=[
            _list_item(record, schedules.get(record.id), subjects, accounts)
            for record in records
        ],
        next_cursor=next_cursor,
    )


def _list_item(
    record: CohortRecord,
    schedule: scheduling.CohortSchedule | None,
    subjects: dict[uuid.UUID, SubjectReference],
    accounts: dict[uuid.UUID, identity.AccountReference],
) -> CohortListItem:
    tutor = None
    if schedule is not None and schedule.tutor_user_id is not None:
        account = accounts.get(schedule.tutor_user_id)
        tutor = SessionTutor(
            id=schedule.tutor_user_id,
            display_name="" if account is None else account.display_name,
        )
    return CohortListItem(
        id=record.id,
        name=record.name,
        template=CohortTemplateName(id=record.template.id, name=record.template.name),
        subject=_subject(subjects[record.subject_id]),
        max_students=record.max_students,
        tutor=tutor,
        assignment_status=(
            None if schedule is None else schedule.assignment_status  # type: ignore[arg-type]
        ),
        first_session_on=None if schedule is None else schedule.first_session_on,
        last_session_on=None if schedule is None else schedule.last_session_on,
        next_starts_at=None if schedule is None else schedule.next_starts_at,
        status=_status(schedule),
    )


async def list_cohort_sessions(
    session: AsyncSession, cohort_id: uuid.UUID, query: CohortSessionsQuery
) -> CohortSessions:
    """``GET /api/v1/cohorts/{id}/sessions`` (F4): the #57 range rules
    (``422 range_invalid``, ``range_too_long``), then the cohort's sessions
    overlapping ``[from, to)`` by start, in every state; ``404
    cohort_not_found`` when unknown. At most four statements (the rows, the
    tutors, the rooms, and the cohort when no row was found)."""
    items = await scheduling.list_cohort_sessions(
        session, cohort_id, query.from_, query.to
    )
    if not items and not await courses.cohort_exists(session, cohort_id):
        raise courses.CohortNotFoundError()
    return CohortSessions(items=items)
