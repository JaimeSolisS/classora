import type { PermissionCode, SessionUser } from './session';

// Route and navigation access (#28 F). UX only: hiding a page or a link
// proves nothing; the API checks every request (spec §16, architecture §8).
// The names mirror the API's route access (#21): `public` routes sit outside
// the guard (only /login and the error elements), every route under the guard
// is `authenticated`, and a route that `require`s a permission declares
// `handle: { permission }`.

/** What a route (its `handle`) or a navigation item requires. */
export interface AccessRequirement {
  permission?: PermissionCode;
}

/**
 * Whether `user` may open something with `requirement`. No requirement means
 * any signed-in user. Fails closed: a permission that is not a string, or that
 * the user's permissions do not list (including a code that is not in the
 * catalog), is denied.
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
  if (!('permission' in requirement) || requirement.permission === undefined) {
    return true;
  }
  const { permission } = requirement as { permission: unknown };
  return (
    typeof permission === 'string' &&
    (user.permissions as readonly string[]).includes(permission)
  );
}

/** The access requirement in a route's `handle`, if it declares one. */
export function routeRequirement(handle: unknown): AccessRequirement | null {
  if (typeof handle !== 'object' || handle === null) {
    return null;
  }
  if (!('permission' in handle)) {
    return null;
  }
  // Passed on unchecked: canAccess denies anything that is not a held code.
  return { permission: handle.permission as PermissionCode };
}
