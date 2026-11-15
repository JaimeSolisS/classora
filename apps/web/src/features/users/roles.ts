import type { ParseKeys } from 'i18next';
import { canAccess } from '@/features/auth/access';
import type {
  PermissionCode,
  RoleCode,
  SessionUser,
} from '@/features/auth/session';

// Role names and who may assign which role (ADR-0007 D4), for the user
// administration pages (#29 invitations, #169 registration requests, #31).
// UX only: the API decides with may_assign_role.

/** Every role, in the spec's order (spec §2). */
export const ROLE_ORDER: readonly RoleCode[] = [
  'owner',
  'administrator',
  'academic_coordinator',
  'finance_staff',
  'tutor',
  'student',
  'guardian',
];

/** Staff roles: inviting one asks for the password (ADR-0007 D10). */
export const STAFF_ROLES: ReadonlySet<RoleCode> = new Set<RoleCode>([
  'owner',
  'administrator',
  'academic_coordinator',
  'finance_staff',
]);

/** The `common` catalog key of a role's name. */
export function roleNameKey(role: RoleCode): ParseKeys<'common'> {
  return `roles.${role}`;
}

/** The permission that lets a user assign `role`. */
export function assignPermission(role: RoleCode): PermissionCode {
  if (role === 'owner') {
    return 'user.owner_role.assign';
  }
  if (role === 'administrator') {
    return 'user.admin_role.assign';
  }
  return 'user.role.assign';
}

/** Whether `user` may assign `role` (fails closed without a user). */
export function canAssignRole(
  user: SessionUser | null | undefined,
  role: RoleCode,
): boolean {
  return canAccess(user, { permission: assignPermission(role) });
}

/** The roles `user` may assign, in {@link ROLE_ORDER}. */
export function assignableRoles(
  user: SessionUser | null | undefined,
): RoleCode[] {
  return ROLE_ORDER.filter((role) => canAssignRole(user, role));
}
