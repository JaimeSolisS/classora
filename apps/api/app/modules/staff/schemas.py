"""Tutor profile bodies (#43 C1-C3).

The contact fields are the account's (``users.email``/``phone``, #31) with
the #31 formats; ``display_name`` is the account's too. Only
``professional_experience`` is stored by ``staff``.
"""

import uuid
from typing import Annotated, Any, cast

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
from app.modules.staff.models import EXPERIENCE_MAX_LENGTH
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
