"""Evaluations and the unified academic history (#80 M5-13; spec §2, §4
"structured diagnostic and ongoing evaluations … complete academic history
grouped by school year/semester/period", §9, §16, W5.3, W5.4; architecture
§4.1, §4.2).

Tutors record evaluations of a student from a template (its criteria are
copied, E2), finalize them (the student and the guardians with academic
access are notified, without scores or text) and amend them with an
internal reason; staff with ``evaluation.manage`` manage the templates and
void a final evaluation with a reason. Final, non-voided evaluations from
every tutor roll up into one academic history per student
(:func:`academic_history`), the **only** read that families get: drafts,
voided evaluations, change reasons and void data never reach it.
``tests/integration/test_session_notes_leak.py`` (the shared family leak
walk) proves no other ``GET`` route carries them.

The routes are in :mod:`app.workflows.evaluations_router`; the bodies and
views in :mod:`app.workflows.evaluation_schemas`.

**Who** (E5; the pure rules in ``academics.evaluation_rules``):

- templates: ``evaluation.manage`` holders read and manage all; users with
  the ``tutor`` role read active ones; anyone else ``403
  permission_denied``;
- create: a ``tutor``-role user whose current assignments
  (``scheduling.service.student_ids_for_tutor``) include the student; an
  ``evaluation.manage`` holder who isn't gets ``403 evaluation_tutor_only``;
  anyone else, and an unknown student, ``404``;
- an evaluation: its author always reads it (any state) and writes it
  while the student is one of their current assignments (else ``403
  evaluation_read_only``); a reviewer reads every final one (voided too)
  and voids; a tutor with the student assigned reads every final,
  non-voided one; a draft reaches only its author; anyone else the ``404``
  of an unknown id;
- the history: reviewers and tutors with the student assigned read it
  all; a former author only their own entries; the student and the linked
  guardians with academic access (``scheduling.service
  .ensure_family_student_access``: ``403 student_consent_required`` for a
  linked guardian of an adult without the consent); anyone else ``404``.

**Orders:** templates C2 (``401`` → ``422`` schema → ``403`` → ``404`` →
the template ``FOR UPDATE`` → ``409 version_conflict`` → ``422`` / ``409``
content → write); create D2 (:func:`create_evaluation`); writes on an
evaluation D3 (``404`` → ``403`` → the row ``FOR UPDATE`` → ``409
version_conflict`` → ``409`` state → ``422`` content → write).
"""

import uuid
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Final, cast
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.authz import PermissionDeniedError, Principal, RecordNotFoundError
from app.core.models import ensure_version
from app.core.pagination import CursorPage
from app.modules.academics import service as academics
from app.modules.administration import service as administration
from app.modules.catalog import service as catalog
from app.modules.courses import service as courses
from app.modules.families import service as families
from app.modules.identity import service as identity
from app.modules.scheduling import service as scheduling
from app.workflows.evaluation_schemas import (
    AcademicHistory,
    AssignmentGradeEntry,
    EvaluationContext,
    EvaluationContextAssessment,
    EvaluationContextCourse,
    EvaluationContextParticipant,
    EvaluationCourse,
    EvaluationCreate,
    EvaluationCriterionView,
    EvaluationKind,
    EvaluationPatch,
    EvaluationPerson,
    EvaluationScoreInput,
    EvaluationScoreView,
    EvaluationSessionRef,
    EvaluationStatus,
    EvaluationStudent,
    EvaluationSubject,
    EvaluationTemplateCreate,
    EvaluationTemplateList,
    EvaluationTemplateRef,
    EvaluationTemplateReplace,
    EvaluationTemplateView,
    EvaluationView,
    EvaluationVoid,
    EvaluationVoided,
    GroupBy,
    HistoryCourse,
    HistoryCriterion,
    HistoryEntry,
    HistoryGroup,
    HistoryItem,
    HistoryPeriod,
    HistorySubjectGroup,
)

rules = academics.evaluation_rules
assignment_rules = academics.assignment_rules
Relation = rules.EvaluationRelation

FINALIZED_NOTIFICATION: Final = "academics.evaluation_finalized"
VOIDED_NOTIFICATION: Final = "academics.evaluation_voided"


class EvaluationFinalizedParams(BaseModel):
    """``academics.evaluation_finalized`` (B3): no score, percentage,
    feedback, comment or tutor."""

    student_id: str
    evaluation_id: str
    subject_id: str
    evaluated_on: str


class EvaluationVoidedParams(BaseModel):
    """``academics.evaluation_voided`` (B3): no reason."""

    student_id: str
    evaluation_id: str


def register_evaluation_notifications() -> None:
    """Register both types (again: a no-op); ``app.wiring`` calls it."""
    administration.register_notification_type(
        FINALIZED_NOTIFICATION, EvaluationFinalizedParams
    )
    administration.register_notification_type(
        VOIDED_NOTIFICATION, EvaluationVoidedParams
    )


def _field(path: Sequence[str | int], code: str, message: str) -> Any:
    return (list(path), code, message)


def _invalid(field: str, code: str, message: str) -> academics.EvaluationFieldError:
    return academics.EvaluationFieldError.one(field, code, message)


# --- Shared ---------------------------------------------------------------------------


async def _assigned(
    session: AsyncSession, principal: Principal
) -> frozenset[uuid.UUID]:
    """The caller's current assignments (one statement; none without the
    ``tutor`` role)."""
    if not rules.is_tutor(principal):
        return frozenset()
    return await scheduling.student_ids_for_tutor(
        session, principal.user_id, now=clock.now()
    )


@dataclass(frozen=True)
class _Caller:
    principal: Principal
    assigned: frozenset[uuid.UUID]

    @property
    def reviewer(self) -> bool:
        return rules.is_reviewer(self.principal)

    def relation(self, evaluation: academics.Evaluation) -> rules.EvaluationRelation:
        return rules.evaluation_relation(
            self.principal,
            author_user_id=evaluation.author_user_id,
            student_assigned=evaluation.student_id in self.assigned,
        )

    def can_read(self, evaluation: academics.Evaluation) -> bool:
        return rules.can_read(
            self.relation(evaluation),
            status=evaluation.status,
            voided=evaluation.voided_at is not None,
        )

    def can(
        self, action: rules.EvaluationAction, evaluation: academics.Evaluation
    ) -> bool:
        return rules.can(
            action,
            self.relation(evaluation),
            reviewer=self.reviewer,
            status=evaluation.status,
            voided=evaluation.voided_at is not None,
        )


def _subject(
    subjects: Mapping[uuid.UUID, Any], subject_id: uuid.UUID
) -> EvaluationSubject:
    found = subjects.get(subject_id)
    return EvaluationSubject(id=subject_id, name="" if found is None else found.name)


# --- C: templates ---------------------------------------------------------------------


def _ensure_template_reader(principal: Principal) -> bool:
    """C2 step 3 for the reads: ``403 permission_denied`` unless the caller
    holds ``evaluation.manage`` or the ``tutor`` role. Returns whether the
    caller reads inactive templates too."""
    if rules.is_reviewer(principal):
        return True
    if rules.is_tutor(principal):
        return False
    raise PermissionDeniedError()


def _template_view(
    row: academics.TemplateRow, subjects: Mapping[uuid.UUID, Any]
) -> EvaluationTemplateView:
    template = row.template
    return EvaluationTemplateView(
        id=template.id,
        version=template.version,
        name=template.name,
        kind=cast(EvaluationKind, template.kind),
        subject=(
            None
            if template.subject_id is None
            else _subject(subjects, template.subject_id)
        ),
        description=template.description,
        is_active=template.is_active,
        criteria=[
            EvaluationCriterionView(
                position=c.position,
                title=c.title,
                description=c.description,
                max_score=Decimal(c.max_score),
            )
            for c in row.criteria
        ],
        created_at=template.created_at,
        updated_at=template.updated_at,
    )


async def _template_views(
    session: AsyncSession, rows: Sequence[academics.TemplateRow]
) -> list[EvaluationTemplateView]:
    subjects = await catalog.subject_references(
        session,
        {r.template.subject_id for r in rows if r.template.subject_id is not None},
    )
    return [_template_view(row, subjects) for row in rows]


async def list_templates(
    session: AsyncSession,
    principal: Principal,
    *,
    status: str,
    kind: str | None,
    subject_id: uuid.UUID | None,
    q: str | None,
) -> EvaluationTemplateList:
    """``GET /api/v1/evaluation-templates`` (C3): at most 3 statements."""
    sees_inactive = _ensure_template_reader(principal)
    if not sees_inactive:
        status = "active"
    is_active = {"active": True, "inactive": False}.get(status)
    rows = await academics.list_templates(
        session,
        is_active=is_active,
        kind=kind,
        subject_id=subject_id,
        q=rules.clean(q),
        limit=rules.TEMPLATES_MAX,
    )
    return EvaluationTemplateList(items=await _template_views(session, rows))


async def get_template(
    session: AsyncSession, principal: Principal, template_id: uuid.UUID
) -> EvaluationTemplateView:
    """``GET /api/v1/evaluation-templates/{id}``: ``404`` also for an
    inactive template and a tutor-only caller."""
    sees_inactive = _ensure_template_reader(principal)
    row = await academics.template_with_criteria(session, template_id)
    if row is None or not (sees_inactive or row.template.is_active):
        raise RecordNotFoundError()
    [view] = await _template_views(session, [row])
    return view


def _criteria_values(
    body: EvaluationTemplateCreate,
) -> list[academics.CriterionValues]:
    return [
        academics.CriterionValues(
            title=c.title, description=c.description, max_score=c.max_score
        )
        for c in body.criteria
    ]


async def _ensure_active_subject(session: AsyncSession, subject_id: uuid.UUID) -> None:
    """``422 subject_unknown`` for an unknown or inactive subject (or one
    under an inactive level); the subject stays ``FOR SHARE``."""
    subject = await catalog.get_subject_for_reference(session, subject_id, lock=True)
    if (
        subject is None
        or not subject.is_active
        or not subject.education_level_is_active
    ):
        raise _invalid("subject_id", "subject_unknown", "Choose an active subject.")


async def create_template(
    session: AsyncSession, *, body: EvaluationTemplateCreate
) -> EvaluationTemplateView:
    """``POST /api/v1/evaluation-templates`` (C1, ``evaluation.manage``)."""
    if body.subject_id is not None:
        await _ensure_active_subject(session, body.subject_id)
    row = await academics.create_template(
        session,
        name=body.name,
        kind=body.kind,
        subject_id=body.subject_id,
        description=body.description,
        criteria=_criteria_values(body),
    )
    [view] = await _template_views(session, [row])
    return view


async def replace_template(
    session: AsyncSession, *, template_id: uuid.UUID, body: EvaluationTemplateReplace
) -> EvaluationTemplateView:
    """``PUT /api/v1/evaluation-templates/{id}`` (C1, C2). Existing
    evaluations never change (C4)."""
    stored = await academics.template_with_criteria(session, template_id, lock="update")
    if stored is None:
        raise RecordNotFoundError()
    ensure_version(stored.template, body.version)
    if body.kind != stored.template.kind:
        raise _invalid("kind", "kind_immutable", "A template's kind never changes.")
    if body.subject_id is not None and body.subject_id != stored.template.subject_id:
        await _ensure_active_subject(session, body.subject_id)
    row = await academics.replace_template(
        session,
        stored,
        name=body.name,
        subject_id=body.subject_id,
        description=body.description,
        is_active=body.is_active,
        criteria=_criteria_values(body),
    )
    [view] = await _template_views(session, [row])
    return view


# --- D8: the working view -------------------------------------------------------------


async def _views(
    session: AsyncSession,
    caller: _Caller,
    rows: Sequence[academics.WorkingRow],
    *,
    students: Mapping[uuid.UUID, str] | None = None,
) -> list[EvaluationView]:
    """The views of ``rows``: the scores, the students (unless given), the
    people, the subjects and the cohorts in at most five statements whatever
    the number of evaluations."""
    if not rows:
        return []
    evaluations = [row.evaluation for row in rows]
    scores = await academics.scores_of(session, [e.id for e in evaluations])
    if students is None:
        refs = await families.student_references(
            session, {e.student_id for e in evaluations}
        )
        students = {key: ref.display_name for key, ref in refs.items()}
    people = await identity.get_account_references(
        session,
        {e.author_user_id for e in evaluations}
        | {e.voided_by_user_id for e in evaluations if e.voided_by_user_id},
    )
    subjects = await catalog.subject_references(
        session, {e.subject_id for e in evaluations}
    )
    cohorts = await courses.cohort_names(
        session, {e.cohort_id for e in evaluations if e.cohort_id is not None}
    )

    def person(user_id: uuid.UUID) -> EvaluationPerson:
        account = people.get(user_id)
        return EvaluationPerson(
            display_name="" if account is None else account.display_name
        )

    views = []
    for row in rows:
        e = row.evaluation
        voided = None
        if e.voided_at is not None:
            assert e.voided_by_user_id is not None  # noqa: S101 (CHECK)
            voided = EvaluationVoided(
                at=e.voided_at,
                by=person(e.voided_by_user_id),
                reason=e.void_reason or "",
            )
        course = None
        if e.cohort_id is not None:
            assert e.course_assessment_id is not None  # noqa: S101 (CHECK)
            course = EvaluationCourse(
                cohort_id=e.cohort_id,
                cohort_name=cohorts.get(e.cohort_id, ""),
                assessment_id=e.course_assessment_id,
                assessment_title=e.course_assessment_title or "",
            )
        final = e.status == rules.FINAL
        views.append(
            EvaluationView(
                id=e.id,
                version=e.version,
                status=cast(EvaluationStatus, e.status),
                student=EvaluationStudent(
                    id=e.student_id, display_name=students.get(e.student_id, "")
                ),
                subject=_subject(subjects, e.subject_id),
                author=person(e.author_user_id),
                is_own=e.author_user_id == caller.principal.user_id,
                template=EvaluationTemplateRef(
                    id=e.template_id, name=e.template_name, version=e.template_version
                ),
                kind=cast(EvaluationKind, e.kind),
                evaluated_on=e.evaluated_on,
                class_session=(
                    None
                    if e.class_session_id is None or row.session_starts_at is None
                    else EvaluationSessionRef(
                        id=e.class_session_id,
                        starts_at=row.session_starts_at.astimezone(UTC),
                    )
                ),
                course=course,
                scores=[
                    EvaluationScoreView(
                        position=s.position,
                        title=s.criterion_title,
                        description=s.criterion_description,
                        max_score=Decimal(s.max_score),
                        score=None if s.score is None else Decimal(s.score),
                        comment=s.comment,
                    )
                    for s in scores[e.id]
                ],
                feedback=e.feedback,
                score_total=e.score_total,
                max_total=e.max_total,
                score_percent=e.score_percent,
                finalized_at=e.finalized_at,
                amended_at=e.amended_at,
                last_change_reason=e.last_change_reason,
                voided=voided,
                created_at=e.created_at,
                can_edit=not final and caller.can("edit", e),
                can_finalize=caller.can("finalize", e),
                can_amend=final and caller.can("edit", e),
                can_delete=caller.can("delete", e),
                can_void=caller.can("void", e),
            )
        )
    return views


async def _view_of(
    session: AsyncSession, caller: _Caller, evaluation: academics.Evaluation
) -> EvaluationView:
    starts_at = None
    if evaluation.class_session_id is not None:
        context = await scheduling.evaluation_session(
            session, evaluation.class_session_id
        )
        starts_at = None if context is None else context.starts_at
    [view] = await _views(
        session, caller, [academics.WorkingRow(evaluation, starts_at)]
    )
    return view


# --- D1, D2: create -------------------------------------------------------------------


def _score_errors(
    written: Sequence[EvaluationScoreInput], max_scores: Mapping[int, Decimal]
) -> list[Any]:
    """D2 step 10 / D4: positions that exist and appear once, scores within
    ``0..max_score``."""
    errors: list[Any] = []
    seen: set[int] = set()
    for index, item in enumerate(written):
        path: list[str | int] = ["body", "scores", index]
        if item.position not in max_scores:
            errors.append(
                _field(
                    [*path, "position"],
                    "criterion_unknown",
                    "The template has no criterion at this position.",
                )
            )
            continue
        if item.position in seen:
            errors.append(
                _field(
                    [*path, "position"],
                    "duplicate_criterion",
                    "This criterion is scored twice.",
                )
            )
            continue
        seen.add(item.position)
        if item.score is not None and not (
            Decimal(0) <= item.score <= max_scores[item.position]
        ):
            errors.append(
                _field(
                    [*path, "score"],
                    "score_out_of_range",
                    "The score is between 0 and the criterion's maximum.",
                )
            )
    return errors


def _score_inputs(
    written: Sequence[EvaluationScoreInput],
) -> list[academics.ScoreInput]:
    return [
        academics.ScoreInput(
            position=item.position,
            score=item.score,
            score_set="score" in item.model_fields_set,
            comment=item.comment,
            comment_set="comment" in item.model_fields_set,
        )
        for item in written
    ]


def _date_errors(evaluated_on: date, today: date) -> list[Any]:
    if evaluated_on > today:
        return [
            _field(
                ["body", "evaluated_on"],
                "evaluated_on_in_future",
                "The date can't be in the future.",
            )
        ]
    if evaluated_on < rules.EARLIEST_EVALUATED_ON:
        return [
            _field(
                ["body", "evaluated_on"],
                "evaluated_on_invalid",
                "The date is before 2000-01-01.",
            )
        ]
    return []


async def create_evaluation(
    session: AsyncSession, *, principal: Principal, body: EvaluationCreate
) -> EvaluationView:
    """``POST /api/v1/evaluations`` (D2): ``404`` (an unknown student, or a
    caller who is neither a tutor with the student assigned nor a reviewer)
    → ``403 evaluation_tutor_only`` → the template ``FOR SHARE`` (``422
    template_unknown`` / ``409 template_inactive``) → the subject (``422
    subject_mismatch`` / ``subject_required`` / ``subject_unknown``) → the
    session ``FOR SHARE`` (``422 class_session_invalid``, ``409
    session_not_evaluable``, ``422 subject_mismatch``) → the course link
    (``422 course_assessment_requires_course_session`` /
    ``course_assessment_unknown``) → the date (``422
    evaluated_on_in_future`` / ``evaluated_on_invalid``) → the scores (``422
    criterion_unknown`` / ``duplicate_criterion`` / ``score_out_of_range``)
    → the draft. Nobody is notified."""
    refs = await families.student_references(session, [body.student_id])
    if body.student_id not in refs:
        raise RecordNotFoundError()
    assigned = await _assigned(session, principal)
    qualifies = body.student_id in assigned
    if not qualifies and not rules.is_reviewer(principal):
        raise RecordNotFoundError()
    if not qualifies:
        raise academics.EvaluationTutorOnlyError()
    template = await academics.template_with_criteria(
        session, body.template_id, lock="share"
    )
    if template is None:
        raise _invalid(
            "template_id", "template_unknown", "No such evaluation template."
        )
    if not template.template.is_active:
        raise academics.EvaluationTemplateInactiveError()
    subject_id = body.subject_id
    if template.template.subject_id is not None:
        if subject_id is not None and subject_id != template.template.subject_id:
            raise _invalid(
                "subject_id",
                "subject_mismatch",
                "The subject differs from the template's.",
            )
        subject_id = template.template.subject_id
    else:
        if subject_id is None:
            raise _invalid("subject_id", "subject_required", "Choose a subject.")
        await _ensure_active_subject(session, subject_id)

    zone = await administration.get_center_time_zone(session)
    context = None
    if body.class_session_id is not None:
        context = await scheduling.evaluation_session(
            session, body.class_session_id, share=True
        )
        if (
            context is None
            or context.tutor_user_id != principal.user_id
            or body.student_id not in context.participant_ids
        ):
            raise _invalid(
                "class_session_id",
                "class_session_invalid",
                "Not one of your classes with this student.",
            )
        if not rules.is_evaluable(context.state, context.starts_at, clock.now()):
            raise academics.SessionNotEvaluableError(context.state)
        if context.subject_id != subject_id:
            raise _invalid(
                "subject_id",
                "subject_mismatch",
                "The subject differs from the class's.",
            )
    assessment = None
    if body.course_assessment_id is not None:
        if (
            context is None
            or context.kind != rules.COURSE_KIND
            or context.cohort_id is None
        ):
            raise _invalid(
                "course_assessment_id",
                "course_assessment_requires_course_session",
                "Link a course class to choose an assessment.",
            )
        assessment = await courses.cohort_assessment(
            session, context.cohort_id, body.course_assessment_id
        )
        if assessment is None:
            raise _invalid(
                "course_assessment_id",
                "course_assessment_unknown",
                "The course has no such assessment.",
            )
    today = clock.today_in(zone)
    evaluated_on = body.evaluated_on
    if evaluated_on is None:
        evaluated_on = (
            today
            if context is None
            else context.starts_at.astimezone(ZoneInfo(zone)).date()
        )
    max_scores = {c.position: Decimal(c.max_score) for c in template.criteria}
    errors = [
        *_date_errors(evaluated_on, today),
        *_score_errors(body.scores, max_scores),
    ]
    if errors:
        raise academics.EvaluationFieldError(errors)
    evaluation, _ = await academics.create_evaluation(
        session,
        student_id=body.student_id,
        subject_id=subject_id,
        author_user_id=principal.user_id,
        template_id=template.template.id,
        template_version=template.template.version,
        template_name=template.template.name,
        kind=template.template.kind,
        criteria=[
            academics.CopiedCriterion(
                position=c.position,
                title=c.title,
                description=c.description,
                max_score=Decimal(c.max_score),
            )
            for c in template.criteria
        ],
        evaluated_on=evaluated_on,
        class_session_id=None if context is None else context.id,
        cohort_id=None if assessment is None or context is None else context.cohort_id,
        course_assessment_id=None if assessment is None else assessment.id,
        course_assessment_title=None if assessment is None else assessment.title,
        scores={item.position: item for item in _score_inputs(body.scores)},
        feedback=body.feedback,
    )
    return await _view_of(session, _Caller(principal, assigned), evaluation)


# --- D3-D7: reads and writes on one evaluation ----------------------------------------


async def _readable(
    session: AsyncSession, principal: Principal, evaluation_id: uuid.UUID
) -> tuple[_Caller, academics.Evaluation]:
    evaluation = await academics.evaluation_by_id(session, evaluation_id)
    if evaluation is None:
        raise RecordNotFoundError()
    caller = _Caller(principal, await _assigned(session, principal))
    if not caller.can_read(evaluation):
        raise RecordNotFoundError()
    return caller, evaluation


async def get_evaluation(
    session: AsyncSession, principal: Principal, evaluation_id: uuid.UUID
) -> EvaluationView:
    """``GET /api/v1/evaluations/{id}``."""
    caller, evaluation = await _readable(session, principal, evaluation_id)
    return await _view_of(session, caller, evaluation)


_WRITE_ERRORS: Final = {
    rules.EVALUATION_AUTHOR_ONLY: academics.EvaluationAuthorOnlyError,
    rules.EVALUATION_READ_ONLY: academics.EvaluationReadOnlyError,
    rules.PERMISSION_DENIED: PermissionDeniedError,
}
_STATE_ERRORS: Final = {
    rules.EVALUATION_FINAL: academics.EvaluationFinalError,
    rules.EVALUATION_NOT_DRAFT: academics.EvaluationNotDraftError,
    rules.EVALUATION_NOT_FINAL: academics.EvaluationNotFinalError,
    rules.EVALUATION_VOIDED: academics.EvaluationVoidedError,
}


async def _locked_for(
    session: AsyncSession,
    principal: Principal,
    evaluation_id: uuid.UUID,
    action: rules.EvaluationAction,
    version: int,
) -> tuple[_Caller, academics.Evaluation]:
    """D3 steps 3-7."""
    caller, evaluation = await _readable(session, principal, evaluation_id)
    block = rules.write_block(
        action, caller.relation(evaluation), reviewer=caller.reviewer
    )
    if block is not None:
        raise _WRITE_ERRORS[block]()
    locked = await academics.evaluation_for_update(session, evaluation_id)
    if locked is None:
        raise RecordNotFoundError()
    ensure_version(locked, version)
    state = rules.state_block(
        action, status=locked.status, voided=locked.voided_at is not None
    )
    if state is not None:
        raise _STATE_ERRORS[state]()
    return caller, locked


async def edit_evaluation(
    session: AsyncSession,
    *,
    principal: Principal,
    evaluation_id: uuid.UUID,
    body: EvaluationPatch,
) -> EvaluationView:
    """``PATCH /api/v1/evaluations/{id}`` (D4): a draft changes freely; a
    final one needs a change reason and keeps every criterion scored (the
    totals are recomputed). A request that changes nothing writes nothing."""
    caller, evaluation = await _locked_for(
        session, principal, evaluation_id, "edit", body.version
    )
    final = evaluation.status == rules.FINAL
    rows = (await academics.scores_of(session, [evaluation.id]))[evaluation.id]
    max_scores = {row.position: Decimal(row.max_score) for row in rows}
    errors: list[Any] = []
    reason = body.change_reason
    if final and (reason is None or len(reason) > rules.REASON_MAX):
        errors.append(
            _field(
                ["body", "change_reason"],
                "change_reason_required",
                "Say why you amend it (1-500 characters).",
            )
        )
    if body.evaluated_on is not None:
        errors += _date_errors(
            body.evaluated_on, await administration.center_today(session)
        )
    errors += _score_errors(body.scores, max_scores)
    if final and not errors:
        cleared = {
            item.position
            for item in body.scores
            if "score" in item.model_fields_set and item.score is None
        }
        for index, item in enumerate(body.scores):
            if item.position in cleared:
                errors.append(
                    _field(
                        ["body", "scores", index, "score"],
                        "score_required",
                        "A final evaluation keeps a score for every criterion.",
                    )
                )
    if errors:
        raise academics.EvaluationFieldError(errors)
    await academics.change_evaluation(
        session,
        evaluation,
        rows,
        evaluated_on=body.evaluated_on,
        scores=_score_inputs(body.scores),
        feedback=body.feedback,
        feedback_set="feedback" in body.model_fields_set,
        amend_reason=reason if final else None,
        now=clock.now(),
    )
    return await _view_of(session, caller, evaluation)


async def finalize_evaluation(
    session: AsyncSession,
    *,
    principal: Principal,
    evaluation_id: uuid.UUID,
    version: int,
) -> EvaluationView:
    """``POST /api/v1/evaluations/{id}/finalize`` (D5): every criterion
    scored (``409 evaluation_incomplete``); the student and the guardians
    with academic access are notified."""
    caller, evaluation = await _locked_for(
        session, principal, evaluation_id, "finalize", version
    )
    rows = (await academics.scores_of(session, [evaluation.id]))[evaluation.id]
    missing = [row.position for row in rows if row.score is None]
    if missing:
        raise academics.EvaluationIncompleteError(missing)
    await academics.finalize_evaluation(session, evaluation, rows, now=clock.now())
    recipients = await families.academic_notification_recipients(
        session, evaluation.student_id, today=await administration.center_today(session)
    )
    params = EvaluationFinalizedParams(
        student_id=str(evaluation.student_id),
        evaluation_id=str(evaluation.id),
        subject_id=str(evaluation.subject_id),
        evaluated_on=evaluation.evaluated_on.isoformat(),
    )
    for user_id in recipients:
        await administration.create_notification(
            session,
            recipient_user_id=user_id,
            notification_type=FINALIZED_NOTIFICATION,
            params=params,
        )
    return await _view_of(session, caller, evaluation)


async def void_evaluation(
    session: AsyncSession,
    *,
    principal: Principal,
    evaluation_id: uuid.UUID,
    body: EvaluationVoid,
) -> EvaluationView:
    """``POST /api/v1/evaluations/{id}/void`` (D6, ``evaluation.manage``):
    it leaves every family and history read; the author is notified."""
    caller, evaluation = await _locked_for(
        session, principal, evaluation_id, "void", body.version
    )
    if body.reason is None or len(body.reason) > rules.REASON_MAX:
        raise _invalid(
            "reason", "reason_required", "Say why it is voided (1-500 characters)."
        )
    await academics.void_evaluation(
        session,
        evaluation,
        actor_id=principal.user_id,
        reason=body.reason,
        now=clock.now(),
    )
    if evaluation.author_user_id != principal.user_id:
        await administration.create_notification(
            session,
            recipient_user_id=evaluation.author_user_id,
            notification_type=VOIDED_NOTIFICATION,
            params=EvaluationVoidedParams(
                student_id=str(evaluation.student_id),
                evaluation_id=str(evaluation.id),
            ),
        )
    return await _view_of(session, caller, evaluation)


async def delete_evaluation(
    session: AsyncSession,
    *,
    principal: Principal,
    evaluation_id: uuid.UUID,
    version: int,
) -> None:
    """``DELETE /api/v1/evaluations/{id}?version=`` (D7): a draft only;
    every route then answers ``404`` for it."""
    _, evaluation = await _locked_for(
        session, principal, evaluation_id, "delete", version
    )
    await academics.soft_delete_evaluation(
        session, evaluation, actor_id=principal.user_id, now=clock.now()
    )


# --- E: the working list --------------------------------------------------------------


async def list_student_evaluations(
    session: AsyncSession,
    principal: Principal,
    student_id: uuid.UUID,
    *,
    status: str | None,
    subject_id: uuid.UUID | None,
    class_session_id: uuid.UUID | None,
    cursor: str | None,
    limit: int,
) -> CursorPage[EvaluationView]:
    """``GET /api/v1/students/{id}/evaluations`` (E1, E2): reviewers read
    every final evaluation (voided too); tutors with the student assigned
    every final, non-voided one; an author also their own in any state (a
    former author only theirs, read-only); anyone else, and an unknown id,
    the same ``404``. At most 8 statements."""
    reviewer = rules.is_reviewer(principal)
    tutor = rules.is_tutor(principal)
    if not reviewer and not tutor:
        raise RecordNotFoundError()
    refs = await families.student_references(session, [student_id])
    if student_id not in refs:
        raise RecordNotFoundError()
    assigned = await _assigned(session, principal)
    student_assigned = student_id in assigned
    if (
        not reviewer
        and not student_assigned
        and not await academics.has_own_evaluation(
            session, student_id, principal.user_id
        )
    ):
        raise RecordNotFoundError()
    page = await academics.working_page(
        session,
        student_id,
        sessions=scheduling.note_session_columns(),
        all_final=reviewer,
        live_final=student_assigned,
        own_user_id=principal.user_id if tutor else None,
        status=status,
        subject_id=subject_id,
        class_session_id=class_session_id,
        cursor=cursor,
        limit=limit,
    )
    items = await _views(
        session,
        _Caller(principal, assigned),
        page.rows,
        students={student_id: refs[student_id].display_name},
    )
    return CursorPage[EvaluationView](items=items, next_cursor=page.next_cursor)


# --- F: the unified history -----------------------------------------------------------


async def _history_scope(
    session: AsyncSession, principal: Principal, student_id: uuid.UUID
) -> uuid.UUID | None:
    """F1: ``None`` (every author) for reviewers, tutors with the student
    assigned and the family; the caller's id for a former author (of an
    evaluation, or the creator of homework the student received, #82 G1);
    ``403 student_consent_required`` / ``404`` otherwise."""
    if rules.is_reviewer(principal):
        if not await families.student_references(session, [student_id]):
            raise RecordNotFoundError()
        return None
    tutor = rules.is_tutor(principal)
    if tutor and student_id in await _assigned(session, principal):
        return None
    try:
        await scheduling.ensure_family_student_access(session, principal, student_id)
        return None
    except RecordNotFoundError:
        if not tutor:
            raise
    if await academics.has_own_evaluation(
        session, student_id, principal.user_id
    ) or await academics.has_own_assignment(session, student_id, principal.user_id):
        return principal.user_id
    raise RecordNotFoundError()


def _entry_date(entry: HistoryItem) -> date:
    return entry.evaluated_on if isinstance(entry, HistoryEntry) else entry.graded_on


def _period(period: Any) -> HistoryPeriod:
    return HistoryPeriod(
        id=period.id,
        name=period.name,
        period_type=period.period_type,
        starts_on=period.starts_on,
        ends_on=period.ends_on,
    )


def _groups(
    entries: Sequence[HistoryItem], periods: Sequence[Any], *, grouped: bool
) -> list[HistoryGroup]:
    """F4: entries by period (the active period of the type holding the
    date; ``None`` last), then subject (name casefold, then id), each with
    the evaluations' count and average percentage, the homework grades'
    count and average percentage (#82 G1: kept apart) and the entry ids of
    both kinds (newest first)."""
    buckets: dict[uuid.UUID | None, dict[uuid.UUID, list[HistoryItem]]] = defaultdict(
        lambda: defaultdict(list)
    )
    by_id = {period.id: period for period in periods}
    for entry in entries:
        period = rules.period_for(_entry_date(entry), periods) if grouped else None
        buckets[None if period is None else period.id][entry.subject.id].append(entry)

    def order(key: uuid.UUID | None) -> tuple[int, int]:
        if key is None:
            return (1, 0)
        return (0, -by_id[key].starts_on.toordinal())

    groups = []
    for key in sorted(buckets, key=order):
        subjects = []
        for items in buckets[key].values():
            evaluations = [i for i in items if isinstance(i, HistoryEntry)]
            grades = [i for i in items if isinstance(i, AssignmentGradeEntry)]
            subjects.append(
                HistorySubjectGroup(
                    subject=items[0].subject,
                    count=len(evaluations),
                    average_percent=rules.average_percent(
                        [item.score_percent for item in evaluations]
                    ),
                    assignment_count=len(grades),
                    assignment_average_percent=rules.average_percent(
                        [item.score_percent for item in grades]
                    ),
                    entry_ids=[item.id for item in items],
                )
            )
        subjects.sort(key=lambda g: (g.subject.name.casefold(), str(g.subject.id)))
        groups.append(
            HistoryGroup(
                period=None if key is None else _period(by_id[key]), subjects=subjects
            )
        )
    return groups


async def academic_history(
    session: AsyncSession,
    principal: Principal,
    student_id: uuid.UUID,
    *,
    group_by: GroupBy,
    subject_id: uuid.UUID | None,
    from_: date | None,
    to: date | None,
) -> AcademicHistory:
    """``GET /api/v1/students/{id}/academic-history`` (F1-F4, #82 G1): the
    one family-safe read of evaluations and homework grades. At most 10
    statements whatever the number of entries, criteria or periods."""
    if from_ is not None and to is not None and from_ > to:
        raise academics.EvaluationFieldError(
            [(["query", "from"], "range_invalid", "'from' is after 'to'.")]
        )
    author = await _history_scope(session, principal, student_id)
    subjects = catalog.subject_name_columns()
    found = await academics.history_rows(
        session,
        student_id,
        subjects=subjects,
        author_user_id=author,
        subject_id=subject_id,
        from_=from_,
        to=to,
        limit=rules.HISTORY_MAX_ENTRIES + 1,
    )
    grades = await academics.history_grades(
        session,
        student_id,
        subjects=subjects,
        creator_user_id=author,
        subject_id=subject_id,
        from_=from_,
        to=to,
        limit=rules.HISTORY_MAX_ENTRIES + 1,
    )
    authors = await identity.get_account_references(
        session,
        {e.author_user_id for e, _ in found}
        | {g.assignment.creator_user_id for g in grades},
    )
    merged: list[HistoryItem] = [
        *await _entries(session, found, authors),
        *await _grade_entries(session, grades, authors),
    ]
    merged.sort(key=_history_order, reverse=True)
    truncated = len(merged) > rules.HISTORY_MAX_ENTRIES
    entries = merged[: rules.HISTORY_MAX_ENTRIES]
    periods: list[Any] = []
    if group_by != "none" and entries:
        dates = [_entry_date(e) for e in entries]
        periods = await catalog.periods_overlapping(
            session, group_by, min(dates), max(dates)
        )
    return AcademicHistory(
        student_id=student_id,
        group_by=group_by,
        truncated=truncated,
        entries=entries,
        groups=(
            _groups(entries, periods, grouped=group_by != "none") if entries else []
        ),
    )


def _history_order(entry: HistoryItem) -> tuple[date, datetime, str]:
    """F2 + G1 (sorted in reverse): the date (``evaluated_on`` /
    ``graded_on``), then the instant (``finalized_at`` / ``graded_at``),
    then the id."""
    instant = entry.finalized_at if isinstance(entry, HistoryEntry) else entry.graded_at
    return (_entry_date(entry), instant, str(entry.id))


async def _grade_entries(
    session: AsyncSession,
    grades: Sequence[academics.GradeRow],
    authors: Mapping[uuid.UUID, Any],
) -> list[AssignmentGradeEntry]:
    """G1: the closed homework grade entries; the zone (for ``is_late``) in
    at most one statement (cached for the Unit of Work)."""
    if not grades:
        return []
    zone = await administration.get_center_time_zone(session)
    entries = []
    for grade in grades:
        submission, assignment = grade.submission, grade.assignment
        assert submission.graded_on is not None  # noqa: S101 (graded rows only)
        assert submission.graded_at is not None  # noqa: S101
        assert submission.score is not None  # noqa: S101 (scored rows only)
        assert submission.score_percent is not None  # noqa: S101
        assert assignment.max_score is not None  # noqa: S101 (a score needs it)
        author = authors.get(assignment.creator_user_id)
        deadline = assignment_rules.deadline_instant(
            assignment.due_on, assignment.due_time, zone
        )
        entries.append(
            AssignmentGradeEntry(
                kind="assignment_grade",
                id=submission.id,
                graded_on=submission.graded_on,
                subject=EvaluationSubject(
                    id=assignment.subject_id, name=grade.subject_name
                ),
                title=assignment.title,
                author=EvaluationPerson(
                    display_name="" if author is None else author.display_name
                ),
                max_score=assignment.max_score,
                score=submission.score,
                score_percent=submission.score_percent,
                feedback=submission.feedback,
                due_on=assignment.due_on,
                is_late=assignment_rules.is_late(submission.submitted_at, deadline),
                graded_at=submission.graded_at,
                updated=submission.grade_amended_at is not None,
            )
        )
    return entries


async def _entries(
    session: AsyncSession,
    found: Iterable[tuple[academics.Evaluation, str]],
    authors: Mapping[uuid.UUID, Any],
) -> list[HistoryEntry]:
    """The closed history entries (F3): the scores and the cohorts' names
    in at most two statements (the authors' names are given)."""
    found = list(found)
    if not found:
        return []
    evaluations = [evaluation for evaluation, _ in found]
    scores = await academics.scores_of(session, [e.id for e in evaluations])
    cohorts = await courses.cohort_names(
        session, {e.cohort_id for e in evaluations if e.cohort_id is not None}
    )
    entries = []
    for e, subject_name in found:
        assert e.finalized_at is not None  # noqa: S101 (final rows only)
        assert e.score_total is not None and e.max_total is not None  # noqa: S101
        assert e.score_percent is not None  # noqa: S101
        author = authors.get(e.author_user_id)
        entries.append(
            HistoryEntry(
                kind="evaluation",
                id=e.id,
                evaluated_on=e.evaluated_on,
                subject=EvaluationSubject(id=e.subject_id, name=subject_name),
                evaluation_kind=cast(EvaluationKind, e.kind),
                template_name=e.template_name,
                author=EvaluationPerson(
                    display_name="" if author is None else author.display_name
                ),
                criteria=[
                    HistoryCriterion(
                        title=s.criterion_title,
                        max_score=Decimal(s.max_score),
                        score=Decimal(s.score if s.score is not None else 0),
                        comment=s.comment,
                    )
                    for s in scores[e.id]
                ],
                score_total=e.score_total,
                max_total=e.max_total,
                score_percent=e.score_percent,
                feedback=e.feedback,
                course=(
                    None
                    if e.cohort_id is None
                    else HistoryCourse(
                        cohort_name=cohorts.get(e.cohort_id, ""),
                        assessment_title=e.course_assessment_title or "",
                    )
                ),
                finalized_at=e.finalized_at,
                updated=e.amended_at is not None,
            )
        )
    return entries


# --- AM1: the evaluation context of a class -------------------------------------------


async def evaluation_context(
    session: AsyncSession, principal: Principal, class_session_id: uuid.UUID
) -> EvaluationContext:
    """``GET /api/v1/class-sessions/{id}/evaluation-context`` (AM1): the
    session's current tutor only (anyone else, and an unknown id, the same
    ``404``). At most 4 statements."""
    context = await scheduling.evaluation_session(session, class_session_id)
    if (
        context is None
        or not rules.is_tutor(principal)
        or context.tutor_user_id != principal.user_id
    ):
        raise RecordNotFoundError()
    students = await families.student_references(session, context.participant_ids)
    subjects = await catalog.subject_references(session, [context.subject_id])
    course = None
    if context.kind == rules.COURSE_KIND and context.cohort_id is not None:
        found = await courses.cohort_assessments(session, context.cohort_id)
        if found is not None:
            course = EvaluationContextCourse(
                cohort_id=found.cohort_id,
                cohort_name=found.cohort_name,
                assessments=[
                    EvaluationContextAssessment(
                        id=a.id,
                        title=a.title,
                        is_required=a.is_required,
                        min_score_percent=a.min_score_percent,
                    )
                    for a in found.assessments
                ],
            )
    participants = sorted(
        (
            EvaluationContextParticipant(
                student_id=student_id,
                display_name=students[student_id].display_name
                if student_id in students
                else "",
            )
            for student_id in context.participant_ids
        ),
        key=lambda p: (p.display_name.casefold(), str(p.student_id)),
    )
    return EvaluationContext(
        class_session_id=context.id,
        kind=context.kind,
        state=context.state,
        starts_at=context.starts_at,
        subject=_subject(subjects, context.subject_id),
        evaluable=rules.is_evaluable(context.state, context.starts_at, clock.now()),
        participants=participants,
        course=course,
    )
