"""The ``identity.role_permissions`` reference seed (ADR-0007, fixed in code).

The database's role permission sets must equal
:data:`app.modules.identity.permissions.ROLE_PERMISSIONS`, so this seed
**reconciles** the mapping table:

- it inserts the missing (role, permission) pairs, through
  :func:`~app.seeds.upsert.upsert_rows` on the pair's unique key, resolving
  role and permission ids by code;
- it **deletes** the pairs of the mapped roles that the code no longer lists,
  so removing a permission from a role in code revokes it on the next
  ``make seed``.

This is the one documented exception to the M1-17 "seeds never delete" rule
(see :mod:`app.seeds.registry`), and it applies only to ``role_permissions``.
Rows of ``roles`` and ``permissions`` are never deleted; a permission code no
longer in the catalog may stay in its table, and permission resolution ignores
it. SQLAlchemy Core only; never commits.
"""

import uuid
from collections.abc import Collection, Mapping
from dataclasses import dataclass

from sqlalchemy import delete, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.models import Permission, Role, RolePermission
from app.seeds.upsert import UpsertResult, upsert_rows


@dataclass(frozen=True)
class RolePermissionsSeed:
    """Reconciles ``role_permissions`` with ``mapping`` (role code -> codes).

    Only the roles that are keys of ``mapping`` are reconciled.
    """

    name: str
    mapping: Mapping[str, Collection[str]]

    async def apply(self, session: AsyncSession) -> UpsertResult:
        role_ids: dict[str, uuid.UUID] = {
            code: id_ for code, id_ in await session.execute(select(Role.code, Role.id))
        }
        permission_ids: dict[str, uuid.UUID] = {
            code: id_
            for code, id_ in await session.execute(
                select(Permission.code, Permission.id)
            )
        }
        missing_roles = set(self.mapping) - set(role_ids)
        missing_permissions = (
            set().union(*self.mapping.values()) - set(permission_ids)
            if self.mapping
            else set()
        )
        if missing_roles or missing_permissions:
            # Counts only: the seed order (permissions, roles, then this seed)
            # guarantees every code exists.
            raise ValueError(
                f"{self.name}: {len(missing_roles)} role(s) and "
                f"{len(missing_permissions)} permission(s) are not seeded"
            )

        wanted = [
            {"role_id": role_ids[role], "permission_id": permission_ids[code]}
            for role, codes in sorted(self.mapping.items())
            for code in sorted(codes)
        ]
        inserted = await upsert_rows(
            session, RolePermission, wanted, key=("role_id", "permission_id")
        )

        removal = delete(RolePermission).where(
            RolePermission.role_id.in_([role_ids[role] for role in self.mapping])
        )
        if wanted:
            removal = removal.where(
                tuple_(RolePermission.role_id, RolePermission.permission_id).not_in(
                    [(row["role_id"], row["permission_id"]) for row in wanted]
                )
            )
        deleted = await session.scalars(removal.returning(RolePermission.id))
        return UpsertResult(
            inserted=inserted.inserted,
            updated=inserted.updated,
            deleted=len(deleted.all()),
        )
