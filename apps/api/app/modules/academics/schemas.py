"""Session closeout bodies owned by academics (#77 C2, D1; #78 C1-C3, D1)
and the session note bodies and views (#79 C1, C3, D1, F3).

Closed allow-lists (``extra="forbid"``). The summary fields are trimmed;
whitespace-only counts as empty (``None``); over 2000 characters after
trimming is ``422 too_long``. Whether a field is required depends on the
attendance (E4), so that check runs after the session lock
(:func:`~app.modules.academics.closeout_rules.summary_errors`).
"""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from app.modules.academics import note_rules
from app.modules.academics.closeout_rules import (
    ACTUAL_MINUTES_RANGE,
    REASON_MAX,
    SUMMARY_MAX,
    clean_text,
)


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _trimmed(value: Any) -> Any:
    return clean_text(value) if isinstance(value, str) else value


type SummaryText = Annotated[str | None, BeforeValidator(_trimmed)]


class CloseoutSummaryInput(_Closed):
    """The structured summary as the tutor submits it. `topics`,
    `performance` and `next_steps` are required unless every participant is
    marked absent; `homework` is optional. Each field is trimmed, at most
    2000 characters."""

    topics: SummaryText = None
    performance: SummaryText = None
    homework: SummaryText = None
    next_steps: SummaryText = None

    @field_validator("topics", "performance", "homework", "next_steps")
    @classmethod
    def _not_too_long(cls, value: str | None) -> str | None:
        if value is not None and len(value) > SUMMARY_MAX:
            raise PydanticCustomError(
                "too_long", "At most {max} characters.", {"max": SUMMARY_MAX}
            )
        return value


class CloseoutSummary(_Closed):
    """The structured summary as stored (`null`: left empty)."""

    topics: str | None = Field(description="Topics covered.")
    performance: str | None = Field(description="How the class went.")
    homework: str | None = Field(description="Homework set (optional).")
    next_steps: str | None = Field(description="Next steps.")


# --- Review (#78) -----------------------------------------------------------------


def _too_long(reason: str) -> None:
    if len(reason) > REASON_MAX:
        raise PydanticCustomError(
            "reason_too_long",
            "The reason is at most {max} characters.",
            {"max": REASON_MAX},
        )


def _required_reason(value: Any) -> Any:
    if value is None:
        raise PydanticCustomError("reason_required", "Give a reason.")
    if not isinstance(value, str):
        return value
    reason = value.strip()
    if not reason:
        raise PydanticCustomError("reason_required", "Give a reason.")
    _too_long(reason)
    return reason


def _optional_reason(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    reason = value.strip()
    if not reason:
        return None
    _too_long(reason)
    return reason


class _WithRequiredReason(_Closed):
    @model_validator(mode="before")
    @classmethod
    def _missing_reason(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return {"reason": None} | data
        return data

    _reason = field_validator("reason", mode="before", check_fields=False)(
        _required_reason
    )


class CloseoutApprove(_Closed):
    """`POST /api/v1/class-sessions/{id}/closeout/approve` (#78 C1)."""

    version: int = Field(description="The closeout's version, as last read.")


class CloseoutRequestChanges(_WithRequiredReason):
    """`POST /api/v1/class-sessions/{id}/closeout/request-changes` (#78 C2):
    why the tutor must change it (trimmed, 1-1000 characters:
    reason_required / reason_too_long)."""

    version: int = Field(description="The closeout's version, as last read.")
    reason: str = Field(description="What to change: 1-1000 characters, trimmed.")


class CloseoutCorrection(_WithRequiredReason):
    """`POST /api/v1/class-sessions/{id}/closeout/corrections` (#78 C3): the
    members given change (`actual_minutes` 0-720; a `summary` member set to
    `null` clears it), with a reason (trimmed, 1-1000 characters). On an
    approved closeout the correction is a new approval."""

    version: int = Field(description="The closeout's version, as last read.")
    actual_minutes: int | None = Field(
        default=None,
        ge=ACTUAL_MINUTES_RANGE[0],
        le=ACTUAL_MINUTES_RANGE[1],
        description="The corrected teaching minutes, 0-720 (left out: unchanged).",
    )
    summary: CloseoutSummaryInput | None = Field(
        default=None,
        description="The summary members to change (left out: unchanged).",
    )
    reason: str = Field(description="Why: 1-1000 characters, trimmed.")


class CloseoutResubmit(_Closed):
    """`PUT /api/v1/class-sessions/{id}/closeout` (#78 D1): the full
    replacement of the minutes and the summary, and a reason (trimmed, at
    most 1000 characters; required to request a correction of an approved
    closeout)."""

    version: int = Field(description="The closeout's version, as last read.")
    actual_minutes: int = Field(
        ge=ACTUAL_MINUTES_RANGE[0],
        le=ACTUAL_MINUTES_RANGE[1],
        description="Actual teaching minutes, 0-720.",
    )
    summary: CloseoutSummaryInput
    reason: Annotated[str | None, BeforeValidator(_optional_reason)] = Field(
        default=None,
        description="A note for the reviewer; required for a correction request.",
    )


# --- On behalf of the tutor (#301 C1, D1) -------------------------------------------


class _OnBehalf(_WithRequiredReason):
    actual_minutes: int = Field(
        ge=ACTUAL_MINUTES_RANGE[0],
        le=ACTUAL_MINUTES_RANGE[1],
        description="Actual teaching minutes, 0-720.",
    )
    summary: CloseoutSummaryInput
    reason: str = Field(
        description="Why you close it out for the tutor: 1-1000 characters,"
        " trimmed (reason_required / reason_too_long)."
    )


class CloseoutOnBehalfSubmit(_OnBehalf):
    """`POST /api/v1/class-sessions/{id}/closeout/on-behalf` (#301 C1): a
    reviewer closes out an ended class for its tutor. The #77 rules apply
    (every participant's attendance marked first; the summary required
    unless everyone was absent); the closeout stays the tutor's."""

    version: int = Field(description="The session's version, as last read.")


class CloseoutTakeOver(_OnBehalf):
    """`PUT /api/v1/class-sessions/{id}/closeout/on-behalf` (#301 D1): a
    reviewer resubmits a closeout sent back to its tutor (the full
    replacement of the minutes and the summary), with a reason."""

    version: int = Field(description="The closeout's version, as last read.")


# --- Session notes (#79 C1, C3, D1, F3) ---------------------------------------------


def _note_body(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    body = value.strip()
    if not body:
        raise PydanticCustomError("body_required", "Write the note.")
    if len(body) > note_rules.BODY_MAX:
        raise PydanticCustomError(
            "too_long", "At most {max} characters.", {"max": note_rules.BODY_MAX}
        )
    return body


def _unpublish_reason(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    reason = note_rules.clean(value)
    if reason is not None and len(reason) > note_rules.UNPUBLISH_REASON_MAX:
        raise PydanticCustomError(
            "reason_too_long",
            "The reason is at most {max} characters.",
            {"max": note_rules.UNPUBLISH_REASON_MAX},
        )
    return reason


type NoteBody = Annotated[str, BeforeValidator(_note_body)]
type NoteVisibility = Literal["internal", "family"]


class SessionNoteCreate(_Closed):
    """`POST /api/v1/class-sessions/{id}/notes` (#79 C1): a note about one
    participant of the session. It is always created internal: families
    never see it until you share it."""

    student_id: uuid.UUID = Field(description="A participant of the session.")
    body: NoteBody = Field(
        description="The note: plain text, trimmed, 1-4000 characters"
        " (body_required / too_long)."
    )


class SessionNoteEdit(_Closed):
    """`PATCH /api/v1/notes/{id}` (#79 D2): the new text of an internal
    note."""

    version: int = Field(description="The note's version, as last read.")
    body: NoteBody = Field(
        description="The note: plain text, trimmed, 1-4000 characters."
    )


class SessionNoteVersion(_Closed):
    """`POST /api/v1/notes/{id}/publish` (#79 D3)."""

    version: int = Field(description="The note's version, as last read.")


class SessionNoteUnpublish(_Closed):
    """`POST /api/v1/notes/{id}/unpublish` (#79 D4): a reason (trimmed, at
    most 500 characters) is required from anyone but the author."""

    version: int = Field(description="The note's version, as last read.")
    reason: Annotated[str | None, BeforeValidator(_unpublish_reason)] = Field(
        default=None,
        description="Why it stops being shared: 1-500 characters, trimmed;"
        " required unless you wrote the note (reason_required /"
        " reason_too_long).",
    )


class NotePerson(_Closed):
    display_name: str


class NoteSubject(_Closed):
    id: uuid.UUID
    name: str


class NoteStudent(_Closed):
    id: uuid.UUID
    display_name: str


class SessionNoteView(_Closed):
    """A tutor's note about one student in one session (#79 C3), for staff
    with academic_note.review and tutors. `visibility`: `internal` notes are
    never shown to families; `family` notes are shared with the student and
    their guardians with academic access. The `can_*` flags say what you may
    do now."""

    id: uuid.UUID
    version: int = Field(description="The note's version (send it back).")
    class_session_id: uuid.UUID
    session_starts_at: datetime
    subject: NoteSubject
    student: NoteStudent
    author: NotePerson
    is_own: bool = Field(description="You wrote it.")
    body: str = Field(description="Plain text.")
    visibility: NoteVisibility
    published_at: datetime | None
    published_by: NotePerson | None
    unpublished_at: datetime | None = Field(
        description="When it last stopped being shared (cleared when shared again)."
    )
    unpublish_reason: str | None
    edited: bool = Field(description="The text changed after it was written.")
    created_at: datetime
    can_edit: bool
    can_publish: bool
    can_unpublish: bool
    can_delete: bool


class SessionNoteList(_Closed):
    """`GET /api/v1/class-sessions/{id}/notes` (#79 E1): oldest first."""

    items: list[SessionNoteView]


class FamilyLearningNote(_Closed):
    """A note the tutor shared with the family (#79 F3): a closed
    allow-list (no author id, no internal fields, no other students)."""

    id: uuid.UUID
    class_session_id: uuid.UUID
    session_starts_at: datetime
    subject_name: str
    author: NotePerson
    body: str = Field(description="Plain text.")
    published_at: datetime
