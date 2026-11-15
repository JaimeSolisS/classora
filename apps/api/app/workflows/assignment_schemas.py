"""Homework assignment bodies and closed views (#82 C1, C4, C6, D1, D2, E2,
E3, F1, AM1).

Every model is a closed allow-list (``extra="forbid"``). Scores and
percentages are decimals with at most two places: JSON strings on the way
out (``"7.50"``, ADR-0004), strings or numbers on the way in. Text is
plain and trimmed; whitespace-only optional text is ``null``.

The bodies check what needs no stored data (``422`` at step 2 of every
order: types, lengths of the homework's own text, ``max_score`` range and
precision, duplicate or too many recipients). The submission and grade
content checks (``too_long``, ``url_invalid``, ``submission_empty``,
``score_required``, …) run in ``app.workflows.assignments`` after the
access, version and state checks (C3).

Two shapes: :class:`AssignmentView` (the working view with the roster:
the creator and ``assignment.review`` holders only) and
:class:`StudentAssignment` (the homework with **one** student's row: the
same for every audience, families included; never user ids, the roster,
counts, other students or the internal change reason).
"""

import uuid
from datetime import date, datetime, time
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    field_validator,
)
from pydantic.json_schema import SkipJsonSchema
from pydantic_core import PydanticCustomError

from app.modules.academics.service import assignment_rules as rules
from app.workflows.evaluation_schemas import DecimalIn, DecimalOut


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _text(maximum: int, *, required: str) -> Any:
    def validate(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        cleaned = rules.clean(value)
        if cleaned is None:
            raise PydanticCustomError(required, "This field is required.")
        if len(cleaned) > maximum:
            raise PydanticCustomError(
                "too_long", "At most {max} characters.", {"max": maximum}
            )
        return cleaned

    return BeforeValidator(validate)


type SubmissionState = Literal["pending", "missing", "submitted", "graded"]
type SubmittedVia = Literal["student", "tutor"]
type Due = Literal["upcoming", "past"]

_TIME_OUT = PlainSerializer(lambda value: value.strftime("%H:%M:%S"), return_type=str)


def _naive_time(value: time | None) -> time | None:
    """A local wall-clock time: no offset (``time_has_offset``)."""
    if value is not None and value.tzinfo is not None:
        raise PydanticCustomError(
            "time_has_offset", "Write a local time without an offset."
        )
    return value


LocalTime = Annotated[time, _TIME_OUT]


def _trim_only(value: Any) -> Any:
    return rules.clean(value) if isinstance(value, str) else value


Trimmed = Annotated[str | None, BeforeValidator(_trim_only)]


def _max_score(value: Decimal | None) -> Decimal | None:
    if value is not None and not rules.MAX_SCORE_MIN <= value <= rules.MAX_SCORE_MAX:
        raise PydanticCustomError(
            "max_score_out_of_range", "The maximum score is 0.5 to 1000."
        )
    return value


def _recipients(value: list[uuid.UUID] | None) -> list[uuid.UUID] | None:
    if value is None:
        return None
    if len(set(value)) != len(value):
        raise PydanticCustomError(
            "duplicate_recipient", "A student appears more than once."
        )
    if len(value) > rules.RECIPIENTS_MAX:
        raise PydanticCustomError(
            "recipients_too_many",
            "At most {max} students.",
            {"max": rules.RECIPIENTS_MAX},
        )
    return value


Title = Annotated[str, _text(rules.TITLE_MAX, required="title_required")]
Instructions = Annotated[
    str, _text(rules.INSTRUCTIONS_MAX, required="instructions_required")
]


# --- Bodies (C1, C4, D1, D2) ----------------------------------------------------------


class AssignmentCreate(_Closed):
    """`POST /api/v1/assignments` (#82 C1): homework for one or more of your
    current students. Each student and the guardians with access to their
    academic records see it at once and are notified (no draft)."""

    subject_id: uuid.UUID = Field(
        description="An active subject (subject_unknown); with a class, the"
        " class's subject (subject_mismatch). It never changes."
    )
    title: Title = Field(description="1-200 characters, plain text.")
    instructions: Instructions = Field(description="1-4000 characters, plain text.")
    due_on: date = Field(
        description="The deadline's local date: from the center's today"
        " (due_in_past) to 365 days ahead (due_too_far)."
    )
    due_time: Annotated[time | None, AfterValidator(_naive_time)] = Field(
        default=None,
        description="The deadline's local time (HH:MM[:SS], no offset); null"
        " means the end of due_on. On today it must not have passed"
        " (due_in_past).",
    )
    max_score: DecimalIn | None = Field(
        default=None,
        description="0.5-1000, at most two decimal places: graded with a score;"
        " null: graded with feedback only.",
    )
    class_session_id: uuid.UUID | None = Field(
        default=None,
        description="Optional: one of your confirmed or completed classes"
        " (class_session_invalid / session_not_assignable). Never changes.",
    )
    student_ids: list[uuid.UUID] | None = Field(
        default=None,
        description="1-100 of your current students, each once (with a class:"
        " its participants). Omitted with a class: the class's participants who"
        " are your current students. Required without a class"
        " (recipients_required).",
    )

    _check_max_score = field_validator("max_score")(_max_score)
    _check_students = field_validator("student_ids")(_recipients)


def _not_null(value: Any) -> Any:
    """AM1: only ``due_time`` and ``max_score`` take an explicit ``null``."""
    if value is None:
        raise PydanticCustomError("null_not_allowed", "This field can't be null.")
    return value


_NOT_NULL = BeforeValidator(_not_null)


class AssignmentPatch(_Closed):
    """`PATCH /api/v1/assignments/{id}` (#82 C4, AM1): a key you leave out
    keeps its value. `due_time: null` clears the time (the end of due_on);
    `max_score: null` makes the homework feedback-only. `null` for any other
    key is a validation error."""

    version: int = Field(description="The assignment's version, as last read.")
    title: Annotated[Title | SkipJsonSchema[None], _NOT_NULL] = Field(
        default=None, description="1-200 characters."
    )
    instructions: Annotated[Instructions | SkipJsonSchema[None], _NOT_NULL] = Field(
        default=None, description="1-4000 characters."
    )
    due_on: Annotated[date | SkipJsonSchema[None], _NOT_NULL] = Field(
        default=None,
        description="A new local date: not before the center's today"
        " (due_in_past), at most 365 days ahead (due_too_far).",
    )
    due_time: Annotated[time | None, AfterValidator(_naive_time)] = Field(
        default=None, description="null clears the time (the end of due_on)."
    )
    max_score: DecimalIn | None = Field(
        default=None,
        description="0.5-1000; null: feedback only. Locked once a student is"
        " graded (assignment_has_grades).",
    )
    add_student_ids: Annotated[list[uuid.UUID] | SkipJsonSchema[None], _NOT_NULL] = (
        Field(
            default=None,
            description="Students to add (your current students; with a class, its"
            " participants). Existing recipients are ignored; each new one is"
            " notified.",
        )
    )
    remove_student_ids: Annotated[list[uuid.UUID] | SkipJsonSchema[None], _NOT_NULL] = (
        Field(
            default=None,
            description="Recipients to remove; one who submitted can't be removed"
            " (recipient_has_submission).",
        )
    )

    _check_max_score = field_validator("max_score")(_max_score)
    _check_add = field_validator("add_student_ids")(_recipients)
    _check_remove = field_validator("remove_student_ids")(_recipients)


class SubmissionPut(_Closed):
    """`PUT /api/v1/assignments/{id}/students/{student_id}/submission` (#82
    D1): the student submits text and/or a link (at least one), or the tutor
    who set the homework records work handed in on paper (both may be
    empty). Replacing an existing submission needs its version."""

    version: int | None = Field(
        default=None,
        description="The submission's version, as last read: required when"
        " something was already submitted (version_required).",
    )
    text: Trimmed = Field(
        default=None, description="Plain text, at most 10000 characters."
    )
    url: Trimmed = Field(
        default=None,
        description="An absolute http or https link with a host, no user name"
        " or password, at most 2000 characters (url_invalid). Never opened by"
        " the server.",
    )


class GradePut(_Closed):
    """`PUT /api/v1/assignments/{id}/students/{student_id}/grade` (#82 D2):
    with a max_score, a score (and optional feedback); without one,
    feedback only. Changing a grade needs a reason (internal: families never
    see it)."""

    version: int = Field(description="The submission's version, as last read.")
    score: DecimalIn | None = Field(
        default=None,
        description="0 to max_score, at most two decimal places (score_required"
        " / score_out_of_range / score_not_allowed).",
    )
    feedback: Trimmed = Field(
        default=None,
        description="Plain text, at most 4000 characters; required without a"
        " max_score (feedback_required).",
    )
    change_reason: Trimmed = Field(
        default=None,
        description="Required to change a grade: 1-500 characters"
        " (change_reason_required). Ignored on the first grade.",
    )


# --- Views (C6, E2, E3, F1) -----------------------------------------------------------


class AssignmentSubject(_Closed):
    id: uuid.UUID
    name: str


class AssignmentPerson(_Closed):
    display_name: str


class AssignmentStudent(_Closed):
    id: uuid.UUID
    display_name: str


class AssignmentSession(_Closed):
    id: uuid.UUID
    starts_at: datetime


class AssignmentCounts(_Closed):
    recipients: int
    pending: int
    missing: int
    submitted: int
    late: int = Field(description="Submitted or graded after the deadline.")
    graded: int


class RosterEntry(_Closed):
    """One recipient's row in the working view."""

    student: AssignmentStudent
    version: int = Field(description="The submission's version (send it back).")
    state: SubmissionState
    is_late: bool
    submitted_at: datetime | None
    submitted_via: SubmittedVia | None
    text: str | None
    url: str | None
    score: DecimalOut | None
    score_percent: DecimalOut | None
    feedback: str | None
    graded_at: datetime | None
    grade_amended_at: datetime | None
    last_change_reason: str | None = Field(
        description="Internal: never shown to families."
    )
    can_record_submission: bool
    can_grade: bool


class AssignmentSummary(_Closed):
    """A homework assignment in the working list (#82 F1): no roster."""

    id: uuid.UUID
    version: int = Field(description="The assignment's version (send it back).")
    subject: AssignmentSubject
    title: str
    instructions: str
    due_on: date
    due_time: LocalTime | None
    deadline_at: datetime = Field(
        description="The deadline as an instant (due_time null: the end of due_on"
        " in the center's time zone)."
    )
    max_score: DecimalOut | None
    class_session: AssignmentSession | None
    creator: AssignmentPerson
    is_own: bool = Field(description="You set it.")
    created_at: datetime
    updated_at: datetime
    counts: AssignmentCounts
    can_edit: bool
    can_delete: bool


class AssignmentView(AssignmentSummary):
    """A homework assignment with its roster, for the tutor who set it and
    staff with assignment.review (#82 C6). The roster is ordered by name."""

    roster: list[RosterEntry]


class AssignmentList(_Closed):
    """`GET /api/v1/assignments` (#82 F1): newest deadline first."""

    items: list[AssignmentSummary]
    next_cursor: str | None


class StudentSubmission(_Closed):
    """One student's submission and grade (#82 E2)."""

    state: SubmissionState
    is_late: bool
    submitted_at: datetime | None
    submitted_via: SubmittedVia | None
    text: str | None
    url: str | None
    score: DecimalOut | None
    score_percent: DecimalOut | None
    feedback: str | None
    graded_at: datetime | None
    updated: bool = Field(description="The grade was changed after it was given.")
    version: int = Field(description="The submission's version (send it back).")


class StudentAssignmentSession(_Closed):
    starts_at: datetime


class StudentAssignment(_Closed):
    """A homework assignment with one student's submission (#82 E2): the same
    closed shape for staff, tutors, the student and guardians."""

    id: uuid.UUID
    subject: AssignmentSubject
    title: str
    instructions: str
    due_on: date
    due_time: LocalTime | None
    deadline_at: datetime
    max_score: DecimalOut | None
    creator: AssignmentPerson
    class_session: StudentAssignmentSession | None
    submission: StudentSubmission
    can_submit: bool = Field(description="You are the student and may submit now.")
    can_grade: bool = Field(description="You set it and may grade it now.")


class AssignmentStateSummary(_Closed):
    pending: int
    missing: int
    submitted: int
    graded: int


class StudentAssignmentList(_Closed):
    """`GET /api/v1/students/{id}/assignments` (#82 E3): the summary counts
    the whole filtered set (ignoring `state` and the cursor)."""

    items: list[StudentAssignment]
    summary: AssignmentStateSummary
    next_cursor: str | None
