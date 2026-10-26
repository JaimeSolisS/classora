"""Progress report schedules (#316 M5-16c; spec §2, §4 "weekly/monthly …
progress reports prepared by tutors require admin approval before family
visibility", §9, §16; architecture §4.1, §4.2).

Academic staff (``progress_report.review``: Owner, Administrator, Academic
Coordinator) set, per student, which current tutor owes a weekly or monthly
report, optionally for one subject. After each period ends the worker
creates that tutor's empty draft (:mod:`app.workflows.
progress_report_schedule_tasks`), which then follows #83's workflow. This
module gives the staff routes (:mod:`app.workflows.
progress_report_schedules_router`) and the eligibility rule the job shares.

**Access** (E4): every route is ``require("progress_report.review")``,
checked before anything is read; everyone else gets ``403
permission_denied``, and an unknown student or schedule is ``404``.

**Orders:** add (C2) ``404`` → ``422 student_inactive`` → ``422
schedule_author_invalid`` (``body.author_user_id``) → ``422
subject_unknown`` / ``schedule_subject_not_assigned`` (``body.subject_id``)
→ ``422 schedule_starts_on_invalid`` (``body.starts_on``) → the insert →
``409 progress_report_schedule_exists``. Change (C3) ``404`` → the schedule
``FOR UPDATE`` → ``409 version_conflict`` → unchanged (no write) →
deactivate, or reactivate (C2's student, author and subject checks again,
top-level codes; ``starts_on`` moves to the current period at the earliest;
``409`` when another active schedule holds the key).
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final, cast

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.authz import Principal, RecordNotFoundError
from app.core.models import ensure_version
from app.modules.academics import service as academics
from app.modules.administration import service as administration
from app.modules.catalog import service as catalog
from app.modules.families import service as families
from app.modules.identity import service as identity
from app.modules.scheduling import service as scheduling
from app.workflows.progress_report_schedule_schemas import (
    EligibleAuthor,
    ProgressReportScheduleCreate,
    ProgressReportScheduleList,
    ProgressReportScheduleUpdate,
    ProgressReportScheduleView,
    ScheduleAuthor,
    ScheduleCadence,
    ScheduleIneligibleReason,
    ScheduleLatestPeriod,
    SchedulePerson,
    ScheduleSubject,
)

rules = academics.progress_report_schedule_rules

TUTOR_ROLE: Final = "tutor"
ACTIVE_STATE: Final = "active"

DUE_NOTIFICATION: Final = "academics.progress_report_due"


class ProgressReportDueParams(BaseModel):
    """``academics.progress_report_due`` (B4): the author's new placeholder,
    by ids, kind and period only (no text, no names)."""

    student_id: str
    report_id: str
    kind: str
    period_start: str
    period_end: str


def register_schedule_notifications() -> None:
    """Register the ``due`` type (again: a no-op); ``app.wiring`` and the
    job call it."""
    administration.register_notification_type(DUE_NOTIFICATION, ProgressReportDueParams)


# --- Eligibility (shared with the job) ------------------------------------------------


def author_is_tutor(account: Any | None) -> bool:
    """An existing, active user with the ``tutor`` role."""
    return (
        account is not None
        and account.state == ACTIVE_STATE
        and TUTOR_ROLE in account.roles
    )


def subject_is_active(subject: Any | None) -> bool:
    """The subject and its level are active (#83's create rule)."""
    return (
        subject is not None
        and bool(subject.is_active)
        and bool(subject.education_level_is_active)
    )


def ineligible_reason(
    schedule: academics.ProgressReportSchedule,
    *,
    student: Any | None,
    author: Any | None,
    author_subjects: frozenset[uuid.UUID],
    subject: Any | None,
) -> ScheduleIneligibleReason | None:
    """The first rule that would stop the job now (D3), from facts read by
    the caller: ``author_subjects`` are the subjects in which the author
    currently teaches the student (empty: not a current assignment)."""
    return rules.eligibility(
        student_active=student is not None and bool(student.is_active),
        author_tutor=author_is_tutor(author),
        author_current=bool(author_subjects),
        subject_set=schedule.subject_id is not None,
        subject_active=subject_is_active(subject),
        subject_assigned=schedule.subject_id in author_subjects,
    )


# --- Views ----------------------------------------------------------------------------


@dataclass(frozen=True)
class _Facts:
    students: Mapping[uuid.UUID, Any]
    people: Mapping[uuid.UUID, Any]
    subjects: Mapping[uuid.UUID, Any]
    teaching: Mapping[uuid.UUID, frozenset[uuid.UUID]]
    """The student's current tutors → their subjects with the student."""
    today: date
    reports: Sequence[academics.PeriodReportRow]


def _period_key(
    schedule: academics.ProgressReportSchedule, today: date
) -> academics.PeriodKey | None:
    if not schedule.is_active:
        return None
    due = rules.due_period(schedule.cadence, schedule.starts_on, today)
    if due is None:
        return None
    return academics.PeriodKey(
        schedule_id=schedule.id,
        student_id=schedule.student_id,
        author_user_id=schedule.author_user_id,
        kind=schedule.cadence,
        subject_id=schedule.subject_id,
        period_start=due[0],
    )


def _latest_period(
    schedule: academics.ProgressReportSchedule, facts: _Facts
) -> ScheduleLatestPeriod | None:
    key = _period_key(schedule, facts.today)
    if key is None:
        return None
    _, period_end = rules.latest_ended_period(schedule.cadence, facts.today)
    placeholder, manual = academics.split_period_reports(key, facts.reports)

    def found(row: academics.PeriodReportRow | None) -> Any:
        if row is None:
            return None
        return rules.PeriodReport(
            id=str(row.id), status=row.status, deleted=row.deleted
        )

    status, report_id = rules.latest_status(found(placeholder), found(manual))
    return ScheduleLatestPeriod(
        period_start=key.period_start,
        period_end=period_end,
        status=status,
        report_id=None if report_id is None else uuid.UUID(report_id),
    )


def _view(
    schedule: academics.ProgressReportSchedule, facts: _Facts
) -> ProgressReportScheduleView:
    author = facts.people.get(schedule.author_user_id)
    creator = facts.people.get(schedule.created_by_user_id)
    subject = (
        None if schedule.subject_id is None else facts.subjects.get(schedule.subject_id)
    )
    return ProgressReportScheduleView(
        id=schedule.id,
        version=schedule.version,
        student_id=schedule.student_id,
        author=ScheduleAuthor(
            user_id=schedule.author_user_id,
            display_name="" if author is None else author.display_name,
        ),
        cadence=cast(ScheduleCadence, schedule.cadence),
        subject=(
            None
            if schedule.subject_id is None
            else ScheduleSubject(
                id=schedule.subject_id, name="" if subject is None else subject.name
            )
        ),
        starts_on=schedule.starts_on,
        is_active=schedule.is_active,
        created_at=schedule.created_at,
        created_by=SchedulePerson(
            display_name="" if creator is None else creator.display_name
        ),
        ineligible_reason=ineligible_reason(
            schedule,
            student=facts.students.get(schedule.student_id),
            author=author,
            author_subjects=facts.teaching.get(schedule.author_user_id, frozenset()),
            subject=subject,
        ),
        latest_period=_latest_period(schedule, facts),
    )


async def _facts(
    session: AsyncSession,
    student: Any,
    schedules: Sequence[academics.ProgressReportSchedule],
    *,
    now: datetime,
) -> _Facts:
    """The zone, the current tutors, the people, the subjects and the
    latest-period reports: five statements (four without a due schedule)."""
    today = rules.local_today(now, await administration.get_center_time_zone(session))
    teaching = await scheduling.current_tutor_subjects_for_student(
        session, student.id, now=now
    )
    people = await identity.get_account_references(
        session,
        {s.author_user_id for s in schedules}
        | {s.created_by_user_id for s in schedules}
        | set(teaching),
    )
    subject_ids = {s.subject_id for s in schedules if s.subject_id is not None}
    for taught in teaching.values():
        subject_ids |= taught
    subjects = await catalog.subject_references(session, subject_ids)
    keys = [
        key for key in (_period_key(s, today) for s in schedules) if key is not None
    ]
    reports = await academics.period_reports(session, keys)
    return _Facts(
        students={student.id: student},
        people=people,
        subjects=subjects,
        teaching=teaching,
        today=today,
        reports=reports,
    )


def _eligible_authors(facts: _Facts) -> list[EligibleAuthor]:
    """B1's tutors who are active tutor-role users, with their active
    subjects (by name), by display name (casefold) then id."""
    found = []
    for user_id, subject_ids in facts.teaching.items():
        account = facts.people.get(user_id)
        if account is None or not author_is_tutor(account):
            continue
        subjects = sorted(
            (
                facts.subjects[subject_id]
                for subject_id in subject_ids
                if subject_is_active(facts.subjects.get(subject_id))
            ),
            key=lambda s: (s.name.casefold(), str(s.id)),
        )
        found.append(
            EligibleAuthor(
                user_id=user_id,
                display_name=account.display_name,
                subjects=[ScheduleSubject(id=s.id, name=s.name) for s in subjects],
            )
        )
    return sorted(found, key=lambda a: (a.display_name.casefold(), str(a.user_id)))


async def _student(session: AsyncSession, student_id: uuid.UUID) -> Any:
    found = (await families.student_references(session, [student_id])).get(student_id)
    if found is None:
        raise RecordNotFoundError()
    return found


async def _one_view(
    session: AsyncSession, schedule: academics.ProgressReportSchedule
) -> ProgressReportScheduleView:
    student = await _student(session, schedule.student_id)
    facts = await _facts(session, student, [schedule], now=clock.now())
    return _view(schedule, facts)


# --- S1 -------------------------------------------------------------------------------


async def list_schedules(
    session: AsyncSession, student_id: uuid.UUID
) -> ProgressReportScheduleList:
    """S1 (C1): at most seven statements, the same for 1 and 20 schedules."""
    student = await _student(session, student_id)
    schedules = await academics.schedules_for_student(
        session, student_id, limit=rules.LIST_LIMIT
    )
    facts = await _facts(session, student, schedules, now=clock.now())
    return ProgressReportScheduleList(
        items=[_view(schedule, facts) for schedule in schedules],
        eligible_authors=_eligible_authors(facts),
    )


# --- S2 -------------------------------------------------------------------------------

_AUTHOR_INVALID: Final = (
    "author_user_id",
    "schedule_author_invalid",
    "Choose an active tutor who currently teaches the student.",
)
_SUBJECT_UNKNOWN: Final = ("subject_id", "subject_unknown", "Choose an active subject.")
_SUBJECT_NOT_ASSIGNED: Final = (
    "subject_id",
    "schedule_subject_not_assigned",
    "Choose a subject the tutor currently teaches the student.",
)
_STARTS_ON_INVALID: Final = (
    "starts_on",
    rules.STARTS_ON_INVALID,
    "Choose the start of the current period or a later one (a Monday for a"
    " weekly schedule, the 1st for a monthly one), at most a year ahead.",
)


async def _author_subjects(
    session: AsyncSession, author_user_id: uuid.UUID, student_id: uuid.UUID
) -> frozenset[uuid.UUID] | None:
    """The subjects in which the author currently teaches the student, or
    ``None`` when the author isn't an active tutor who does (two
    statements at most)."""
    account = await identity.get_account_reference(session, author_user_id)
    if not author_is_tutor(account):
        return None
    subjects = await scheduling.subject_ids_for_tutor_student(
        session, author_user_id, student_id, now=clock.now()
    )
    return subjects or None


async def create_schedule(
    session: AsyncSession,
    *,
    principal: Principal,
    student_id: uuid.UUID,
    body: ProgressReportScheduleCreate,
) -> ProgressReportScheduleView:
    """``POST /api/v1/students/{id}/progress-report-schedules`` (C2). Nobody
    is notified."""
    student = await _student(session, student_id)
    if not student.is_active:
        raise academics.ScheduleStudentInactiveError()
    subjects = await _author_subjects(session, body.author_user_id, student_id)
    if subjects is None:
        raise academics.ProgressReportScheduleFieldError(*_AUTHOR_INVALID)
    if body.subject_id is not None:
        subject = await catalog.get_subject_for_reference(
            session, body.subject_id, lock=True
        )
        if not subject_is_active(subject):
            raise academics.ProgressReportScheduleFieldError(*_SUBJECT_UNKNOWN)
        if body.subject_id not in subjects:
            raise academics.ProgressReportScheduleFieldError(*_SUBJECT_NOT_ASSIGNED)
    today = await administration.center_today(session)
    starts_on = (
        rules.current_period_start(body.cadence, today)
        if body.starts_on is None
        else body.starts_on
    )
    if rules.starts_on_error(body.cadence, starts_on, today) is not None:
        raise academics.ProgressReportScheduleFieldError(*_STARTS_ON_INVALID)
    schedule = await academics.create_schedule(
        session,
        student_id=student_id,
        author_user_id=body.author_user_id,
        cadence=body.cadence,
        subject_id=body.subject_id,
        starts_on=starts_on,
        created_by_user_id=principal.user_id,
    )
    return await _one_view(session, schedule)


# --- S3 -------------------------------------------------------------------------------


async def _check_reactivation(
    session: AsyncSession, schedule: academics.ProgressReportSchedule
) -> None:
    """C2's student, author and subject steps at ``now`` (top-level codes:
    the body has no such fields)."""
    student = await _student(session, schedule.student_id)
    if not student.is_active:
        raise academics.ScheduleStudentInactiveError()
    subjects = await _author_subjects(
        session, schedule.author_user_id, schedule.student_id
    )
    if subjects is None:
        raise academics.ScheduleAuthorInvalidError()
    if schedule.subject_id is not None:
        subject = await catalog.get_subject_for_reference(
            session, schedule.subject_id, lock=True
        )
        if not subject_is_active(subject):
            raise academics.ScheduleSubjectUnknownError()
        if schedule.subject_id not in subjects:
            raise academics.ScheduleSubjectNotAssignedError()


async def update_schedule(
    session: AsyncSession,
    *,
    schedule_id: uuid.UUID,
    body: ProgressReportScheduleUpdate,
) -> ProgressReportScheduleView:
    """``PATCH /api/v1/progress-report-schedules/{id}`` (C3)."""
    schedule = await academics.get_schedule_for_update(session, schedule_id)
    if schedule is None:
        raise RecordNotFoundError()
    ensure_version(schedule, body.version)
    if schedule.is_active != body.is_active:
        if body.is_active:
            await _check_reactivation(session, schedule)
            today = await administration.center_today(session)
            await academics.reactivate_schedule(
                session,
                schedule,
                starts_on=rules.reactivated_starts_on(
                    schedule.cadence, schedule.starts_on, today
                ),
            )
        else:
            await academics.deactivate_schedule(session, schedule)
    return await _one_view(session, schedule)
