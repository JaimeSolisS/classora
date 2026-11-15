"""Import request and response bodies (#122 B1, B8, C1, C2, D1, E1)."""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints

from app.modules.administration.imports.models import (
    FILE_NAME_MAX_LENGTH,
    KIND_MAX_LENGTH,
)
from app.modules.administration.schemas import AccountName

type ImportJobStatus = Literal[
    "staged", "committing", "committed", "failed", "discarded", "expired"
]
type ImportRowStatus = Literal[
    "ready", "duplicate", "error", "created", "skipped", "failed"
]


def _blank_to_none(value: Any) -> Any:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


FileName = Annotated[
    Annotated[str, StringConstraints(max_length=FILE_NAME_MAX_LENGTH)] | None,
    BeforeValidator(_blank_to_none),
]


class ImportJobCreate(BaseModel):
    """A new import (``POST /api/v1/imports``): the CSV's text, read by the
    browser (UTF-8; ``,`` or ``;``). The text is parsed in the request and
    never stored; the whole body is at most 2 MiB."""

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(
        min_length=1,
        max_length=KIND_MAX_LENGTH,
        description="A registered import kind: students or tutors.",
    )
    file_name: FileName = Field(
        default=None, description="The file's name, for the history (optional)."
    )
    csv: str = Field(description="The file's text. The first line is the header.")


class ImportVersion(BaseModel):
    """The job version the caller loaded (commit and discard)."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)


class ImportCounts(BaseModel):
    """The rows by status. ``ready`` counts rows still waiting to be
    imported; ``created``, ``skipped`` and ``failed`` the commit's
    outcomes."""

    total: int
    ready: int
    duplicate: int
    error: int
    created: int
    skipped: int
    failed: int


class ImportJobDetail(BaseModel):
    """One import job. ``expires_at`` is set while the job is ``staged`` or
    ``failed``: its last change plus 7 days, when it expires with its staged
    rows."""

    id: uuid.UUID
    kind: str
    status: ImportJobStatus
    file_name: str | None
    ignored_columns: list[str] = Field(
        description="Header names the kind doesn't read (lower case)."
    )
    counts: ImportCounts
    created_by: AccountName
    created_at: datetime
    committed_by: AccountName | None
    committed_at: datetime | None
    expires_at: datetime | None
    version: int


class ImportRowIssue(BaseModel):
    """An error or warning: the column (``null`` for the whole row) and a
    stable code. Never a value."""

    column: str | None
    code: str


class ImportRowItem(BaseModel):
    """One staged row, by its file line. ``values`` holds the cells of the
    kind's columns while the job is open; ``null`` after commit."""

    row_number: int
    status: ImportRowStatus
    values: dict[str, str] | None
    errors: list[ImportRowIssue]
    warnings: list[ImportRowIssue]
    record_id: uuid.UUID | None
