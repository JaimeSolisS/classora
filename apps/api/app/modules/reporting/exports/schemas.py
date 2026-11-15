"""Export request and response shapes (#124 B1-B3, C1)."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.modules.reporting.exports.datasets import ExportParameters
from app.modules.reporting.exports.registry import ColumnKind, DateFilter, ExportFormat

type ExportJobStatus = Literal["queued", "ready", "failed", "expired"]
type ExportErrorCode = Literal[
    "row_limit_exceeded", "size_limit_exceeded", "permission_lost", "error"
]
type ExportLanguage = Literal["es", "en"]


class ExportColumnItem(BaseModel):
    """One column of a dataset, with its label in the caller's language."""

    code: str
    kind: ColumnKind
    groupable: bool
    label: str


class ExportDatasetItem(BaseModel):
    """A dataset the caller may export (#124 B1)."""

    code: str
    label: str = Field(description="The dataset's name in the caller's language.")
    personal: bool = Field(
        description="Names people: requesting it needs a recent password"
        " confirmation (reauth_required)."
    )
    date_filter: DateFilter = Field(
        description="Whether the filters take a date range: required, optional or none."
    )
    columns: list[ExportColumnItem]


class ExportDatasetList(BaseModel):
    items: list[ExportDatasetItem]


class ExportJobDetail(BaseModel):
    """One export of the caller (#124 B3). No file bytes and no download URL:
    the file is at ``/api/v1/exports/{id}/download`` while ``ready``."""

    id: uuid.UUID
    dataset: str
    format: ExportFormat
    status: ExportJobStatus
    parameters: ExportParameters = Field(
        description="As sent, with the default columns filled in."
    )
    language: ExportLanguage = Field(
        description="The language of the headers and labels (the caller's"
        " stored language at request)."
    )
    row_count: int | None
    byte_size: int | None
    file_name: str
    error_code: ExportErrorCode | None
    created_at: datetime
    finished_at: datetime | None
    expires_at: datetime | None = Field(
        description="When the file is deleted (24 hours after it is ready)."
    )
