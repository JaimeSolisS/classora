"""Request and response bodies of the cohort routes (#65 D1, F;
``app.workflows.cohorts_router``).

They combine both modules' public schemas (a cohort is a ``courses`` row
whose sessions are a ``scheduling`` series), so they live next to the
workflow that composes the two (ADR-0002: a module never imports another
module's internals or ``app.workflows``).
"""

import re
import uuid
from datetime import date, datetime, time
from typing import Any, Final, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)
from pydantic_core import PydanticCustomError

from app.modules.courses.schemas import (
    CohortStatus,
    CohortSyllabus,
    CohortTemplate,
    CohortTemplateName,
)
from app.modules.scheduling.schemas import (
    ClassSeriesSummary,
    CohortSessionItem,
    SchedulingWarning,
    SeriesOverride,
    SessionStateCode,
    SessionTutor,
)

COHORT_NAME_MAX: Final = 120
COHORT_MAX_STUDENTS: Final = 500
_START_TIME: Final = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")


def _check_name(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    name = value.strip()
    if not name:
        raise PydanticCustomError("name_required", "Give the cohort a name.")
    if len(name) > COHORT_NAME_MAX:
        raise PydanticCustomError(
            "name_too_long", "A cohort name is at most 120 characters."
        )
    return name


def _check_start_time(value: Any) -> Any:
    # Only HH:MM (JSON) or a whole-minute time (Python callers).
    if isinstance(value, time) and value.tzinfo is None:
        if value.second == 0 and value.microsecond == 0:
            return value
    elif isinstance(value, str) and _START_TIME.fullmatch(value):
        return value
    raise PydanticCustomError("time_format", "Give the time as HH:MM.")


class CohortRecurrence(BaseModel):
    """The cohort's weekly rule (#65 D1, E3): the weekdays and the interval
    only. There is no ``until`` or ``count``: the server derives ``COUNT``
    so that the template's sessions fall outside the closures known now.
    The ranges are checked after the locks, as ``422`` on
    ``recurrence.<field>`` (``weekdays_empty``, ``weekday_out_of_range``,
    ``weekdays_duplicate``, ``interval_out_of_range``)."""

    model_config = ConfigDict(extra="forbid")

    weekdays: list[int] = Field(
        description=(
            "ISO weekdays (1 = Monday … 7 = Sunday), at least one, no duplicates."
        )
    )
    interval: int = Field(default=1, description="Every 1-4 weeks (default 1).")


class CohortCreate(BaseModel):
    """A cohort of a course template (``POST /api/v1/cohorts``, #65 D1).

    The template is frozen as its current version's snapshot; the session
    length is the snapshot's (no ``duration_minutes``); the number of
    sessions is the snapshot's, with ``COUNT`` derived around the known
    closures (no ``until`` / ``count``); no ``rrule`` text and no students
    (enrollment is #66). ``override`` (the #236 pairs) needs
    ``schedule.override``."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="1-120 characters, trimmed; unique (any case).")
    template_id: uuid.UUID = Field(description="An active course template.")
    subject_id: uuid.UUID = Field(
        description="One of the template's subjects: every session has it."
    )
    max_students: int = Field(
        ge=1, le=COHORT_MAX_STUDENTS, description="The cohort maximum, 1-500."
    )
    tutor_user_id: uuid.UUID | None = Field(
        default=None,
        description="The tutor to offer the series to; null: unassigned.",
    )
    respond_by: AwareDatetime | None = Field(
        default=None,
        description=(
            "With a tutor: an earlier deadline for the series offer, after now"
            " and no later than the default; truncated to whole minutes."
        ),
    )
    classroom_id: uuid.UUID | None = Field(
        default=None, description="The classroom; null is no room."
    )
    starts_on: date = Field(
        description="The local date the rule starts on (not before today)."
    )
    start_time: time = Field(
        description="The local start time, HH:MM (whole minutes, no seconds)."
    )
    recurrence: CohortRecurrence
    override: SeriesOverride | None = Field(
        default=None,
        description=(
            "Override the listed soft conflicts, occurrence by occurrence, with"
            " a reason (needs schedule.override). Null or absent: every"
            " conflict blocks."
        ),
    )

    _name = field_validator("name", mode="before")(_check_name)
    _start_time = field_validator("start_time", mode="before")(_check_start_time)


class CohortLevel(BaseModel):
    """The subject's education level."""

    id: uuid.UUID
    name: str


class CohortSubject(BaseModel):
    """The cohort's one subject (#65 E4) and its level."""

    id: uuid.UUID
    name: str
    level: CohortLevel


class CohortOccurrenceItem(BaseModel):
    """An upcoming occurrence of the cohort."""

    id: uuid.UUID
    state: SessionStateCode
    starts_at: datetime
    ends_at: datetime


class CohortDetail(BaseModel):
    """One cohort (``GET /api/v1/cohorts/{id}``, #65 F1). The syllabus comes
    from the snapshot, never from the live template. No enrollment data
    yet (#66 adds it)."""

    id: uuid.UUID
    name: str
    version: int
    created_at: datetime
    updated_at: datetime
    template: CohortTemplate = Field(
        description="The template, with its name and version at snapshot time."
    )
    template_snapshot_id: uuid.UUID
    subject: CohortSubject
    max_students: int
    effective_capacity: int = Field(
        description=(
            "min(max_students, the current series' room capacity); max_students"
            " without a room."
        )
    )
    status: CohortStatus = Field(
        description=(
            "upcoming before the first session starts, ended once the last one"
            " has ended, in_progress in between (from the series rule)."
        )
    )
    planned_session_count: int = Field(description="The snapshot's session count.")
    session_minutes: int = Field(description="The snapshot's session length.")
    first_session_on: date | None = Field(
        description="The local date of the first session (from the rule)."
    )
    last_session_on: date | None = Field(
        description="The local date of the last session (from the rule)."
    )
    series: ClassSeriesSummary | None = Field(
        description="The current series (the latest start), without occurrences."
    )
    next_occurrences: list[CohortOccurrenceItem] = Field(
        description="Up to 10 upcoming occurrences, by start."
    )
    syllabus: CohortSyllabus


class CohortCreated(CohortDetail):
    """The created cohort."""

    warnings: list[SchedulingWarning] = Field(description="Always empty for now.")


class CohortListItem(BaseModel):
    """A cohort in the list (#65 F2)."""

    id: uuid.UUID
    name: str
    template: CohortTemplateName = Field(description="The snapshot's template name.")
    subject: CohortSubject
    max_students: int
    tutor: SessionTutor | None = Field(description="The current series' tutor.")
    assignment_status: Literal["pending", "accepted"] | None = Field(
        description="The current series' tutor assignment; null without a tutor."
    )
    first_session_on: date | None
    last_session_on: date | None
    next_starts_at: datetime | None
    status: CohortStatus


class CohortPage(BaseModel):
    """A page of cohorts (cursor pagination)."""

    items: list[CohortListItem]
    next_cursor: str | None


class CohortSessions(BaseModel):
    """The cohort's sessions overlapping the range, by start (#65 F4)."""

    items: list[CohortSessionItem]
