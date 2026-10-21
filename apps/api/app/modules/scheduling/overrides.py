"""Overrides of soft scheduling conflicts (#49 C, D; spec §3, W1.6; ADR-0005
"Conflict wire format and overrides").

A caller who holds ``schedule.override`` resends a write with
``override: {codes, reason}``
(:class:`~app.modules.scheduling.schemas.SessionOverride`).
The server then **recomputes** every conflict inside the write's
transaction, under the student locks, and decides with
:func:`evaluate_override` (pure):

- **any hard conflict**: refused (``409 scheduling_conflict`` with the
  complete sorted list, hard and soft). A hard code is never overridden; one
  inside ``codes`` is already ``422 code_not_overridable`` (the schema);
- **a soft conflict whose code is not listed**: refused the same way;
- **every conflict soft and listed**: allowed. The overridden conflicts are
  the computed soft conflicts (code and details), not the client's list;
- **listed codes that don't occur** are ignored (#49 E1): never stored or
  audited;
- **no conflict at all**: allowed with nothing overridden, so no override
  row and no override event.

Without an ``override`` the same function allows only a write with no
conflict. The ``403`` for a caller without ``schedule.override`` comes
before anything is read or locked (the route checks it).

When the write is allowed and something was overridden, the caller writes
the row and the event with :func:`record_session_override`, in the same
transaction, after the write itself: one ``class_session_overrides`` row
(append-only) and one named audit event ``scheduling.session_override``
(``details``: ``override_id``, ``codes``, ``conflicts``, ``reason``). No
notification is sent.

**Every later write path that runs the conflict checks** (#53 series,
#56 / #65 enrollments, #58 reschedule, #59 / #60 requests) uses these two
functions, so an override means the same thing everywhere.

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit_event
from app.modules.scheduling.conflicts import Conflict
from app.modules.scheduling.models import ClassSessionOverride
from app.modules.scheduling.schemas import SessionOverride

OVERRIDE_EVENT = "scheduling.session_override"


@dataclass(frozen=True)
class OverrideDecision:
    """``allowed``: the write may go ahead. ``overridden``: the computed soft
    conflicts it overrides (empty when there was no conflict)."""

    allowed: bool
    overridden: tuple[Conflict, ...] = ()


def evaluate_override(
    conflicts: Sequence[Conflict], override: SessionOverride | None
) -> OverrideDecision:
    """The decision for the conflicts just computed (see the module
    docstring). Pure."""
    if not conflicts:
        return OverrideDecision(allowed=True)
    if override is None or any(c.severity == "hard" for c in conflicts):
        return OverrideDecision(allowed=False)
    listed = set(override.codes)
    if any(conflict.code not in listed for conflict in conflicts):
        return OverrideDecision(allowed=False)
    return OverrideDecision(allowed=True, overridden=tuple(conflicts))


def overridden_codes(conflicts: Sequence[Conflict]) -> list[str]:
    """The codes of ``conflicts`` once each, in their (rule) order."""
    return list(dict.fromkeys(conflict.code for conflict in conflicts))


async def record_session_override(
    session: AsyncSession,
    class_session_id: uuid.UUID,
    conflicts: Sequence[Conflict],
    reason: str,
    *,
    actor_id: uuid.UUID,
) -> ClassSessionOverride:
    """Store the override of ``conflicts`` (the computed, sorted soft
    conflicts) on the session and write its audit event, in the caller's
    transaction. ``reason`` is the trimmed reason."""
    if not conflicts or any(c.severity != "soft" for c in conflicts):
        raise ValueError("only soft conflicts are overridden")
    codes = overridden_codes(conflicts)
    wire = [{"code": c.code, "details": c.details} for c in conflicts]
    row = ClassSessionOverride(
        id=uuid.uuid7(),
        class_session_id=class_session_id,
        codes=codes,
        conflicts=wire,
        reason=reason,
        actor_user_id=actor_id,
    )
    session.add(row)
    await session.flush()
    record_audit_event(
        session,
        action=OVERRIDE_EVENT,
        record_type="class_sessions",
        record_id=class_session_id,
        details={
            "override_id": row.id,
            "codes": codes,
            "conflicts": wire,
            "reason": reason,
        },
    )
    return row
