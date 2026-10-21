"""Tutor qualifications (#44; spec §3, §16; re-exported by
``staff.service``).

A tutor **requests** to teach one subject (the level follows from the
subject, #32). The request is a ``pending`` :class:`TutorQualification` plus
a ``staff.qualification`` approval request (``administration.service``), in
one transaction. A reviewer holding ``qualification.review`` approves or
rejects it in the approval inbox; :mod:`app.modules.staff.qualification_approval`
mirrors the decision onto the qualification and notifies the tutor, in the
inbox's Unit of Work. The qualification keeps its own status, so
eligibility (:func:`is_qualified` and friends) never reads the approval
tables.

**Who acts** (the routes already require ``tutor.view``):

- list: whoever may see the tutor (#43 record policy); others ``404``;
- request and cancel: only the tutor themselves (holding ``tutor``, active).
  Another caller who may see the tutor gets ``403 permission_denied``, one
  who can't ``404``.

**Request order** (after the body's ``422``): ``404`` → ``403`` → ``422`` on
``subject_id`` (unknown) → ``409 subject_inactive`` (the subject or its level;
both rows are locked ``FOR SHARE``) → ``409 qualification_exists`` (a
pre-check, and the partial unique index ``uq_tutor_qualifications_active``
at flush, so two concurrent requests give one ``201`` and one ``409``).

**Cancel order:** ``404`` (tutor) → ``403`` → ``404`` (no such
qualification of this tutor) → ``409 approval_transition_invalid`` unless
``pending``. The approval request is locked before the qualification, in the
order a decision takes, so a cancel racing a decision can't deadlock.

Revoking an approved qualification (#198) is
:mod:`app.modules.staff.qualification_revocation`.

**Only approved counts** (#44 D2): :func:`approved_subject_ids`,
:func:`is_qualified`, :func:`qualified_tutor_ids` and the profile's
``teaching_subjects``. A later-deactivated subject keeps its approved
qualifications. Whether the user still holds the ``tutor`` role is the
caller's check (M4-06).

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from collections.abc import Iterable, Sequence
from typing import Final

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.authz import PermissionDeniedError, Principal, RecordNotFoundError
from app.modules.administration import service as administration
from app.modules.catalog import service as catalog
from app.modules.catalog.schemas import SubjectReference
from app.modules.identity import service as identity
from app.modules.identity.schemas import RoleMember
from app.modules.staff.errors import (
    QualificationExistsError,
    SubjectInactiveError,
    SubjectUnknownError,
)
from app.modules.staff.models import (
    ACTIVE_STATUSES,
    ACTIVE_UNIQUE,
    APPROVED,
    CANCELLED,
    PENDING,
    TutorQualification,
)
from app.modules.staff.permissions import TUTOR, can_view_tutor
from app.modules.staff.schemas import (
    QualificationItem,
    QualificationStatusFilter,
    TeachingSubject,
)

QUALIFICATION_APPROVAL_TYPE: Final = "staff.qualification"
"""The approval request type of a qualification request."""


async def visible_tutor(
    session: AsyncSession, principal: Principal, user_id: uuid.UUID
) -> RoleMember:
    """The tutor ``user_id``: ``404`` when unknown, without the ``tutor``
    role now, or not visible to the caller (one indistinguishable error)."""
    if not can_view_tutor(principal, user_id):
        raise RecordNotFoundError()
    member = await identity.get_role_member(session, TUTOR, user_id)
    if member is None:
        raise RecordNotFoundError()
    return member


def _ensure_self(principal: Principal, member: RoleMember) -> None:
    """Only the tutor themselves, active, requests and cancels (#44 B2)."""
    if principal.user_id != member.id or member.state != "active":
        raise PermissionDeniedError()


def _sort_key(reference: SubjectReference) -> tuple[int, str, str]:
    return (
        reference.education_level_sort_order,
        reference.education_level_name.lower(),
        reference.name.lower(),
    )


async def items_of(
    session: AsyncSession, rows: Sequence[TutorQualification]
) -> list[QualificationItem]:
    """Items ordered by the level's ``sort_order``, the subject name, then
    ``created_at``; one statement for the names."""
    references = await catalog.subject_references(
        session, {row.subject_id for row in rows}
    )
    ordered = sorted(
        rows,
        key=lambda row: (*_sort_key(references[row.subject_id]), row.created_at),
    )
    return [_item(row, references[row.subject_id]) for row in ordered]


def _item(row: TutorQualification, reference: SubjectReference) -> QualificationItem:
    return QualificationItem(
        id=row.id,
        subject_id=row.subject_id,
        subject_name=reference.name,
        education_level_id=reference.education_level_id,
        education_level_name=reference.education_level_name,
        subject_is_active=reference.is_active,
        status=row.status,  # type: ignore[arg-type]
        requested_at=row.created_at,
        decided_at=row.decided_at,
        rejection_reason=row.rejection_reason,
        revoked_at=row.revoked_at,
        revocation_reason=row.revocation_reason,
        version=row.version,
    )


async def list_qualifications(
    session: AsyncSession,
    *,
    principal: Principal,
    user_id: uuid.UUID,
    status: QualificationStatusFilter = "all",
) -> list[QualificationItem]:
    """The tutor's qualifications with ``status`` (``all``: every one)."""
    await visible_tutor(session, principal, user_id)
    query = select(TutorQualification).where(
        TutorQualification.tutor_user_id == user_id
    )
    if status != "all":
        query = query.where(TutorQualification.status == status)
    rows = (await session.scalars(query)).all()
    return await items_of(session, rows)


async def _flush_new(session: AsyncSession) -> None:
    """Flush a new qualification; a concurrent duplicate that won the race
    (``uq_tutor_qualifications_active``) is ``409 qualification_exists``."""
    try:
        await session.flush()
    except IntegrityError as exc:
        if ACTIVE_UNIQUE in str(exc.orig):
            raise QualificationExistsError() from None
        raise


async def request_qualification(
    session: AsyncSession,
    *,
    principal: Principal,
    user_id: uuid.UUID,
    subject_id: uuid.UUID,
) -> QualificationItem:
    """A new ``pending`` qualification and its approval request (see the
    module docstring for the order)."""
    member = await visible_tutor(session, principal, user_id)
    _ensure_self(principal, member)
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
    qualification = TutorQualification(
        tutor_user_id=user_id, subject_id=subject_id, status=PENDING
    )
    session.add(qualification)
    await _flush_new(session)
    await administration.create_approval_request(
        session,
        request_type=QUALIFICATION_APPROVAL_TYPE,
        subject_id=qualification.id,
        requester_user_id=user_id,
    )
    await session.refresh(qualification, ["created_at", "version"])
    return _item(qualification, reference)


async def cancel_qualification(
    session: AsyncSession,
    *,
    principal: Principal,
    user_id: uuid.UUID,
    qualification_id: uuid.UUID,
) -> QualificationItem:
    """The tutor withdraws a ``pending`` request (see the module docstring
    for the order)."""
    member = await visible_tutor(session, principal, user_id)
    _ensure_self(principal, member)
    owned = await session.scalar(
        select(TutorQualification.id)
        .where(TutorQualification.id == qualification_id)
        .where(TutorQualification.tutor_user_id == user_id)
    )
    if owned is None:
        raise RecordNotFoundError()
    approval_id = await administration.approval_request_id_for_subject(
        session,
        request_type=QUALIFICATION_APPROVAL_TYPE,
        subject_id=qualification_id,
    )
    if approval_id is None:  # pragma: no cover - created together
        raise administration.ApprovalTransitionInvalidError()
    # The approval request first (409 unless open), then the qualification.
    await administration.cancel_approval_request(
        session, request_id=approval_id, by_user_id=principal.user_id
    )
    qualification = await locked_qualification(session, qualification_id)
    if qualification is None or qualification.status != PENDING:
        raise administration.ApprovalTransitionInvalidError()
    qualification.status = CANCELLED
    qualification.decided_at = clock.now()
    qualification.decided_by_user_id = principal.user_id
    await session.flush()
    [item] = await items_of(session, [qualification])
    return item


async def locked_qualification(
    session: AsyncSession, qualification_id: uuid.UUID
) -> TutorQualification | None:
    """The qualification, locked ``FOR UPDATE`` (fresh from the database)."""
    qualification: TutorQualification | None = await session.scalar(
        select(TutorQualification)
        .where(TutorQualification.id == qualification_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return qualification


# --- Only approved counts (#44 D1, D2) ------------------------------------------------


async def teaching_subjects(
    session: AsyncSession, tutor_user_id: uuid.UUID
) -> list[TeachingSubject]:
    """The subjects of the tutor's approved qualifications, by the level's
    ``sort_order``, then the subject name. Two statements."""
    subject_ids = await approved_subject_ids(session, tutor_user_id)
    references = await catalog.subject_references(session, subject_ids)
    return [
        TeachingSubject(
            subject_id=reference.id,
            subject_name=reference.name,
            education_level_id=reference.education_level_id,
            education_level_name=reference.education_level_name,
            subject_is_active=reference.is_active,
        )
        for reference in sorted(references.values(), key=_sort_key)
    ]


async def approved_subject_ids(
    session: AsyncSession, tutor_user_id: uuid.UUID
) -> frozenset[uuid.UUID]:
    """The subjects the tutor holds an approved qualification for."""
    rows = await session.scalars(
        select(TutorQualification.subject_id)
        .where(TutorQualification.tutor_user_id == tutor_user_id)
        .where(TutorQualification.status == APPROVED)
    )
    return frozenset(rows)


async def is_qualified(
    session: AsyncSession, tutor_user_id: uuid.UUID, subject_id: uuid.UUID
) -> bool:
    """Whether the tutor holds an approved qualification for the subject
    (pending, rejected, cancelled and revoked never count)."""
    found = await session.scalar(
        select(TutorQualification.id)
        .where(TutorQualification.tutor_user_id == tutor_user_id)
        .where(TutorQualification.subject_id == subject_id)
        .where(TutorQualification.status == APPROVED)
        .limit(1)
    )
    return found is not None


async def qualified_tutor_ids(
    session: AsyncSession, subject_id: uuid.UUID
) -> frozenset[uuid.UUID]:
    """The users holding an approved qualification for the subject. Whether
    they still hold the ``tutor`` role and are active is the caller's check
    (M4-06)."""
    rows = await session.scalars(
        select(TutorQualification.tutor_user_id)
        .where(TutorQualification.subject_id == subject_id)
        .where(TutorQualification.status == APPROVED)
    )
    return frozenset(rows)


async def qualifications_by_id(
    session: AsyncSession, qualification_ids: Iterable[uuid.UUID]
) -> list[TutorQualification]:
    """The qualifications with these ids (unknown ids left out), unlocked."""
    ids = set(qualification_ids)
    if not ids:
        return []
    return list(
        (
            await session.scalars(
                select(TutorQualification).where(TutorQualification.id.in_(ids))
            )
        ).all()
    )
