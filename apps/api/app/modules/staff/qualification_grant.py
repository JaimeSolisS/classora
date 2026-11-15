"""Granting a tutor a qualification directly (HF-05 A, E1: owner-approved
amendment of spec §2; re-exported by ``staff.service``).

A holder of ``qualification.review`` (Owners and Administrators; the route
requires it) records that a tutor teaches a subject: the
:class:`~app.modules.staff.models.TutorQualification` is created
``approved`` at once (``decided_at`` = ``created_at`` = now,
``decided_by_user_id`` = the caller), with **no** ``staff.qualification``
approval request: the reviewer is the approver acting directly. A tutor's
own subjects still go through the request and the inbox
(:mod:`app.modules.staff.qualifications`).

**Order** (after the route's ``401`` / ``csrf_failed`` / ``403`` and the
body's ``422``):

1. ``404`` when the user is unknown or doesn't hold the ``tutor`` role
   (``visible_tutor``; reviewers see every tutor);
2. ``403 permission_denied`` when the tutor is the caller (no self-grant);
3. ``422`` on ``["body", "subject_id"]`` for an unknown subject;
4. ``409 subject_inactive`` (the subject or its level; both rows are locked
   ``FOR SHARE``, as for a request);
5. ``409 qualification_exists`` when a ``pending`` or ``approved`` row
   exists: a pre-check, plus ``uq_tutor_qualifications_active`` at flush, so
   two concurrent grants (or a grant racing a request) give one ``201`` and
   one ``409``. A pending request is decided in the inbox, not by a grant.

Any account state is accepted (``pending`` invitees included), as for a
revoke; eligibility already counts only active accounts. The tutor gets one
``staff.qualification_approved`` notification in the same Unit of Work; the
automatic ``create`` audit event of the row is the audit trail. No
reauthentication (not in ADR-0007 D10).

Runs in the caller's Unit of Work and never commits.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.authz import PermissionDeniedError, Principal
from app.modules.administration import service as administration
from app.modules.catalog import service as catalog
from app.modules.staff.errors import (
    QualificationExistsError,
    SubjectInactiveError,
    SubjectUnknownError,
)
from app.modules.staff.models import ACTIVE_STATUSES, APPROVED, TutorQualification
from app.modules.staff.qualification_approval import (
    QUALIFICATION_APPROVED,
    QUALIFICATION_REVIEW,
    QualificationDecisionParams,
)
from app.modules.staff.qualifications import flush_new, item_of, visible_tutor
from app.modules.staff.schemas import QualificationItem


async def grant_qualification(
    session: AsyncSession,
    *,
    principal: Principal,
    user_id: uuid.UUID,
    subject_id: uuid.UUID,
) -> QualificationItem:
    """A new ``approved`` qualification of the tutor ``user_id`` for the
    subject (see the module docstring for the order)."""
    if not principal.has(QUALIFICATION_REVIEW):  # the route requires it too
        raise PermissionDeniedError()
    member = await visible_tutor(session, principal, user_id)
    if member.id == principal.user_id:
        raise PermissionDeniedError()
    reference = await catalog.get_subject_for_reference(session, subject_id, lock=True)
    if reference is None:
        raise SubjectUnknownError()
    if not (reference.is_active and reference.education_level_is_active):
        raise SubjectInactiveError()
    existing = await session.scalar(
        select(TutorQualification.id)
        .where(TutorQualification.tutor_user_id == user_id)
        .where(TutorQualification.subject_id == subject_id)
        .where(TutorQualification.status.in_(ACTIVE_STATUSES))
        .limit(1)
    )
    if existing is not None:
        raise QualificationExistsError()
    now = clock.now()
    qualification = TutorQualification(
        tutor_user_id=user_id,
        subject_id=subject_id,
        status=APPROVED,
        created_at=now,
        decided_at=now,
        decided_by_user_id=principal.user_id,
    )
    session.add(qualification)
    await flush_new(session)
    await administration.create_notification(
        session,
        recipient_user_id=user_id,
        notification_type=QUALIFICATION_APPROVED,
        params=QualificationDecisionParams(
            subject_name=reference.name,
            education_level_name=reference.education_level_name,
        ),
    )
    await session.refresh(qualification, ["created_at", "version"])
    return item_of(qualification, reference)
