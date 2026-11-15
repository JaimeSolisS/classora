import type { PermissionCode, RoleCode, SessionUser } from './session';

// Route and navigation access (#28 F). UX only: hiding a page or a link
// proves nothing; the API checks every request (spec §16, architecture §8).
// The names mirror the API's route access (#21): `public` routes sit outside
// the guard (only /login and the error elements), every route under the guard
// is `authenticated`, and a route that `require`s a permission declares
// `handle: { permission }`. A page that serves holders of any of several
// permissions (the approval inbox, #40 F1: one review permission per approval
// type) declares `handle: { anyPermission: [...] }`. A page for one role
// only (the tutor's "My offers", #52 H1) declares `handle: { role }`; the API
// still filters by record policy. A page for holders of any of several roles
// declares `anyRole`, and a page that serves several kinds of users (the
// groups pages, #234 A1: staff with schedule.view, or students) declares
// `anyOf: [...]`, which passes when any one of its entries passes.

/** What a route (its `handle`) or a navigation item requires. */
export interface AccessRequirement {
  /** This permission must be held. */
  permission?: PermissionCode;
  /** At least one of these must be held (an empty list allows no one). */
  anyPermission?: readonly PermissionCode[];
  /** The user must hold this role (#52 H1). */
  role?: RoleCode;
  /** At least one of these roles must be held (an empty list allows no one). */
  anyRole?: readonly RoleCode[];
  /**
   * At least one of these requirements must pass (#234 A1). An empty or
   * non-array value allows no one.
   */
  anyOf?: readonly AccessRequirement[];
}

function hasRole(user: SessionUser, role: unknown): boolean {
  return (
    typeof role === 'string' && (user.roles as readonly string[]).includes(role)
  );
}

function holds(user: SessionUser, permission: unknown): boolean {
  return (
    typeof permission === 'string' &&
    (user.permissions as readonly string[]).includes(permission)
  );
}

/**
 * Whether `user` may open something with `requirement`. No requirement means
 * any signed-in user. `permission` must be held, and `anyPermission` needs at
 * least one held entry; when both are given, both apply. Fails closed: a
 * permission that is not a string, or that the user's permissions do not list
 * (including a code that is not in the catalog), is denied, and an
 * `anyPermission` that is not a non-empty array is denied. `role` must be one
 * of the user's roles (a value that is not a string is denied); `anyRole`
 * needs at least one of the user's roles, and `anyOf` at least one entry
 * that passes this same check (both deny anything but a non-empty array).
 */
export function canAccess(
  user: SessionUser | null | undefined,
  requirement?: AccessRequirement | null,
): boolean {
  if (!user) {
    return false;
  }
  if (requirement === undefined || requirement === null) {
    return true;
  }
  const { permission, anyPermission, role, anyRole, anyOf } = requirement as {
    permission?: unknown;
    anyPermission?: unknown;
    role?: unknown;
    anyRole?: unknown;
    anyOf?: unknown;
  };
  if (permission !== undefined && !holds(user, permission)) {
    return false;
  }
  if (role !== undefined && !hasRole(user, role)) {
    return false;
  }
  if (
    anyPermission !== undefined &&
    !(
      Array.isArray(anyPermission) &&
      anyPermission.some((entry) => holds(user, entry))
    )
  ) {
    return false;
  }
  if (
    anyRole !== undefined &&
    !(Array.isArray(anyRole) && anyRole.some((entry) => hasRole(user, entry)))
  ) {
    return false;
  }
  if (
    anyOf !== undefined &&
    !(
      Array.isArray(anyOf) &&
      anyOf.some(
        (entry: unknown) =>
          typeof entry === 'object' && entry !== null && canAccess(user, entry),
      )
    )
  ) {
    return false;
  }
  return true;
}

/** The access requirement in a route's `handle`, if it declares one. */
export function routeRequirement(handle: unknown): AccessRequirement | null {
  if (typeof handle !== 'object' || handle === null) {
    return null;
  }
  const requirement: AccessRequirement = {};
  if ('permission' in handle) {
    // Passed on unchecked: canAccess denies anything that is not a held code.
    requirement.permission = handle.permission as PermissionCode;
  }
  if ('anyPermission' in handle) {
    requirement.anyPermission =
      handle.anyPermission as readonly PermissionCode[];
  }
  if ('role' in handle) {
    // Passed on unchecked: canAccess denies anything that is not a held role.
    requirement.role = handle.role as RoleCode;
  }
  if ('anyRole' in handle) {
    requirement.anyRole = handle.anyRole as readonly RoleCode[];
  }
  if ('anyOf' in handle) {
    // Passed on unchecked: canAccess denies a malformed list or entry.
    requirement.anyOf = handle.anyOf as readonly AccessRequirement[];
  }
  return Object.keys(requirement).length > 0 ? requirement : null;
}
