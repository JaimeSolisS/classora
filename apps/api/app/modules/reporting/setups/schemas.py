"""Saved export setup request and response shapes (#125 B1-B4)."""

import uuid
from datetime import datetime
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.modules.administration.schemas import AccountName
from app.modules.reporting.exports.datasets import ExportParameters
from app.modules.reporting.setups.models import NAME_MAX_LENGTH, SharedRole

type SetupName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=NAME_MAX_LENGTH),
]


class ReportSetupItem(BaseModel):
    """One saved export setup the caller may see (#125 B1)."""

    id: uuid.UUID
    name: str
    dataset: str
    parameters: ExportParameters = Field(
        description="The export's parameters exactly as saved: post them"
        " unchanged to POST /api/v1/exports to run the setup."
    )
    shared_role: SharedRole | None = Field(
        description="The staff role the setup is shared with, or null (private)."
    )
    created_by: AccountName
    mine: bool = Field(description="Whether the caller created it (and may edit it).")
    can_run: bool = Field(
        description="Whether the caller may export its dataset now. Running"
        " still applies every export check (reauthentication, the limit)."
    )
    version: int
    created_at: datetime
    updated_at: datetime


class ReportSetupList(BaseModel):
    items: list[ReportSetupItem]


class ReportSetupCreate(BaseModel):
    """``POST /api/v1/report-setups`` (#125 B3)."""

    model_config = ConfigDict(extra="forbid")

    name: SetupName
    parameters: ExportParameters
    shared_role: SharedRole | None = None


class ReportSetupUpdate(BaseModel):
    """``PATCH /api/v1/report-setups/{id}`` (#125 B4): the fields sent
    change; ``shared_role: null`` unshares."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    name: SetupName | None = None
    parameters: ExportParameters | None = None
    shared_role: SharedRole | None = None

    @model_validator(mode="after")
    def _no_null_name_or_parameters(self) -> Self:
        for field in ("name", "parameters"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} must not be null")
        return self
