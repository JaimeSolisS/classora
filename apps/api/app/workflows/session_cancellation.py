"""The cross-module steps of a session cancellation (#55 C5, E3; ADR-0002).

``scheduling.service.cancel_class_session`` calls
:func:`on_session_cancelled` once per cancelled session, after the
transition and in the same transaction (registered by ``app.wiring`` with
``scheduling.service.register_cancellation_step``; ``scheduling`` never
imports this package).

Today it is a **no-op**: a center cancellation stores no policy
classification (#55 E3).

- **Billing (M6-14, #99) is not done here.** The job
  ``billing:reconcile_session_charges``
  (:mod:`app.workflows.schedule_reconciliation`) reads the committed
  cancellation (``scheduling.service.cancellation_classification``) and
  credits the charge or lets it stand by the cancellation billing rules,
  outside the scheduling transaction (#99 Q1: a billing failure never rolls
  a cancellation back). The cancel and change-request approval routes queue
  it after their commit.
- **M7-04** (tutor pay) adds its step here: apply the cancellation pay rule
  to the tutor of the session.

``initiated_by`` is ``"center"`` for an administrator's cancel (#55) and
``"family"`` / ``"tutor"`` for an approved request (#62, M4-17), which also
passes its ``change_request_id`` (``None`` otherwise). M7-04 reads
the request's effective outcome (classification, makeup) with
``scheduling.service.get_change_request_outcome(session, change_request_id)``
instead of classifying the cancellation again.
"""

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.scheduling.service import CancellationInitiator


async def on_session_cancelled(
    session: AsyncSession,
    *,
    class_session_id: uuid.UUID,
    initiated_by: CancellationInitiator,
    cancelled_at: datetime,
    by_user_id: uuid.UUID | None,
    change_request_id: uuid.UUID | None = None,
) -> None:
    """Run the pay steps of one cancelled session (none yet; billing runs in
    the #99 job)."""
    del session, class_session_id, initiated_by, cancelled_at, by_user_id
    del change_request_id
