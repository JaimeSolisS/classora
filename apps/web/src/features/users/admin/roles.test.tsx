import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { applyLanguage } from '@/i18n';
import { CSRF_HEADER, getCsrfToken } from '@/features/auth/csrf';
import type { RoleCode, SessionUserBody } from '@/features/auth/session';
import { useUiStore } from '@/stores/ui';
import {
  meAs,
  problem,
  stubApi,
  TEST_USER,
  type ApiStub,
  type Responder,
} from '@/test/api-stub';
import { alerts, click, fieldByLabel, typeInto } from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  type RenderedApp,
} from '@/test/render-app';
import type { UserDetail } from './api';

// #170 G1–G6: the roles editor and the delegated permissions on the user
// detail page, behind a stubbed fetch: which boxes are disabled, the body
// sent, the #28 password dialog and the single retry, the errors in each
// section, and the local sign-out after changing your own roles (no
// /auth/logout request). Tokens are low-entropy placeholders.

const TUTOR_ID = '00000000-0000-4000-8000-0000000000a1';
const ADMIN_ID = '00000000-0000-4000-8000-0000000000a2';
const detail = (id: string) => `GET /api/v1/users/${id}`;
const rolesRoute = (id: string) => `PUT /api/v1/users/${id}/roles`;
const delegationsRoute = (id: string) => `PUT /api/v1/users/${id}/delegations`;
const REAUTH = 'POST /api/v1/auth/reauthenticate';
const LOGOUT = 'POST /api/v1/auth/logout';
const LIST = 'GET /api/v1/users';

const EVERY_ROLE: RoleCode[] = [
  'owner',
  'administrator',
  'academic_coordinator',
  'finance_staff',
  'tutor',
  'student',
  'guardian',
];
const NOT_PRIVILEGED: RoleCode[] = EVERY_ROLE.slice(2);

const OWNER: SessionUserBody = {
  ...TEST_USER,
  roles: ['owner'],
  permissions: [
    'user.view',
    'user.manage',
    'user.role.assign',
    'user.admin_role.assign',
    'user.owner_role.assign',
    'user.delegation.manage',
  ],
};

const TUTOR: UserDetail = {
  id: TUTOR_ID,
  username: 'ana.tutor',
  display_name: 'Ana Tutor',
  state: 'active',
  roles: ['tutor', 'guardian'],
  created_at: '2026-10-01T15:00:00Z',
  email: null,
  phone: null,
  version: 3,
  updated_at: '2026-10-02T15:00:00Z',
  delegations: [],
  password_set: true,
  allowed_actions: {
    edit: true,
    suspend: true,
    archive: true,
    reactivate: false,
    reset_password: true,
    assignable_roles: EVERY_ROLE,
    manage_delegations: false,
  },
};

const ADMIN: UserDetail = {
  ...TUTOR,
  id: ADMIN_ID,
  username: 'bea.admin',
  display_name: 'Bea Admin',
  roles: ['administrator'],
  delegations: ['finance.refund.approve'],
  version: 5,
  allowed_actions: { ...TUTOR.allowed_actions, manage_delegations: true },
};

/** The signed-in Owner's own record. */
const SELF: UserDetail = {
  ...TUTOR,
  id: TEST_USER.id,
  username: TEST_USER.username,
  display_name: TEST_USER.display_name,
  roles: ['owner'],
  version: 7,
  allowed_actions: {
    ...TUTOR.allowed_actions,
    suspend: false,
    archive: false,
    reset_password: false,
  },
};

let app: RenderedApp | undefined;

const main = () => app!.container.querySelector('main')!;
const dialogs = () => [...document.querySelectorAll('[role="dialog"]')];
const dialogTitled = (title: string) =>
  (dialogs().find((d) => d.querySelector('h2')?.textContent === title) ??
    null) as HTMLElement | null;

function section(title: string): HTMLElement {
  const heading = [...main().querySelectorAll('h2')].find(
    (h) => h.textContent === title,
  );
  const found = heading?.closest('section');
  if (!found) {
    throw new Error(`No section "${title}"`);
  }
  return found;
}

/** The checkbox labelled `text` (Radix renders a button with role=checkbox). */
function box(root: ParentNode, text: string): HTMLButtonElement {
  const label = [...root.querySelectorAll('label')].find(
    (l) => l.textContent === text,
  );
  const element = label ? document.getElementById(label.htmlFor) : null;
  if (
    !(element instanceof HTMLButtonElement) ||
    element.getAttribute('role') !== 'checkbox'
  ) {
    throw new Error(`No checkbox labelled "${text}"`);
  }
  return element;
}

const checked = (element: HTMLElement) =>
  element.getAttribute('aria-checked') === 'true';

function stub(
  user: SessionUserBody = OWNER,
  routes: Record<string, Responder | Responder[]> = {},
): ApiStub {
  return stubApi({
    'GET /api/v1/auth/me': meAs(user),
    [REAUTH]: () =>
      jsonResponse(200, { reauthenticated_until: '2026-10-19T18:05:00Z' }),
    [LIST]: () =>
      jsonResponse(200, { items: [], total: 0, limit: 25, offset: 0 }),
    [detail(TUTOR_ID)]: () => jsonResponse(200, TUTOR),
    [detail(ADMIN_ID)]: () => jsonResponse(200, ADMIN),
    [detail(SELF.id)]: () => jsonResponse(200, SELF),
    ...routes,
  });
}

async function confirmPassword() {
  const dialog = dialogTitled('Confirm your password');
  expect(dialog).not.toBeNull();
  typeInto(fieldByLabel(dialog!, 'Password'), 'my-own-password');
  await click(buttonNamed(dialog!, 'Confirm'));
}

beforeEach(() => {
  applyLanguage('en');
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.localStorage.clear();
  window.sessionStorage.clear();
});

// --- G1: the roles editor -----------------------------------------------------------

describe('the roles editor (G1)', () => {
  it('a fieldset with seven checkboxes; the held roles are checked', async () => {
    stub();
    app = await renderApp(`/users/${TUTOR_ID}`);

    const roles = section('Roles');
    expect(roles.querySelector('fieldset legend')?.textContent).toBe('Roles');
    expect(roles.querySelectorAll('[role="checkbox"]')).toHaveLength(7);
    expect(checked(box(roles, 'Tutor'))).toBe(true);
    expect(checked(box(roles, 'Parent or guardian'))).toBe(true);
    expect(checked(box(roles, 'Owner'))).toBe(false);
    expect(main().textContent).not.toContain("You can't change this role.");
  });

  it('disables exactly the roles outside assignable_roles, with the reason', async () => {
    stub(OWNER, {
      [detail(TUTOR_ID)]: () =>
        jsonResponse(200, {
          ...TUTOR,
          allowed_actions: {
            ...TUTOR.allowed_actions,
            assignable_roles: NOT_PRIVILEGED,
          },
        }),
    });
    app = await renderApp(`/users/${TUTOR_ID}`);

    const roles = section('Roles');
    const disabled = [...roles.querySelectorAll('[role="checkbox"]')]
      .filter((b) => (b as HTMLButtonElement).disabled)
      .map((b) => roles.querySelector(`label[for="${b.id}"]`)?.textContent);
    expect(disabled).toEqual(['Owner', 'Administrator']);
    const reasonId = box(roles, 'Owner').getAttribute('aria-describedby');
    expect(document.getElementById(reasonId!)?.textContent).toBe(
      "You can't change this role.",
    );
    expect(box(roles, 'Tutor').getAttribute('aria-describedby')).toBeNull();
  });

  it('no roles editor when nothing is assignable', async () => {
    stub(OWNER, {
      [detail(TUTOR_ID)]: () =>
        jsonResponse(200, {
          ...TUTOR,
          allowed_actions: { ...TUTOR.allowed_actions, assignable_roles: [] },
        }),
    });
    app = await renderApp(`/users/${TUTOR_ID}`);

    expect(main().querySelectorAll('[role="checkbox"]')).toHaveLength(0);
    // The header still lists the roles.
    expect(main().textContent).toContain('Tutor, Parent or guardian');
  });

  it('Save is enabled only when the selection changed', async () => {
    stub();
    app = await renderApp(`/users/${TUTOR_ID}`);
    const roles = section('Roles');
    const save = buttonNamed(roles, 'Save roles');

    expect(save.disabled).toBe(true);
    await click(box(roles, 'Finance staff'));
    expect(save.disabled).toBe(false);
    await click(box(roles, 'Finance staff'));
    expect(save.disabled).toBe(true);
  });

  it('at least one role: no request without one', async () => {
    const api = stub();
    app = await renderApp(`/users/${TUTOR_ID}`);
    const roles = section('Roles');

    await click(box(roles, 'Tutor'));
    await click(box(roles, 'Parent or guardian'));
    await click(buttonNamed(roles, 'Save roles'));

    expect(roles.textContent).toContain('Choose at least one role.');
    expect(box(roles, 'Owner').getAttribute('aria-invalid')).toBe('true');
    expect(api.calls(rolesRoute(TUTOR_ID))).toHaveLength(0);
  });

  it('sends the set and the version, asks for the password, retries once, says saved', async () => {
    const api = stub(OWNER, {
      [rolesRoute(TUTOR_ID)]: [
        () => problem(403, 'reauth_required'),
        () =>
          jsonResponse(200, {
            ...TUTOR,
            roles: ['finance_staff', 'tutor', 'guardian'],
            version: 4,
          }),
      ],
    });
    app = await renderApp(`/users/${TUTOR_ID}`);
    const listsBefore = api.calls(LIST).length;

    await click(box(section('Roles'), 'Finance staff'));
    await click(buttonNamed(section('Roles'), 'Save roles'));
    await confirmPassword();

    const calls = api.calls(rolesRoute(TUTOR_ID));
    expect(calls.map((c) => c.body)).toEqual([
      { roles: ['finance_staff', 'tutor', 'guardian'], version: 3 },
      { roles: ['finance_staff', 'tutor', 'guardian'], version: 3 },
    ]);
    expect(calls[1]!.headers.get(CSRF_HEADER)).toBe(TEST_USER.csrf_token);
    expect(api.calls(REAUTH)).toHaveLength(1);
    expect(dialogs()).toHaveLength(0);
    const roles = section('Roles');
    expect(roles.querySelector('[role="status"]')?.textContent).toBe(
      'Roles saved.',
    );
    expect(checked(box(roles, 'Finance staff'))).toBe(true);
    expect(buttonNamed(roles, 'Save roles').disabled).toBe(true);
    expect(main().textContent).toContain(
      'Finance staff, Tutor, Parent or guardian',
    );
    // The cached detail holds the response.
    const cached = app.queryClient.getQueryData<UserDetail>([
      'users',
      'detail',
      TUTOR_ID,
    ]);
    expect(cached?.version).toBe(4);
    expect(api.calls(LIST).length).toBeGreaterThanOrEqual(listsBefore);
    expect(api.calls(LOGOUT)).toHaveLength(0);
  });
});

// --- G3: errors in the section --------------------------------------------------------

describe('role errors (G3)', () => {
  it.each([
    ['last_active_owner', 409, 'This is the only active Owner account.'],
    ['permission_denied', 403, "You don't have permission to do this."],
  ])('%s shows in the roles alert', async (code, status, text) => {
    stub(OWNER, {
      [rolesRoute(TUTOR_ID)]: () => problem(status, code),
    });
    app = await renderApp(`/users/${TUTOR_ID}`);

    await click(box(section('Roles'), 'Student'));
    await click(buttonNamed(section('Roles'), 'Save roles'));

    expect(alerts(section('Roles')).join(' ')).toContain(text);
    expect(alerts(section('Profile'))).toEqual([]);
  });

  it('a cancelled password dialog shows reauth_required in the section', async () => {
    const api = stub(OWNER, {
      [rolesRoute(TUTOR_ID)]: () => problem(403, 'reauth_required'),
    });
    app = await renderApp(`/users/${TUTOR_ID}`);

    await click(box(section('Roles'), 'Student'));
    await click(buttonNamed(section('Roles'), 'Save roles'));
    await click(buttonNamed(dialogTitled('Confirm your password')!, 'Cancel'));

    expect(api.calls(rolesRoute(TUTOR_ID))).toHaveLength(1);
    expect(alerts(section('Roles')).join(' ')).toContain(
      'Confirm your password to continue.',
    );
  });

  it('version_conflict offers Reload, which shows the server roles', async () => {
    const api = stub(OWNER, {
      [detail(TUTOR_ID)]: [
        () => jsonResponse(200, TUTOR),
        () => jsonResponse(200, { ...TUTOR, roles: ['tutor'], version: 9 }),
      ],
      [rolesRoute(TUTOR_ID)]: () => problem(409, 'version_conflict'),
    });
    app = await renderApp(`/users/${TUTOR_ID}`);

    await click(box(section('Roles'), 'Student'));
    await click(buttonNamed(section('Roles'), 'Save roles'));
    await click(buttonNamed(section('Roles'), 'Reload'));

    expect(api.calls(detail(TUTOR_ID))).toHaveLength(2);
    expect(checked(box(section('Roles'), 'Student'))).toBe(false);
    expect(checked(box(section('Roles'), 'Parent or guardian'))).toBe(false);
    expect(alerts(section('Roles'))).toEqual([]);
  });
});

// --- G4: your own roles ---------------------------------------------------------------

describe('your own roles (G4)', () => {
  it('warns, confirms, then signs out locally without /auth/logout', async () => {
    const api = stub(OWNER, {
      [rolesRoute(SELF.id)]: [
        () => problem(403, 'reauth_required'),
        () => jsonResponse(200, { ...SELF, roles: ['owner', 'tutor'] }),
      ],
    });
    app = await renderApp(`/users/${SELF.id}`);
    act(() => useUiStore.getState().setNavOpen(true));
    const roles = section('Roles');
    expect(roles.textContent).toContain(
      'Changing your own roles signs you out.',
    );
    expect(getCsrfToken()).toBe(TEST_USER.csrf_token);

    await click(box(roles, 'Tutor'));
    await click(buttonNamed(roles, 'Save roles'));
    const confirm = dialogTitled('Change your own roles');
    expect(confirm?.textContent).toContain(
      "Save your roles? You'll be signed out and need to sign in again.",
    );
    expect(api.calls(rolesRoute(SELF.id))).toHaveLength(0);
    await click(buttonNamed(confirm!, 'Save and sign out'));
    await confirmPassword();

    expect(api.calls(rolesRoute(SELF.id)).map((c) => c.body)).toEqual([
      { roles: ['owner', 'tutor'], version: 7 },
      { roles: ['owner', 'tutor'], version: 7 },
    ]);
    expect(api.calls(LOGOUT)).toHaveLength(0);
    expect(app.location()).toBe('/login');
    expect(getCsrfToken()).toBeNull();
    expect(useUiStore.getState().navOpen).toBe(false);
    // Both caches were cleared: only the signed-out session remains.
    const queries = app.queryClient.getQueryCache().getAll();
    expect(queries.map((q) => q.queryKey)).toEqual([['auth', 'me']]);
    expect(app.queryClient.getQueryData(['auth', 'me'])).toEqual({
      user: null,
      reason: 'signed_out',
      notice: { notice: 'roles_changed', username: TEST_USER.username },
    });
    expect(app.queryClient.getMutationCache().getAll()).toHaveLength(0);
    expect(main().textContent).toContain(
      'Your roles changed, so you were signed out. Sign in again to continue.',
    );
    expect(fieldByLabel(main(), 'Username').value).toBe(TEST_USER.username);
  });

  it('cancelling the confirmation sends nothing and gives focus back to Save', async () => {
    const api = stub();
    app = await renderApp(`/users/${SELF.id}`);
    const roles = section('Roles');

    await click(box(roles, 'Tutor'));
    await click(buttonNamed(roles, 'Save roles'));
    await click(buttonNamed(dialogTitled('Change your own roles')!, 'Cancel'));

    expect(dialogs()).toHaveLength(0);
    expect(api.calls(rolesRoute(SELF.id))).toHaveLength(0);
    expect(document.activeElement).toBe(buttonNamed(roles, 'Save roles'));
    expect(app.location()).toBe(`/users/${SELF.id}`);
  });

  it('an error keeps you signed in on the page', async () => {
    stub(OWNER, {
      [rolesRoute(SELF.id)]: () => problem(409, 'last_active_owner'),
    });
    app = await renderApp(`/users/${SELF.id}`);

    await click(box(section('Roles'), 'Tutor'));
    await click(buttonNamed(section('Roles'), 'Save roles'));
    await click(
      buttonNamed(dialogTitled('Change your own roles')!, 'Save and sign out'),
    );

    expect(app.location()).toBe(`/users/${SELF.id}`);
    expect(alerts(section('Roles')).join(' ')).toContain(
      'This is the only active Owner account.',
    );
    expect(getCsrfToken()).toBe(TEST_USER.csrf_token);
  });
});

// --- G2, G5: delegated permissions ----------------------------------------------------

describe('delegated permissions (G2, G5)', () => {
  it('absent for a user without the administrator role', async () => {
    stub();
    app = await renderApp(`/users/${TUTOR_ID}`);

    expect(main().textContent).not.toContain('Delegated permissions');
  });

  it('read-only without manage_delegations', async () => {
    stub(OWNER, {
      [detail(ADMIN_ID)]: () =>
        jsonResponse(200, {
          ...ADMIN,
          allowed_actions: {
            ...ADMIN.allowed_actions,
            manage_delegations: false,
          },
        }),
    });
    app = await renderApp(`/users/${ADMIN_ID}`);

    const delegations = section('Delegated permissions');
    expect(delegations.querySelectorAll('[role="checkbox"]')).toHaveLength(0);
    expect(delegations.querySelector('form')).toBeNull();
    expect(delegations.textContent).toContain('Approve refunds');
  });

  it('read-only and empty says so', async () => {
    stub(OWNER, {
      [detail(ADMIN_ID)]: () =>
        jsonResponse(200, {
          ...ADMIN,
          delegations: [],
          allowed_actions: {
            ...ADMIN.allowed_actions,
            manage_delegations: false,
          },
        }),
    });
    app = await renderApp(`/users/${ADMIN_ID}`);

    expect(section('Delegated permissions').textContent).toContain(
      'No delegated permissions.',
    );
  });

  it('editable: four described checkboxes; saving sends the set with the password dialog', async () => {
    const api = stub(OWNER, {
      [delegationsRoute(ADMIN_ID)]: [
        () => problem(403, 'reauth_required'),
        () =>
          jsonResponse(200, {
            ...ADMIN,
            delegations: ['finance.refund.approve', 'user.admin_role.assign'],
            version: 6,
          }),
      ],
    });
    app = await renderApp(`/users/${ADMIN_ID}`);
    const delegations = section('Delegated permissions');
    expect(delegations.querySelectorAll('[role="checkbox"]')).toHaveLength(4);
    const assign = box(delegations, 'Assign the Administrator role');
    expect(
      document.getElementById(assign.getAttribute('aria-describedby')!)
        ?.textContent,
    ).toBe('Give or remove the Administrator role for other users.');
    expect(checked(box(delegations, 'Approve refunds'))).toBe(true);

    await click(assign);
    await click(buttonNamed(delegations, 'Save delegated permissions'));
    await confirmPassword();

    expect(api.calls(delegationsRoute(ADMIN_ID)).map((c) => c.body)).toEqual([
      {
        permissions: ['finance.refund.approve', 'user.admin_role.assign'],
        version: 5,
      },
      {
        permissions: ['finance.refund.approve', 'user.admin_role.assign'],
        version: 5,
      },
    ]);
    expect(
      section('Delegated permissions').querySelector('[role="status"]')
        ?.textContent,
    ).toBe('Delegated permissions saved.');
    expect(app.location()).toBe(`/users/${ADMIN_ID}`);
    expect(api.calls(LOGOUT)).toHaveLength(0);
  });

  it.each([
    [
      'delegation_target_invalid',
      422,
      "Permissions are delegated only to Administrators who aren't archived.",
    ],
    ['delegation_forbidden', 403, 'Only an Owner can delegate permissions.'],
  ])('%s shows in the delegations alert', async (code, status, text) => {
    stub(OWNER, {
      [delegationsRoute(ADMIN_ID)]: () => problem(status, code),
    });
    app = await renderApp(`/users/${ADMIN_ID}`);

    await click(box(section('Delegated permissions'), 'Finalize payroll'));
    await click(
      buttonNamed(
        section('Delegated permissions'),
        'Save delegated permissions',
      ),
    );

    expect(alerts(section('Delegated permissions')).join(' ')).toContain(text);
    expect(alerts(section('Roles'))).toEqual([]);
  });
});
