"""Catalog request and response bodies (the public schemas of the module).

Request bodies forbid unknown fields (``422``): in particular a subject's
``education_level_id`` can't be sent in a ``PATCH`` (its level never
changes). In an update body, a field that is not sent stays unchanged and
``null`` is ``422``.
"""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.modules.catalog.models import NAME_MAX_LENGTH, SORT_ORDER_MAX

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
