"""Shapes of the unbilled enrollment items (#92 F2; ``app.workflows.
enrollment_charges``)."""

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.workflows.session_charge_schemas import UnbilledReason, UnbilledRecord

type EnrollmentChargeKind = Literal["group_enrollment", "cohort_enrollment"]
"""The fee kind of a period: a group's flat fee or a cohort's course fee."""


class UnbilledEnrollmentItem(BaseModel):
    """One group or cohort enrollment period whose fee the enrollment sweeper
    can't post (``GET /api/v1/billing/unbilled-enrollments``)."""

    kind: EnrollmentChargeKind
    enrollment_id: uuid.UUID
    enrolled_at: datetime
    withdrawn_at: datetime | None
    service_date: date = Field(
        description="The enrollment's local date in the center's time zone."
    )
    student: UnbilledRecord
    family: UnbilledRecord | None = Field(
        description="The student's family (null: none, reason no_billing_account)."
    )
    education_level: UnbilledRecord | None = Field(
        description="The student's current level (null: none)."
    )
    subject: UnbilledRecord
    group: UnbilledRecord | None = Field(
        description="The group of a group_enrollment period (else null)."
    )
    cohort: UnbilledRecord | None = Field(
        description="The cohort of a cohort_enrollment period (else null)."
    )
    reason: UnbilledReason
