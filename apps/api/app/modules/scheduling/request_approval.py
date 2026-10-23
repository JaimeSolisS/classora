"""The ``scheduling.session_request`` approval type (#59 E; spec W2.3).

One inbox item per session request (the subject is the **request**),
created with the request, the requester being the guardian or student who
sent it. Reviewers hold ``schedule.manage`` (Owners and Administrators);
items can't be sent back for changes, and a rejection needs a reason. The
requester withdrawing the request cancels its item
(:func:`~app.modules.scheduling.requests.withdraw_session_request`).

**Until #60:** ``approve`` and ``reject`` raise
:class:`~app.modules.scheduling.errors.SessionRequestReviewUnavailableError`
(``409 session_request_review_unavailable``): the item stays ``pending``
and the Unit of Work rolls back (#40 B3). **#60 replaces both** (confirm =
create the session through #48's path and offer it to the chosen tutor;
reject = the reason and the requester's notification);
``tests/integration/test_session_requests.py`` pins this placeholder so it
can't survive #60 unnoticed.

``summaries``: ``{student_name, subject_name, education_level_name,
starts_at, ends_at, duration_minutes, requested_by, note}`` by request id,
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
from app.modules.scheduling.errors import SessionRequestReviewUnavailableError
from app.modules.scheduling.models import SessionRequest
from app.modules.scheduling.offer_records import rfc3339
from app.modules.scheduling.requests import SESSION_REQUEST_TYPE

SCHEDULE_MANAGE: Final = "schedule.manage"


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
        # Placeholder until #60 (module docstring): #60 replaces it.
        raise SessionRequestReviewUnavailableError()

    async def reject(
        self,
        session: AsyncSession,
        request: administration.ApprovalRequestView,
        principal: Principal,
        reason: str | None,
    ) -> None:
        # Placeholder until #60 (module docstring): #60 replaces it.
        raise SessionRequestReviewUnavailableError()

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
            }
        return summaries


SESSION_REQUEST_APPROVAL_HANDLER: Final = SessionRequestApprovalHandler()
