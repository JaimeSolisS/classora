"""The ``scheduling.change_request`` approval type (#62 C6; spec §3).

One inbox item per change request (the subject is the **request**), created
with it, the requester being the guardian, student or tutor who sent it
(``high`` priority when the session starts within 24 hours, E6). Reviewers
hold ``schedule.manage`` (Owners and Administrators); everyone else gets
``404`` for the item. Items can't be sent back for changes, and a rejection
needs a reason. The requester withdrawing the request cancels its item.

**Decisions go through the review routes (E3, the #60 E1 pattern):** staff
approve, approve as an exception or reject on
``/schedule/change-requests/<id>`` (``app.workflows.change_request_decision``):
the workflow decides the request first and then resolves this item through
``administration.service.decide_approval_request`` in the same Unit of Work.
So ``approve`` accepts only a request that is already ``approved``, and
``reject`` only one already ``rejected``; otherwise both raise
:class:`~app.modules.scheduling.errors.ChangeRequestReviewRouteRequiredError`
(``409 change_request_review_route_required`` with ``details.review_path``),
the item stays ``pending`` and the Unit of Work rolls back. The generic body
can't carry the exception or the room, and locking the request here (after
the core locked the item) would reverse the session → request → item lock
order, so the request is read without a lock. ``inbox_decisions = False``:
the inbox lists no approve/reject for these items, only the review link.

``summaries`` (three statements whatever the page size: the requests with
their sessions, the students and the subjects): ``{change_request_id, kind,
initiated_by, student_name, subject_name, education_level_name, starts_at,
proposed_starts_at, classification, status, class_session_id}``.
"""

import uuid
from collections.abc import Iterable, Mapping
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal
from app.modules.administration import service as administration
from app.modules.catalog import service as catalog
from app.modules.families import service as families
from app.modules.scheduling.change_request_review import review_path
from app.modules.scheduling.change_requests import CHANGE_REQUEST_TYPE, student_of
from app.modules.scheduling.errors import ChangeRequestReviewRouteRequiredError
from app.modules.scheduling.models import (
    CHANGE_REQUEST_APPROVED,
    CHANGE_REQUEST_REJECTED,
    ClassSession,
    ClassSessionParticipant,
    SessionChangeRequest,
)
from app.modules.scheduling.offer_records import rfc3339

SCHEDULE_MANAGE: Final = "schedule.manage"


async def _status(session: AsyncSession, change_request_id: uuid.UUID) -> str | None:
    # No lock (module docstring); fresh from the database or, inside the
    # decision workflow, the row it has just decided.
    found: str | None = await session.scalar(
        select(SessionChangeRequest.status)
        .where(SessionChangeRequest.id == change_request_id)
        .execution_options(populate_existing=True)
    )
    return found


class ChangeRequestApprovalHandler:
    """The :class:`~app.modules.administration.service.ApprovalHandler` of
    change requests; the subject is the request."""

    request_type: Final = CHANGE_REQUEST_TYPE
    review_permission: Final = SCHEDULE_MANAGE
    supports_request_changes: Final = False
    reason_required_on_reject: Final = True
    inbox_decisions: Final = False
    """E3: the inbox lists no approve/reject for these items; staff decide on
    the review page."""

    async def approve(
        self,
        session: AsyncSession,
        request: administration.ApprovalRequestView,
        principal: Principal,
    ) -> None:
        if await _status(session, request.subject_id) != CHANGE_REQUEST_APPROVED:
            raise ChangeRequestReviewRouteRequiredError(review_path(request.subject_id))

    async def reject(
        self,
        session: AsyncSession,
        request: administration.ApprovalRequestView,
        principal: Principal,
        reason: str | None,
    ) -> None:
        if await _status(session, request.subject_id) != CHANGE_REQUEST_REJECTED:
            raise ChangeRequestReviewRouteRequiredError(review_path(request.subject_id))

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
        """See the module docstring; three statements."""
        ids = set(subject_ids)
        if not ids:
            return {}
        participant = (
            select(ClassSessionParticipant.student_id)
            .where(ClassSessionParticipant.class_session_id == ClassSession.id)
            .order_by(ClassSessionParticipant.student_id)
            .limit(1)
            .correlate(ClassSession)
            .scalar_subquery()
        )
        rows = (
            await session.execute(
                select(SessionChangeRequest, ClassSession.subject_id, participant)
                .join(
                    ClassSession,
                    ClassSession.id == SessionChangeRequest.class_session_id,
                )
                .where(SessionChangeRequest.id.in_(ids))
            )
        ).all()
        if not rows:
            return {}
        students = await families.student_references(
            session,
            {
                found
                for request, _, participant_id in rows
                if (found := student_of(request, participant_id)) is not None
            },
        )
        subjects = await catalog.subject_references(
            session, {subject_id for _, subject_id, _ in rows}
        )
        summaries: dict[uuid.UUID, Mapping[str, Any]] = {}
        for request, subject_id, participant_id in rows:
            about = student_of(request, participant_id)
            student = None if about is None else students.get(about)
            subject = subjects.get(subject_id)
            summaries[request.id] = {
                "change_request_id": str(request.id),
                "kind": request.kind,
                "initiated_by": request.initiated_by,
                "student_name": "" if student is None else student.display_name,
                "subject_name": "" if subject is None else subject.name,
                "education_level_name": ""
                if subject is None
                else subject.education_level_name,
                "starts_at": rfc3339(request.session_starts_at),
                "proposed_starts_at": None
                if request.proposed_starts_at is None
                else rfc3339(request.proposed_starts_at),
                "classification": request.classification,
                "status": request.status,
                "class_session_id": str(request.class_session_id),
            }
        return summaries


CHANGE_REQUEST_APPROVAL_HANDLER: Final = ChangeRequestApprovalHandler()
