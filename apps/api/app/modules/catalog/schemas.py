"""Catalog request and response bodies (the public schemas of the module).

Request bodies forbid unknown fields (``422``): in particular a subject's
``education_level_id`` can't be sent in a ``PATCH`` (its level never
changes). In an update body, a field that is not sent stays unchanged and
``null`` is ``422``.
"""

import re
import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationInfo,
    field_validator,
)

from app.modules.catalog.models import (
    CLOSURE_MAX_SPAN_DAYS,
    NAME_MAX_LENGTH,
    PERIOD_MAX_SPAN_DAYS,
    SORT_ORDER_MAX,
)

# The status filter of the list routes: ``active`` (the default, what new
# records may choose), ``inactive`` or ``all``.
type CatalogStatus = Literal["active", "inactive", "all"]

CatalogName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=NAME_MAX_LENGTH),
]
"""A catalog name: trimmed, 1 to 100 characters, never blank. Inner spaces
are kept as typed."""

SortOrder = Annotated[int, Field(ge=0, le=SORT_ORDER_MAX)]


def _not_null[T](value: T | None) -> T:
    # Runs only when the field is sent: null never clears a catalog field.
    if value is None:
        raise ValueError("This field cannot be null.")
    return value


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EducationLevelCreate(_Body):
    """A new education level (``POST /api/v1/education-levels``). Without
    ``sort_order`` the level goes last (the current maximum + 10, at most
    9999). It starts active."""

    name: CatalogName
    sort_order: SortOrder | None = None


class EducationLevelUpdate(_Body):
    """A change to a level (``PATCH /api/v1/education-levels/{level_id}``)
    with the ``version`` the caller loaded. Fields not sent stay unchanged."""

    version: int = Field(ge=1)
    name: CatalogName | None = None
    sort_order: SortOrder | None = None
    is_active: bool | None = None

    _fields_not_null = field_validator("name", "sort_order", "is_active")(_not_null)


class SubjectCreate(_Body):
    """A new subject of the level in the path. It starts active."""

    name: CatalogName


class SubjectUpdate(_Body):
    """A change to a subject (``PATCH /api/v1/subjects/{subject_id}``) with
    the ``version`` the caller loaded. The level can't be changed."""

    version: int = Field(ge=1)
    name: CatalogName | None = None
    is_active: bool | None = None

    _fields_not_null = field_validator("name", "is_active")(_not_null)


class EducationLevelDetail(BaseModel):
    """One education level. ``active_subject_count`` counts only its active
    subjects."""

    id: uuid.UUID
    name: str
    sort_order: int
    is_active: bool
    active_subject_count: int
    version: int
    created_at: datetime
    updated_at: datetime


class SubjectDetail(BaseModel):
    """One subject with its level's id and current name."""

    id: uuid.UUID
    education_level_id: uuid.UUID
    education_level_name: str
    name: str
    is_active: bool
    version: int
    created_at: datetime
    updated_at: datetime


class EducationLevelReference(BaseModel):
    """A level as another module's record shows it (#33): its id, current
    name and whether it is still active (an inactive level stays assigned and
    is shown as such)."""

    id: uuid.UUID
    name: str
    is_active: bool


# --- Academic periods and closures (#38) ---------------------------------------------

type PeriodType = Literal["school_year", "semester", "term"]
type ClosureType = Literal["holiday", "closure", "vacation"]

ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _iso_date_text(value: Any) -> Any:
    # Only the YYYY-MM-DD form (no number, no time part); a date object from
    # Python code passes through.
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or ISO_DATE.fullmatch(value) is None:
        raise ValueError("Use the YYYY-MM-DD form.")
    return value


LocalDate = Annotated[
    date,
    BeforeValidator(_iso_date_text),
    Field(description="A calendar date, YYYY-MM-DD (no time zone)."),
]
"""A local calendar date (ADR-0004): only ``YYYY-MM-DD``."""


def range_error(starts_on: date, ends_on: date, max_span: int) -> str | None:
    """Why ``[starts_on, ends_on]`` (inclusive) is not a valid range, or
    ``None``."""
    if starts_on > ends_on:
        return "The end date can't be before the start date."
    if (ends_on - starts_on).days > max_span:
        return f"The dates can be at most {max_span} days apart."
    return None


def _checked_range(value: date | None, info: ValidationInfo, max_span: int) -> Any:
    # Both dates sent: the range must be valid (the error goes on ends_on).
    # A PATCH that sends one date is checked by the service against the
    # stored one.
    starts_on = info.data.get("starts_on")
    if isinstance(starts_on, date) and isinstance(value, date):
        message = range_error(starts_on, value, max_span)
        if message is not None:
            raise ValueError(message)
    return value


class AcademicPeriodCreate(_Body):
    """A new academic period (``POST /api/v1/academic-periods``): inclusive
    dates, at most 730 days apart. It starts active."""

    name: CatalogName
    period_type: PeriodType
    starts_on: LocalDate
    ends_on: LocalDate = Field(description="Inclusive; on or after starts_on.")

    @field_validator("ends_on")
    @classmethod
    def _range(cls, value: date | None, info: ValidationInfo) -> Any:
        return _checked_range(value, info, PERIOD_MAX_SPAN_DAYS)


class AcademicPeriodUpdate(_Body):
    """A change to a period with the ``version`` the caller loaded. Fields not
    sent stay unchanged; the type can't change (deactivate and recreate)."""

    version: int = Field(ge=1)
    name: CatalogName | None = None
    starts_on: LocalDate | None = None
    ends_on: LocalDate | None = None
    is_active: bool | None = None

    _fields_not_null = field_validator("name", "starts_on", "ends_on", "is_active")(
        _not_null
    )

    @field_validator("ends_on")
    @classmethod
    def _range(cls, value: date | None, info: ValidationInfo) -> Any:
        return _checked_range(value, info, PERIOD_MAX_SPAN_DAYS)


class AcademicPeriodDetail(BaseModel):
    """One academic period. ``starts_on`` and ``ends_on`` are inclusive local
    dates."""

    id: uuid.UUID
    name: str
    period_type: PeriodType
    starts_on: date
    ends_on: date
    is_active: bool
    version: int
    created_at: datetime
    updated_at: datetime


class ClosureCreate(_Body):
    """A new center closure (``POST /api/v1/closures``): whole local days,
    inclusive, at most 366 days apart. It starts active."""

    name: CatalogName
    closure_type: ClosureType
    starts_on: LocalDate
    ends_on: LocalDate = Field(description="Inclusive; on or after starts_on.")

    @field_validator("ends_on")
    @classmethod
    def _range(cls, value: date | None, info: ValidationInfo) -> Any:
        return _checked_range(value, info, CLOSURE_MAX_SPAN_DAYS)


class ClosureUpdate(_Body):
    """A change to a closure with the ``version`` the caller loaded. Fields
    not sent stay unchanged."""

    version: int = Field(ge=1)
    name: CatalogName | None = None
    closure_type: ClosureType | None = None
    starts_on: LocalDate | None = None
    ends_on: LocalDate | None = None
    is_active: bool | None = None

    _fields_not_null = field_validator(
        "name", "closure_type", "starts_on", "ends_on", "is_active"
    )(_not_null)

    @field_validator("ends_on")
    @classmethod
    def _range(cls, value: date | None, info: ValidationInfo) -> Any:
        return _checked_range(value, info, CLOSURE_MAX_SPAN_DAYS)


class ClosureDetail(BaseModel):
    """One closure. ``starts_on`` and ``ends_on`` are inclusive local dates."""

    id: uuid.UUID
    name: str
    closure_type: ClosureType
    starts_on: date
    ends_on: date
    is_active: bool
    version: int
    created_at: datetime
    updated_at: datetime


class ClosureCheck(BaseModel):
    """Whether the center is closed on a local date
    (``GET /api/v1/closures/check``): the active closures that contain it."""

    date: date
    closed: bool
    closures: list[ClosureDetail]
