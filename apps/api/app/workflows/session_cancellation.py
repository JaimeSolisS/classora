"""The cross-module steps of a session cancellation (#55 C5, E3; ADR-0002).

``scheduling.service.cancel_class_session`` calls
:func:`on_session_cancelled` once per cancelled session, after the
transition and in the same transaction (registered by ``app.wiring`` with
``scheduling.service.register_cancellation_step``; ``scheduling`` never
imports this package).

Today it is a **no-op**: a center cancellation stores no policy
classification (#55 E3). The steps to add here:

- **M6-14** (billing adjustment): evaluate the scheduling policy
  (``administration.service.classify_change``) for the cancellation and
  adjust the family's charges;
- **M7-04** (tutor pay): apply the cancellation pay rule to the tutor of
  the session.

``initiated_by`` is ``"center"`` for an administrator's cancel (#55) and
``"family"`` / ``"tutor"`` for an approved request (#62, M4-17).
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
) -> None:
    """Run the billing and pay steps of one cancelled session (none yet)."""
    del session, class_session_id, initiated_by, cancelled_at, by_user_id
