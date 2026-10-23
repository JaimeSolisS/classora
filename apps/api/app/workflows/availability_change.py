"""The ``staff.availability_change`` approval type (M4-18 #63 D; spec §3:
"Changes affecting existing assignments require administrator approval").

Approving touches both ``staff`` (the availability) and ``scheduling`` (the
affected sessions, their offers and flags), and neither module imports the
other (ADR-0002), so the handler lives here and composes
``staff.service`` and ``scheduling.service``. ``app.wiring`` registers it.

Reviewers hold ``tutor.manage`` (Owners and Administrators, E4); requests
can't be sent back for changes; a rejection needs a reason. The decision
runs through the generic inbox routes, in the inbox's Unit of Work, after
the core locked the approval request (so the inbox decides it:
``inbox_decisions`` stays true).

**Approve** (D3), in this order:

1. the change request ``FOR UPDATE``: ``409 approval_transition_invalid``
   unless ``pending``;
2. the tutor: the availability settings row ``FOR UPDATE`` (weekly), then
   ``scheduling.service.lock_tutor`` (D5);
3. staleness: a weekly request whose ``base_version`` is not the current
   version, or a delete request whose exception is gone, is ``409
   availability_change_stale`` (the inbox item stays ``pending``);
4. the affected sessions are **recomputed** with the rules as they are now
   (the stored list is the reviewer's snapshot);
5. the change is applied by the same ``staff`` code as a direct change
   (same audit events; the actor is the approver);
6. every recomputed session is locked by ascending id and flagged: an
   ``offered`` one has its pending offer withdrawn and becomes
   ``unassigned``, a ``confirmed`` one keeps its state and tutor (E1);
7. the request ``approved``;
8. the tutor's ``staff.availability_change_approved`` (F1) and, when
   sessions were flagged, one ``scheduling.reassignment_needed`` to the
   ``schedule.assign`` holders (F2).

**Reject** (D4): the request ``FOR UPDATE`` → ``rejected`` with the reason
(copied from the inbox decision) → ``staff.availability_change_rejected``
to the tutor; nothing else changes.

**Lock order:** approval request (the core) → change request → settings row
→ tutor advisory lock → session rows by id → offer rows. The requester's
withdrawal (``staff.service.cancel_change_request``) takes the approval
request before the change request too, so the two never deadlock.
"""

import uuid
from collections.abc import Iterable, Mapping
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.authz import Principal
from app.modules.administration import service as administration
from app.modules.scheduling import service as scheduling
from app.modules.staff import service as staff

TUTOR_MANAGE: Final = "tutor.manage"
"""The identity catalog's review permission (repeated here: ADR-0002)."""


class AvailabilityChangeHandler:
    """The :class:`~app.modules.administration.service.ApprovalHandler` of
    availability change requests; the subject is the change request."""

    request_type: Final = staff.AVAILABILITY_CHANGE_TYPE
    review_permission: Final = TUTOR_MANAGE
    supports_request_changes: Final = False
    reason_required_on_reject: Final = True

    async def _pending(
        self, session: AsyncSession, request: administration.ApprovalRequestView
    ) -> staff.TutorAvailabilityChangeRequest:
        row = await staff.lock_change_request(session, request.subject_id)
        if row is None or row.status != staff.CHANGE_PENDING:
            raise administration.ApprovalTransitionInvalidError()
        return row

    async def approve(
        self,
        session: AsyncSession,
        request: administration.ApprovalRequestView,
        principal: Principal,
    ) -> None:
        row = await self._pending(session, request)
        await staff.lock_availability(session, row)
        await scheduling.lock_tutor(session, row.tutor_user_id)
        plan = await staff.plan_change(session, row)
        affected = await scheduling.affected_tutor_sessions(
            session,
            row.tutor_user_id,
            current=plan.current,
            proposed=plan.proposed,
            now=clock.now(),
            dates=plan.dates,
        )
        await staff.apply_change(session, row)
        flagged = await scheduling.flag_sessions_for_reassignment(
            session,
            tutor_user_id=row.tutor_user_id,
            class_session_ids=[item.id for item in affected],
            source_id=row.id,
            actor_id=principal.user_id,
        )
        staff.finish_change_request(row, staff.CHANGE_APPROVED)
        await session.flush()
        await staff.notify_change_decided(session, row, affected_count=len(flagged))
        await scheduling.notify_reassignment_needed(
            session, tutor_user_id=row.tutor_user_id, flagged=flagged
        )

    async def reject(
        self,
        session: AsyncSession,
        request: administration.ApprovalRequestView,
        principal: Principal,
        reason: str | None,
    ) -> None:
        # The core requires the reason (reason_required_on_reject).
        row = await self._pending(session, request)
        staff.finish_change_request(row, staff.CHANGE_REJECTED, reason=reason)
        await session.flush()
        await staff.notify_change_decided(
            session, row, affected_count=len(row.affected_session_ids)
        )

    async def request_changes(
        self,
        session: AsyncSession,
        request: administration.ApprovalRequestView,
        principal: Principal,
        reason: str,
    ) -> None:
        # Never called: supports_request_changes is False.
        raise NotImplementedError

    async def summaries(
        self, session: AsyncSession, subject_ids: Iterable[uuid.UUID]
    ) -> Mapping[uuid.UUID, Mapping[str, Any]]:
        """D2: two statements per page."""
        return await staff.change_request_summaries(session, subject_ids)


AVAILABILITY_CHANGE_HANDLER: Final = AvailabilityChangeHandler()
