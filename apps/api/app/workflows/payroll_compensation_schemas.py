"""Payroll compensation responses (#105 G1, G2): tutors with their pay, and
no contact data (#105 E3)."""

import uuid
from datetime import date

from pydantic import BaseModel

from app.modules.identity.schemas import UserState
from app.modules.payroll.schemas import (
    ClassKind,
    CompensationRateItem,
    CurrentTerms,
    PayTermsItem,
)


class PayrollTutorItem(BaseModel):
    """A tutor on the payroll list. ``current_terms`` is ``null`` without
    terms or when not on payroll; ``current_rate_kinds`` are the class kinds
    with a rate in effect today."""

    user_id: uuid.UUID
    display_name: str
    username: str
    state: UserState
    current_terms: CurrentTerms | None
    current_rate_kinds: list[ClassKind]


class CompensationTutor(BaseModel):
    """The tutor a compensation read is about."""

    user_id: uuid.UUID
    display_name: str
    state: UserState


class TutorCompensation(BaseModel):
    """All of a tutor's versions. ``today`` is the center's local date the
    ``status`` values were computed with."""

    tutor: CompensationTutor
    today: date
    terms: list[PayTermsItem]
    rates: list[CompensationRateItem]
