"""Audit log response bodies."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class AuditChange(BaseModel):
    """One column's value before and after the change (null when there is none)."""

    old: Any
    new: Any


class AuditEventListItem(BaseModel):
    """One audit event; the actor and the record are given by id."""

    id: uuid.UUID
    occurred_at: datetime
    actor_id: uuid.UUID | None
    action: str
    record_type: str | None
    record_id: uuid.UUID | None
    changes: dict[str, AuditChange] | None
    details: dict[str, Any] | None
    request_id: str | None
