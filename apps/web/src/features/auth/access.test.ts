import { describe, expect, it } from 'vitest';
import { canAccess, routeRequirement } from './access';
import type { PermissionCode, RoleCode, SessionUser } from './session';

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

describe('canAccess with role (#52 H1)', () => {
  const tutor: SessionUser = { ...user, roles: ['tutor'], permissions: [] };

  it('allows a user who holds the role', () => {
    expect(canAccess(tutor, { role: 'tutor' })).toBe(true);
  });

  it('denies a user without the role, whatever their permissions', () => {
    expect(canAccess(user, { role: 'tutor' })).toBe(false);
    expect(canAccess(tutor, { role: 'tutor', permission: 'user.view' })).toBe(
      false,
    );
  });

  it('fails closed on a malformed role', () => {
    expect(canAccess(tutor, { role: ['tutor'] as unknown as RoleCode })).toBe(
      false,
    );
    expect(canAccess(tutor, { role: null as unknown as RoleCode })).toBe(false);
  });
});

describe('routeRequirement', () => {
  it('reads handle.role', () => {
    expect(routeRequirement({ role: 'tutor' })).toEqual({ role: 'tutor' });
  });

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

describe('canAccess with anyRole and anyOf (#234 A1)', () => {
  const student: SessionUser = { ...user, roles: ['student'], permissions: [] };
  const tutor: SessionUser = { ...user, roles: ['tutor'], permissions: [] };
  const finance: SessionUser = {
    ...user,
    roles: ['finance_staff'],
    permissions: ['schedule.view'],
  };
  const groups = {
    anyOf: [{ permission: 'schedule.view' }, { anyRole: ['student'] }],
  } as const;

  it('anyRole passes with one of the roles and fails without', () => {
    expect(canAccess(student, { anyRole: ['tutor', 'student'] })).toBe(true);
    expect(canAccess(user, { anyRole: ['tutor', 'student'] })).toBe(false);
  });

  it('anyRole fails closed when empty or not an array', () => {
    expect(canAccess(student, { anyRole: [] })).toBe(false);
    expect(
      canAccess(student, { anyRole: 'student' as unknown as RoleCode[] }),
    ).toBe(false);
    expect(
      canAccess(student, { anyRole: [null] as unknown as RoleCode[] }),
    ).toBe(false);
  });

  it('anyOf passes when any entry passes', () => {
    expect(canAccess(finance, groups)).toBe(true);
    expect(canAccess(student, groups)).toBe(true);
    expect(canAccess(tutor, groups)).toBe(false);
    expect(canAccess(user, { ...groups, permission: 'user.view' })).toBe(false);
  });

  it('anyOf with other fields: all of them apply', () => {
    expect(canAccess(finance, { ...groups, anyRole: ['finance_staff'] })).toBe(
      true,
    );
    expect(canAccess(student, { ...groups, anyRole: ['finance_staff'] })).toBe(
      false,
    );
  });

  it('anyOf fails closed when empty, not an array or with bad entries', () => {
    expect(canAccess(finance, { anyOf: [] })).toBe(false);
    expect(
      canAccess(finance, {
        anyOf: { permission: 'schedule.view' } as unknown as [],
      }),
    ).toBe(false);
    expect(
      canAccess(finance, { anyOf: [null, 'schedule.view'] as unknown as [] }),
    ).toBe(false);
  });

  it('anyOf nests', () => {
    expect(
      canAccess(student, {
        anyOf: [{ anyOf: [{ role: 'tutor' }, { anyRole: ['student'] }] }],
      }),
    ).toBe(true);
    expect(canAccess(student, { anyOf: [{ anyOf: [] }] })).toBe(false);
  });

  it('routeRequirement reads anyRole and anyOf', () => {
    expect(routeRequirement(groups)).toEqual(groups);
    expect(routeRequirement({ anyRole: ['student'] })).toEqual({
      anyRole: ['student'],
    });
    expect(canAccess(student, routeRequirement({ anyOf: 'x' }))).toBe(false);
  });
});
