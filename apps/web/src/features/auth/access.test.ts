import { describe, expect, it } from 'vitest';
import { canAccess, routeRequirement } from './access';
import type { PermissionCode, SessionUser } from './session';

// F6 (pure part): the one access check of the guard and the navigation.

const user: SessionUser = {
  id: 'u1',
  username: 'admin',
  display_name: 'Admin',
  roles: ['administrator'],
  // A delegated permission arrives in `permissions` like any other.
  permissions: ['user.view', 'payroll.finalize'],
};

describe('canAccess', () => {
  it('allows any signed-in user without a requirement', () => {
    expect(canAccess(user)).toBe(true);
    expect(canAccess(user, null)).toBe(true);
    expect(canAccess(user, {})).toBe(true);
  });

  it('allows a held permission, including a delegated one', () => {
    expect(canAccess(user, { permission: 'user.view' })).toBe(true);
    expect(canAccess(user, { permission: 'payroll.finalize' })).toBe(true);
  });

  it('denies a permission the user lacks', () => {
    expect(canAccess(user, { permission: 'audit.view' })).toBe(false);
  });

  it('denies everything without a user', () => {
    expect(canAccess(null)).toBe(false);
    expect(canAccess(undefined, { permission: 'user.view' })).toBe(false);
  });

  it('fails closed on an unknown or malformed permission', () => {
    expect(
      canAccess(user, { permission: 'not.a.permission' as PermissionCode }),
    ).toBe(false);
    expect(
      canAccess(user, { permission: null as unknown as PermissionCode }),
    ).toBe(false);
    expect(
      canAccess(user, {
        permission: ['user.view'] as unknown as PermissionCode,
      }),
    ).toBe(false);
  });
});

describe('routeRequirement', () => {
  it('reads handle.permission', () => {
    expect(routeRequirement({ permission: 'user.view' })).toEqual({
      permission: 'user.view',
    });
  });

  it('is null without a handle or a permission', () => {
    expect(routeRequirement(undefined)).toBeNull();
    expect(routeRequirement({ crumb: 'x' })).toBeNull();
  });

  it('passes a malformed value on, so canAccess denies it', () => {
    const requirement = routeRequirement({ permission: 42 });
    expect(canAccess(user, requirement)).toBe(false);
  });
});
