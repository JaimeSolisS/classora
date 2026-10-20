"""The identity module's public API (ADR-0002): other modules import only this.

- Usernames: :func:`normalize_username` (ADR-0007, D7).
- Passwords: :func:`hash_password`, :func:`verify_password`,
  :func:`needs_rehash`, :func:`validate_password` (D5, D6; ``passwords.py``).
- Permissions: :func:`effective_permissions` (database) and
  :func:`resolve_permissions` (pure), :func:`may_assign_role`, the role codes
  and :data:`STAFF_ROLES` (D4; ``permissions.py``).
- Delegation: :func:`grant_delegation` and :func:`revoke_delegation`.
- Self-registration approvals (#40 D2): :data:`REGISTRATION_APPROVAL_HANDLER`
  (type :data:`REGISTRATION_APPROVAL_TYPE`), registered by the composition
  roots with ``administration.service.register_approval_handler``.
- Account references (#34 B1): :func:`get_account_reference` and
  :func:`get_account_references` (``schemas.AccountReference``: id, username,
  display name, state and roles; never a hash or contact data).
- Privacy notice (#174; ``privacy.py``): :func:`current_privacy_notice`,
  :func:`accept_privacy_notice` and :func:`has_accepted_current_privacy_notice`
  (schema ``schemas.PrivacyNotice``; errors
  :class:`PrivacyNoticeOutdatedError`, :class:`PrivacyNoticeUnavailableError`).
- Preferences (#36; ``preferences.py``): :func:`get_user_preferences` and
  :func:`update_user_preferences` (schema ``schemas.UserPreferences``; codes
  :data:`LANGUAGES` and :data:`THEMES`). A value is ``None`` until the user
  stores it.
- Authorization: :func:`load_principal` (the :class:`~app.core.authz.Principal`
  of an active user, with ``privacy_notice_accepted``) and
  :func:`resolve_session_principal`, the
  :data:`~app.core.authz.PrincipalResolver` that ``build_app`` registers
  (session cookie -> unrevoked ``auth_sessions`` row -> expiry check ->
  :func:`load_principal`; #23, #25).
- Lifecycle (#25; ADR-0007 D8): the only sanctioned ways to change a user's
  state, roles and password: :func:`change_user_state`,
  :func:`add_user_role`, :func:`remove_user_role` and :func:`set_password`.
  Each revokes all of the user's sessions (:func:`revoke_user_sessions`) in
  the caller's transaction when it changes something that must end them.
  Callers (#29, #30, #31, #35) add authority checks and reauthentication.
- **Last-active-Owner guard** (#31; ADR-0007 D4): :func:`change_user_state`
  (``active`` -> ``suspended``/``archived`` of a user holding ``owner``) and
  :func:`remove_user_role` (``owner`` from an ``active`` user) take a
  transaction-scoped advisory lock (:data:`LAST_ACTIVE_OWNER_LOCK_KEY`), count
  the other active Owners and raise :class:`LastActiveOwnerError` when there
  is none, changing nothing. The guard lives only here, so every caller gets
  it: #169 registration approval and rejection, the #31 user-state routes,
  #170 role changes and #35 consent revocation. Routes never repeat it.
- **Student-login gate** (#35 B1; ADR-0007 D18, #29 E3): an account that holds
  ``student`` becomes ``active`` (:func:`change_user_state`) or gets
  ``student`` while ``active`` (:func:`add_user_role`) only when the
  registered :class:`StudentLoginGate` allows it. ``identity`` can't import
  ``families`` (ADR-0002, #34 H3), so ``families`` implements the gate and the
  composition roots (the API app factory, the CLI and the worker) register it
  with :func:`register_student_login_gate`. With none registered, activating
  a student **fails closed** (:class:`StudentLoginGateMissingError`). Before
  the gate runs, the user's row is locked (:func:`lock_user`), so every path
  locks the user row before the student profile row.

Every function runs in the caller's Unit of Work and never commits. Nothing
here deletes a user: archived users keep their history (spec §2, §16).
"""

import re
import uuid
from collections import defaultdict
from collections.abc import Iterable
from typing import Final, Literal, Protocol

from fastapi import Request
from sqlalchemy import func, literal_column, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.audit import record_audit_event
from app.core.authz import (
    Principal,
    RecordNotFoundError,
    ResolvedSession,
    SessionExpiredError,
    is_passive_session,
)
from app.core.config import Settings
from app.modules.identity import sessions
from app.modules.identity.errors import (
    DelegationForbiddenError,
    DelegationTargetInvalidError,
    InvalidUserStateTransitionError,
    LastActiveOwnerError,
    PasswordEqualsUsernameError,
    PasswordPolicyError,
    PasswordTooLongError,
    PasswordTooShortError,
    PermissionNotDelegableError,
    PrivacyNoticeOutdatedError,
    PrivacyNoticeUnavailableError,
    UnknownRoleError,
    UsernameInvalidError,
)
from app.modules.identity.models import (
    AuthSession,
    Permission,
    Role,
    RolePermission,
    User,
    UserDelegation,
    UserRole,
)
from app.modules.identity.passwords import (
    hash_password,
    needs_rehash,
    validate_password,
    verify_password,
)
from app.modules.identity.permissions import (
    ACTIVE,
    ADMINISTRATOR,
    ARCHIVED,
    DELEGABLE_PERMISSIONS,
    OWNER,
    PENDING,
    REGISTRATION_APPROVAL_TYPE,
    ROLE_CODES,
    STAFF_ROLES,
    STUDENT,
    SUSPENDED,
    USER_DELEGATION_MANAGE,
    USER_STATES,
    may_assign_role,
    resolve_permissions,
)
from app.modules.identity.preferences import (
    LANGUAGES,
    THEMES,
    UNSET,
    UserPreferences,
    get_user_preferences,
    update_user_preferences,
)
from app.modules.identity.privacy import (
    accept_privacy_notice,
    accepted_current_notice,
    current_privacy_notice,
    has_accepted_current_privacy_notice,
    privacy_notice_versions,
)
from app.modules.identity.registration_approval import (
    REGISTRATION_APPROVAL_HANDLER,
)
from app.modules.identity.schemas import AccountReference, PrivacyNotice

__all__ = [
    "ACTIVE",
    "ADMINISTRATOR",
    "LANGUAGES",
    "OWNER",
    "REGISTRATION_APPROVAL_HANDLER",
    "REGISTRATION_APPROVAL_TYPE",
    "ROLE_CODES",
    "STAFF_ROLES",
    "THEMES",
    "UNSET",
    "DelegationForbiddenError",
    "DelegationTargetInvalidError",
    "InvalidUserStateTransitionError",
    "LastActiveOwnerError",
    "PasswordEqualsUsernameError",
    "PasswordPolicyError",
    "PasswordTooLongError",
    "PasswordTooShortError",
    "PermissionNotDelegableError",
    "PrivacyNotice",
    "PrivacyNoticeOutdatedError",
    "PrivacyNoticeUnavailableError",
    "StudentLoginGate",
    "StudentLoginGateMissingError",
    "UnknownRoleError",
    "UserPreferences",
    "AccountReference",
    "UsernameInvalidError",
    "accept_privacy_notice",
    "add_user_role",
    "change_user_state",
    "current_privacy_notice",
    "effective_permissions",
    "get_account_reference",
    "get_account_references",
    "get_user_preferences",
    "grant_delegation",
    "has_accepted_current_privacy_notice",
    "hash_password",
    "load_principal",
    "lock_owner_guard",
    "lock_user",
    "may_assign_role",
    "needs_rehash",
    "normalize_username",
    "privacy_notice_versions",
    "register_student_login_gate",
    "remove_user_role",
    "resolve_permissions",
    "resolve_session_principal",
    "revoke_delegation",
    "revoke_user_sessions",
    "set_password",
    "update_user_preferences",
    "validate_password",
    "verify_password",
]

_USERNAME = re.compile(r"[a-z0-9._-]{3,64}")


def normalize_username(raw: str) -> str:
    """The stored form of a username, or :class:`UsernameInvalidError`.

    Trims surrounding whitespace, rejects any non-ASCII character *before*
    lower-casing (so the Kelvin sign cannot become ``k``), lower-cases, then
    requires 3 to 64 characters from ``a-z 0-9 . _ -``.
    """
    trimmed = raw.strip()
    if not trimmed.isascii():
        raise UsernameInvalidError()
    username = trimmed.lower()
    if _USERNAME.fullmatch(username) is None:
        raise UsernameInvalidError()
    return username


async def _user_state(session: AsyncSession, user_id: uuid.UUID) -> str | None:
    state: str | None = await session.scalar(
        select(User.state).where(User.id == user_id)
    )
    return state


async def _user_roles(session: AsyncSession, user_id: uuid.UUID) -> set[str]:
    rows = await session.scalars(
        select(Role.code)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user_id)
    )
    return set(rows)


async def effective_permissions(
    session: AsyncSession, user_id: uuid.UUID
) -> frozenset[str]:
    """The user's effective permission codes, from the database (ADR-0007, D4).

    Loads the state, the roles, the seeded ``role_permissions`` of those roles
    and the delegations, then applies :func:`resolve_permissions`. An unknown
    user, or a user who is not ``active``, has none.
    """
    state = await _user_state(session, user_id)
    if state is None:
        return frozenset()
    roles = await _user_roles(session, user_id)
    role_permissions: defaultdict[str, set[str]] = defaultdict(set)
    rows = await session.execute(
        select(Role.code, Permission.code)
        .select_from(UserRole)
        .join(Role, Role.id == UserRole.role_id)
        .join(RolePermission, RolePermission.role_id == Role.id)
        .join(Permission, Permission.id == RolePermission.permission_id)
        .where(UserRole.user_id == user_id)
    )
    for role_code, permission_code in rows:
        role_permissions[role_code].add(permission_code)
    delegated = await session.scalars(
        select(Permission.code)
        .join(UserDelegation, UserDelegation.permission_id == Permission.id)
        .where(UserDelegation.user_id == user_id)
    )
    return resolve_permissions(
        state, roles, set(delegated), role_permissions=role_permissions
    )


async def load_principal(session: AsyncSession, user_id: uuid.UUID) -> Principal | None:
    """The :class:`Principal` of an ``active`` user, or ``None``.

    ``None`` for an unknown user or one whose state is not ``active``.
    Otherwise the user's role codes, :func:`effective_permissions` and
    ``privacy_notice_accepted`` (#174: the current version, read in the same
    statement as the state, on every call; never cached, so a version
    published while the user is signed in applies on their next request).
    Never commits.
    """
    row = (
        await session.execute(
            select(User.state, accepted_current_notice(User.id)).where(
                User.id == user_id
            )
        )
    ).one_or_none()
    if row is None or row[0] != ACTIVE:
        return None
    return Principal(
        user_id=user_id,
        roles=frozenset(await _user_roles(session, user_id)),
        permissions=await effective_permissions(session, user_id),
        privacy_notice_accepted=bool(row[1]),
    )


async def get_account_references(
    session: AsyncSession, user_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, AccountReference]:
    """The :class:`AccountReference` of each existing user in ``user_ids``,
    by id, in one statement (none when ``user_ids`` is empty). Unknown ids are
    left out."""
    ids = set(user_ids)
    if not ids:
        return {}
    role_codes = func.array_remove(
        func.array_agg(Role.code), literal_column("NULL")
    ).label("role_codes")
    rows = await session.execute(
        select(User.id, User.username, User.display_name, User.state, role_codes)
        .outerjoin(UserRole, UserRole.user_id == User.id)
        .outerjoin(Role, Role.id == UserRole.role_id)
        .where(User.id.in_(ids))
        .group_by(User.id)
    )
    return {
        row.id: AccountReference(
            id=row.id,
            username=row.username,
            display_name=row.display_name,
            state=row.state,
            roles=frozenset(row.role_codes or ()),
        )
        for row in rows
    }


async def get_account_reference(
    session: AsyncSession, user_id: uuid.UUID
) -> AccountReference | None:
    """The :class:`AccountReference` of one user, or ``None`` when unknown."""
    return (await get_account_references(session, [user_id])).get(user_id)


async def resolve_session_principal(
    request: Request, session: AsyncSession, settings: Settings
) -> ResolvedSession | None:
    """The principal of the request's session cookie with its raw token, or
    ``None`` (#23; the token lets core check the CSRF token, #24).

    In this order (#25 C3):

    1. ``None`` for a missing cookie, a value that is not a valid token, or an
       unknown or revoked session;
    2. :class:`~app.core.authz.SessionExpiredError` for an unrevoked session
       past its idle or absolute limit (it writes nothing, so further requests
       cannot revive it);
    3. ``None`` for a user who is not ``active``;
    4. otherwise the principal, after the throttled ``last_seen_at`` update
       (:func:`sessions.touch_session`, at most once a minute; never on a
       request marked :func:`~app.core.authz.passive_session`, #42), with the
       session's id and ``reauthenticated_at`` (#26) from the same row.

    Reads only the current mode's cookie name.
    """
    token = sessions.request_token(request, settings)
    if token is None:
        return None
    auth_session = await sessions.find_live_session(session, token)
    if auth_session is None:
        return None
    now = clock.now()
    if sessions.is_expired(auth_session, now):
        raise SessionExpiredError()
    principal = await load_principal(session, auth_session.user_id)
    if principal is None:
        return None
    if not is_passive_session(request):
        await sessions.touch_session(session, auth_session, now)
    return ResolvedSession(
        principal=principal,
        session_token=token,
        session_id=auth_session.id,
        reauthenticated_at=auth_session.reauthenticated_at,
    )


async def is_session_live(session: AsyncSession, session_id: uuid.UUID) -> bool:
    """Whether the session ``session_id`` is still live: unrevoked (so not
    logged out), not past its idle or absolute limit, and its user
    ``active``. Read-only: never refreshes ``last_seen_at``. The live
    notification stream re-checks its session with it (#42 C4; registered
    by ``app.main.build_app``)."""
    row = (
        await session.execute(
            select(AuthSession, User.state)
            .join(User, User.id == AuthSession.user_id)
            .where(AuthSession.id == session_id, AuthSession.revoked_at.is_(None))
        )
    ).one_or_none()
    if row is None:
        return False
    auth_session, state = row
    return state == ACTIVE and not sessions.is_expired(auth_session, clock.now())


# --- Lifecycle: sessions, state, roles, password (#25) -------------------------------

SESSIONS_REVOKED_EVENT: Final = "identity.sessions_revoked"

RevocationReason = Literal[
    "suspended", "archived", "role_added", "role_removed", "password_changed"
]

ALLOWED_STATE_TRANSITIONS: Final[frozenset[tuple[str, str]]] = frozenset(
    {
        (PENDING, ACTIVE),  # approval (#29)
        (PENDING, ARCHIVED),  # rejection (#29)
        (ACTIVE, SUSPENDED),
        (ACTIVE, ARCHIVED),
        (SUSPENDED, ACTIVE),  # reactivation
        (SUSPENDED, ARCHIVED),
        (ARCHIVED, ACTIVE),  # reactivation (#31)
    }
)
"""Every allowed ``(from, to)`` state change. Nothing goes back to
``pending``; ``pending`` and ``archived`` users cannot be suspended."""

_REVOKING_STATES: Final[dict[str, RevocationReason]] = {
    SUSPENDED: "suspended",
    ARCHIVED: "archived",
}


async def revoke_user_sessions(
    session: AsyncSession, *, user_id: uuid.UUID, reason: RevocationReason
) -> int:
    """Revoke every unrevoked session of ``user_id``, expired or not.

    Returns the count. When it is at least 1, writes
    ``identity.sessions_revoked`` (``record_type="users"``, ``details`` with
    ``reason`` and ``count``); when it is 0, writes nothing. Other users'
    sessions are untouched.
    """
    count = await sessions.revoke_user_sessions(session, user_id)
    if count:
        record_audit_event(
            session,
            action=SESSIONS_REVOKED_EVENT,
            record_type=User.__tablename__,
            record_id=user_id,
            details={"reason": reason, "count": count},
        )
    return count


async def _get_user(session: AsyncSession, user_id: uuid.UUID) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise RecordNotFoundError()
    return user


# --- The last-active-Owner guard (#31; ADR-0007 D4) ----------------------------------

LAST_ACTIVE_OWNER_LOCK_KEY: Final = 7_164_227_892_382_950_770
"""The ``pg_advisory_xact_lock`` key of the guard: the bytes ``b"clsOwner"``
read as a big-endian 64-bit integer. One constant key, so every guarded change
in any transaction waits for the others; the lock ends with the transaction
(it works through PgBouncer in transaction mode)."""


async def lock_owner_guard(session: AsyncSession) -> None:
    """Take the guard's transaction-scoped advisory lock (re-entrant within a
    transaction). A caller that locks the user's row before reaching the guard
    takes this first, so every path locks in the order advisory lock → row and
    two concurrent changes of one Owner never deadlock (#170 QA D1)."""
    await session.execute(
        select(func.pg_advisory_xact_lock(LAST_ACTIVE_OWNER_LOCK_KEY))
    )


async def _ensure_another_active_owner(
    session: AsyncSession, user_id: uuid.UUID
) -> None:
    """Take the guard's lock, then raise :class:`LastActiveOwnerError` unless an
    ``active`` user other than ``user_id`` holds ``owner``.

    The count runs after the lock is granted, so under READ COMMITTED it sees
    every guarded change committed before: of two concurrent suspensions of
    the last two active Owners, the second one is refused.
    """
    await lock_owner_guard(session)
    others = await session.scalar(
        select(func.count())
        .select_from(UserRole)
        .join(Role, Role.id == UserRole.role_id)
        .join(User, User.id == UserRole.user_id)
        .where(Role.code == OWNER, User.state == ACTIVE, User.id != user_id)
    )
    if not others:
        raise LastActiveOwnerError()


# --- The student-login gate (#35 B1; ADR-0007 D18) ---------------------------------


class StudentLoginGate(Protocol):
    """Decides whether an account holding ``student`` may be ``active``.

    ``ensure_login_allowed`` raises a Problem Details error (``409
    student_profile_required`` or ``409 guardian_consent_required``) to refuse
    and returns ``None`` to allow. It runs in the caller's Unit of Work, after
    the user's row is locked, and never commits."""

    async def ensure_login_allowed(
        self, session: AsyncSession, *, user_id: uuid.UUID
    ) -> None: ...


class StudentLoginGateMissingError(RuntimeError):
    """No :class:`StudentLoginGate` is registered: activating a student fails
    closed (a ``500``) instead of skipping the consent check."""


_student_login_gate: StudentLoginGate | None = None


def register_student_login_gate(gate: StudentLoginGate | None) -> None:
    """Register the gate for this process (``None`` unregisters it; tests
    only). The API app factory, ``app.cli`` and ``app.worker`` register the
    ``families`` implementation."""
    global _student_login_gate
    _student_login_gate = gate


def registered_student_login_gate() -> StudentLoginGate | None:
    """The registered gate, or ``None``."""
    return _student_login_gate


async def lock_user(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Lock the user's row (``FOR UPDATE``) until the transaction ends.

    The lock order of student logins (#35, the #170 lesson): the
    last-active-Owner advisory lock (when needed), then the user row, then
    the student profile row. Changes nothing in the session's identity map."""
    await session.execute(select(User.id).where(User.id == user_id).with_for_update())


async def _ensure_student_login_allowed(
    session: AsyncSession, user_id: uuid.UUID
) -> None:
    gate = _student_login_gate
    if gate is None:
        raise StudentLoginGateMissingError(
            "No student-login gate is registered; refusing to activate a student."
        )
    await lock_user(session, user_id)
    await gate.ensure_login_allowed(session, user_id=user_id)


async def change_user_state(
    session: AsyncSession, *, user_id: uuid.UUID, new_state: str
) -> bool:
    """Move a user to ``new_state`` (:data:`ALLOWED_STATE_TRANSITIONS`).

    The same state is a no-op that returns ``False`` and writes nothing. A
    forbidden transition (or an unknown state) raises
    :class:`InvalidUserStateTransitionError` and changes nothing; an unknown
    user raises :class:`~app.core.authz.RecordNotFoundError`. Moving an
    ``active`` user who holds ``owner`` to ``suspended`` or ``archived``
    raises :class:`LastActiveOwnerError` when no other active Owner exists
    (the guard, see the module docstring). Moving a user who holds
    ``student`` to ``active`` first passes the student-login gate (#35;
    ``409 student_profile_required`` / ``guardian_consent_required``, nothing
    written). Moving to ``suspended`` or ``archived`` revokes every session of
    the user. The state change is audited automatically (``users``
    ``update``).
    """
    user = await _get_user(session, user_id)
    if new_state not in USER_STATES:
        raise InvalidUserStateTransitionError()
    if user.state == new_state:
        return False
    if (user.state, new_state) not in ALLOWED_STATE_TRANSITIONS:
        raise InvalidUserStateTransitionError()
    if new_state == ACTIVE and STUDENT in await _user_roles(session, user_id):
        await _ensure_student_login_allowed(session, user_id)
    if (
        user.state == ACTIVE
        and new_state in _REVOKING_STATES
        and OWNER in await _user_roles(session, user_id)
    ):
        await _ensure_another_active_owner(session, user_id)
    user.state = new_state
    await session.flush()
    reason = _REVOKING_STATES.get(new_state)
    if reason is not None:
        await revoke_user_sessions(session, user_id=user_id, reason=reason)
    return True


async def _role_id(session: AsyncSession, role: str) -> uuid.UUID:
    if role not in ROLE_CODES:
        raise UnknownRoleError()
    role_id: uuid.UUID | None = await session.scalar(
        select(Role.id).where(Role.code == role)
    )
    if role_id is None:
        # The catalog lists it, but the reference seeds have not run.
        raise RuntimeError("Roles are not seeded: run `make seed` first.")
    return role_id


async def _user_role(
    session: AsyncSession, user_id: uuid.UUID, role_id: uuid.UUID
) -> UserRole | None:
    user_role: UserRole | None = await session.scalar(
        select(UserRole).where(UserRole.user_id == user_id, UserRole.role_id == role_id)
    )
    return user_role


async def add_user_role(
    session: AsyncSession, *, user_id: uuid.UUID, role: str
) -> bool:
    """Give the user ``role`` and revoke their sessions (``role_added``).

    Adding a role the user already has is a no-op that returns ``False``. An
    unknown role code raises :class:`UnknownRoleError`; an unknown user
    :class:`~app.core.authz.RecordNotFoundError`. Adding ``student`` to an
    ``active`` user first passes the student-login gate (#35; a ``pending``
    user is checked when activated). No authority checks here (#31).
    Audited automatically (``user_roles`` ``create``).
    """
    role_id = await _role_id(session, role)
    user = await _get_user(session, user_id)
    if await _user_role(session, user_id, role_id) is not None:
        return False
    if role == STUDENT and user.state == ACTIVE:
        await _ensure_student_login_allowed(session, user_id)
    session.add(UserRole(user_id=user_id, role_id=role_id))
    await session.flush()
    await revoke_user_sessions(session, user_id=user_id, reason="role_added")
    return True


async def remove_user_role(
    session: AsyncSession, *, user_id: uuid.UUID, role: str
) -> bool:
    """Take ``role`` from the user and revoke their sessions (``role_removed``).

    Removing a role the user does not have is a no-op that returns ``False``.
    The row is deleted as an ORM instance, so it is audited (``user_roles``
    ``delete``). Removing ``administrator`` also deletes every delegation of
    the user, one ORM delete each (audited ``user_delegations`` ``delete``),
    so none is left behind (#170 B1). The same errors as
    :func:`add_user_role`, plus :class:`LastActiveOwnerError` when ``role`` is
    ``owner``, the user is ``active`` and no other active Owner exists (the
    guard).
    """
    role_id = await _role_id(session, role)
    user = await _get_user(session, user_id)
    user_role = await _user_role(session, user_id, role_id)
    if user_role is None:
        return False
    if role == OWNER and user.state == ACTIVE:
        await _ensure_another_active_owner(session, user_id)
    await session.delete(user_role)
    if role == ADMINISTRATOR:
        for delegation in await session.scalars(
            select(UserDelegation)
            .where(UserDelegation.user_id == user_id)
            .order_by(UserDelegation.id)
        ):
            await session.delete(delegation)
    await session.flush()
    await revoke_user_sessions(session, user_id=user_id, reason="role_removed")
    return True


async def set_password(
    session: AsyncSession, *, user_id: uuid.UUID, new_password: str
) -> None:
    """Apply the password policy, store the new hash and revoke every session
    of the user (``password_changed``).

    A policy failure raises a ``PasswordPolicyError`` subclass and changes
    nothing. Writes no password event of its own (#30 does) and never logs or
    audits the password (``password_hash`` is excluded from ``users`` events).
    """
    user = await _get_user(session, user_id)
    validate_password(new_password, username=user.username)
    user.password_hash = hash_password(new_password)
    await session.flush()
    await revoke_user_sessions(session, user_id=user_id, reason="password_changed")


async def _check_delegation(
    session: AsyncSession,
    *,
    actor_id: uuid.UUID,
    user_id: uuid.UUID,
    permission: str,
    grant: bool,
) -> uuid.UUID:
    """Apply the delegation rules; return the permission's id.

    Both directions: the actor holds ``user.delegation.manage``, the
    permission is delegable and the target exists. A grant also needs a
    non-archived Administrator; a revocation does not (#170 B2), so the
    delegations of an archived or former Administrator can be cleaned up.
    """
    if USER_DELEGATION_MANAGE not in await effective_permissions(session, actor_id):
        raise DelegationForbiddenError()
    if permission not in DELEGABLE_PERMISSIONS:
        raise PermissionNotDelegableError()
    state = await _user_state(session, user_id)
    if state is None or (
        grant
        and (
            state == ARCHIVED
            or ADMINISTRATOR not in await _user_roles(session, user_id)
        )
    ):
        raise DelegationTargetInvalidError()
    permission_id = await session.scalar(
        select(Permission.id).where(Permission.code == permission)
    )
    if permission_id is None:
        # The catalog lists it, but the reference seeds have not run.
        raise RuntimeError("Permissions are not seeded: run `make seed` first.")
    return permission_id


DELEGATION_UNIQUE_CONSTRAINT: Final = "uq_user_delegations_user_id"
"""The unique constraint on ``(user_id, permission_id)`` (migration 0003)."""


async def grant_delegation(
    session: AsyncSession,
    *,
    actor_id: uuid.UUID,
    user_id: uuid.UUID,
    permission: str,
) -> None:
    """Delegate ``permission`` to the Administrator ``user_id`` (idempotent).

    Only an active Owner (``user.delegation.manage``) may grant, only a
    delegable permission, and only to a non-archived Administrator; otherwise
    a typed error is raised and nothing changes. Granting an existing
    delegation is a no-op. A new delegation is written through the ORM, so it
    is audited (``create`` on ``user_delegations``, #22). Callers add
    reauthentication (#31).
    """
    permission_id = await _check_delegation(
        session, actor_id=actor_id, user_id=user_id, permission=permission, grant=True
    )
    if await _delegation(session, user_id, permission_id) is not None:
        return
    try:
        # A savepoint, so a concurrent grant of the same delegation (unique
        # constraint) leaves the caller's transaction usable: still a no-op.
        # Any other integrity error (for example a foreign key) propagates.
        async with session.begin_nested():
            session.add(
                UserDelegation(
                    user_id=user_id,
                    permission_id=permission_id,
                    granted_by_user_id=actor_id,
                )
            )
    except IntegrityError as exc:
        if DELEGATION_UNIQUE_CONSTRAINT not in str(exc.orig):
            raise


async def _delegation(
    session: AsyncSession, user_id: uuid.UUID, permission_id: uuid.UUID
) -> UserDelegation | None:
    delegation: UserDelegation | None = await session.scalar(
        select(UserDelegation).where(
            UserDelegation.user_id == user_id,
            UserDelegation.permission_id == permission_id,
        )
    )
    return delegation


async def revoke_delegation(
    session: AsyncSession,
    *,
    actor_id: uuid.UUID,
    user_id: uuid.UUID,
    permission: str,
) -> None:
    """Remove a delegation: the actor and catalog rules of
    :func:`grant_delegation`, but any existing user is a valid target (#170
    B2: an archived or former Administrator's delegations can be removed).

    Revoking a delegation that does not exist is a no-op. The row is deleted
    through the ORM, so it is audited (``delete`` on ``user_delegations``).
    """
    permission_id = await _check_delegation(
        session, actor_id=actor_id, user_id=user_id, permission=permission, grant=False
    )
    delegation = await _delegation(session, user_id, permission_id)
    if delegation is not None:
        await session.delete(delegation)
        await session.flush()
