"""Roles, the permission catalog and role permission sets (ADR-0007, D4).

Each role's permission set is fixed in code here and written to the database
by the reference seeds (``make seed``). Effective permissions are the union of
a user's roles plus the delegable permissions an Owner granted them while they
hold ``administrator``; record-level policies are applied on top by the
issues that use each permission.

Later milestones add their own permissions to :data:`PERMISSIONS` and
:data:`ROLE_PERMISSIONS`; the unit tests in
``tests/unit/test_identity_permissions.py`` guard the invariants (the Owner
holds everything, the delegable set is exactly ADR-0007's).
"""

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

# --- Roles (spec §2) -------------------------------------------------------------

OWNER = "owner"
ADMINISTRATOR = "administrator"
ACADEMIC_COORDINATOR = "academic_coordinator"
FINANCE_STAFF = "finance_staff"
TUTOR = "tutor"
STUDENT = "student"
GUARDIAN = "guardian"

ROLE_CODES: tuple[str, ...] = (
    OWNER,
    ADMINISTRATOR,
    ACADEMIC_COORDINATOR,
    FINANCE_STAFF,
    TUTOR,
    STUDENT,
    GUARDIAN,
)

STAFF_ROLES: frozenset[str] = frozenset(
    {OWNER, ADMINISTRATOR, ACADEMIC_COORDINATOR, FINANCE_STAFF}
)
"""Staff roles: the strict session class (#25) and reauthenticated invitations (#29)."""

# --- User states (spec §2) -------------------------------------------------------

PENDING = "pending"
ACTIVE = "active"
SUSPENDED = "suspended"
ARCHIVED = "archived"

USER_STATES: tuple[str, ...] = (PENDING, ACTIVE, SUSPENDED, ARCHIVED)

# --- Permissions ---------------------------------------------------------------

USER_VIEW = "user.view"
USER_MANAGE = "user.manage"
USER_INVITE = "user.invite"
USER_ROLE_ASSIGN = "user.role.assign"
USER_ADMIN_ROLE_ASSIGN = "user.admin_role.assign"
USER_OWNER_ROLE_ASSIGN = "user.owner_role.assign"
USER_DELEGATION_MANAGE = "user.delegation.manage"
# S105: a permission code, not a password.
USER_PASSWORD_RESET_ISSUE = "user.password_reset.issue"  # noqa: S105
REGISTRATION_REVIEW = "registration.review"
AUDIT_VIEW = "audit.view"
CATALOG_MANAGE = "catalog.manage"
STUDENT_VIEW = "student.view"
STUDENT_MANAGE = "student.manage"
GUARDIAN_VIEW = "guardian.view"
FAMILY_MANAGE = "family.manage"
CONSENT_RECORD = "consent.record"
PAYROLL_FINALIZE = "payroll.finalize"
SCHEDULE_OVERRIDE = "schedule.override"
FINANCE_REFUND_APPROVE = "finance.refund.approve"


@dataclass(frozen=True)
class PermissionDef:
    """One catalog entry: its code and whether an Owner may delegate it."""

    code: str
    delegable: bool = False


PERMISSIONS: tuple[PermissionDef, ...] = (
    PermissionDef(USER_VIEW),
    PermissionDef(USER_MANAGE),
    PermissionDef(USER_INVITE),
    PermissionDef(USER_ROLE_ASSIGN),
    PermissionDef(USER_ADMIN_ROLE_ASSIGN, delegable=True),
    PermissionDef(USER_OWNER_ROLE_ASSIGN),
    PermissionDef(USER_DELEGATION_MANAGE),
    PermissionDef(USER_PASSWORD_RESET_ISSUE),
    PermissionDef(REGISTRATION_REVIEW),
    PermissionDef(AUDIT_VIEW),
    PermissionDef(CATALOG_MANAGE),
    PermissionDef(STUDENT_VIEW),
    PermissionDef(STUDENT_MANAGE),
    PermissionDef(GUARDIAN_VIEW),
    PermissionDef(FAMILY_MANAGE),
    PermissionDef(CONSENT_RECORD),
    PermissionDef(PAYROLL_FINALIZE, delegable=True),
    PermissionDef(SCHEDULE_OVERRIDE, delegable=True),
    PermissionDef(FINANCE_REFUND_APPROVE, delegable=True),
)
"""The permission catalog, in seed order."""

PERMISSION_CODES: frozenset[str] = frozenset(p.code for p in PERMISSIONS)

DELEGABLE_PERMISSIONS: frozenset[str] = frozenset(
    p.code for p in PERMISSIONS if p.delegable
)
"""Granted only by an Owner, only to Administrators (ADR-0007)."""

ROLE_PERMISSIONS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        OWNER: PERMISSION_CODES,
        ADMINISTRATOR: frozenset(
            {
                USER_VIEW,
                USER_MANAGE,
                USER_INVITE,
                USER_ROLE_ASSIGN,
                USER_PASSWORD_RESET_ISSUE,
                REGISTRATION_REVIEW,
                AUDIT_VIEW,
                CATALOG_MANAGE,
                STUDENT_VIEW,
                STUDENT_MANAGE,
                GUARDIAN_VIEW,
                FAMILY_MANAGE,
                CONSENT_RECORD,
            }
        ),
        ACADEMIC_COORDINATOR: frozenset({STUDENT_VIEW, STUDENT_MANAGE, GUARDIAN_VIEW}),
        FINANCE_STAFF: frozenset({STUDENT_VIEW, GUARDIAN_VIEW}),
        # Record policies limit these to assigned students, self and linked
        # children (#21, #33-#35).
        TUTOR: frozenset({STUDENT_VIEW}),
        STUDENT: frozenset({STUDENT_VIEW, CONSENT_RECORD}),
        GUARDIAN: frozenset({STUDENT_VIEW, GUARDIAN_VIEW, CONSENT_RECORD}),
    }
)
"""Each role's fixed permission set (seeded into ``role_permissions``)."""


def may_assign_role(actor_permissions: Collection[str], role: str) -> bool:
    """Whether an actor with ``actor_permissions`` may assign or remove ``role``.

    Authority only (ADR-0007, D4): callers add reauthentication, audit and the
    last-active-Owner guard. An unknown role is never assignable.
    """
    if role == OWNER:
        return USER_OWNER_ROLE_ASSIGN in actor_permissions
    if role == ADMINISTRATOR:
        return USER_ADMIN_ROLE_ASSIGN in actor_permissions
    if role in ROLE_CODES:
        return USER_ROLE_ASSIGN in actor_permissions
    return False


def resolve_permissions(
    state: str,
    roles: Iterable[str],
    delegated: Iterable[str],
    *,
    role_permissions: Mapping[str, Collection[str]] = ROLE_PERMISSIONS,
) -> frozenset[str]:
    """A user's effective permissions (pure; ADR-0007, D4).

    - Only an ``active`` user has permissions; any other state gets none.
    - The union of ``role_permissions[r]`` for each of ``roles`` (unknown roles
      add nothing). The loader passes the seeded mapping from the database.
    - Plus the ``delegated`` codes that are delegable, only while the user
      holds ``administrator``.
    - Codes no longer in the catalog are dropped.
    """
    if state != ACTIVE:
        return frozenset()
    held = set(roles)
    codes: set[str] = set()
    for role in held:
        codes.update(role_permissions.get(role, ()))
    if ADMINISTRATOR in held:
        codes.update(DELEGABLE_PERMISSIONS.intersection(delegated))
    return frozenset(codes & PERMISSION_CODES)
