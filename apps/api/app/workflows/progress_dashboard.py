"""The progress dashboard reads (#84 M5-17; spec §2 current and former tutor
access, §3 "linked attendance … and approved summaries where permitted", §4
"progress dashboards with grade trends, attendance, completed objectives and
learning goals", §16, W5.3, W5.4; architecture §4.1, §4.2).

Two closed, family-safe reads per student, the **same shape for every
audience** (E2):

- :func:`student_progress` (``GET /api/v1/students/{id}/progress``): live
  aggregates and monthly trends for a center-local range: attendance,
  evaluation and homework-grade averages (kept apart, #82 E9), homework
  completion and learning-plan progress (E6);
- :func:`session_log` (``GET /api/v1/students/{id}/session-log``): the
  student's started sessions, newest first, each with the student's own
  attendance, the **approved** closeout summary (E3: only while the closeout
  is ``approved``, so text under review never shows) and the count of the
  notes shared with the family.

The routes are in :mod:`app.workflows.progress_dashboard_router`; the views
in :mod:`app.workflows.progress_dashboard_schemas`. Cross-module reads
happen only here (ADR-0002): ``scheduling`` gives the in-scope sessions (the
#83 ``student_attendance_summary`` rule), ``academics`` the grades, homework,
plans, approved summaries and note counts, ``catalog`` and ``identity`` the
names.

**Who** (E4; the pure rule ``academics.progress_dashboard_rules
.progress_audience``), the first that applies:

1. ``evaluation.manage`` holders (Owner, Administrator, Academic
   Coordinator): both reads, any student;
2. a ``tutor``-role user with the student in their current assignments
   (``scheduling.service.student_ids_for_tutor``): both reads, every tutor's
   sessions;
3. the family (``scheduling.service.ensure_family_student_access``, #79):
   the student and a linked guardian of a minor, or of an adult who gave
   them ``guardian_academic_access``; a linked guardian of an adult without
   it gets ``403 student_consent_required`` (checked on every request);
4. a former tutor (a ``tutor``-role user who taught an in-scope session of
   the student: its current tutor or its closeout's tutor): the session log
   with only the sessions they taught; the progress summary is ``404``;
5. anyone else (Finance, other tutors, unlinked users) and an unknown id: the
   same ``404``.

**Order:** ``401`` → ``422`` (schema, then the query: the range rules and
``subject_unknown``) → the record policy above → the reads. A ``403`` or
``404`` reads no academic data. Neither read writes, audits or notifies.
"""

import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Final, cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.authz import Principal, RecordNotFoundError
from app.core.errors import AppError
from app.modules.academics import service as academics
from app.modules.administration import service as administration
from app.modules.catalog import service as catalog
from app.modules.families import service as families
from app.modules.identity import service as identity
from app.modules.scheduling import service as scheduling
from app.workflows.progress_dashboard_schemas import (
    ProgressAttendance,
    ProgressAttendanceMonth,
    ProgressGradeMonth,
    ProgressGrades,
    ProgressGradeSubject,
    ProgressHomework,
    ProgressPlanItem,
    ProgressPlans,
    ProgressSubject,
    SessionLog,
    SessionLogItem,
    SessionLogSummary,
    SessionLogTutor,
    StudentProgress,
)

rules = academics.progress_dashboard_rules
evaluation_rules = academics.evaluation_rules
assignment_rules = academics.assignment_rules
Audience = rules.Audience

_SUBJECT_UNKNOWN_MESSAGE: Final = "No such subject."


class ProgressQueryError(AppError):
    """``422 validation_error`` with one query field error: ``range_invalid``,
    ``range_in_future``, ``range_too_long`` or ``subject_unknown`` (B1, C1)."""

    status = 422
    code = "validation_error"
    detail = "The request is not valid."

    def __init__(self, issue: rules.RangeIssue) -> None:
        field, code, message = issue
        super().__init__(
            errors=[{"path": ["query", field], "code": code, "message": message}]
        )


# --- A: the record policy -------------------------------------------------------------


async def _family_access(
    session: AsyncSession, principal: Principal, student_id: uuid.UUID
) -> rules.FamilyAccess:
    """The #79 family check as a value (no statement without a family
    role)."""
    try:
        await scheduling.ensure_family_student_access(session, principal, student_id)
    except scheduling.StudentConsentRequiredError:
        return "consent_required"
    except RecordNotFoundError:
        return "none"
    return "allowed"


async def _audience(
    session: AsyncSession,
    principal: Principal,
    student_id: uuid.UUID,
    route: rules.DashboardRoute,
    *,
    now: datetime,
) -> rules.Audience:
    """A1: staff → current tutor → family → former tutor (session log only)
    → ``404``; ``403 student_consent_required`` for a linked guardian of an
    adult without the consent whom nothing else allows. Each check runs only
    when the previous ones didn't allow the caller."""
    if evaluation_rules.is_reviewer(principal):
        if not await families.student_references(session, [student_id]):
            raise RecordNotFoundError()
        return Audience.STAFF
    tutor = evaluation_rules.is_tutor(principal)
    current = tutor and student_id in await scheduling.student_ids_for_tutor(
        session, principal.user_id, now=now
    )
    family: rules.FamilyAccess = "none"
    former = False
    if not current:
        family = await _family_access(session, principal, student_id)
        if family != "allowed" and tutor and route == "session_log":
            former = await scheduling.tutor_taught_student(
                session,
                principal.user_id,
                student_id,
                now=now,
                taught_sessions=academics.closeout_sessions_of(principal.user_id),
            )
    audience = rules.progress_audience(
        staff=False, current_tutor=current, family=family, former_tutor=former
    )
    match rules.route_block(audience, family, route):
        case rules.CONSENT_REQUIRED:
            raise scheduling.StudentConsentRequiredError()
        case rules.NOT_FOUND:
            raise RecordNotFoundError()
    return audience


# --- B: the progress summary ----------------------------------------------------------


def _subject(subject_id: uuid.UUID, name: str) -> ProgressSubject:
    return ProgressSubject(id=subject_id, name=name)


def _by_name(item: tuple[uuid.UUID, str]) -> tuple[str, str]:
    return (item[1].casefold(), str(item[0]))


def _grade_values(
    scores: Sequence[Decimal], grades: Sequence[Decimal]
) -> dict[str, Any]:
    return {
        "evaluation_count": len(scores),
        "evaluation_average_percent": evaluation_rules.average_percent(scores),
        "assignment_count": len(grades),
        "assignment_average_percent": assignment_rules.average_percent(list(grades)),
    }


def _grades(
    scores: Iterable[academics.ScoreRow],
    graded: Iterable[academics.HomeworkRow],
) -> ProgressGrades:
    """B3: the totals, per subject (by name) and per (month, subject) with a
    value (by month, then subject name)."""
    names: dict[uuid.UUID, str] = {}
    by_subject: dict[uuid.UUID, tuple[list[Decimal], list[Decimal]]] = defaultdict(
        lambda: ([], [])
    )
    by_month: dict[tuple[str, uuid.UUID], tuple[list[Decimal], list[Decimal]]] = (
        defaultdict(lambda: ([], []))
    )
    all_scores: list[Decimal] = []
    all_grades: list[Decimal] = []
    for score in scores:
        names[score.subject_id] = score.subject_name
        all_scores.append(score.score_percent)
        by_subject[score.subject_id][0].append(score.score_percent)
        month = rules.month_key(score.evaluated_on)
        by_month[(month, score.subject_id)][0].append(score.score_percent)
    for row in graded:
        assert row.score_percent is not None  # noqa: S101 (counted grades only)
        assert row.graded_on is not None  # noqa: S101
        names[row.subject_id] = row.subject_name
        all_grades.append(row.score_percent)
        by_subject[row.subject_id][1].append(row.score_percent)
        month = rules.month_key(row.graded_on)
        by_month[(month, row.subject_id)][1].append(row.score_percent)
    subject_order = sorted(by_subject, key=lambda s: _by_name((s, names[s])))
    month_order = sorted(
        by_month, key=lambda key: (key[0], *_by_name((key[1], names[key[1]])))
    )
    return ProgressGrades(
        **_grade_values(all_scores, all_grades),
        by_subject=[
            ProgressGradeSubject(
                subject=_subject(s, names[s]), **_grade_values(*by_subject[s])
            )
            for s in subject_order
        ],
        by_month=[
            ProgressGradeMonth(
                month=month,
                subject=_subject(s, names[s]),
                **_grade_values(*by_month[(month, s)]),
            )
            for month, s in month_order
        ],
    )


def _homework(
    rows: Iterable[academics.HomeworkRow], *, zone: str, now: datetime
) -> ProgressHomework:
    counts = academics.homework_state_counts(rows, zone=zone, now=now)
    return ProgressHomework(
        **counts,
        completion_percent=rules.completion_percent(
            counts["submitted"], counts["graded"], counts["missing"]
        ),
    )


def _plan_order(item: tuple[Any, str, Any]) -> tuple[Any, ...]:
    """B3: active plans first by target date (none last), then completed ones
    by completion (the latest first); the id last."""
    plan = item[0]
    if plan.status == academics.learning_plan_rules.ACTIVE:
        target: date | None = plan.target_on
        return (0, target is None, target or date.max, 0.0, str(plan.id))
    closed: datetime | None = plan.closed_at
    return (
        1,
        closed is None,
        date.max,
        -closed.timestamp() if closed is not None else 0.0,
        str(plan.id),
    )


async def _plans(
    session: AsyncSession,
    plans: Sequence[tuple[Any, str]],
    *,
    today: date,
) -> ProgressPlans:
    """The listed plans with the #81 counts (one statement for the
    milestones, none without a plan)."""
    milestones = await academics.milestone_facts(
        session, [plan.id for plan, _ in plans]
    )
    counted = [
        (plan, name, academics.plan_counts(milestones[plan.id], today))
        for plan, name in plans
    ]
    counted.sort(key=_plan_order)
    return ProgressPlans(
        active=sum(
            1
            for plan, _, _ in counted
            if plan.status == academics.learning_plan_rules.ACTIVE
        ),
        completed=sum(
            1
            for plan, _, _ in counted
            if plan.status == academics.learning_plan_rules.COMPLETED
        ),
        objectives_complete=sum(c.objectives_complete for _, _, c in counted),
        objectives_total=sum(c.objectives_total for _, _, c in counted),
        milestones_overdue=sum(c.milestones_overdue for _, _, c in counted),
        items=[
            ProgressPlanItem(
                id=plan.id,
                title=plan.title,
                subject=_subject(plan.subject_id, name),
                status=plan.status,
                progress_percent=c.progress_percent,
                objectives_complete=c.objectives_complete,
                objectives_total=c.objectives_total,
                milestones_done=c.milestones_done,
                milestones_total=c.milestones_total,
                milestones_overdue=c.milestones_overdue,
                target_on=plan.target_on,
            )
            for plan, name, c in counted[: rules.PLAN_ITEMS_MAX]
        ],
    )


def _months(
    rows: Iterable[scheduling.MonthAttendance],
    start: date,
    end: date,
    keep: Callable[[uuid.UUID], bool],
) -> list[ProgressAttendanceMonth]:
    """E6: every month of the range, oldest first, zeros included."""
    totals: dict[str, dict[str, int]] = {
        month: dict.fromkeys(
            ("sessions", "present", "late", "absent", "not_recorded"), 0
        )
        for month in rules.months_between(start, end)
    }
    for row in rows:
        if not keep(row.subject_id) or row.month not in totals:
            continue
        bucket = totals[row.month]
        bucket["sessions"] += row.sessions
        bucket["present"] += row.present
        bucket["late"] += row.late
        bucket["absent"] += row.absent
        bucket["not_recorded"] += row.not_recorded
    return [
        ProgressAttendanceMonth(month=month, **counts)
        for month, counts in totals.items()
    ]


async def _filter_subject(
    session: AsyncSession, subject_id: uuid.UUID | None
) -> ProgressSubject | None:
    """``422 subject_unknown`` for an unknown subject (an inactive one is
    allowed: history). One statement, none without a filter."""
    if subject_id is None:
        return None
    found = (await catalog.subject_references(session, [subject_id])).get(subject_id)
    if found is None:
        raise ProgressQueryError(
            ("subject_id", rules.SUBJECT_UNKNOWN, _SUBJECT_UNKNOWN_MESSAGE)
        )
    return _subject(found.id, found.name)


async def student_progress(
    session: AsyncSession,
    principal: Principal,
    student_id: uuid.UUID,
    *,
    from_: date | None,
    to: date | None,
    subject_id: uuid.UUID | None,
) -> StudentProgress:
    """``GET /api/v1/students/{id}/progress`` (B1-B6, AM1). At most 12
    statements whatever the number of sessions, grades, homework or plans:
    the zone, the subject, the policy (up to 3), the attendance summary and
    months, the evaluations, the homework, the plans and their milestones."""
    zone = await administration.get_center_time_zone(session)
    now = clock.now()
    today = clock.today_in(zone)
    start, end = rules.default_range(today, from_=from_, to=to)
    issue = rules.progress_range_issue(start, end, today)
    if issue is not None:
        raise ProgressQueryError(issue)
    subject = await _filter_subject(session, subject_id)
    await _audience(session, principal, student_id, "progress", now=now)

    names = catalog.subject_name_columns()
    attendance = await scheduling.student_attendance_summary(
        session, student_id, start, end, subject_id=subject_id, time_zone=zone, now=now
    )
    months = await scheduling.student_attendance_by_month(
        session, student_id, start, end, time_zone=zone, now=now, subjects=names
    )
    scores = await academics.evaluation_scores(
        session, student_id, start, end, subjects=names
    )
    homework = await academics.homework_rows(
        session, student_id, start, end, subjects=names
    )
    plans = await academics.dashboard_plans(session, student_id, subjects=names)

    def keep(candidate: uuid.UUID) -> bool:
        return subject_id is None or candidate == subject_id

    def due(row: academics.HomeworkRow) -> bool:
        return start <= row.due_on <= end

    subjects: dict[uuid.UUID, str] = {}
    for row in months:
        subjects[row.subject_id] = row.subject_name
    for score in scores:
        subjects[score.subject_id] = score.subject_name
    for item in homework:
        subjects[item.subject_id] = item.subject_name
    for plan, name in plans:
        subjects[plan.subject_id] = name

    return StudentProgress(
        student_id=student_id,
        from_=start,
        to=end,
        subject=subject,
        subjects=[_subject(*item) for item in sorted(subjects.items(), key=_by_name)],
        attendance=ProgressAttendance(
            sessions=attendance.sessions,
            present=attendance.present,
            late=attendance.late,
            absent=attendance.absent,
            not_recorded=attendance.not_recorded,
            attendance_percent=rules.attendance_percent(
                attendance.present, attendance.late, attendance.absent
            ),
        ),
        attendance_by_month=_months(months, start, end, keep),
        grades=_grades(
            (score for score in scores if keep(score.subject_id)),
            (
                row
                for row in homework
                if keep(row.subject_id) and academics.is_counted_grade(row, start, end)
            ),
        ),
        homework=_homework(
            (row for row in homework if keep(row.subject_id) and due(row)),
            zone=zone,
            now=now,
        ),
        plans=await _plans(
            session,
            [(plan, name) for plan, name in plans if keep(plan.subject_id)],
            today=today,
        ),
    )


# --- C: the session log ---------------------------------------------------------------


async def session_log(
    session: AsyncSession,
    principal: Principal,
    student_id: uuid.UUID,
    *,
    from_: date | None,
    to: date | None,
    subject_id: uuid.UUID | None,
    cursor: str | None,
    limit: int,
) -> SessionLog:
    """``GET /api/v1/students/{id}/session-log`` (C1-C4). At most 6
    statements whatever the page and the caller's roles: the policy (staff:
    1; a tutor-role caller: their assignments, then the family check (up to
    3, the zone included) and the "taught" probe when needed) and **one**
    read: the rows with the subjects' and tutors' names, the student's
    marks, the approved summaries and the shared note counts joined in
    (``academics``' unexecuted selects). The zone is read only for a date
    bound, once per Unit of Work."""
    issue = rules.log_range_issue(from_, to)
    if issue is not None:
        raise ProgressQueryError(issue)
    now = clock.now()
    audience = await _audience(session, principal, student_id, "session_log", now=now)
    zone = (
        await administration.get_center_time_zone(session)
        if from_ is not None or to is not None
        else None
    )
    page = await scheduling.student_session_log(
        session,
        student_id,
        from_=from_,
        to=to,
        subject_id=subject_id,
        taught_by=(
            (principal.user_id, academics.closeout_sessions_of(principal.user_id))
            if audience is Audience.FORMER_TUTOR
            else None
        ),
        cursor=cursor,
        limit=limit,
        time_zone=zone,
        now=now,
        subjects=catalog.subject_name_columns(),
        people=identity.display_name_columns(),
        extras=(
            academics.approved_summary_columns(),
            academics.family_note_count_columns(student_id),
        ),
    )
    items = []
    for row in page.rows:
        summary, notes = row.extras
        items.append(
            SessionLogItem(
                class_session_id=row.class_session_id,
                starts_at=row.starts_at.astimezone(UTC),
                subject=_subject(row.subject_id, row.subject_name),
                session_kind=cast(Any, row.kind),
                tutor=(
                    None
                    if row.tutor_display_name is None
                    else SessionLogTutor(display_name=row.tutor_display_name)
                ),
                attendance=row.attendance,
                summary=(
                    None
                    if summary is None
                    else SessionLogSummary(
                        topics=summary[0],
                        performance=summary[1],
                        homework=summary[2],
                        next_steps=summary[3],
                    )
                ),
                published_note_count=0 if notes is None else int(notes[0]),
            )
        )
    return SessionLog(items=items, next_cursor=page.next_cursor)
