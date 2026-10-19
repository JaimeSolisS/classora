"""Identity tables (spec §2, §12; ADR-0007).

- ``users``: a login account. ``username`` is stored normalized (lower-case
  ASCII, see ``service.normalize_username``) and the CHECK enforces the form,
  so no ``citext`` extension is needed. ``password_hash`` is ``NULL`` until the
  account has a password (an unredeemed invitation, #29).
- ``roles``, ``permissions``: reference rows seeded from
  :mod:`app.modules.identity.permissions`.
- ``role_permissions``, ``user_roles``: the role permission sets and each
  user's roles (several per user).
- ``user_delegations``: delegable permissions an Owner granted to an
  Administrator. A composite foreign key to ``permissions (id, delegable)``
  plus ``permission_delegable = true`` makes the database reject a delegation
  of a non-delegable permission.
- ``auth_sessions``: login sessions (#23, ADR-0007 D1, D8). Only the SHA-256
  hash of the session token is stored; the token itself exists only in the
  cookie. ``revoked_at`` ``NULL`` means the session was not revoked; it may
  still have expired (``expires_at``, ``idle_timeout_seconds``, #25).
- ``account_tokens``: single-use account tokens (#29; ADR-0007 D11): an
  an invitation (#29) or a password reset (#30) (``kind``). Only the SHA-256
  hash of the token is stored. A partial unique index allows at most one unused,
  unreplaced token per user and kind.

No foreign key cascades: archived users keep their history (spec §2, §16).

``users``, ``user_roles`` and ``user_delegations`` are :class:`~app.core.audit.Audited`
(#22): every change is an audit event, and ``password_hash`` is never stored in
one. ``roles``, ``permissions`` and ``role_permissions`` are reference data
fixed in code (ADR-0007) and are not audited. ``auth_sessions`` is not audited
either: it holds token hashes and changes on every login; login and logout
write explicit events instead (``app.modules.identity.sessions``).
``account_tokens`` is not audited for the same reason; invitations write
named events instead (``app.modules.identity.invitations``).
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.audit import Audited
from app.core.models import Model, VersionedMixin
from app.modules.identity.permissions import USER_STATES

USERNAME_PATTERN = "^[a-z0-9._-]{3,64}$"


class User(Audited, VersionedMixin, Model):
    __tablename__ = "users"
    __audit_exclude__ = frozenset({"password_hash"})
    __table_args__ = (
        CheckConstraint(f"username ~ '{USERNAME_PATTERN}'", name="username_format"),
        CheckConstraint("display_name ~ '[^[:space:]]'", name="display_name_not_blank"),
        CheckConstraint(
            "state IN ({})".format(", ".join(f"'{s}'" for s in USER_STATES)),
            name="state_valid",
        ),
    )

    username: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(Text, server_default=text("'pending'"))


class Role(Model):
    __tablename__ = "roles"

    code: Mapped[str] = mapped_column(String(64), unique=True)


class Permission(Model):
    __tablename__ = "permissions"
    # Target of the composite foreign key from user_delegations.
    __table_args__ = (UniqueConstraint("id", "delegable"),)

    code: Mapped[str] = mapped_column(String(100), unique=True)
    delegable: Mapped[bool] = mapped_column(Boolean)


class RolePermission(Model):
    __tablename__ = "role_permissions"
    __table_args__ = (UniqueConstraint("role_id", "permission_id"),)

    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("roles.id", ondelete="RESTRICT")
    )
    permission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("permissions.id", ondelete="RESTRICT")
    )


class UserRole(Audited, Model):
    __tablename__ = "user_roles"
    __table_args__ = (UniqueConstraint("user_id", "role_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    # Indexed to find the users of a role (for example every Owner).
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("roles.id", ondelete="RESTRICT"), index=True
    )


class UserDelegation(Audited, Model):
    __tablename__ = "user_delegations"
    __table_args__ = (
        UniqueConstraint("user_id", "permission_id"),
        ForeignKeyConstraint(
            ["permission_id", "permission_delegable"],
            ["permissions.id", "permissions.delegable"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("permission_delegable", name="permission_delegable"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    permission_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    # Always true; it exists only so the composite foreign key can require a
    # delegable permission.
    permission_delegable: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )
    granted_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )


class AuthSession(Model):
    """One login session (ADR-0007 D1, D8). Not audited (see the module doc).

    Expiry (#25): ``expires_at`` is the absolute end and
    ``idle_timeout_seconds`` the idle limit, both fixed at login from the
    user's role class (``sessions.session_limits``). A session is expired when
    ``now >= expires_at`` or ``now >= last_seen_at + idle_timeout_seconds``;
    that is computed, never stored.

    Reauthentication (#26; ADR-0007 D10): ``reauthenticated_at`` is the time of
    the session's last password reauthentication, ``NULL`` until the first.
    The 5-minute window is computed from it (``app.core.reauth``), never
    stored. A new session (including a rotated one) starts with ``NULL``.
    """

    __tablename__ = "auth_sessions"
    __table_args__ = (
        CheckConstraint("octet_length(token_hash) = 32", name="token_hash_length"),
        CheckConstraint(
            "idle_timeout_seconds > 0", name="idle_timeout_seconds_positive"
        ),
    )

    # Indexed: #25 revokes every session of a user.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    # sha256(token): the raw token is never stored.
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, unique=True)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # The absolute expiry (#25).
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    idle_timeout_seconds: Mapped[int] = mapped_column(Integer)
    # The last password reauthentication in this session (#26).
    reauthenticated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


ACCOUNT_TOKEN_KINDS: tuple[str, ...] = ("invitation", "password_reset")
"""The kinds of :class:`AccountToken`: invitations (#29) and password resets
(#30, which widened the CHECK in migration 0010)."""


class AccountToken(Model):
    """One single-use account token (#29; ADR-0007 D11). Not audited (see the
    module doc).

    ``token_hash`` is ``sha256(token)``: the raw token is never stored. A token
    is valid while ``used_at`` and ``revoked_at`` are ``NULL`` and ``now <
    expires_at`` (``app.modules.identity.tokens``). ``revoked_at`` is set when
    a newer token of the same user and kind replaces it. ``issued_by_user_id``
    is ``NULL`` only for tokens issued by a shell command (#30).
    """

    __tablename__ = "account_tokens"
    __table_args__ = (
        CheckConstraint(
            "kind IN ({})".format(", ".join(f"'{k}'" for k in ACCOUNT_TOKEN_KINDS)),
            name="kind_valid",
        ),
        CheckConstraint("octet_length(token_hash) = 32", name="token_hash_length"),
        CheckConstraint(
            "used_at IS NULL OR revoked_at IS NULL", name="used_or_revoked"
        ),
        Index(
            "uq_account_tokens_live_user_kind",
            "user_id",
            "kind",
            unique=True,
            postgresql_where=text("used_at IS NULL AND revoked_at IS NULL"),
        ),
    )

    # Indexed: the invitation list and reissue find a user's tokens.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    kind: Mapped[str] = mapped_column(Text)
    # sha256(token): the raw token is never stored.
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    issued_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
