"""Student and guardian request and response bodies (#33, #34; the public
schemas of the module).

Request bodies forbid unknown fields (``422``). Strings are trimmed; for an
optional string, ``""`` becomes ``null``. In ``StudentUpdate`` a field that is
not sent stays unchanged, ``null`` clears ``email``, ``phone``,
``school_name``, ``school_grade`` and ``education_level_id``, and ``null`` for
any other field is ``422``.

``StudentDetail.personal`` holds the birthdate, the contact details and the
emergency contacts. It is ``null`` for a viewer with limited access (owner
decision #33 E2 (a)); list items never carry it. ``StudentDetail.guardians``
is ``null`` for viewers without a staff role and ``guardian.view`` (#34 E1).

Family bodies (#172) follow them too: ``name`` is trimmed (1-200 characters,
never blank) and ``null`` for ``name`` or ``is_active`` is ``422``.

Guardian bodies (#34) follow the same rules: in ``GuardianUpdate`` ``null``
clears ``email``, ``phone`` and ``user_id``, and is ``422`` for a name or
``is_active``.
"""

import re
import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

from app.modules.catalog.schemas import EducationLevelReference
from app.modules.families.models import (
    CONTACT_NAME_MAX_LENGTH,
    EARLIEST_BIRTHDATE,
    EMAIL_MAX_LENGTH,
    FAMILY_NAME_MAX_LENGTH,
    MAX_CONTACTS,
    NAME_MAX_LENGTH,
    PHONE_MAX_LENGTH,
    RELATIONSHIP_MAX_LENGTH,
    SCHOOL_GRADE_MAX_LENGTH,
    SCHOOL_NAME_MAX_LENGTH,
)
from app.modules.identity.schemas import UserState

# The #31 F1 contact-field patterns (``models.EMAIL_PATTERN`` /
# ``PHONE_PATTERN``; PostgreSQL ``[:space:]`` is ``\s`` here), the same as
# ``app.modules.identity.schemas`` (a unit test keeps them equal).
EMAIL_REGEX = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
PHONE_REGEX = r"^\+?[0-9][0-9 ().-]{5,30}[0-9]$"

SEARCH_MAX_LENGTH = 100
SEARCH_MAX_TERMS = 5

# The status filter of the list: ``active`` (the default), ``inactive`` or
# ``all``.
type StudentStatus = Literal["active", "inactive", "all"]


def _blank_to_none(value: Any) -> Any:
    """Trim an optional string; an empty one clears the field (``None``)."""
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


PersonName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=NAME_MAX_LENGTH),
]
"""Given names or family names: trimmed, 1-100 characters, never blank."""
ContactName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=CONTACT_NAME_MAX_LENGTH
    ),
]
Relationship = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=RELATIONSHIP_MAX_LENGTH
    ),
]
ContactPhoneRequired = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, max_length=PHONE_MAX_LENGTH, pattern=PHONE_REGEX
    ),
]
OptionalEmail = Annotated[
    Annotated[str, StringConstraints(max_length=EMAIL_MAX_LENGTH, pattern=EMAIL_REGEX)]
    | None,
    BeforeValidator(_blank_to_none),
]
OptionalPhone = Annotated[
    Annotated[str, StringConstraints(max_length=PHONE_MAX_LENGTH, pattern=PHONE_REGEX)]
    | None,
    BeforeValidator(_blank_to_none),
]
SchoolName = Annotated[
    Annotated[str, StringConstraints(max_length=SCHOOL_NAME_MAX_LENGTH)] | None,
    BeforeValidator(_blank_to_none),
]
SchoolGrade = Annotated[
    Annotated[str, StringConstraints(max_length=SCHOOL_GRADE_MAX_LENGTH)] | None,
    BeforeValidator(_blank_to_none),
]
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _iso_date_text(value: Any) -> Any:
    # Only the YYYY-MM-DD form (no number, no time part); a date object from
    # Python code passes through.
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or ISO_DATE.fullmatch(value) is None:
        raise ValueError("Use the YYYY-MM-DD form.")
    return value


Birthdate = Annotated[
    date,
    BeforeValidator(_iso_date_text),
    Field(
        ge=EARLIEST_BIRTHDATE,
        description=(
            "YYYY-MM-DD, from 1900-01-01 up to today in the center's time zone."
        ),
    ),
]


def _not_null[T](value: T | None) -> T:
    # Runs only when the field is sent: null never clears a required field.
    if value is None:
        raise ValueError("This field cannot be null.")
    return value


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmergencyContactCreate(_Body):
    """A new emergency contact. Its place in the list is its ``position``."""

    name: ContactName
    relationship: Relationship = Field(
        description="Free text, for example Madre or Vecina."
    )
    phone: ContactPhoneRequired


class EmergencyContactUpdate(EmergencyContactCreate):
    """An emergency contact in a ``PATCH``: with the ``id`` of one of this
    student's contacts to keep (and update) it, without one to add it."""

    id: uuid.UUID | None = None


EmergencyContacts = Annotated[
    list[EmergencyContactCreate], Field(min_length=1, max_length=MAX_CONTACTS)
]
EmergencyContactsUpdate = Annotated[
    list[EmergencyContactUpdate], Field(min_length=1, max_length=MAX_CONTACTS)
]


class StudentCreate(_Body):
    """A new student profile (``POST /api/v1/students``). It starts active.
    The list order of ``emergency_contacts`` (1 to 5) is their order."""

    given_names: PersonName
    family_names: PersonName
    birthdate: Birthdate
    email: OptionalEmail = None
    phone: OptionalPhone = None
    school_name: SchoolName = None
    school_grade: SchoolGrade = Field(
        default=None, description="Free text, for example 2.º de secundaria."
    )
    education_level_id: uuid.UUID | None = Field(
        default=None, description="An active level of the catalog."
    )
    emergency_contacts: EmergencyContacts


class StudentUpdate(_Body):
    """A change to a profile (``PATCH /api/v1/students/{student_id}``) with
    the ``version`` the caller loaded. ``emergency_contacts``, when sent, is
    the complete new list (contacts left out are removed)."""

    version: int = Field(ge=1)
    given_names: PersonName | None = None
    family_names: PersonName | None = None
    birthdate: Birthdate | None = None
    email: OptionalEmail = None
    phone: OptionalPhone = None
    school_name: SchoolName = None
    school_grade: SchoolGrade = None
    education_level_id: uuid.UUID | None = None
    is_active: bool | None = None
    emergency_contacts: EmergencyContactsUpdate | None = None

    _fields_not_null = field_validator(
        "given_names",
        "family_names",
        "birthdate",
        "is_active",
        "emergency_contacts",
    )(_not_null)


class EmergencyContact(BaseModel):
    """One emergency contact; ``position`` 1 is called first."""

    id: uuid.UUID
    name: str
    relationship: str
    phone: str
    position: int


class StudentPersonal(BaseModel):
    """The personal part of a profile, only for full-access viewers."""

    birthdate: date
    email: str | None
    phone: str | None
    emergency_contacts: list[EmergencyContact] = Field(description="In position order.")


class StudentListItem(BaseModel):
    """One student in the list. No birthdate or contact data (spec §16)."""

    id: uuid.UUID
    given_names: str
    family_names: str
    is_active: bool
    is_minor: bool = Field(
        description="Under 18 today in the center's time zone (ADR-0007 D17)."
    )
    school_grade: str | None
    education_level: EducationLevelReference | None


type GuardianRelationship = Literal[
    "mother",
    "father",
    "grandparent",
    "stepparent",
    "sibling",
    "other_relative",
    "legal_guardian",
    "other",
]
"""A link's relationship code (``constants.GUARDIAN_RELATIONSHIPS``; a unit
test keeps both equal)."""


class StudentGuardianItem(BaseModel):
    """A guardian linked to a student, as staff see it on the student."""

    guardian_id: uuid.UUID
    given_names: str
    family_names: str
    relationship: GuardianRelationship
    phone: str | None
    is_active: bool
    has_account: bool = Field(description="The profile is linked to a login.")


class FamilyReference(BaseModel):
    """The family a student or guardian belongs to (#172 E1)."""

    id: uuid.UUID
    name: str


FAMILY_FIELD_DESCRIPTION = (
    "The family this profile belongs to (#172); null when it belongs to none"
    " or the viewer may not see it."
)


class StudentLogin(BaseModel):
    """The student's login account, shown only to ``family.manage`` holders
    (#35 C3)."""

    user_id: uuid.UUID
    username: str
    display_name: str
    state: UserState


class StudentDetail(BaseModel):
    """One student profile. ``personal`` is ``null`` for a viewer with
    limited access (Finance Staff, assigned tutors, a linked guardian of an
    adult student). ``guardians`` is ``null`` for viewers without a staff
    role and ``guardian.view``."""

    id: uuid.UUID
    given_names: str
    family_names: str
    is_active: bool
    is_minor: bool
    school_name: str | None
    school_grade: str | None
    education_level: EducationLevelReference | None = Field(
        description="Shown even when the level is inactive."
    )
    personal: StudentPersonal | None
    guardians: list[StudentGuardianItem] | None = Field(
        description=(
            "The linked guardians, by family names then given names; null"
            " unless the viewer holds a staff role and guardian.view."
        )
    )
    family: FamilyReference | None = Field(
        description=FAMILY_FIELD_DESCRIPTION
        + " Shown only to viewers with a staff role and guardian.view."
    )
    login: StudentLogin | None = Field(
        description=(
            "The linked login account (#35); null when none is linked or the"
            " viewer doesn't hold family.manage."
        ),
    )
    version: int = Field(description="Send it back with every change.")
    created_at: datetime
    updated_at: datetime


def search_terms(q: str) -> list[str]:
    """The whitespace-separated terms of a search."""
    return q.split()


def _at_most_five_terms(value: str) -> str:
    if len(search_terms(value)) > SEARCH_MAX_TERMS:
        raise ValueError(f"Use at most {SEARCH_MAX_TERMS} words.")
    return value


SearchText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=SEARCH_MAX_LENGTH
    ),
    AfterValidator(_at_most_five_terms),
]
"""A name search: trimmed, 1-100 characters, at most five words."""


# --- Guardian profiles and links (#34) -------------------------------------------

# The status filter of the guardians list.
type GuardianStatus = Literal["active", "inactive", "all"]


class GuardianCreate(_Body):
    """A new guardian profile (``POST /api/v1/guardians``). It starts active.
    ``user_id`` links a login account (one that holds the ``guardian`` role
    and isn't archived)."""

    given_names: PersonName
    family_names: PersonName
    email: OptionalEmail = None
    phone: OptionalPhone = None
    user_id: uuid.UUID | None = Field(
        default=None,
        description="An account with the guardian role that isn't archived.",
    )


class GuardianUpdate(_Body):
    """A change to a guardian profile (``PATCH /api/v1/guardians/{id}``) with
    the ``version`` the caller loaded. ``null`` clears ``email``, ``phone``
    and ``user_id``."""

    version: int = Field(ge=1)
    given_names: PersonName | None = None
    family_names: PersonName | None = None
    email: OptionalEmail = None
    phone: OptionalPhone = None
    user_id: uuid.UUID | None = None
    is_active: bool | None = None

    _fields_not_null = field_validator("given_names", "family_names", "is_active")(
        _not_null
    )


class GuardianListItem(BaseModel):
    """One guardian in the list. No contact data (spec §16)."""

    id: uuid.UUID
    given_names: str
    family_names: str
    is_active: bool
    has_account: bool
    student_count: int = Field(description="The number of linked students.")


class GuardianAccount(BaseModel):
    """The linked login, shown only to ``family.manage`` holders."""

    id: uuid.UUID
    username: str
    display_name: str
    state: UserState


class GuardianStudentItem(BaseModel):
    """A student linked to the guardian."""

    student_id: uuid.UUID
    given_names: str
    family_names: str
    relationship: GuardianRelationship
    is_minor: bool
    is_active: bool


class GuardianDetail(BaseModel):
    """One guardian profile. ``account`` is ``null`` unless the viewer holds
    ``family.manage``."""

    id: uuid.UUID
    given_names: str
    family_names: str
    email: str | None
    phone: str | None
    is_active: bool
    has_account: bool
    account: GuardianAccount | None
    students: list[GuardianStudentItem] = Field(
        description="By family names, then given names."
    )
    family: FamilyReference | None = Field(description=FAMILY_FIELD_DESCRIPTION)
    version: int = Field(description="Send it back with every change.")
    created_at: datetime
    updated_at: datetime


class StudentGuardianCreate(_Body):
    """A link (``POST /api/v1/students/{student_id}/guardians``)."""

    guardian_id: uuid.UUID = Field(description="An active guardian profile.")
    relationship: GuardianRelationship


# --- Family groupings (#172) --------------------------------------------------------

# The status filter of the families list.
type FamilyStatus = Literal["active", "inactive", "all"]

FamilyName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=FAMILY_NAME_MAX_LENGTH
    ),
]
"""A family name: trimmed, 1-200 characters, never blank. Not unique."""


class FamilyCreate(_Body):
    """A new family (``POST /api/v1/families``): active, with no members."""

    name: FamilyName = Field(description="For example Familia García López.")


class FamilyUpdate(_Body):
    """A change to a family (``PATCH /api/v1/families/{id}``) with the
    ``version`` the caller loaded. ``is_active`` deactivates or reactivates;
    the members are kept."""

    version: int = Field(ge=1)
    name: FamilyName | None = None
    is_active: bool | None = None

    _fields_not_null = field_validator("name", "is_active")(_not_null)


class FamilyListItem(BaseModel):
    """One family in the list (no guardian count: a guardian viewer can't
    learn how many co-guardians exist)."""

    id: uuid.UUID
    name: str
    is_active: bool
    student_count: int = Field(description="The number of member students.")


class FamilyStudentItem(BaseModel):
    """A student of the family. Membership is not access: the student's own
    record still follows the viewer's links."""

    student_id: uuid.UUID
    given_names: str
    family_names: str
    is_minor: bool
    is_active: bool


class FamilyGuardianItem(BaseModel):
    """A guardian profile of the family."""

    guardian_id: uuid.UUID
    given_names: str
    family_names: str
    phone: str | None
    has_account: bool
    is_active: bool


class FamilyDetail(BaseModel):
    """One family with its members, each list by family names then given
    names. A guardian viewer sees only their own profile in ``guardians``."""

    id: uuid.UUID
    name: str
    is_active: bool
    students: list[FamilyStudentItem]
    guardians: list[FamilyGuardianItem] = Field(
        description=(
            "Every guardian of the family for a viewer with a staff role; only"
            " their own profile for a guardian."
        )
    )
    version: int = Field(description="Send it back with every change.")
    created_at: datetime
    updated_at: datetime


# --- Student logins and consent records (#35) -------------------------------------


class StudentAccountUpdate(_Body):
    """Link a login account to the profile, or ``null`` to unlink it (``PUT
    /api/v1/students/{student_id}/account``), with the ``version`` the caller
    loaded."""

    user_id: uuid.UUID | None = Field(
        description=(
            "An account that isn't archived and isn't linked to another"
            " student, or null to unlink."
        )
    )
    version: int = Field(ge=1)


type ConsentScope = Literal["student_login", "guardian_academic_access"]
"""``constants.CONSENT_SCOPES`` (a unit test keeps both equal)."""

type ConsentMethod = Literal["in_app", "paper", "in_person"]
"""``constants.CONSENT_METHODS`` (a unit test keeps both equal)."""

type RevocationCause = Literal["revoked", "guardian_unlinked"]
"""``constants.REVOCATION_CAUSES`` (a unit test keeps both equal)."""


class ConsentCreate(_Body):
    """A consent (``POST /api/v1/students/{student_id}/consents``)."""

    scope: ConsentScope = Field(
        description=(
            "student_login (a guardian allows a minor's login) or"
            " guardian_academic_access (an adult student shares academic"
            " records with this guardian)."
        )
    )
    guardian_id: uuid.UUID = Field(
        description=(
            "For student_login, the guardian who consents; for"
            " guardian_academic_access, the guardian who receives access. An"
            " active guardian linked to the student."
        )
    )
    method: ConsentMethod = Field(
        description="Staff: paper or in_person. A guardian or a student: in_app."
    )


class ConsentRecordItem(BaseModel):
    """One consent record as the viewer sees it."""

    id: uuid.UUID
    scope: ConsentScope
    guardian_id: uuid.UUID
    guardian_given_names: str
    guardian_family_names: str
    method: ConsentMethod
    notice_version: int = Field(
        description="The privacy-notice version current when it was given."
    )
    granted_at: datetime
    revoked_at: datetime | None
    revocation_cause: RevocationCause | None
    can_revoke: bool = Field(description="The viewer may revoke it now.")
    suspends_login_on_revoke: bool = Field(
        description=(
            "Revoking it would suspend the student's login (the last valid"
            " student_login consent of a minor with an active login)."
        )
    )


class ConsentGuardianOption(BaseModel):
    """A linked guardian the viewer may name in a consent."""

    guardian_id: uuid.UUID
    given_names: str
    family_names: str
    relationship: GuardianRelationship
    is_active: bool


class ConsentOverview(BaseModel):
    """A student's consents as the viewer may see them (``GET
    /api/v1/students/{student_id}/consents``)."""

    student_id: uuid.UUID
    is_minor: bool
    login_state: UserState | None = Field(
        description="The linked login's state, or null when none is linked."
    )
    current_notice_version: int = Field(
        description="The privacy-notice version a new consent records."
    )
    guardians: list[ConsentGuardianOption] = Field(
        description=(
            "Staff: every linked guardian; a guardian: themselves; a student:"
            " their own linked guardians."
        )
    )
    consents: list[ConsentRecordItem] = Field(
        description="Active records first, then by granted_at, newest first."
    )
    can_record: list[ConsentScope] = Field(
        description="The scopes the viewer may record now (by role and age)."
    )


class ConsentRevocation(BaseModel):
    """The revoked record (``POST …/consents/{consent_id}/revoke``)."""

    consent: ConsentRecordItem
    student_login_suspended: bool = Field(
        description="The revocation suspended the student's login."
    )
