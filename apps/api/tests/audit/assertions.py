"""Reusable audit assertions (M2-04 G1).

Every issue that audits something asserts its events with these helpers
(``_docs/testing-guidelines.md``, "Audit"):

- :func:`audit_events_for` lists the matching events, oldest first;
- :func:`assert_no_secret_in_audit` fails if any audit row's ``changes`` or
  ``details`` contains one of the given strings (a password, a hash, a token).
"""

import uuid

from sqlalchemy import Text, cast, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditEvent


async def audit_events_for(
    session: AsyncSession,
    *,
    record_type: str | None = None,
    record_id: uuid.UUID | None = None,
    action: str | None = None,
) -> list[AuditEvent]:
    """The events matching every given filter, oldest first."""
    query = select(AuditEvent).order_by(AuditEvent.occurred_at, AuditEvent.id)
    if record_type is not None:
        query = query.where(AuditEvent.record_type == record_type)
    if record_id is not None:
        query = query.where(AuditEvent.record_id == record_id)
    if action is not None:
        query = query.where(AuditEvent.action == action)
    return list((await session.scalars(query)).all())


async def assert_no_secret_in_audit(session: AsyncSession, *secrets: str) -> None:
    """No audit row's ``changes`` or ``details`` text contains any of ``secrets``."""
    assert secrets, "name at least one secret"
    rows = await session.execute(
        select(
            AuditEvent.id,
            cast(AuditEvent.changes, Text),
            cast(AuditEvent.details, Text),
        )
    )
    for event_id, changes, details in rows:
        for secret in secrets:
            for name, text in (("changes", changes), ("details", details)):
                assert secret not in (text or ""), (
                    f"audit event {event_id}: {name} contains a secret"
                )
