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

No foreign key cascades: archived users keep their history (spec §2, §16).
"""

import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Model, VersionedMixin
from app.modules.identity.permissions import USER_STATES

USERNAME_PATTERN = "^[a-z0-9._-]{3,64}$"


class User(VersionedMixin, Model):
    __tablename__ = "users"
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


class UserRole(Model):
    __tablename__ = "user_roles"
    __table_args__ = (UniqueConstraint("user_id", "role_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    # Indexed to find the users of a role (for example every Owner).
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("roles.id", ondelete="RESTRICT"), index=True
    )


class UserDelegation(Model):
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
