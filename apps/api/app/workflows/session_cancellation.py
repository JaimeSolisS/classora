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
- **Tutor pay stays out of this seam** (ADR-0017 E9). Payroll derives pay from
  committed state in ``recalculate``
  (:mod:`app.workflows.payroll_calculation`).

``initiated_by`` is ``"center"`` for an administrator's cancel (#55) and
``"family"`` / ``"tutor"`` for an approved request (#62, M4-17), which also
passes its ``change_request_id`` (``None`` otherwise). Payroll consumes the
stored columns of the approved request through the batch reader
``scheduling.service.payable_cancellations`` (the same ones
``get_change_request_outcome`` returns) and never classifies a cancellation
again.
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
    """No step: billing runs in the #99 job, and payroll reads committed state
    in ``recalculate``."""
    del session, class_session_id, initiated_by, cancelled_at, by_user_id
    del change_request_id
