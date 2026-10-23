"""The cross-module steps of a session moved at a family's or a tutor's
request (#62 C2 step 12; ADR-0002; architecture §4.2).

``app.workflows.change_request_decision`` calls :func:`on_session_rescheduled`
once per approved reschedule request, after the move and in the same Unit
of Work (there is no event bus). Today it is a **no-op**. The steps to add
here:

- **M6-14** (billing adjustment): a rescheduled session **keeps its charge**
  (spec §3); read the request's effective outcome with
  ``scheduling.service.get_change_request_outcome(session,
  change_request_id)`` if a fee applies;
- **M7-04** (tutor pay): apply the reschedule pay rule to the session's
  tutor.

Neither step stores money here. A staff move (``PATCH
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
    """Run the billing and pay steps of one rescheduled session (none yet)."""
    del session, class_session_id, previous_starts_at, previous_ends_at
    del initiated_by, change_request_id, by_user_id
