// The consent section's mode (#35 I2): which actions the viewer gets, from
// their roles. UX only; the API applies the record policy.

export type ConsentMode = 'staff' | 'guardian' | 'student';

const STAFF_ROLES = new Set(['owner', 'administrator']);

/** The viewer's mode from their roles (staff first), or null. */
export function consentMode(roles: readonly string[]): ConsentMode | null {
  if (roles.some((role) => STAFF_ROLES.has(role))) {
    return 'staff';
  }
  if (roles.includes('guardian')) {
    return 'guardian';
  }
  if (roles.includes('student')) {
    return 'student';
  }
  return null;
}
