"""Revoking an approved tutor qualification (#198; spec §3, §16; re-exported
by ``staff.service``).

A holder of ``qualification.review`` (Owners and Administrators; the route
requires it) withdraws an **approved** qualification with a reason. The row
stays, with ``status = 'revoked'``, ``revoked_at``, ``revoked_by_user_id``
and ``revocation_reason``; the approval decision fields (``decided_at``,
``decided_by_user_id``) are kept, so the history reads "approved by X on
D1, revoked by Y on D2". The ORM audits the change (``update`` on
``tutor_qualifications``).

**Order** (after the route's ``403`` and the body's ``422``):

1. lock the qualification ``FOR UPDATE``; missing, or not the tutor
   ``user_id``'s → ``404``;
2. ``version`` mismatch → ``409 version_conflict``;
3. not ``approved`` → ``409 qualification_not_approved`` (pending ones are
   rejected through the approval inbox);
4. revoke, and notify the tutor (``staff.qualification_revoked``, params
   ``subject_name`` and ``education_level_name``; never the reason) in the
   same Unit of Work.

Any approved qualification can be revoked, whatever the tutor's current
roles or state and whatever the subject's or level's active state (#198
B5): the tutor isn't looked up through the #43 view policy. The
``staff.qualification`` approval request is **not** touched: it stays
``approved`` as the record of the original decision, and
``tutor_qualifications.status`` is the source of truth for eligibility
(#198 B6). No reauthentication (not in ADR-0007 D10). Class sessions are not
read or changed: M4 evaluates qualification live (#198 E1 (a)).

Two concurrent revokes with the same version: the second waits on the lock,
then sees the bumped version (``409 version_conflict``), so exactly one
notification is created.

Runs in the caller's Unit of Work and never commits.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.authz import PermissionDeniedError, Principal, RecordNotFoundError
from app.core.models import ensure_version
from app.modules.administration import service as administration
from app.modules.catalog import service as catalog
from app.modules.staff.errors import QualificationNotApprovedError
from app.modules.staff.models import APPROVED, REVOKED
from app.modules.staff.qualification_approval import (
    QUALIFICATION_REVIEW,
    QUALIFICATION_REVOKED,
    QualificationDecisionParams,
)
from app.modules.staff.qualifications import items_of, locked_qualification
from app.modules.staff.schemas import QualificationItem


async def revoke_qualification(
    session: AsyncSession,
    *,
    principal: Principal,
    user_id: uuid.UUID,
    qualification_id: uuid.UUID,
    reason: str,
    version: int,
) -> QualificationItem:
    """Revoke the tutor ``user_id``'s approved qualification (see the module
    docstring for the order)."""
    if not principal.has(QUALIFICATION_REVIEW):  # the route requires it too
        raise PermissionDeniedError()
    qualification = await locked_qualification(session, qualification_id)
    if qualification is None or qualification.tutor_user_id != user_id:
        raise RecordNotFoundError()
    ensure_version(qualification, version)
    if qualification.status != APPROVED:
        raise QualificationNotApprovedError()
    qualification.status = REVOKED
    qualification.revoked_at = clock.now()
    qualification.revoked_by_user_id = principal.user_id
    qualification.revocation_reason = reason
    await session.flush()
    reference = await catalog.get_subject_for_reference(
        session, qualification.subject_id, lock=False
    )
    if reference is None:  # pragma: no cover - the foreign key prevents it
        raise RecordNotFoundError()
    await administration.create_notification(
        session,
        recipient_user_id=qualification.tutor_user_id,
        notification_type=QUALIFICATION_REVOKED,
        params=QualificationDecisionParams(
            subject_name=reference.name,
            education_level_name=reference.education_level_name,
        ),
    )
    [item] = await items_of(session, [qualification])
    return item
