"""Session closeout bodies owned by academics (#77 C2, D1; #78 C1-C3, D1).

Closed allow-lists (``extra="forbid"``). The summary fields are trimmed;
whitespace-only counts as empty (``None``); over 2000 characters after
trimming is ``422 too_long``. Whether a field is required depends on the
attendance (E4), so that check runs after the session lock
(:func:`~app.modules.academics.closeout_rules.summary_errors`).
"""

from typing import Annotated, Any

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

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
