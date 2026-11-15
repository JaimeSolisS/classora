"""Course completion of a cohort (#85 M5-18; spec §3 completion criteria,
§2 tutor and family access, §4 "grades visible according to permissions").

``GET /api/v1/cohorts/{cohort_id}/completion``
(:mod:`app.workflows.cohorts_router`, ``authenticated()`` plus this record
policy) composes ``courses`` (the cohort, its frozen snapshot and active
enrollments, and the pure rules :mod:`app.modules.courses.completion`),
``scheduling`` (the access facts, the cohort status, the participation
counts), ``academics`` (the counted evaluations) and ``families`` (the
students' names). Nothing is stored (E5: live and read-only, no override,
no notification, no audit row).

**Who reads which rows (E6), checked in this order (C1)** after the
route's ``401`` and the path's ``422``:

1. the record-policy facts (``scheduling.record_viewer`` with
   ``evaluation.manage`` as the staff permission);
2. **staff** (``evaluation.manage``: Owner, Administrator, Academic
   Coordinator): every active enrollment;
3. **the tutor who currently teaches the cohort** (#246 A2,
   ``scheduling.taught_cohort_ids``): every active enrollment;
4. **students and guardians**: their visible students (a minor, or an
   adult who gave the guardian ``guardian_academic_access``) with an active
   enrollment, only those rows;
5. nothing visible: ``403 permission_denied`` for a ``course.view`` holder
   (Finance: #80 E5 keeps scores away from them; they can see the cohort
   exists), else ``404 cohort_not_found``, the body of an unknown id. An
   unknown id is ``404`` for everyone.

With several roles the caller gets the union (each student once). The
facts are read on every request, so a withdrawal, an unlink, a revoked
consent or the end of teaching applies at once. No academic data is read
before the decision: only the access facts, whether the caller teaches the
cohort, and the cohort with its active enrollments.

**Statements (C3):** at most eight whatever the number of students,
assessments and sessions: the facts (0-2), teaching (0-1), the cohort with
its enrollments (1), then the names, the cohort's series status, the
participation and the evaluations (1 each; none without rows).
"""

import uuid
from datetime import UTC
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.authz import PermissionDeniedError, Principal
from app.modules.academics import service as academics
from app.modules.courses import service as courses
from app.modules.families import service as families
from app.modules.scheduling import service as scheduling
from app.workflows.cohort_schemas import (
    AssessmentCompletionItem,
    AssessmentsCompletion,
    AttendanceCompletion,
    CohortCompletion,
    CompletionAssessment,
    CompletionCriteria,
    CompletionModule,
    CompletionStudent,
    CompletionTemplate,
    ModuleCompletionItem,
    ModulesCompletion,
    StudentCompletion,
)
from app.workflows.cohorts import status_of

EVALUATION_MANAGE: Final = "evaluation.manage"
COURSE_VIEW: Final = "course.view"

rules = courses.completion


async def _refuse(
    session: AsyncSession, principal: Principal, cohort_id: uuid.UUID
) -> None:
    """Step 5: ``403`` for a ``course.view`` holder when the cohort exists,
    otherwise the unknown-id ``404``."""
    if principal.has(COURSE_VIEW) and await courses.cohort_exists(session, cohort_id):
        raise PermissionDeniedError()
    raise courses.CohortNotFoundError()


def _criteria(criteria: courses.completion.Criteria) -> CompletionCriteria:
    return CompletionCriteria(
        min_attendance_percent=criteria.min_attendance_percent,
        required_modules=[
            CompletionModule(id=m.id, title=m.title) for m in criteria.required_modules
        ],
        assessments=[
            CompletionAssessment(
                id=a.id,
                title=a.title,
                module_id=a.module_id,
                is_required=a.is_required,
                min_score_percent=a.min_score_percent,
            )
            for a in criteria.assessments
        ],
    )


def _row(
    enrollment: courses.ActiveEnrollment,
    display_name: str,
    result: courses.completion.StudentCompletionResult,
) -> StudentCompletion:
    attendance = result.attendance
    return StudentCompletion(
        student=CompletionStudent(id=enrollment.student_id, display_name=display_name),
        enrolled_at=enrollment.enrolled_at.astimezone(UTC),
        status=result.status,
        final=result.final,
        attendance=AttendanceCompletion(
            status=attendance.status,
            min_percent=attendance.min_percent,
            held=attendance.held,
            attended=attendance.attended,
            absent=attendance.absent,
            remaining=attendance.remaining,
            awaiting_closeout=attendance.awaiting_closeout,
            attendance_percent=attendance.attendance_percent,
        ),
        assessments=AssessmentsCompletion(
            status=result.assessments.status,
            items=[
                AssessmentCompletionItem(
                    assessment_id=item.assessment_id,
                    title=item.title,
                    is_required=item.is_required,
                    min_score_percent=item.min_score_percent,
                    status=item.status,
                    score_percent=item.score_percent,
                    evaluated_on=item.evaluated_on,
                    attempts=item.attempts,
                )
                for item in result.assessments.items
            ],
        ),
        modules=ModulesCompletion(
            status=result.modules.status,
            items=[
                ModuleCompletionItem(
                    module_id=item.module_id,
                    title=item.title,
                    status=item.status,
                    basis=item.basis,
                )
                for item in result.modules.items
            ],
        ),
    )


async def get_cohort_completion(
    session: AsyncSession, principal: Principal, cohort_id: uuid.UUID
) -> CohortCompletion:
    """``GET /api/v1/cohorts/{id}/completion``: see the module docstring."""
    now = clock.now()
    viewer = await scheduling.record_viewer(
        session, principal, staff_permission=EVALUATION_MANAGE
    )
    teaches = False
    if not viewer.staff and viewer.tutor_id is not None:
        teaches = cohort_id in await scheduling.taught_cohort_ids(
            session, viewer.tutor_id, now=now, cohort_id=cohort_id
        )
    whole_roster = viewer.staff or teaches
    if not whole_roster and not viewer.students:
        await _refuse(session, principal, cohort_id)
    basis = await courses.cohort_completion_basis(session, cohort_id)
    if basis is None:
        raise courses.CohortNotFoundError()
    enrollments = [
        e for e in basis.enrollments if whole_roster or e.student_id in viewer.students
    ]
    if not whole_roster and not enrollments:
        if principal.has(COURSE_VIEW):
            raise PermissionDeniedError()
        raise courses.CohortNotFoundError()

    criteria = rules.criteria_of(basis.content, schema_version=basis.schema_version)
    student_ids = [e.student_id for e in enrollments]
    names = await families.student_references(session, student_ids)
    schedules = await scheduling.cohort_schedules(session, [cohort_id], now=now)
    cohort_status = status_of(schedules.get(cohort_id))
    participation = await scheduling.cohort_participation(
        session, cohort_id, student_ids, now=now
    )
    counted = await academics.cohort_assessment_evaluations(
        session, cohort_id, student_ids
    )

    by_student: dict[uuid.UUID, dict[uuid.UUID, rules.CountedEvaluation]] = {}
    for (owner, assessment_id), found in counted.items():
        by_student.setdefault(owner, {})[assessment_id] = rules.CountedEvaluation(
            evaluation_id=found.evaluation_id,
            score_percent=found.score_percent,
            evaluated_on=found.evaluated_on,
            attempts=found.attempts,
        )

    rows: list[tuple[str, str, StudentCompletion]] = []
    for enrollment in enrollments:
        student_id = enrollment.student_id
        counts = participation.get(student_id, scheduling.ParticipationCounts())
        facts = rules.Participation(
            held=counts.held,
            attended=counts.attended,
            absent=counts.absent,
            remaining=counts.remaining,
            awaiting_closeout=counts.awaiting_closeout,
        )
        result = rules.evaluate_student(
            criteria,
            facts,
            by_student.get(student_id, {}),
            final=rules.is_final(cohort_status, facts),
        )
        reference = names.get(student_id)
        name = "" if reference is None else reference.display_name
        rows.append((name.casefold(), str(student_id), _row(enrollment, name, result)))
    rows.sort(key=lambda item: (item[0], item[1]))
    return CohortCompletion(
        cohort_id=basis.cohort_id,
        cohort_name=basis.cohort_name,
        cohort_status=cohort_status,
        template=CompletionTemplate(
            name=basis.template_name, version=basis.template_version
        ),
        criteria=_criteria(criteria),
        students=[row for _, _, row in rows],
    )
