"""Self-registration requests (#169; spec §9; ADR-0007 D6, D7, D13).

A parent or guardian, or a student, requests an account on a public page:
a username, a display name, a password and the role they ask for (Parent or
guardian, or Student only). The request creates a ``pending`` user with the
password's Argon2id hash and **no role**, who cannot sign in, and a
``pending`` :class:`~app.modules.identity.models.RegistrationRequest`.

An Owner or Administrator (``registration.review``) confirms the person's
identity out of band, then approves (the requested role is added and the user
becomes ``active``) or rejects (the user is archived and the password hash is
cleared; the username stays taken). Registration never links or enrolls
anyone. No email is sent (spec §1, §14).

Audit (#22): ``identity.registration_requested`` (no actor),
``identity.registration_approved`` and ``identity.registration_rejected``
(the reviewer), each on the request with ``user_id`` and ``requested_role``.
No event holds a password or hash. A request that changes nothing (``404``,
``409``, ``422``, ``429``) writes no event.

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from typing import Final

from sqlalchemy import exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.audit import record_audit_event
from app.core.authz import PermissionDeniedError, Principal, RecordNotFoundError
from app.core.pagination import OffsetPage, OffsetParams
from app.modules.identity import service
from app.modules.identity.errors import (
    RegistrationAlreadyDecidedError,
    UsernameTakenError,
)
from app.modules.identity.models import RegistrationRequest, User
from app.modules.identity.permissions import ACTIVE, ARCHIVED, PENDING
from app.modules.identity.schemas import RegistrationRequestItem

REGISTRATION_REQUESTED_EVENT: Final = "identity.registration_requested"
REGISTRATION_APPROVED_EVENT: Final = "identity.registration_approved"
REGISTRATION_REJECTED_EVENT: Final = "identity.registration_rejected"

REQUEST_PENDING: Final = "pending"
APPROVED: Final = "approved"
REJECTED: Final = "rejected"

_USERNAME_UNIQUE: Final = "uq_users_username"


def _record(session: AsyncSession, action: str, request: RegistrationRequest) -> None:
    record_audit_event(
        session,
        action=action,
        record_type=RegistrationRequest.__tablename__,
        record_id=request.id,
        details={"user_id": request.user_id, "requested_role": request.requested_role},
    )


async def submit_registration(
    session: AsyncSession,
    *,
    username: str,
    display_name: str,
    password: str,
    role: str,
) -> None:
    """Create a pending user without a role and a pending request.

    In this order: ``422 username_invalid`` (normalization), ``409
    username_taken`` when any user (in any state) has the username, also when
    a concurrent request inserts it first, then the password policy (``422``
    with the policy code). Only the Argon2id hash is stored.
    """
    normalized = service.normalize_username(username)
    if await session.scalar(select(exists().where(User.username == normalized))):
        raise UsernameTakenError()
    service.validate_password(password, username=normalized)
    user = User(
        id=uuid.uuid7(),
        username=normalized,
        display_name=display_name,
        state=PENDING,
        password_hash=service.hash_password(password),
    )
    try:
        # A savepoint: a concurrent request that inserted the same username
        # first is 409, not 500.
        async with session.begin_nested():
            session.add(user)
    except IntegrityError as exc:
        if _USERNAME_UNIQUE in str(exc.orig):
            raise UsernameTakenError() from None
        raise
    request = RegistrationRequest(id=uuid.uuid7(), user_id=user.id, requested_role=role)
    session.add(request)
    await session.flush()
    _record(session, REGISTRATION_REQUESTED_EVENT, request)


def _item(request: RegistrationRequest, user: User) -> RegistrationRequestItem:
    return RegistrationRequestItem.model_validate(
        {
            "id": request.id,
            "username": user.username,
            "display_name": user.display_name,
            "requested_role": request.requested_role,
            "status": request.status,
            "submitted_at": request.created_at,
            "decided_at": request.decided_at,
        }
    )


async def list_registration_requests(
    session: AsyncSession, *, status: str, page: OffsetParams
) -> OffsetPage[RegistrationRequestItem]:
    """The requests with ``status``, oldest first (``created_at``, then id)."""
    base = (
        select(RegistrationRequest, User)
        .join(User, User.id == RegistrationRequest.user_id)
        .where(RegistrationRequest.status == status)
    )
    total = await session.scalar(select(func.count()).select_from(base.subquery()))
    rows = (
        await session.execute(
            base.order_by(RegistrationRequest.created_at, RegistrationRequest.id)
            .limit(page.limit)
            .offset(page.offset)
        )
    ).all()
    return OffsetPage[RegistrationRequestItem](
        items=[_item(row.RegistrationRequest, row.User) for row in rows],
        total=total or 0,
        limit=page.limit,
        offset=page.offset,
    )


async def _pending_request(
    session: AsyncSession, request_id: uuid.UUID
) -> tuple[RegistrationRequest, User]:
    """The request and its user, both locked; ``404`` when unknown, ``409``
    unless both are still pending."""
    # Locked: two decisions on the same request run one after the other, and
    # the second sees the first one's result.
    request: RegistrationRequest | None = await session.scalar(
        select(RegistrationRequest)
        .where(RegistrationRequest.id == request_id)
        .with_for_update()
    )
    if request is None:
        raise RecordNotFoundError()
    user: User | None = await session.scalar(
        select(User).where(User.id == request.user_id).with_for_update()
    )
    if request.status != REQUEST_PENDING or user is None or user.state != PENDING:
        raise RegistrationAlreadyDecidedError()
    return request, user


def _decide(request: RegistrationRequest, status: str, principal: Principal) -> None:
    request.status = status
    request.decided_at = clock.now()
    request.decided_by_user_id = principal.user_id


async def approve_registration(
    session: AsyncSession, *, principal: Principal, request_id: uuid.UUID
) -> RegistrationRequestItem:
    """Activate the request's user with the requested role.

    ``404 not_found``, ``409 registration_already_decided``, then ``403
    permission_denied`` unless the reviewer may assign the role (defence in
    depth). The role is added, then the user moves ``pending → active``
    through :func:`~app.modules.identity.service.change_user_state`, the one
    activation path (#35 adds the minor-consent check there).
    """
    request, user = await _pending_request(session, request_id)
    if not service.may_assign_role(principal.permissions, request.requested_role):
        raise PermissionDeniedError()
    await service.add_user_role(session, user_id=user.id, role=request.requested_role)
    await service.change_user_state(session, user_id=user.id, new_state=ACTIVE)
    _decide(request, APPROVED, principal)
    await session.flush()
    _record(session, REGISTRATION_APPROVED_EVENT, request)
    return _item(request, user)


async def reject_registration(
    session: AsyncSession, *, principal: Principal, request_id: uuid.UUID
) -> RegistrationRequestItem:
    """Archive the request's user and clear the password hash.

    ``404 not_found`` and ``409 registration_already_decided`` as for
    approval. The username stays taken: archived users keep their history.
    """
    request, user = await _pending_request(session, request_id)
    # Data minimization: the account can never sign in again.
    user.password_hash = None
    await service.change_user_state(session, user_id=user.id, new_state=ARCHIVED)
    _decide(request, REJECTED, principal)
    await session.flush()
    _record(session, REGISTRATION_REJECTED_EVENT, request)
    return _item(request, user)
