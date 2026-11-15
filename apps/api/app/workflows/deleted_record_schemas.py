"""Recently deleted record bodies and views (#129 B1, B2).

Every model is a closed allow-list (``extra="forbid"``). A list item names
the record and its student, never its body, narrative, score or feedback
(``tests/integration/test_deleted_records.py`` pins the fields).
"""

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.modules.academics.service import deleted_records

DeletedRecordKind = deleted_records.DeletedRecordKind


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DeletedRecordStudent(_Closed):
    """The record's student (an assignment's first recipient by name)."""

    id: uuid.UUID
    display_name: str


class DeletedRecordItem(_Closed):
    """One record the caller deleted and may still restore."""

    kind: DeletedRecordKind
    id: uuid.UUID
    version: int = Field(description="Send it back to restore the record.")
    student: DeletedRecordStudent
    title: str | None = Field(
        description="The assignment's or plan's title, or the evaluation"
        " template's name; null for notes and progress reports."
    )
    reference_date: date = Field(
        description="The note's class date (center time), the evaluation"
        " date, the plan's start, the due date, or the report's period start"
        " (its creation date for an on-demand report)."
    )
    deleted_at: datetime
    restorable_until: datetime = Field(
        description="After this instant the record can't be restored; a daily"
        " job then removes it for good."
    )


class DeletedRecordList(_Closed):
    """`GET /api/v1/deleted-records` (B1): newest deletion first."""

    items: list[DeletedRecordItem]
    has_more: bool = Field(description="Only the 100 most recent are listed.")


class DeletedRecordRestore(_Closed):
    """`POST /api/v1/deleted-records/{kind}/{id}/restore` (B2)."""

    version: int = Field(description="The record's version, as listed.")


class RestoredRecord(_Closed):
    """The restored record: live again, with its new version."""

    kind: DeletedRecordKind
    id: uuid.UUID
    version: int
