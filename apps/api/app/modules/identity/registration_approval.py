"""The ``identity.registration`` approval type (#40 D2).

:data:`REGISTRATION_APPROVAL_HANDLER` plugs self-registration (#169) into the
approval inbox (``administration.service``): reviewers hold
``registration.review``; approving and rejecting run the #169 logic of
:mod:`app.modules.identity.registrations` (the role-authority check, the
role, the state change through the student-login gate, the cleared hash on
rejection, ``registration_requests.status``/``decided_*`` and the
``identity.registration_*`` events), in the inbox's Unit of Work. Requests
can't be sent back for changes, and a rejection needs no reason.

Exported by ``identity.service``; the composition roots register it with
``administration.service.register_approval_handler``. The calls into
``registrations`` are imported when they run: ``registrations`` imports
``identity.service``, which imports this module.
"""

import uuid
from collections.abc import Iterable, Mapping
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal
from app.modules.administration import service as administration
from app.modules.identity.permissions import (
    REGISTRATION_APPROVAL_TYPE,
    REGISTRATION_REVIEW,
)


class RegistrationApprovalHandler:
    """The :class:`~app.modules.administration.service.ApprovalHandler` of
    self-registration requests; the subject is the registration request."""

    request_type: Final = REGISTRATION_APPROVAL_TYPE
    review_permission: Final = REGISTRATION_REVIEW
    supports_request_changes: Final = False
    reason_required_on_reject: Final = False

    async def approve(
        self,
        session: AsyncSession,
        request: administration.ApprovalRequestView,
        principal: Principal,
    ) -> None:
        from app.modules.identity import registrations

        await registrations.approve_registration(
            session, principal=principal, request_id=request.subject_id
        )

    async def reject(
        self,
        session: AsyncSession,
        request: administration.ApprovalRequestView,
        principal: Principal,
        reason: str | None,
    ) -> None:
        # The reason (optional) is kept on the approval request only.
        from app.modules.identity import registrations

        await registrations.reject_registration(
            session, principal=principal, request_id=request.subject_id
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
        from app.modules.identity import registrations

        return await registrations.registration_summaries(session, subject_ids)


REGISTRATION_APPROVAL_HANDLER: Final = RegistrationApprovalHandler()
