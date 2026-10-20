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
  privacy_notice_required: false,
  preferences: { language: 'es', theme: 'system' },
  center_time_zone: 'America/Mexico_City',
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

describe('canAccess with anyPermission (#40 F1)', () => {
  it('allows a user who holds one of the permissions', () => {
    expect(
      canAccess(user, { anyPermission: ['registration.review', 'user.view'] }),
    ).toBe(true);
    expect(canAccess(user, { anyPermission: ['payroll.finalize'] })).toBe(true);
  });

  it('denies a user who holds none of them', () => {
    expect(
      canAccess(user, { anyPermission: ['registration.review', 'audit.view'] }),
    ).toBe(false);
  });

  it('denies an empty list', () => {
    expect(canAccess(user, { anyPermission: [] })).toBe(false);
  });

  it('applies permission and anyPermission together', () => {
    expect(
      canAccess(user, {
        permission: 'user.view',
        anyPermission: ['payroll.finalize'],
      }),
    ).toBe(true);
    expect(
      canAccess(user, {
        permission: 'audit.view',
        anyPermission: ['payroll.finalize'],
      }),
    ).toBe(false);
    expect(
      canAccess(user, {
        permission: 'user.view',
        anyPermission: ['audit.view'],
      }),
    ).toBe(false);
  });

  it('fails closed on a malformed list', () => {
    expect(
      canAccess(user, {
        anyPermission: 'user.view' as unknown as PermissionCode[],
      }),
    ).toBe(false);
    expect(
      canAccess(user, {
        anyPermission: [null, 42] as unknown as PermissionCode[],
      }),
    ).toBe(false);
    expect(
      canAccess(user, {
        anyPermission: null as unknown as PermissionCode[],
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

  it('reads handle.anyPermission', () => {
    expect(
      routeRequirement({ anyPermission: ['registration.review'] }),
    ).toEqual({ anyPermission: ['registration.review'] });
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
