"""The ``staff.qualification`` approval type (#44 C1-C4).

:data:`QUALIFICATION_APPROVAL_HANDLER` plugs tutor qualification requests
into the approval inbox (``administration.service``): reviewers hold
``qualification.review`` (Owners and Administrators); requests can't be sent
back for changes, and a rejection needs a reason.

Approving or rejecting runs in the inbox's Unit of Work, after the approval
request row is locked: the qualification is locked ``FOR UPDATE``, must
still be ``pending`` (else ``409 approval_transition_invalid``), takes the
decision (``approved`` or ``rejected`` with the reason, ``decided_at``,
``decided_by_user_id``), and the tutor gets one notification
(``staff.qualification_approved`` / ``staff.qualification_rejected``, params
``subject_name`` and ``education_level_name``). The rejection reason is not
a param (the #41 200-character cap): the tutor reads it on their
qualifications list. Everything commits or rolls back with the decision.

Exported by ``staff.service``; ``app.wiring`` registers the handler and the
qualification notification types, #198's ``staff.qualification_revoked``
included (:func:`register_qualification_notifications`).
"""

import uuid
from collections.abc import Iterable, Mapping
from typing import Any, Final

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.authz import Principal
from app.modules.administration import service as administration
from app.modules.catalog import service as catalog
from app.modules.identity import service as identity
from app.modules.staff.models import APPROVED, PENDING, REJECTED
from app.modules.staff.qualifications import (
    QUALIFICATION_APPROVAL_TYPE,
    locked_qualification,
    qualifications_by_id,
)

QUALIFICATION_REVIEW: Final = "qualification.review"
"""The identity catalog's review permission (repeated here: ADR-0002)."""

QUALIFICATION_APPROVED: Final = "staff.qualification_approved"
QUALIFICATION_REJECTED: Final = "staff.qualification_rejected"
QUALIFICATION_REVOKED: Final = "staff.qualification_revoked"
"""Sent by a revocation (#198 B4; ``staff/qualification_revocation.py``)."""


class QualificationDecisionParams(BaseModel):
    """The params of the three qualification notifications (never a
    reason: the #41 200-character cap)."""

    subject_name: str
    education_level_name: str


def register_qualification_notifications() -> None:
    """Register the three notification types (again: a no-op)."""
    for code in (QUALIFICATION_APPROVED, QUALIFICATION_REJECTED, QUALIFICATION_REVOKED):
        administration.register_notification_type(code, QualificationDecisionParams)


class QualificationApprovalHandler:
    """The :class:`~app.modules.administration.service.ApprovalHandler` of
    tutor qualification requests; the subject is the qualification."""

    request_type: Final = QUALIFICATION_APPROVAL_TYPE
    review_permission: Final = QUALIFICATION_REVIEW
    supports_request_changes: Final = False
    reason_required_on_reject: Final = True

    async def _decide(
        self,
        session: AsyncSession,
        request: administration.ApprovalRequestView,
        principal: Principal,
        *,
        status: str,
        reason: str | None,
        notification_type: str,
    ) -> None:
        qualification = await locked_qualification(session, request.subject_id)
        if qualification is None or qualification.status != PENDING:
            raise administration.ApprovalTransitionInvalidError()
        qualification.status = status
        qualification.rejection_reason = reason
        qualification.decided_at = clock.now()
        qualification.decided_by_user_id = principal.user_id
        await session.flush()
        reference = await catalog.get_subject_for_reference(
            session, qualification.subject_id, lock=False
        )
        if reference is None:  # pragma: no cover - the foreign key prevents it
            raise administration.ApprovalTransitionInvalidError()
        await administration.create_notification(
            session,
            recipient_user_id=qualification.tutor_user_id,
            notification_type=notification_type,
            params=QualificationDecisionParams(
                subject_name=reference.name,
                education_level_name=reference.education_level_name,
            ),
        )

    async def approve(
        self,
        session: AsyncSession,
        request: administration.ApprovalRequestView,
        principal: Principal,
    ) -> None:
        await self._decide(
            session,
            request,
            principal,
            status=APPROVED,
            reason=None,
            notification_type=QUALIFICATION_APPROVED,
        )

    async def reject(
        self,
        session: AsyncSession,
        request: administration.ApprovalRequestView,
        principal: Principal,
        reason: str | None,
    ) -> None:
        # The core requires the reason (reason_required_on_reject).
        await self._decide(
            session,
            request,
            principal,
            status=REJECTED,
            reason=reason,
            notification_type=QUALIFICATION_REJECTED,
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
        """``{tutor_display_name, subject_name, education_level_name}`` by
        qualification id; three statements."""
        qualifications = await qualifications_by_id(session, subject_ids)
        references = await catalog.subject_references(
            session, {q.subject_id for q in qualifications}
        )
        accounts = await identity.get_account_references(
            session, {q.tutor_user_id for q in qualifications}
        )
        summaries: dict[uuid.UUID, Mapping[str, Any]] = {}
        for qualification in qualifications:
            reference = references.get(qualification.subject_id)
            account = accounts.get(qualification.tutor_user_id)
            if reference is None or account is None:  # pragma: no cover
                continue
            summaries[qualification.id] = {
                "tutor_display_name": account.display_name,
                "subject_name": reference.name,
                "education_level_name": reference.education_level_name,
            }
        return summaries


QUALIFICATION_APPROVAL_HANDLER: Final = QualificationApprovalHandler()
