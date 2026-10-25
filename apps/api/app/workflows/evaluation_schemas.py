"""Evaluation bodies and closed views (#80 C1, D1, D4-D8, F3, AM1).

Every model is a closed allow-list (``extra="forbid"``). Scores, maximums,
totals and percentages are decimals with at most two places: JSON strings
on the way out (``"7.50"``, ADR-0004), strings or numbers on the way in.
Text is plain, trimmed; whitespace-only optional text is ``null``.

The bodies check what needs no stored data (``422`` at step 2 of every
order); the checks against the template, the session or the evaluation's
state run in ``app.workflows.evaluations``.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    WithJsonSchema,
    field_validator,
)
from pydantic_core import PydanticCustomError

from app.modules.academics.service import evaluation_rules as rules

type EvaluationKind = Literal["diagnostic", "ongoing"]
type EvaluationStatus = Literal["draft", "final"]
type GroupBy = Literal["none", "school_year", "semester", "term"]
type PeriodType = Literal["school_year", "semester", "term"]

_DECIMAL_SCHEMA = WithJsonSchema(
    {"type": "string", "format": "decimal", "examples": ["7.50"]}
)


def _decimal(value: Any) -> Any:
    """A JSON string or number as a finite ``Decimal`` with at most two
    places (``score_precision``)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float | str):
        try:
            parsed = Decimal(str(value).strip())
        except InvalidOperation:
            raise PydanticCustomError(
                "decimal_invalid", "Write a number such as 7.5."
            ) from None
        if not parsed.is_finite():
            raise PydanticCustomError("decimal_invalid", "Write a number such as 7.5.")
        if not rules.has_cents_only(parsed):
            raise PydanticCustomError("score_precision", "At most two decimal places.")
        return rules.to_cents(parsed)
    return value


type DecimalIn = Annotated[Decimal, BeforeValidator(_decimal), _DECIMAL_SCHEMA]
type DecimalOut = Annotated[
    Decimal,
    PlainSerializer(lambda value: f"{value:.2f}", return_type=str),
    _DECIMAL_SCHEMA,
]


def _text(maximum: int, *, required: str | None = None) -> Any:
    def validate(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        cleaned = rules.clean(value)
        if cleaned is None:
            if required is not None:
                raise PydanticCustomError(required, "This field is required.")
            return None
        if len(cleaned) > maximum:
            raise PydanticCustomError(
                "too_long", "At most {max} characters.", {"max": maximum}
            )
        return cleaned

    return BeforeValidator(validate)


def _trim_only(value: Any) -> Any:
    return rules.clean(value) if isinstance(value, str) else value


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- Templates (C1) -------------------------------------------------------------------


class EvaluationCriterionInput(_Closed):
    title: Annotated[
        str, _text(rules.CRITERION_TITLE_MAX, required="title_required")
    ] = Field(description="1-120 characters, unique in the template ignoring case.")
    description: Annotated[str | None, _text(rules.CRITERION_DESCRIPTION_MAX)] = None
    max_score: DecimalIn = Field(
        description="0.5-1000, at most two decimal places"
        " (max_score_out_of_range / score_precision)."
    )

    @field_validator("max_score")
    @classmethod
    def _in_range(cls, value: Decimal) -> Decimal:
        if not rules.MAX_SCORE_MIN <= value <= rules.MAX_SCORE_MAX:
            raise PydanticCustomError(
                "max_score_out_of_range", "The maximum score is 0.5 to 1000."
            )
        return value


def _criteria(value: list[EvaluationCriterionInput]) -> list[EvaluationCriterionInput]:
    if not value:
        raise PydanticCustomError("criteria_required", "Add at least one criterion.")
    if len(value) > rules.CRITERIA_MAX:
        raise PydanticCustomError(
            "criteria_too_many",
            "At most {max} criteria.",
            {"max": rules.CRITERIA_MAX},
        )
    seen: set[str] = set()
    for criterion in value:
        key = criterion.title.casefold()
        if key in seen:
            raise PydanticCustomError(
                "duplicate_title", "Two criteria have the same title."
            )
        seen.add(key)
    return value


class EvaluationTemplateCreate(_Closed):
    """`POST /api/v1/evaluation-templates` (#80 C1): a new active template.
    Its criteria are copied onto each evaluation made from it, so later
    edits never change existing evaluations."""

    name: Annotated[str, _text(rules.TEMPLATE_NAME_MAX, required="name_required")] = (
        Field(description="1-120 characters, unique ignoring case (duplicate_name).")
    )
    kind: EvaluationKind = Field(description="Never changes after the create.")
    subject_id: uuid.UUID | None = Field(
        default=None, description="An active subject; null means any subject."
    )
    description: Annotated[str | None, _text(rules.TEMPLATE_DESCRIPTION_MAX)] = None
    criteria: list[EvaluationCriterionInput] = Field(
        description="1-20 criteria, in order (criteria_required /"
        " criteria_too_many / duplicate_title)."
    )

    _check_criteria = field_validator("criteria")(_criteria)


class EvaluationTemplateReplace(EvaluationTemplateCreate):
    """`PUT /api/v1/evaluation-templates/{id}` (#80 C1): the whole template,
    with the version you loaded. `kind` must stay the same
    (kind_immutable)."""

    version: int = Field(description="The template's version, as last read.")
    is_active: bool = Field(description="False deactivates the template.")


class EvaluationSubject(_Closed):
    id: uuid.UUID
    name: str


class EvaluationCriterionView(_Closed):
    position: int
    title: str
    description: str | None
    max_score: DecimalOut


class EvaluationTemplateView(_Closed):
    """An evaluation template with its criteria (#80 C1)."""

    id: uuid.UUID
    version: int
    name: str
    kind: EvaluationKind
    subject: EvaluationSubject | None = Field(description="Null: any subject.")
    description: str | None
    is_active: bool
    criteria: list[EvaluationCriterionView]
    created_at: datetime
    updated_at: datetime


class EvaluationTemplateList(_Closed):
    """`GET /api/v1/evaluation-templates` (#80 C3): by name, at most 200."""

    items: list[EvaluationTemplateView]


# --- Evaluations (D1, D4-D6) ----------------------------------------------------------


class EvaluationScoreInput(_Closed):
    """One criterion's score and comment. A field left out keeps its value;
    `null` clears it."""

    position: int = Field(description="The criterion's position (1-based).")
    score: DecimalIn | None = Field(
        default=None,
        description="0 to the criterion's max_score, at most two decimal places.",
    )
    comment: Annotated[str | None, _text(rules.COMMENT_MAX)] = Field(
        default=None, description="Plain text, at most 1000 characters."
    )


type Feedback = Annotated[str | None, _text(rules.FEEDBACK_MAX)]


class EvaluationCreate(_Closed):
    """`POST /api/v1/evaluations` (#80 D1): a draft from a template, for a
    student you currently teach. Nobody is notified until you finalize
    it."""

    student_id: uuid.UUID
    template_id: uuid.UUID = Field(description="An active evaluation template.")
    subject_id: uuid.UUID | None = Field(
        default=None,
        description="Defaults to the template's subject; required for an"
        " any-subject template.",
    )
    evaluated_on: date | None = Field(
        default=None,
        description="A local date, not in the future; defaults to the linked"
        " class's date, else today.",
    )
    class_session_id: uuid.UUID | None = Field(
        default=None, description="Optional: a started class of yours with the student."
    )
    course_assessment_id: uuid.UUID | None = Field(
        default=None,
        description="Optional, only with a course class: an assessment of the"
        " cohort's syllabus.",
    )
    scores: list[EvaluationScoreInput] = Field(default_factory=list)
    feedback: Feedback = Field(
        default=None, description="Plain text, at most 4000 characters."
    )


class EvaluationPatch(_Closed):
    """`PATCH /api/v1/evaluations/{id}` (#80 D4): change a draft, or amend a
    final evaluation with a change reason (internal: families never see
    it). A field left out keeps its value."""

    version: int = Field(description="The evaluation's version, as last read.")
    evaluated_on: date | None = None
    scores: list[EvaluationScoreInput] = Field(default_factory=list)
    feedback: Feedback = Field(
        default=None, description="Plain text, at most 4000 characters; null clears it."
    )
    change_reason: Annotated[str | None, BeforeValidator(_trim_only)] = Field(
        default=None,
        description="Required to amend a final evaluation: 1-500 characters"
        " (change_reason_required). Ignored on a draft.",
    )


class EvaluationVersion(_Closed):
    """`POST /api/v1/evaluations/{id}/finalize` (#80 D5)."""

    version: int = Field(description="The evaluation's version, as last read.")


class EvaluationVoid(_Closed):
    """`POST /api/v1/evaluations/{id}/void` (#80 D6): why (1-500 characters,
    reason_required). The author is notified; families stop seeing it."""

    version: int = Field(description="The evaluation's version, as last read.")
    reason: Annotated[str | None, BeforeValidator(_trim_only)] = Field(
        description="Why it is voided: 1-500 characters, trimmed (reason_required)."
    )


# --- The working view (D8) ------------------------------------------------------------


class EvaluationPerson(_Closed):
    display_name: str


class EvaluationStudent(_Closed):
    id: uuid.UUID
    display_name: str


class EvaluationTemplateRef(_Closed):
    id: uuid.UUID
    name: str = Field(description="As it was when the evaluation was created.")
    version: int


class EvaluationSessionRef(_Closed):
    id: uuid.UUID
    starts_at: datetime


class EvaluationCourse(_Closed):
    cohort_id: uuid.UUID
    cohort_name: str
    assessment_id: uuid.UUID
    assessment_title: str = Field(
        description="As it was when the evaluation was created."
    )


class EvaluationScoreView(_Closed):
    position: int
    title: str
    description: str | None
    max_score: DecimalOut
    score: DecimalOut | None
    comment: str | None


class EvaluationVoided(_Closed):
    at: datetime
    by: EvaluationPerson
    reason: str


class EvaluationView(_Closed):
    """An evaluation, for its author, the tutors currently teaching the
    student and staff with evaluation.manage (#80 D8). Its criteria were
    copied from the template when it was created. The `can_*` flags say
    what you may do now."""

    id: uuid.UUID
    version: int = Field(description="The evaluation's version (send it back).")
    status: EvaluationStatus
    student: EvaluationStudent
    subject: EvaluationSubject
    author: EvaluationPerson
    is_own: bool = Field(description="You wrote it.")
    template: EvaluationTemplateRef
    kind: EvaluationKind
    evaluated_on: date
    class_session: EvaluationSessionRef | None
    course: EvaluationCourse | None
    scores: list[EvaluationScoreView]
    feedback: str | None
    score_total: DecimalOut | None
    max_total: DecimalOut | None
    score_percent: DecimalOut | None
    finalized_at: datetime | None
    amended_at: datetime | None
    last_change_reason: str | None = Field(
        description="Internal: never shown to families."
    )
    voided: EvaluationVoided | None
    created_at: datetime
    can_edit: bool = Field(description="Change this draft.")
    can_finalize: bool
    can_amend: bool = Field(description="Amend this final evaluation (with a reason).")
    can_delete: bool
    can_void: bool


# --- The unified history (F3) ---------------------------------------------------------


class HistoryCriterion(_Closed):
    title: str
    max_score: DecimalOut
    score: DecimalOut
    comment: str | None


class HistoryCourse(_Closed):
    cohort_name: str
    assessment_title: str


class HistoryEntry(_Closed):
    """One entry of the academic history: a final evaluation (closed
    allow-list: no author id, no internal fields)."""

    kind: Literal["evaluation"] = Field(
        description="The entry type: evaluation (this shape) or assignment_grade."
    )
    id: uuid.UUID
    evaluated_on: date
    subject: EvaluationSubject
    evaluation_kind: EvaluationKind
    template_name: str
    author: EvaluationPerson
    criteria: list[HistoryCriterion]
    score_total: DecimalOut
    max_total: DecimalOut
    score_percent: DecimalOut
    feedback: str | None
    course: HistoryCourse | None
    finalized_at: datetime
    updated: bool = Field(description="Amended after it was finalized.")


class AssignmentGradeEntry(_Closed):
    """One entry of the academic history: a scored homework grade (#82 G1;
    closed allow-list: no author id, no submission text or link, no change
    reason). Feedback-only grades are not history entries."""

    kind: Literal["assignment_grade"] = Field(description="The entry type.")
    id: uuid.UUID = Field(description="The submission's id.")
    graded_on: date = Field(description="The local date of the first grade.")
    subject: EvaluationSubject
    title: str
    author: EvaluationPerson = Field(description="The tutor who set it.")
    max_score: DecimalOut
    score: DecimalOut
    score_percent: DecimalOut
    feedback: str | None
    due_on: date
    is_late: bool
    graded_at: datetime
    updated: bool = Field(description="The grade was changed after it was given.")


type HistoryItem = Annotated[
    HistoryEntry | AssignmentGradeEntry, Field(discriminator="kind")
]


class HistoryPeriod(_Closed):
    id: uuid.UUID
    name: str
    period_type: PeriodType
    starts_on: date
    ends_on: date


class HistorySubjectGroup(_Closed):
    subject: EvaluationSubject
    count: int = Field(description="Evaluations only.")
    average_percent: DecimalOut | None = Field(
        description="The mean of the evaluations' percentages; null without one."
    )
    assignment_count: int = Field(description="Scored homework grades (#82).")
    assignment_average_percent: DecimalOut | None = Field(
        description="The mean of the homework grades' percentages; null without one."
    )
    entry_ids: list[uuid.UUID] = Field(description="Both kinds, newest first.")


class HistoryGroup(_Closed):
    period: HistoryPeriod | None = Field(description="Null: no period.")
    subjects: list[HistorySubjectGroup]


class AcademicHistory(_Closed):
    """A student's academic history (#80 F3, #82 G1): one chronological
    list of final evaluations and scored homework grades from every tutor,
    and the same entries grouped by period and subject. The same shape for
    staff, tutors and families."""

    student_id: uuid.UUID
    group_by: GroupBy
    truncated: bool = Field(
        description="More than 500 entries: the oldest are left out."
    )
    entries: list[HistoryItem]
    groups: list[HistoryGroup]


# --- The evaluation context of a class (AM1) -----------------------------------------


class EvaluationContextParticipant(_Closed):
    student_id: uuid.UUID
    display_name: str


class EvaluationContextAssessment(_Closed):
    id: uuid.UUID
    title: str
    is_required: bool
    min_score_percent: int | None


class EvaluationContextCourse(_Closed):
    cohort_id: uuid.UUID
    cohort_name: str
    assessments: list[EvaluationContextAssessment] = Field(
        description="The cohort's syllabus as frozen when it was created."
    )


class EvaluationContext(_Closed):
    """What the evaluation form needs of one of your classes (#80 AM1)."""

    class_session_id: uuid.UUID
    kind: str
    state: str
    starts_at: datetime
    subject: EvaluationSubject
    evaluable: bool = Field(description="Confirmed or completed, and started.")
    participants: list[EvaluationContextParticipant]
    course: EvaluationContextCourse | None
