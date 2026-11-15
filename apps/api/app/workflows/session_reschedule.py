"""The cross-module steps of a session moved at a family's or a tutor's
request (#62 C2 step 12; ADR-0002; architecture §4.2).

``app.workflows.change_request_decision`` calls :func:`on_session_rescheduled`
once per approved reschedule request, after the move and in the same Unit
of Work (there is no event bus). Today it is a **no-op**.

- **Billing (M6-14, #99) is not done here.** A rescheduled session **keeps
  its charge** (spec §3): same ``service_date``, amount and minutes. A move
  that changes the minutes is repriced at the original service date by the
  job ``billing:reconcile_session_charges``
  (:mod:`app.workflows.schedule_reconciliation`), outside this Unit of
  Work; the approval route queues it after its commit.
- **Tutor pay stays out of this seam** (ADR-0017 E4, E9): a reschedule has no
  pay effect, and payroll derives pay from committed state in ``recalculate``
  (:mod:`app.workflows.payroll_calculation`).

No money is stored here. A staff move (``PATCH
/api/v1/class-sessions/{id}``) doesn't call it.
"""

import uuid
from datetime import datetime
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession


async def on_session_rescheduled(
    session: AsyncSession,
    *,
    class_session_id: uuid.UUID,
    previous_starts_at: datetime,
    previous_ends_at: datetime,
    initiated_by: Literal["family", "tutor"],
    change_request_id: uuid.UUID | None,
    by_user_id: uuid.UUID | None,
) -> None:
    """No step: billing runs in the #99 job, and a reschedule has no pay
    effect."""
    del session, class_session_id, previous_starts_at, previous_ends_at
    del initiated_by, change_request_id, by_user_id
