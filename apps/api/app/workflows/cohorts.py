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

**Reads** (#65 F, #66 F, G): :func:`get_cohort_detail` (at most nine
statements, the #66 roster included), :func:`list_cohorts` (at most five
whatever the page size for ``course.view`` holders) and
:func:`list_cohort_sessions` (at most seven). The status (B2) comes from
the series rule, so it holds beyond the materialization horizon.

**Who reads what (#66 G, E4):** a holder of ``course.view`` (Owner,
Administrator, Academic Coordinator, Finance) sees every cohort, its roster
and enrolled count, and every session. Anyone else gets the record policy
(:func:`cohort_viewer`): their **visible students** are their own student
profile (``student`` role) and the students linked to their **active**
guardian profile (``guardian`` role; no academic-consent gate: spec §2
lists scheduling apart from academic records), the union for both roles.
They see only the cohorts where a visible student has an **active**
enrollment (else an empty list, or ``404 cohort_not_found``, never
``403``), without the roster, the count or the series' assignment and
offer data, and of the sessions only the ``confirmed`` and ``completed``
ones a visible student takes part in (ADR-0005 D2), each listing only the
caller's own students. Tutors (until #67) and unlinked accounts have no
visible student. The facts are read on every request, so a withdrawal, an
unlink or a deactivated guardian profile applies at once.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.authz import Principal
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
from app.modules.families import service as families
from app.modules.identity import service as identity
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.schemas import (
    CohortSessionsQuery,
    EnrollmentActor,
    EnrollmentStudent,
    SessionTutor,
)
from app.workflows.cohort_schemas import (
    CohortCreate,
    CohortDetail,
    CohortEnrollmentItem,
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


COURSE_VIEW: Final = "course.view"


class _NoAssignments:
    """Cohort reads never use tutor assignments (#67 adds tutors)."""

    async def student_ids_for_tutor(
        self, session: AsyncSession, user_id: uuid.UUID
    ) -> frozenset[uuid.UUID]:
        return frozenset()


_NO_ASSIGNMENTS: Final = _NoAssignments()


@dataclass(frozen=True)
class CohortViewer:
    """Who reads a cohort (#66 G1): staff (``course.view``), or the caller's
    visible students (their own profile, the students linked to their
    active guardian profile)."""

    staff: bool
    students: frozenset[uuid.UUID]


STAFF_VIEWER: Final = CohortViewer(staff=True, students=frozenset())


async def cohort_viewer(session: AsyncSession, principal: Principal) -> CohortViewer:
    """The G1 facts of ``principal``: no statement for staff, one per family
    role otherwise (``families.service.load_access_facts``)."""
    if principal.has(COURSE_VIEW):
        return STAFF_VIEWER
    facts = await families.load_access_facts(session, principal, _NO_ASSIGNMENTS)
    students = set(facts.linked_student_ids)
    if facts.own_student_id is not None:
        students.add(facts.own_student_id)
    return CohortViewer(staff=False, students=frozenset(students))


def _actor(accounts: dict[uuid.UUID, Any], user_id: uuid.UUID) -> EnrollmentActor:
    account = accounts.get(user_id)
    return EnrollmentActor(
        id=user_id, display_name="" if account is None else account.display_name
    )


def enrollment_item(
    row: courses.CohortEnrollmentRow,
    *,
    overridden: bool,
    students: dict[uuid.UUID, Any],
    accounts: dict[uuid.UUID, Any],
) -> CohortEnrollmentItem:
    """A :class:`CohortEnrollmentItem` (#66 F2) from a stored period."""
    student = students.get(row.student_id)
    return CohortEnrollmentItem(
        id=row.id,
        student=EnrollmentStudent(
            id=row.student_id,
            display_name="" if student is None else student.display_name,
            is_active=False if student is None else student.is_active,
        ),
        enrolled_at=row.enrolled_at.astimezone(UTC),
        enrolled_by=_actor(accounts, row.enrolled_by_user_id),
        withdrawn_at=(
            None if row.withdrawn_at is None else row.withdrawn_at.astimezone(UTC)
        ),
        withdrawn_by=(
            None
            if row.withdrawn_by_user_id is None
            else _actor(accounts, row.withdrawn_by_user_id)
        ),
        withdrawal_reason=row.withdrawal_reason,
        overridden=overridden,
        version=row.version,
    )


def _actors(rows: list[courses.CohortEnrollmentRow]) -> set[uuid.UUID]:
    actors: set[uuid.UUID] = set()
    for row in rows:
        actors.add(row.enrolled_by_user_id)
        if row.withdrawn_by_user_id is not None:
            actors.add(row.withdrawn_by_user_id)
    return actors


async def get_cohort_enrollment(
    session: AsyncSession, cohort_id: uuid.UUID, enrollment_id: uuid.UUID
) -> CohortEnrollmentItem:
    """One enrollment of a cohort (the enroll and withdraw responses; staff
    only). Four statements."""
    [row] = await courses.cohort_enrollment_rows(
        session, cohort_id=cohort_id, enrollment_ids=[enrollment_id]
    )
    overridden = await scheduling.overridden_cohort_enrollment_ids(session, [row.id])
    students = await families.student_references(session, [row.student_id])
    accounts = await identity.get_account_references(session, _actors([row]))
    return enrollment_item(
        row, overridden=row.id in overridden, students=students, accounts=accounts
    )


async def get_cohort_detail(
    session: AsyncSession,
    cohort_id: uuid.UUID,
    viewer: CohortViewer = STAFF_VIEWER,
) -> CohortDetail:
    """``GET /api/v1/cohorts/{id}`` (#65 F1, F3; #66 F1, G3): ``404
    cohort_not_found`` when unknown or hidden from ``viewer``. For staff at
    most nine statements (the cohort with its snapshot; the enrollments;
    their overrides; the series; the next occurrences; the students; the
    subject; the accounts; the room); for a family caller at most nine with
    the access facts."""
    family = None if viewer.staff else viewer.students
    record = await courses.get_cohort(session, cohort_id, visible_to=family)
    rows: list[courses.CohortEnrollmentRow] = []
    overridden: set[uuid.UUID] = set()
    if viewer.staff:
        rows = await courses.cohort_enrollment_rows(session, cohort_id=cohort_id)
        overridden = await scheduling.overridden_cohort_enrollment_ids(
            session, [row.id for row in rows]
        )
    read = await scheduling.cohort_series_read(
        session,
        cohort_id,
        now=clock.now(),
        family=family,
        extra_student_ids={row.student_id for row in rows},
        extra_user_ids=_actors(rows),
    )
    if read is not None:
        subject = read.subject
    else:  # pragma: no cover - a cohort is always created with its series
        subject = (await catalog.subject_references(session, [record.subject_id]))[
            record.subject_id
        ]
    room = None if read is None else read.room
    roster = None
    enrolled_count = None
    if viewer.staff:
        students = {} if read is None else read.students
        accounts = {} if read is None else read.accounts
        items = [
            enrollment_item(
                row,
                overridden=row.id in overridden,
                students=students,
                accounts=accounts,
            )
            for row in rows
        ]
        active = sorted(
            (item for item in items if item.withdrawn_at is None),
            key=lambda item: (item.student.display_name.casefold(), str(item.id)),
        )
        withdrawn = sorted(
            (item for item in items if item.withdrawn_at is not None),
            key=lambda item: (item.withdrawn_at, str(item.id)),
            reverse=True,
        )
        roster = active + withdrawn
        enrolled_count = len(active)
    assert record.syllabus is not None  # noqa: S101 - a detail read has it
    detail = CohortDetail(
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
        enrolled_count=enrolled_count,
        roster=roster,
    )
    return detail if viewer.staff else detail.for_family()


async def list_cohorts(
    session: AsyncSession,
    *,
    q: str | None,
    template_id: uuid.UUID | None,
    subject_id: uuid.UUID | None,
    status: CohortStatusFilter,
    limit: int,
    cursor: str | None,
    viewer: CohortViewer = STAFF_VIEWER,
) -> CohortPage:
    """``GET /api/v1/cohorts`` (#65 F2, #66 F3, G2): a cursor page by name,
    then id (the #56 groups keyset; ``400 invalid_cursor`` for a bad
    cursor). ``status`` ``current`` (the default) is upcoming plus in
    progress. For staff at most five statements whatever the page size
    (every cohort's series facts; the page; the subjects; the tutors; the
    enrolled counts). A family caller sees only the cohorts where a visible
    student has an active enrollment (none: an empty page), with
    ``enrolled_count`` and ``assignment_status`` null, the tutor only once
    they accepted, and ``next_starts_at`` from the ``confirmed`` sessions
    their students take part in."""
    if not viewer.staff and not viewer.students:
        return CohortPage(items=[], next_cursor=None)
    now = clock.now()
    schedules = await scheduling.cohort_schedules(session, None, now=now)
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
        visible_to=None if viewer.staff else viewer.students,
    )
    subjects = await catalog.subject_references(
        session, {record.subject_id for record in records}
    )
    tutors = {
        schedule.tutor_user_id
        for record in records
        if (schedule := schedules.get(record.id)) is not None
        and _shown_tutor(schedule, viewer) is not None
        and schedule.tutor_user_id is not None
    }
    accounts = await identity.get_account_references(session, tutors)
    counts: dict[uuid.UUID, int] = {}
    family_starts: dict[uuid.UUID, Any] = {}
    page_ids = [record.id for record in records]
    if viewer.staff:
        counts = await courses.active_cohort_enrollment_counts(session, page_ids)
    else:
        family_starts = await scheduling.cohort_family_next_starts(
            session, page_ids, viewer.students, now=now
        )
    return CohortPage(
        items=[
            _list_item(
                record,
                schedules.get(record.id),
                subjects,
                accounts,
                viewer=viewer,
                enrolled_count=counts.get(record.id, 0) if viewer.staff else None,
                family_next=family_starts.get(record.id),
            )
            for record in records
        ],
        next_cursor=next_cursor,
    )


def _shown_tutor(
    schedule: scheduling.CohortSchedule, viewer: CohortViewer
) -> uuid.UUID | None:
    """The series' tutor; for a family caller only once they accepted (a
    pending offer is assignment data)."""
    if viewer.staff or schedule.assignment_status == "accepted":
        return schedule.tutor_user_id
    return None


def _list_item(
    record: CohortRecord,
    schedule: scheduling.CohortSchedule | None,
    subjects: dict[uuid.UUID, SubjectReference],
    accounts: dict[uuid.UUID, identity.AccountReference],
    *,
    viewer: CohortViewer,
    enrolled_count: int | None,
    family_next: Any,
) -> CohortListItem:
    tutor = None
    tutor_id = None if schedule is None else _shown_tutor(schedule, viewer)
    if tutor_id is not None:
        account = accounts.get(tutor_id)
        tutor = SessionTutor(
            id=tutor_id,
            display_name="" if account is None else account.display_name,
        )
    next_starts_at = None
    if viewer.staff:
        next_starts_at = None if schedule is None else schedule.next_starts_at
    else:
        next_starts_at = family_next
    return CohortListItem(
        id=record.id,
        name=record.name,
        template=CohortTemplateName(id=record.template.id, name=record.template.name),
        subject=_subject(subjects[record.subject_id]),
        max_students=record.max_students,
        tutor=tutor,
        assignment_status=(
            None if schedule is None or not viewer.staff else schedule.assignment_status  # type: ignore[arg-type]
        ),
        first_session_on=None if schedule is None else schedule.first_session_on,
        last_session_on=None if schedule is None else schedule.last_session_on,
        next_starts_at=next_starts_at,
        status=_status(schedule),
        enrolled_count=enrolled_count,
    )


async def list_cohort_sessions(
    session: AsyncSession,
    cohort_id: uuid.UUID,
    query: CohortSessionsQuery,
    viewer: CohortViewer = STAFF_VIEWER,
) -> CohortSessions:
    """``GET /api/v1/cohorts/{id}/sessions`` (#65 F4, #66 G4): the #57 range
    rules (``422 range_invalid``, ``range_too_long``), then the cohort's
    sessions overlapping ``[from, to)`` by start; ``404 cohort_not_found``
    when unknown or hidden. Staff: every state, at most four statements (the
    rows, the tutors, the rooms, and the cohort when no row was found). A
    family caller: the ``confirmed`` / ``completed`` sessions a visible
    student takes part in, each with ``students`` (only theirs); at most
    seven statements with the access facts."""
    if viewer.staff:
        items = await scheduling.list_cohort_sessions(
            session, cohort_id, query.from_, query.to
        )
        if not items and not await courses.cohort_exists(session, cohort_id):
            raise courses.CohortNotFoundError()
        return CohortSessions(items=items)
    items = await scheduling.list_cohort_sessions(
        session, cohort_id, query.from_, query.to, family=viewer.students
    )
    if not await courses.cohort_visible_to(session, cohort_id, viewer.students):
        raise courses.CohortNotFoundError()
    return CohortSessions(items=items)
