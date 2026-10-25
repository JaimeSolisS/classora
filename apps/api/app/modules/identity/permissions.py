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
# The approval type of self-registration requests (#40 D), reviewed with
# REGISTRATION_REVIEW.
REGISTRATION_APPROVAL_TYPE = "identity.registration"
AUDIT_VIEW = "audit.view"
CATALOG_MANAGE = "catalog.manage"
SETTINGS_MANAGE = "settings.manage"
STUDENT_VIEW = "student.view"
STUDENT_MANAGE = "student.manage"
GUARDIAN_VIEW = "guardian.view"
FAMILY_MANAGE = "family.manage"
CONSENT_RECORD = "consent.record"
PAYROLL_FINALIZE = "payroll.finalize"
SCHEDULE_OVERRIDE = "schedule.override"
FINANCE_REFUND_APPROVE = "finance.refund.approve"
# Tutor profiles (#43, owner decision E1 (a)): the record policy limits a
# tutor to their own profile (staff/permissions.py).
TUTOR_VIEW = "tutor.view"
TUTOR_MANAGE = "tutor.manage"
# Tutor qualification requests (#44): who sees and decides the
# staff.qualification approval requests.
QUALIFICATION_REVIEW = "qualification.review"
# Class sessions (#48; ADR-0005 D9): who reads every session and who
# schedules them. schedule.assign (#51, its first user; #52 reuses it): who
# sees tutor eligibility and replacement suggestions and assigns tutors
# (Owner, Administrator, Academic coordinator; not delegable).
SCHEDULE_VIEW = "schedule.view"
SCHEDULE_MANAGE = "schedule.manage"
SCHEDULE_ASSIGN = "schedule.assign"
# Course templates (#64, owner decision E3 (a)): Finance Staff read them
# (pricing later); tutors and families see a cohort's syllabus through the
# cohort's record policies (#65, #67), not through these.
COURSE_VIEW = "course.view"
COURSE_MANAGE = "course.manage"
# Attendance (#76, owner decision E1 (a)): who corrects a session's
# attendance with a reason (Owner, Administrator, Academic Coordinator; not
# delegable). Tutors mark and schedule.view holders read through the
# attendance record policy (scheduling/attendance_rules.py).
ATTENDANCE_CORRECT = "attendance.correct"


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
    PermissionDef(SETTINGS_MANAGE),
    PermissionDef(STUDENT_VIEW),
    PermissionDef(STUDENT_MANAGE),
    PermissionDef(GUARDIAN_VIEW),
    PermissionDef(FAMILY_MANAGE),
    PermissionDef(CONSENT_RECORD),
    PermissionDef(TUTOR_VIEW),
    PermissionDef(TUTOR_MANAGE),
    PermissionDef(QUALIFICATION_REVIEW),
    PermissionDef(SCHEDULE_VIEW),
    PermissionDef(SCHEDULE_MANAGE),
    PermissionDef(SCHEDULE_ASSIGN),
    PermissionDef(COURSE_VIEW),
    PermissionDef(COURSE_MANAGE),
    PermissionDef(ATTENDANCE_CORRECT),
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
                SETTINGS_MANAGE,
                STUDENT_VIEW,
                STUDENT_MANAGE,
                GUARDIAN_VIEW,
                FAMILY_MANAGE,
                CONSENT_RECORD,
                TUTOR_VIEW,
                TUTOR_MANAGE,
                QUALIFICATION_REVIEW,
                SCHEDULE_VIEW,
                SCHEDULE_MANAGE,
                SCHEDULE_ASSIGN,
                COURSE_VIEW,
                COURSE_MANAGE,
                ATTENDANCE_CORRECT,
            }
        ),
        ACADEMIC_COORDINATOR: frozenset(
            {
                STUDENT_VIEW,
                STUDENT_MANAGE,
                GUARDIAN_VIEW,
                TUTOR_VIEW,
                SCHEDULE_VIEW,
                SCHEDULE_ASSIGN,
                COURSE_VIEW,
                COURSE_MANAGE,
                ATTENDANCE_CORRECT,
            }
        ),
        FINANCE_STAFF: frozenset(
            {STUDENT_VIEW, GUARDIAN_VIEW, SCHEDULE_VIEW, COURSE_VIEW}
        ),
        # Record policies limit these to assigned students, self and linked
        # children (#21, #33-#35), and tutor.view to the tutor's own profile
        # (#43).
        TUTOR: frozenset({STUDENT_VIEW, TUTOR_VIEW}),
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


def may_manage_user(
    actor_permissions: Collection[str],
    *,
    actor_id: object,
    target_id: object,
    target_roles: Iterable[str],
) -> bool:
    """Whether the actor may manage the target's account (#31; ADR-0007 D4 as
    amended by #31): edit the profile, suspend, archive, reactivate and change
    roles (#170).

    Only for a holder of ``user.manage``. Then true when the actor **is** the
    target (their own profile; the routes refuse self state changes), or when
    :func:`may_assign_role` holds for **every** role the target has. A target
    without roles can be managed by any holder of ``user.manage``. So an
    Administrator manages neither an Owner nor another Administrator, unless
    they hold the ``user.admin_role.assign`` delegation (Administrators only).
    """
    if USER_MANAGE not in actor_permissions:
        return False
    if actor_id == target_id:
        return True
    return all(may_assign_role(actor_permissions, role) for role in target_roles)


OWNER_ONLY_RESET_TARGETS: frozenset[str] = frozenset({OWNER, ADMINISTRATOR})
"""Only a holder of the ``owner`` role may reset a user holding one of these
roles (ADR-0007 D12)."""


def may_reset_password_of(
    actor_roles: Collection[str],
    *,
    actor_id: object,
    target_id: object,
    target_roles: Iterable[str],
) -> bool:
    """The #30 authority rule for issuing a password reset (ADR-0007 D12),
    without the permission and state checks: nobody resets their own account,
    and only the ``owner`` **role** (a delegation does not count) resets an
    Owner or an Administrator."""
    if actor_id == target_id:
        return False
    return not (OWNER_ONLY_RESET_TARGETS.intersection(target_roles)) or (
        OWNER in actor_roles
    )


@dataclass(frozen=True)
class UserActions:
    """What the caller may do to one user (``UserDetail.allowed_actions``)."""

    edit: bool
    suspend: bool
    archive: bool
    reactivate: bool
    reset_password: bool
    assignable_roles: tuple[str, ...]
    """Roles the caller may add **and** remove on this target, in
    :data:`ROLE_CODES` order (#170 D1)."""
    manage_delegations: bool
    """The caller may grant or revoke this target's delegations (#170 D1)."""


def assignable_roles(
    actor_permissions: Collection[str],
    *,
    actor_id: object,
    target_id: object,
    target_roles: Iterable[str],
) -> tuple[str, ...]:
    """The roles the actor may add to or remove from the target (#170 A2, D1):
    none unless :func:`may_manage_user`; otherwise every role for which
    :func:`may_assign_role` holds, in :data:`ROLE_CODES` order."""
    if not may_manage_user(
        actor_permissions,
        actor_id=actor_id,
        target_id=target_id,
        target_roles=target_roles,
    ):
        return ()
    return tuple(r for r in ROLE_CODES if may_assign_role(actor_permissions, r))


def may_manage_delegations(
    actor_permissions: Collection[str], *, target_roles: Iterable[str]
) -> bool:
    """Whether the delegations section is editable (#170 D1): the actor holds
    ``user.delegation.manage`` (Owners only) and the target holds
    ``administrator``. The route also lets an Owner revoke from any target
    (#170 B2) and refuses grants to an archived Administrator."""
    return USER_DELEGATION_MANAGE in actor_permissions and ADMINISTRATOR in set(
        target_roles
    )


def allowed_user_actions(
    *,
    actor_id: object,
    actor_roles: Collection[str],
    actor_permissions: Collection[str],
    target_id: object,
    target_state: str,
    target_roles: Collection[str],
    target_password_set: bool,
) -> UserActions:
    """The actions the user detail page offers (#31 E2), computed with the
    same rules as the routes. The routes stay the authority.

    - ``edit``: :func:`may_manage_user`.
    - ``suspend``: an ``active`` target; ``archive``: ``active``,
      ``suspended``, or ``pending`` without a password (an open invitation);
      ``reactivate``: ``suspended`` or ``archived``. All three need
      :func:`may_manage_user` and are false for the caller's own account.
    - ``reset_password``: ``user.password_reset.issue``, an ``active`` target
      and :func:`may_reset_password_of` (#30 C2).
    - ``assignable_roles``: :func:`assignable_roles`; ``manage_delegations``:
      :func:`may_manage_delegations` (#170 D1).

    The last-active-Owner guard is not considered: the server answers ``409
    last_active_owner``.
    """
    manage = may_manage_user(
        actor_permissions,
        actor_id=actor_id,
        target_id=target_id,
        target_roles=target_roles,
    )
    change_state = manage and actor_id != target_id
    return UserActions(
        edit=manage,
        suspend=change_state and target_state == ACTIVE,
        archive=change_state
        and (
            target_state in (ACTIVE, SUSPENDED)
            or (target_state == PENDING and not target_password_set)
        ),
        reactivate=change_state and target_state in (SUSPENDED, ARCHIVED),
        reset_password=USER_PASSWORD_RESET_ISSUE in actor_permissions
        and target_state == ACTIVE
        and may_reset_password_of(
            actor_roles,
            actor_id=actor_id,
            target_id=target_id,
            target_roles=target_roles,
        ),
        assignable_roles=assignable_roles(
            actor_permissions,
            actor_id=actor_id,
            target_id=target_id,
            target_roles=target_roles,
        ),
        manage_delegations=may_manage_delegations(
            actor_permissions, target_roles=target_roles
        ),
    )


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
