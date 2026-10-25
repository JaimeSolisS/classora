"""Session closeout bodies owned by academics (#77 C2, D1).

Closed allow-lists (``extra="forbid"``). The summary fields are trimmed;
whitespace-only counts as empty (``None``); over 2000 characters after
trimming is ``422 too_long``. Whether a field is required depends on the
attendance (E4), so that check runs after the session lock
(:func:`~app.modules.academics.closeout_rules.summary_errors`).
"""

from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError

from app.modules.academics.closeout_rules import SUMMARY_MAX, clean_text


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
