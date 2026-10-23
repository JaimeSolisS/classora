"""The ``scheduling.session_request`` approval type (#59 E; spec W2.3).

One inbox item per session request (the subject is the **request**),
created with the request, the requester being the guardian or student who
sent it. Reviewers hold ``schedule.manage`` (Owners and Administrators);
items can't be sent back for changes, and a rejection needs a reason. The
requester withdrawing the request cancels its item
(:func:`~app.modules.scheduling.requests.withdraw_session_request`).

**Decisions go through the review routes (#60 E1):** staff confirm or
reject on ``/schedule/requests/<id>`` (``POST /api/v1/session-requests/{id}/
confirm`` and ``…/reject``, ``app.workflows.confirm_session_request``):
the workflow decides the request first and then resolves this item through
``administration.service.decide_approval_request`` in the same Unit of Work.
So ``approve`` accepts only a request that is already ``confirmed`` with a
session, and ``reject`` only one already ``rejected``; otherwise both raise
:class:`~app.modules.scheduling.errors.SessionRequestReviewRouteRequiredError`
(``409 session_request_review_route_required`` with ``details.review_path``),
the item stays ``pending`` and the Unit of Work rolls back (#40 B3). The
generic body can't carry a tutor, and locking the request here (after the
core locked the item) would reverse the request → item lock order of the
withdraw and the review routes, so the request is read without a lock.

``summaries``: ``{student_name, subject_name, education_level_name,
starts_at, ends_at, duration_minutes, requested_by, note, status,
class_session_id, session_request_id}`` (#60 adds the last three: the
inbox's "Review" link names the request) by request id,
in four statements whatever the page size (the requests, the students, the
subjects and the requesters' names: three other modules' readers). Staff
may see these.

Exported by ``scheduling.service``; ``app.wiring`` registers it.
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
from app.modules.identity import service as identity
from app.modules.scheduling.errors import SessionRequestReviewRouteRequiredError
from app.modules.scheduling.models import (
    SESSION_REQUEST_CONFIRMED,
    SESSION_REQUEST_REJECTED,
    SessionRequest,
)
from app.modules.scheduling.offer_records import rfc3339
from app.modules.scheduling.request_review import review_path
from app.modules.scheduling.requests import SESSION_REQUEST_TYPE

SCHEDULE_MANAGE: Final = "schedule.manage"


async def _subject(
    session: AsyncSession, request_id: uuid.UUID
) -> SessionRequest | None:
    # No lock (module docstring); fresh from the database or, inside the
    # review workflow, the row it has just decided.
    found: SessionRequest | None = await session.scalar(
        select(SessionRequest)
        .where(SessionRequest.id == request_id)
        .execution_options(populate_existing=True)
    )
    return found


class SessionRequestApprovalHandler:
    """The :class:`~app.modules.administration.service.ApprovalHandler` of
    session requests; the subject is the request."""

    request_type: Final = SESSION_REQUEST_TYPE
    review_permission: Final = SCHEDULE_MANAGE
    supports_request_changes: Final = False
    reason_required_on_reject: Final = True

    async def approve(
        self,
        session: AsyncSession,
        request: administration.ApprovalRequestView,
        principal: Principal,
    ) -> None:
        row = await _subject(session, request.subject_id)
        if not (
            row is not None
            and row.status == SESSION_REQUEST_CONFIRMED
            and row.class_session_id is not None
        ):
            raise SessionRequestReviewRouteRequiredError(
                review_path(request.subject_id)
            )

    async def reject(
        self,
        session: AsyncSession,
        request: administration.ApprovalRequestView,
        principal: Principal,
        reason: str | None,
    ) -> None:
        row = await _subject(session, request.subject_id)
        if row is None or row.status != SESSION_REQUEST_REJECTED:
            raise SessionRequestReviewRouteRequiredError(
                review_path(request.subject_id)
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
        """See the module docstring; four statements."""
        ids = set(subject_ids)
        if not ids:
            return {}
        rows = list(
            await session.scalars(
                select(SessionRequest).where(SessionRequest.id.in_(ids))
            )
        )
        if not rows:
            return {}
        students = await families.student_references(
            session, {row.student_id for row in rows}
        )
        subjects = await catalog.subject_references(
            session, {row.subject_id for row in rows}
        )
        accounts = await identity.get_account_references(
            session, {row.requested_by_user_id for row in rows}
        )
        summaries: dict[uuid.UUID, Mapping[str, Any]] = {}
        for row in rows:
            student = students.get(row.student_id)
            subject = subjects.get(row.subject_id)
            account = accounts.get(row.requested_by_user_id)
            summaries[row.id] = {
                "student_name": "" if student is None else student.display_name,
                "subject_name": "" if subject is None else subject.name,
                "education_level_name": ""
                if subject is None
                else subject.education_level_name,
                "starts_at": rfc3339(row.starts_at),
                "ends_at": rfc3339(row.ends_at),
                "duration_minutes": row.duration_minutes,
                "requested_by": "" if account is None else account.display_name,
                "note": row.note,
                "status": row.status,
                "session_request_id": str(row.id),
                "class_session_id": None
                if row.class_session_id is None
                else str(row.class_session_id),
            }
        return summaries


SESSION_REQUEST_APPROVAL_HANDLER: Final = SessionRequestApprovalHandler()
