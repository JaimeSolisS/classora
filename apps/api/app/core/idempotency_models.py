"""The ``idempotency_keys`` table (#88; ADR-0006 E6, ISSUE-F4).

One row per user, endpoint and ``Idempotency-Key``: the key a financial
``POST`` was first sent with, the hash of that request and the ``2xx``
response it got (``app.core.idempotency``). ``endpoint`` is the method and
the route's path template (``"POST /api/v1/billing/payments"``).
``request_hash`` is the raw 32-byte SHA-256 of the request.

``response_status`` and ``response_body`` are ``NULL`` only inside the
transaction that claims the key: the route stores its response in the same
transaction (``IdempotentRequest.respond``), and a route that forgets to is a
programming error that rolls the claim back, so a committed row always has a
status. ``response_body`` stays ``NULL`` for a ``204``.

Rows are transient: they expire 7 days after the claim and the hourly
``idempotency:purge_keys`` job (``app.core.idempotency_tasks``) deletes them.
``user_id`` is ``ON DELETE RESTRICT`` (no foreign key to ``users`` cascades;
users are archived, never deleted).

Not audited: the keys are bookkeeping of the request, and the business route
audits its own change.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    SmallInteger,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Model


class IdempotencyKey(Model):
    __tablename__ = "idempotency_keys"
    __table_args__ = (
        CheckConstraint(
            "char_length(endpoint) BETWEEN 1 AND 300", name="endpoint_length"
        ),
        CheckConstraint("key ~ '^[A-Za-z0-9_-]{16,128}$'", name="key_format"),
        CheckConstraint("octet_length(request_hash) = 32", name="request_hash_length"),
        CheckConstraint(
            "response_status BETWEEN 200 AND 299", name="response_status_2xx"
        ),
        UniqueConstraint(
            "user_id",
            "endpoint",
            "key",
            name="uq_idempotency_keys_user_endpoint_key",
        ),
        Index("ix_idempotency_keys_expires_at", "expires_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    endpoint: Mapped[str] = mapped_column(Text)
    key: Mapped[str] = mapped_column(Text)
    request_hash: Mapped[bytes] = mapped_column(LargeBinary)
    response_status: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    response_body: Mapped[Any | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
