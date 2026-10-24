"""Course template request and response bodies (the public schemas of the
module, #64).

Request bodies forbid unknown fields (``422``). A template is written as one
document (:class:`CourseTemplateWrite`): list order is position, modules and
assessments carry an optional ``id`` (kept and updated on a ``PUT``; none
creates one), and an assessment names its module by ``module_index``, the
0-based index of a module **in the same body** (``null`` = the whole course).
The bounds below are checked by the schema; the rules that need the whole
document (counts, duplicates, references between items) are checked by
:func:`app.modules.courses.templates.validate_template`.
"""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from app.modules.courses.models import (
    ASSESSMENTS_MAX,
    COHORT_MAX_STUDENTS_MAX,
    COHORT_NAME_MAX_LENGTH,
    MODULES_MAX,
    NAME_MAX_LENGTH,
    PERCENT_MAX,
    PERCENT_MIN,
    SESSION_COUNT_MAX,
    SESSION_MINUTES_MAX,
    SESSION_MINUTES_MIN,
    SUBJECTS_MAX,
    TEXT_MAX_LENGTH,
    TITLE_MAX_LENGTH,
)

# The status filter of the list: ``active`` (the default), ``inactive`` or
# ``all``.
type CourseTemplateStatus = Literal["active", "inactive", "all"]


def _blank_to_none(value: Any) -> Any:
    # Optional texts are trimmed; an empty or blank text is stored as null.
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


TemplateName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=NAME_MAX_LENGTH),
]
"""A template name: trimmed, 1 to 100 characters."""

ItemTitle = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=TITLE_MAX_LENGTH),
]
"""A module or assessment title: trimmed, 1 to 200 characters."""

OptionalText = Annotated[
    Annotated[str, StringConstraints(max_length=TEXT_MAX_LENGTH)] | None,
    BeforeValidator(_blank_to_none),
]
"""A free text of at most 2000 characters, trimmed; blank is ``null``."""

Percent = Annotated[int, Field(ge=PERCENT_MIN, le=PERCENT_MAX)]
PlannedSessions = Annotated[int, Field(ge=1, le=SESSION_COUNT_MAX)]


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CourseTemplateModuleWrite(_Body):
    """One syllabus module of a written template; its position is its index
    in ``modules`` plus one."""

    id: uuid.UUID | None = Field(
        default=None,
        description="A module of this template to keep (PUT only); none creates one.",
    )
    title: ItemTitle
    description: OptionalText = None
    subject_id: uuid.UUID | None = Field(
        default=None, description="One of the template's subject_ids, or null."
    )
    planned_sessions: PlannedSessions | None = None
    is_required: bool
    materials: OptionalText = Field(
        default=None,
        description="Free-text materials references (no files or links).",
    )


class CourseTemplateAssessmentWrite(_Body):
    """One assessment definition of a written template; its position is its
    index in ``assessments`` plus one."""

    id: uuid.UUID | None = Field(
        default=None,
        description=(
            "An assessment of this template to keep (PUT only); none creates one."
        ),
    )
    title: ItemTitle
    module_index: int | None = Field(
        default=None,
        description=(
            "The 0-based index of its module in this body's modules; null is"
            " the whole course."
        ),
    )
    is_required: bool
    min_score_percent: Percent | None = None


class CourseTemplateWrite(_Body):
    """A whole template document (``POST /api/v1/course-templates``)."""

    name: TemplateName
    description: OptionalText = None
    subject_ids: list[uuid.UUID] = Field(max_length=SUBJECTS_MAX)
    session_count: int = Field(ge=1, le=SESSION_COUNT_MAX)
    session_minutes: int = Field(ge=SESSION_MINUTES_MIN, le=SESSION_MINUTES_MAX)
    min_attendance_percent: Percent | None = None
    modules: list[CourseTemplateModuleWrite] = Field(max_length=MODULES_MAX)
    assessments: list[CourseTemplateAssessmentWrite] = Field(
        default_factory=list, max_length=ASSESSMENTS_MAX
    )


class CourseTemplateReplace(CourseTemplateWrite):
    """The new content of a template (``PUT
    /api/v1/course-templates/{template_id}``) with the ``version`` the caller
    loaded. It replaces the whole content: modules and assessments left out
    are deleted."""

    version: int = Field(ge=1)


class CourseTemplateStatusUpdate(_Body):
    """Deactivate or reactivate a template (``PATCH
    /api/v1/course-templates/{template_id}``)."""

    version: int = Field(ge=1)
    is_active: bool


# --- Responses -----------------------------------------------------------------------


class CourseTemplateSubject(BaseModel):
    """A subject of a template, with its level's current name.
    ``is_active`` is false when the subject or its level was deactivated
    after it was linked (it stays linked)."""

    id: uuid.UUID
    name: str
    level_name: str
    is_active: bool


class CourseTemplateModule(BaseModel):
    """One syllabus module, by position (1-based)."""

    id: uuid.UUID
    position: int
    title: str
    description: str | None
    subject_id: uuid.UUID | None
    planned_sessions: int | None
    is_required: bool
    materials: str | None


class CourseTemplateAssessment(BaseModel):
    """One assessment definition, by position (1-based); ``module_id`` null
    is the whole course."""

    id: uuid.UUID
    position: int
    title: str
    module_id: uuid.UUID | None
    is_required: bool
    min_score_percent: int | None


class CourseCompletion(BaseModel):
    """The completion criteria, derived from the template: the minimum
    attendance (null: none), then the required modules and assessments, in
    position order."""

    min_attendance_percent: int | None
    required_module_ids: list[uuid.UUID]
    required_assessment_ids: list[uuid.UUID]


class CourseTemplateSummary(BaseModel):
    """A template in the list. ``total_minutes`` is ``session_count`` ×
    ``session_minutes``."""

    id: uuid.UUID
    name: str
    subjects: list[CourseTemplateSubject]
    session_count: int
    session_minutes: int
    total_minutes: int
    module_count: int
    is_active: bool
    version: int
    updated_at: datetime


class CourseTemplateDetail(CourseTemplateSummary):
    """One template with its modules, assessments and completion criteria."""

    description: str | None
    min_attendance_percent: int | None
    modules: list[CourseTemplateModule]
    assessments: list[CourseTemplateAssessment]
    completion: CourseCompletion
    created_at: datetime


class CourseTemplateSnapshot(BaseModel):
    """An immutable snapshot of a template at one version (#64 E1): what a
    cohort (#65) is created from. ``content`` follows schema version 1
    (``courses.templates.build_snapshot``)."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    template_id: uuid.UUID
    template_version: int
    content: dict[str, Any]


# --- Cohorts (#65) -------------------------------------------------------------------

# The cohort status (#65 B2): computed from its series' rule, never stored.
type CohortStatus = Literal["upcoming", "in_progress", "ended"]
# The status filter of the cohort list (#65 F2): ``current`` (the default) is
# ``upcoming`` plus ``in_progress``.
type CohortStatusFilter = Literal["upcoming", "in_progress", "ended", "current", "all"]


def _check_cohort_name(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    name = value.strip()
    if not name:
        raise PydanticCustomError("name_required", "Give the cohort a name.")
    if len(name) > COHORT_NAME_MAX_LENGTH:
        raise PydanticCustomError(
            "name_too_long", "A cohort name is at most 120 characters."
        )
    return name


class CohortUpdate(_Body):
    """Rename a cohort or change its maximum (``PATCH
    /api/v1/cohorts/{cohort_id}``, #65 E1). At least one of ``name`` and
    ``max_students`` (``no_changes`` on the body). The template, the subject
    and the snapshot never change; the dates, the room and the tutor change
    through the cohort's series."""

    version: int = Field(description="The cohort's version, as read.")
    name: str | None = Field(
        default=None, description="1-120 characters, trimmed; unique (any case)."
    )
    max_students: int | None = Field(
        default=None,
        ge=1,
        le=COHORT_MAX_STUDENTS_MAX,
        description="The cohort maximum, 1-500.",
    )

    _name = field_validator("name", mode="before")(_check_cohort_name)

    @model_validator(mode="after")
    def _something_changes(self) -> CohortUpdate:
        if self.name is None and self.max_students is None:
            raise PydanticCustomError("no_changes", "Change the name or the maximum.")
        return self


class CohortTemplate(BaseModel):
    """The template a cohort was created from, as its snapshot froze it: the
    template's id, and its name and version at snapshot time."""

    id: uuid.UUID
    name: str
    version: int


class CohortTemplateName(BaseModel):
    """The template of a cohort in the list (the snapshot's name)."""

    id: uuid.UUID
    name: str


class CohortSyllabus(BaseModel):
    """A cohort's syllabus, read from its template snapshot (never from the
    live template): the modules and assessments in position order and the
    completion criteria."""

    modules: list[CourseTemplateModule]
    assessments: list[CourseTemplateAssessment]
    completion: CourseCompletion


class CohortRecord(BaseModel):
    """A cohort row with what its snapshot says (the courses half of a
    cohort read; ``app.workflows.cohorts`` adds the schedule)."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    name: str
    template: CohortTemplate
    template_snapshot_id: uuid.UUID
    subject_id: uuid.UUID
    max_students: int
    session_count: int = Field(description="The snapshot's planned sessions.")
    session_minutes: int = Field(description="The snapshot's session length.")
    snapshot_subject_ids: tuple[uuid.UUID, ...]
    syllabus: CohortSyllabus | None = Field(
        description="The syllabus (detail reads only; null in a list)."
    )
    version: int
    created_at: datetime
    updated_at: datetime


class CohortLock(BaseModel):
    """What a holder of the cohort row lock needs (#65 C3, G1)."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    max_students: int
