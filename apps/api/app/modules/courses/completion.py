"""Course completion rules (#85 M5-18; spec §3 "completion criteria include
configurable attendance, modules and required assessments"). Pure: no
database, no clock, no imports outside ``app.core`` and ``courses``.

The criteria come from the cohort's **frozen snapshot** (#64: never the
live template). The caller (``app.workflows.course_completion``) loads the
facts per student and calls :func:`evaluate_student`; nothing is stored
(E5: a live, read-only computation, no override, no notification).

**Attendance (E3).** *Held* sessions are the cohort's ``completed``
sessions the student takes part in; *attended* are those marked
``present`` or ``late``. The criterion is met iff ``attended × 100 ≥
min_attendance_percent × held`` (integers, never a rounded value) and
``held > 0``. The shown ``attendance_percent`` is ``attended / held ×
100``, half-up to two places (``None`` when nothing was held).

**Assessments (E4).** The counted evaluation of an assessment is the
latest final, non-voided one (chosen by the caller). It is ``passed`` when
``score_percent ≥ min_score_percent`` (or, with no minimum, when it
exists), ``failed`` below it, ``not_evaluated`` without one. The criterion
is met when every **required** assessment is passed; a failed one stays
``pending`` until the course is final (a retake is possible).

**Modules (E2).** A required module with required assessments tied to it
(``module_id``) is met when all of them are passed (basis
``assessments``); one without any is met once the course is final (basis
``course_covered``: the course covered it).

**Overall (E5).** ``in_progress`` until the course is final for the
student (:func:`is_final`: the cohort has ended and none of the student's
started sessions waits for a closeout); then ``completed`` when every
criterion is met or not required, otherwise ``not_completed``.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Final, Literal

from app.modules.courses.cohorts import syllabus_of
from app.modules.courses.models import SNAPSHOT_SCHEMA_VERSION
from app.modules.courses.schemas import CohortStatus

type CompletionStatus = Literal["in_progress", "completed", "not_completed"]
type CriterionStatus = Literal["met", "not_met", "pending", "not_required"]
type RequirementStatus = Literal["met", "not_met", "pending"]
type AssessmentOutcome = Literal["passed", "failed", "not_evaluated"]
type ModuleEvidence = Literal["assessments", "course_covered"]

_HUNDRED: Final = Decimal(100)
_CENTS: Final = Decimal("0.01")
_SATISFIED: Final = frozenset({"met", "not_required"})


class UnsupportedSnapshotSchemaError(RuntimeError):
    """The snapshot's ``schema_version`` is not one these rules know (a
    programming error after a template change: ``500``)."""


@dataclass(frozen=True)
class ModuleCriterion:
    id: uuid.UUID
    title: str
    is_required: bool


@dataclass(frozen=True)
class AssessmentCriterion:
    id: uuid.UUID
    title: str
    module_id: uuid.UUID | None
    is_required: bool
    min_score_percent: int | None


@dataclass(frozen=True)
class Criteria:
    """A snapshot's completion criteria; modules and assessments in
    position order."""

    min_attendance_percent: int | None
    modules: tuple[ModuleCriterion, ...]
    assessments: tuple[AssessmentCriterion, ...]

    @property
    def required_modules(self) -> tuple[ModuleCriterion, ...]:
        return tuple(m for m in self.modules if m.is_required)


def criteria_of(content: Mapping[str, Any], *, schema_version: int) -> Criteria:
    """The :class:`Criteria` of a snapshot (A1). Only schema version 1 is
    known; any other raises :class:`UnsupportedSnapshotSchemaError`."""
    if schema_version != SNAPSHOT_SCHEMA_VERSION:
        raise UnsupportedSnapshotSchemaError(
            f"unknown snapshot schema version {schema_version}"
        )
    syllabus = syllabus_of(content, schema_version=schema_version)
    return Criteria(
        min_attendance_percent=syllabus.completion.min_attendance_percent,
        modules=tuple(
            ModuleCriterion(id=m.id, title=m.title, is_required=m.is_required)
            for m in syllabus.modules
        ),
        assessments=tuple(
            AssessmentCriterion(
                id=a.id,
                title=a.title,
                module_id=a.module_id,
                is_required=a.is_required,
                min_score_percent=a.min_score_percent,
            )
            for a in syllabus.assessments
        ),
    )


@dataclass(frozen=True)
class Participation:
    """A student's cohort sessions (B2): ``held`` completed, of which
    ``attended`` (present or late) and ``absent``; ``awaiting_closeout``
    started but still ``confirmed``; ``remaining`` still to come."""

    held: int = 0
    attended: int = 0
    absent: int = 0
    remaining: int = 0
    awaiting_closeout: int = 0


@dataclass(frozen=True)
class CountedEvaluation:
    """The evaluation that counts for one student and assessment (E4, B3)
    and how many counted evaluations exist (``attempts``)."""

    evaluation_id: uuid.UUID
    score_percent: Decimal
    evaluated_on: date
    attempts: int


@dataclass(frozen=True)
class AttendanceResult:
    status: CriterionStatus
    min_percent: int | None
    held: int
    attended: int
    absent: int
    remaining: int
    awaiting_closeout: int
    attendance_percent: Decimal | None


@dataclass(frozen=True)
class AssessmentResult:
    assessment_id: uuid.UUID
    title: str
    is_required: bool
    min_score_percent: int | None
    status: AssessmentOutcome
    score_percent: Decimal | None
    evaluated_on: date | None
    attempts: int


@dataclass(frozen=True)
class AssessmentsResult:
    status: CriterionStatus
    items: tuple[AssessmentResult, ...]


@dataclass(frozen=True)
class ModuleResult:
    module_id: uuid.UUID
    title: str
    status: RequirementStatus
    basis: ModuleEvidence


@dataclass(frozen=True)
class ModulesResult:
    status: CriterionStatus
    items: tuple[ModuleResult, ...]


@dataclass(frozen=True)
class StudentCompletionResult:
    status: CompletionStatus
    final: bool
    attendance: AttendanceResult
    assessments: AssessmentsResult
    modules: ModulesResult


def is_final(cohort_status: CohortStatus, participation: Participation) -> bool:
    """E5: the cohort has ended and none of the student's started sessions
    waits for its closeout."""
    return cohort_status == "ended" and participation.awaiting_closeout == 0


def attendance_percent(attended: int, held: int) -> Decimal | None:
    """``attended / held × 100``, half-up to two places; ``None`` when
    nothing was held (E3, display only)."""
    if held == 0:
        return None
    return (Decimal(attended) * _HUNDRED / Decimal(held)).quantize(
        _CENTS, rounding=ROUND_HALF_UP
    )


def attendance_met(attended: int, held: int, min_percent: int) -> bool:
    """E3: compared in integers, never a rounded value; nothing held is
    never met."""
    return held > 0 and attended * 100 >= min_percent * held


def _open(*, met: bool, final: bool) -> RequirementStatus:
    if met:
        return "met"
    return "not_met" if final else "pending"


def _attendance(
    min_percent: int | None, facts: Participation, *, final: bool
) -> AttendanceResult:
    status: CriterionStatus
    if min_percent is None:
        status = "not_required"
    elif not final:
        status = "pending"
    else:
        status = (
            "met"
            if attendance_met(facts.attended, facts.held, min_percent)
            else "not_met"
        )
    return AttendanceResult(
        status=status,
        min_percent=min_percent,
        held=facts.held,
        attended=facts.attended,
        absent=facts.absent,
        remaining=facts.remaining,
        awaiting_closeout=facts.awaiting_closeout,
        attendance_percent=attendance_percent(facts.attended, facts.held),
    )


def assessment_outcome(
    minimum: int | None, counted: CountedEvaluation | None
) -> AssessmentOutcome:
    """E4: ``passed`` at or above the minimum (or, without one, as soon as
    a counted evaluation exists)."""
    if counted is None:
        return "not_evaluated"
    if minimum is None or counted.score_percent >= Decimal(minimum):
        return "passed"
    return "failed"


def _assessment(
    criterion: AssessmentCriterion, counted: CountedEvaluation | None
) -> AssessmentResult:
    return AssessmentResult(
        assessment_id=criterion.id,
        title=criterion.title,
        is_required=criterion.is_required,
        min_score_percent=criterion.min_score_percent,
        status=assessment_outcome(criterion.min_score_percent, counted),
        score_percent=None if counted is None else counted.score_percent,
        evaluated_on=None if counted is None else counted.evaluated_on,
        attempts=0 if counted is None else counted.attempts,
    )


def _criterion(required: Sequence[bool], *, final: bool) -> CriterionStatus:
    """``required``: whether each required item is met."""
    if not required:
        return "not_required"
    return _open(met=all(required), final=final)


def evaluate_student(
    criteria: Criteria,
    participation: Participation,
    evaluations: Mapping[uuid.UUID, CountedEvaluation],
    *,
    final: bool,
) -> StudentCompletionResult:
    """A2: one student's completion (``evaluations``: the counted evaluation
    per assessment id). Deterministic; reads no clock."""
    attendance = _attendance(
        criteria.min_attendance_percent, participation, final=final
    )
    items = tuple(_assessment(a, evaluations.get(a.id)) for a in criteria.assessments)
    passed = {item.assessment_id for item in items if item.status == "passed"}
    assessments = AssessmentsResult(
        status=_criterion(
            [item.assessment_id in passed for item in items if item.is_required],
            final=final,
        ),
        items=items,
    )
    modules: list[ModuleResult] = []
    for module in criteria.required_modules:
        evidence = [
            a.id
            for a in criteria.assessments
            if a.is_required and a.module_id == module.id
        ]
        if evidence:
            modules.append(
                ModuleResult(
                    module_id=module.id,
                    title=module.title,
                    status=_open(met=all(i in passed for i in evidence), final=final),
                    basis="assessments",
                )
            )
        else:
            modules.append(
                ModuleResult(
                    module_id=module.id,
                    title=module.title,
                    status="met" if final else "pending",
                    basis="course_covered",
                )
            )
    module_result = ModulesResult(
        status=_criterion([m.status == "met" for m in modules], final=final),
        items=tuple(modules),
    )
    status: CompletionStatus
    if not final:
        status = "in_progress"
    elif {
        attendance.status,
        assessments.status,
        module_result.status,
    } <= _SATISFIED:
        status = "completed"
    else:
        status = "not_completed"
    return StudentCompletionResult(
        status=status,
        final=final,
        attendance=attendance,
        assessments=assessments,
        modules=module_result,
    )
