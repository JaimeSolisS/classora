"""Tutor profile bodies (#43 C1-C3) and tutor qualifications (#44 B3, D1;
revocation #198 B1, B3).

The contact fields are the account's (``users.email``/``phone``, #31) with
the #31 formats; ``display_name`` is the account's too. Only
``professional_experience`` is stored by ``staff``. Subject and level names
come from ``catalog.service``.
"""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal, cast

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

from app.modules.identity.schemas import (
    ContactEmail,
    ContactPhone,
    DisplayName,
    UserState,
)
from app.modules.staff.models import (
    EXPERIENCE_MAX_LENGTH,
    REVOCATION_REASON_MAX_LENGTH,
)
from app.modules.staff.permissions import TutorField


def _blank_to_none(value: Any) -> Any:
    """Trim the experience; blank text clears it (``None``)."""
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


ProfessionalExperience = Annotated[
    Annotated[str, StringConstraints(max_length=EXPERIENCE_MAX_LENGTH)] | None,
    BeforeValidator(_blank_to_none),
]


class TutorListItem(BaseModel):
    """One tutor: a user holding the ``tutor`` role, with the account's
    contact fields."""

    user_id: uuid.UUID
    username: str
    display_name: str
    state: UserState
    email: str | None
    phone: str | None


class TeachingSubject(BaseModel):
    """A subject the tutor holds an **approved** qualification for (#44 D1).
    A subject deactivated later stays, with ``subject_is_active`` false."""

    subject_id: uuid.UUID
    subject_name: str
    education_level_id: uuid.UUID
    education_level_name: str
    subject_is_active: bool


class TutorDetail(TutorListItem):
    """One tutor profile, with what the caller may edit."""

    professional_experience: str | None
    version: int = Field(
        description=(
            "The profile's version; 0 while the tutor has no stored profile"
            " (the first edit creates it, at version 1)."
        )
    )
    editable_fields: list[TutorField] = Field(
        description=(
            "The fields the caller may change, sorted; empty: read only."
            " Sending another field is 403 permission_denied."
        )
    )
    teaching_subjects: list[TeachingSubject] = Field(
        description=(
            "The subjects of the tutor's approved qualifications, by the"
            " level's order, then the subject name."
        )
    )


class TutorUpdate(BaseModel):
    """A profile change (``PATCH /api/v1/tutors/{user_id}``). Fields not sent
    stay unchanged; ``""`` or ``null`` clears ``email``, ``phone`` or
    ``professional_experience``."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(
        ge=0, description="The version the caller loaded (0: no profile yet)."
    )
    display_name: DisplayName | None = None
    email: ContactEmail = None
    phone: ContactPhone = None
    professional_experience: ProfessionalExperience = None

    @field_validator("display_name")
    @classmethod
    def _display_name_not_null(cls, value: str | None) -> str | None:
        # Runs only when the field is sent: null is not a display name.
        if value is None:
            raise ValueError("The display name cannot be empty.")
        return value

    def sent_fields(self) -> frozenset[TutorField]:
        """The profile fields in the body (``version`` excluded)."""
        return cast(
            frozenset[TutorField],
            frozenset(field for field in self.model_fields_set if field != "version"),
        )


# --- Tutor qualifications (#44) ------------------------------------------------------

type QualificationStatus = Literal[
    "pending", "approved", "rejected", "cancelled", "revoked"
]
type QualificationStatusFilter = Literal[
    "all", "pending", "approved", "rejected", "cancelled", "revoked"
]


class QualificationCreate(BaseModel):
    """A tutor's request to teach one subject (the level follows from it)."""

    model_config = ConfigDict(extra="forbid")

    subject_id: uuid.UUID


class QualificationItem(BaseModel):
    """One qualification of a tutor, with its subject and level names."""

    id: uuid.UUID
    subject_id: uuid.UUID
    subject_name: str
    education_level_id: uuid.UUID
    education_level_name: str
    subject_is_active: bool
    status: QualificationStatus
    requested_at: datetime
    decided_at: datetime | None
    rejection_reason: str | None = Field(
        description="The reviewer's reason; set only for rejected."
    )
    revoked_at: datetime | None = Field(
        description="When the approval was revoked; set only for revoked."
    )
    revocation_reason: str | None = Field(
        description="The reviewer's revocation reason; set only for revoked."
    )
    version: int


class QualificationRevoke(BaseModel):
    """A reviewer withdraws an approved qualification (#198 B1)."""

    model_config = ConfigDict(extra="forbid")

    reason: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=1,
            max_length=REVOCATION_REASON_MAX_LENGTH,
        ),
    ] = Field(description="Why the qualification is revoked; the tutor sees it.")
    version: int = Field(ge=1, description="The version the caller loaded.")
