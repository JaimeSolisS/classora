"""Shapes of the unbilled session items (#91 D2; ``app.workflows.
session_charges``)."""

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

type UnbilledReason = Literal["no_billing_account", "no_price"]
"""Why a billable session isn't charged (ADR-0006 E1): the student has no
family (so no billing account), or the catalog has no price in effect on
the service date (none, or "not offered")."""

type BillableState = Literal["confirmed", "completed"]
type ClassType = Literal["individual", "group"]
"""The class type of a session charged per session (``group`` since #92)."""


class UnbilledRecord(BaseModel):
    """A referenced record: its id and current name."""

    id: uuid.UUID
    name: str


class UnbilledSessionItem(BaseModel):
    """One confirmed or completed individual or group session participant
    the sweeper skips (``GET /api/v1/billing/unbilled-items``)."""

    class_session_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    service_date: date = Field(
        description="The session's local date in the center's time zone."
    )
    state: BillableState
    scheduled_minutes: int
    subject: UnbilledRecord
    student: UnbilledRecord
    education_level: UnbilledRecord | None = Field(
        description="The student's current level (null: none)."
    )
    family: UnbilledRecord | None = Field(
        description="The student's family (null: none, reason no_billing_account)."
    )
    reason: UnbilledReason
    class_type: ClassType = Field(
        description="individual, or group for a session-priced group (#92)."
    )
    group: UnbilledRecord | None = Field(
        description="The group of a group session (else null)."
    )
