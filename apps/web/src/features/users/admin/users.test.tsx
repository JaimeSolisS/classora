import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { applyLanguage } from '@/i18n';
import { CSRF_HEADER } from '@/features/auth/csrf';
import type { SessionUserBody } from '@/features/auth/session';
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
  mountApp,
  renderApp,
  settle,
  type RenderedApp,
} from '@/test/render-app';
import type { PasswordResetIssued } from '../password-resets/api';
import type { UserDetail, UserListItem, UserListPage } from './api';
import { SEARCH_DEBOUNCE_MS } from './UsersPage';

// #31 J4, K5, K7 (and K1, K2, K4, K6): the /users list, its navigation item,
// the detail page, the profile form, the account status buttons with their
// confirmation and the #28 password dialog, and the reset link that is never
// cached. Tokens are low-entropy placeholders.

const LIST = 'GET /api/v1/users';
const TUTOR_ID = '00000000-0000-4000-8000-0000000000a1';
const DETAIL = `GET /api/v1/users/${TUTOR_ID}`;
const PATCH = `PATCH /api/v1/users/${TUTOR_ID}`;
const action = (name: string) => `POST /api/v1/users/${TUTOR_ID}/${name}`;
const REAUTH = 'POST /api/v1/auth/reauthenticate';
const RESET = 'POST /api/v1/password-resets';
const TOKEN = 'q'.repeat(43);

/** An Owner who may view, manage, invite and reset. */
const OWNER: SessionUserBody = {
  ...TEST_USER,
  permissions: [
    'user.view',
    'user.manage',
    'user.invite',
    'user.password_reset.issue',
  ],
};

const ITEM: UserListItem = {
  id: TUTOR_ID,
  username: 'ana.tutor',
  display_name: 'Ana Tutor',
  state: 'active',
  roles: ['tutor', 'guardian'],
  created_at: '2026-10-01T15:00:00Z',
};

const ALL_ACTIONS: UserDetail['allowed_actions'] = {
  edit: true,
  suspend: true,
  archive: true,
  reactivate: false,
  reset_password: true,
  // The #170 sections are off here; roles.test.tsx covers them.
  assignable_roles: [],
  manage_delegations: false,
};

const TUTOR: UserDetail = {
  ...ITEM,
  email: 'ana@example.com',
  phone: null,
  version: 3,
  updated_at: '2026-10-02T15:00:00Z',
  delegations: [],
  password_set: true,
  allowed_actions: ALL_ACTIONS,
};

const SUSPENDED: UserDetail = {
  ...TUTOR,
  state: 'suspended',
  version: 4,
  allowed_actions: {
    ...ALL_ACTIONS,
    suspend: false,
    reactivate: true,
    reset_password: false,
  },
};

function page(items: UserListItem[], total = items.length, offset = 0) {
  const body: UserListPage = { items, total, limit: 25, offset };
  return () => jsonResponse(200, body);
}

let app: RenderedApp | undefined;

const main = () => app!.container.querySelector('main')!;
const field = (label: string) => fieldByLabel(main(), label);
const navItems = () =>
  [...app!.container.querySelectorAll('header nav a')].map(
    (a) => a.textContent,
  );
const dialogs = () => [...document.querySelectorAll('[role="dialog"]')];
const dialogTitled = (title: string) =>
  (dialogs().find((d) => d.querySelector('h2')?.textContent === title) ??
    null) as HTMLElement | null;
/** The query strings of every list request, in order. */
function listSearches(api: ApiStub): string[] {
  return api.fetch.mock.calls
    .map(([request]) => new URL(request.url))
    .filter((url) => url.pathname === '/api/v1/users')
    .map((url) => url.search);
}

function stub(
  user: SessionUserBody = OWNER,
  routes: Record<string, Responder | Responder[]> = {},
): ApiStub {
  return stubApi({
    'GET /api/v1/auth/me': meAs(user),
    [REAUTH]: () =>
      jsonResponse(200, { reauthenticated_until: '2026-10-19T18:05:00Z' }),
    [LIST]: page([ITEM]),
    [DETAIL]: () => jsonResponse(200, TUTOR),
    ...routes,
  });
}

function selectOption(select: HTMLSelectElement, value: string) {
  act(() => {
    select.value = value;
    select.dispatchEvent(new Event('change', { bubbles: true }));
  });
}

function selectByLabel(label: string): HTMLSelectElement {
  const found = [...main().querySelectorAll('label')].find(
    (l) => l.textContent === label,
  );
  const element = found ? document.getElementById(found.htmlFor) : null;
  if (!(element instanceof HTMLSelectElement)) {
    throw new Error(`No select labelled "${label}"`);
  }
  return element;
}

function cacheText(): string {
  const queries = app!.queryClient
    .getQueryCache()
    .getAll()
    .map((q) => ({ key: q.queryKey, data: q.state.data }));
  const mutations = app!.queryClient
    .getMutationCache()
    .getAll()
    .map((m) => ({ vars: m.state.variables, data: m.state.data }));
  return JSON.stringify({ queries, mutations });
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
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.localStorage.clear();
  window.sessionStorage.clear();
});

// --- J1, J4: route and navigation ---------------------------------------------------

describe('the /users route and navigation (J1, J4)', () => {
  it('shows the list with one h1, the title, the Users item after Home and Invite user', async () => {
    stub();

    app = await renderApp('/users');

    expect(main().querySelectorAll('h1')).toHaveLength(1);
    expect(main().querySelector('h1')?.textContent).toBe('Users');
    expect(document.title).toBe('Users · Classora');
    expect(navItems().slice(0, 2)).toEqual(['Home', 'Users']);
    const invite = [...main().querySelectorAll('a')].find(
      (a) => a.textContent === 'Invite user',
    );
    expect(invite?.getAttribute('href')).toBe('/invitations');
  });

  it('without user.invite: no Invite user link', async () => {
    stub({ ...OWNER, permissions: ['user.view'] });

    app = await renderApp('/users');

    expect(main().textContent).not.toContain('Invite user');
  });

  it('without user.view: no navigation item and the forbidden page', async () => {
    const api = stub({ ...TEST_USER, permissions: [] });

    app = await renderApp('/users');

    expect(navItems()).toEqual(['Home']);
    expect(main().querySelector('h1')?.textContent).toBe(
      "You don't have access to this page",
    );
    expect(api.calls(LIST)).toHaveLength(0);
  });
});

// --- J2, J3, J4: list controls ------------------------------------------------------

describe('the list (J2, J3, J4)', () => {
  it('shows a row per user with a link, roles, state badge and created date', async () => {
    stub();

    app = await renderApp('/users');

    const rows = main().querySelectorAll('tbody tr');
    expect(rows).toHaveLength(1);
    const cells = [...rows[0]!.querySelectorAll('td')].map(
      (c) => c.textContent,
    );
    expect(cells.slice(0, 4)).toEqual([
      'Ana Tutor',
      'ana.tutor',
      'Tutor, Parent or guardian',
      'Active',
    ]);
    expect(cells[4]).toMatch(/2026|Oct/);
    expect(rows[0]!.querySelector('a')?.getAttribute('href')).toBe(
      `/users/${TUTOR_ID}`,
    );
    expect(main().textContent).toContain('1–1 of 1');
  });

  it('the first request asks for page 1 with 25 rows', async () => {
    const api = stub();

    app = await renderApp('/users');

    expect(listSearches(api)).toEqual(['?limit=25&offset=0']);
  });

  it('sends the role and state filters and keeps them in the URL', async () => {
    const api = stub();
    app = await renderApp('/users?page=2');

    selectOption(selectByLabel('Role'), 'finance_staff');
    await settle();
    selectOption(selectByLabel('Status'), 'suspended');
    await settle();

    expect(listSearches(api).slice(-2)).toEqual([
      '?role=finance_staff&limit=25&offset=0',
      '?role=finance_staff&state=suspended&limit=25&offset=0',
    ]);
    expect(app.location()).toBe('/users?role=finance_staff&state=suspended');
    expect(selectByLabel('Role').value).toBe('finance_staff');
  });

  it('the selects offer every role and state', async () => {
    stub();
    app = await renderApp('/users');

    const options = (label: string) =>
      [...selectByLabel(label).options].map((o) => o.textContent);
    expect(options('Role')).toEqual([
      'All roles',
      'Owner',
      'Administrator',
      'Academic coordinator',
      'Finance staff',
      'Tutor',
      'Student',
      'Parent or guardian',
    ]);
    expect(options('Status')).toEqual([
      'All states',
      'Pending',
      'Active',
      'Suspended',
      'Archived',
    ]);
  });

  it('debounces the search by 300 ms and goes back to page 1', async () => {
    vi.useFakeTimers();
    const api = stub();
    app = mountApp('/users?page=3');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    const before = listSearches(api).length;

    typeInto(field('Search by name or username'), 'an');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SEARCH_DEBOUNCE_MS - 50);
    });
    typeInto(field('Search by name or username'), 'ana ');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SEARCH_DEBOUNCE_MS - 1);
    });
    expect(listSearches(api)).toHaveLength(before);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });

    expect(listSearches(api).slice(before)).toEqual([
      '?q=ana&limit=25&offset=0',
    ]);
    expect(app.location()).toBe('/users?q=ana');
  });

  it('pages with Previous and Next', async () => {
    const api = stub(OWNER, {
      [LIST]: [page([ITEM], 60, 0), page([ITEM], 60, 25), page([ITEM], 60, 0)],
    });
    app = await renderApp('/users');

    await click(buttonNamed(main(), 'Next'));

    expect(app.location()).toBe('/users?page=2');
    expect(listSearches(api).at(-1)).toBe('?limit=25&offset=25');
    expect(main().textContent).toContain('26–26 of 60');

    await click(buttonNamed(main(), 'Previous'));

    expect(app.location()).toBe('/users');
    expect(listSearches(api).at(-1)).toBe('?limit=25&offset=0');
  });

  it('shows the empty state', async () => {
    stub(OWNER, { [LIST]: page([]) });

    app = await renderApp('/users?q=nobody');

    expect(main().textContent).toContain('No users match these filters.');
    expect(field('Search by name or username').value).toBe('nobody');
  });

  it('shows an error with Try again', async () => {
    const api = stub(OWNER, {
      // A 4xx: queries retry only network and 5xx failures.
      [LIST]: [() => problem(400, 'bad_request'), page([ITEM])],
    });
    app = await renderApp('/users');

    expect(alerts(main()).join(' ')).toContain(
      "We couldn't process the request.",
    );
    await click(buttonNamed(main(), 'Try again'));

    expect(api.calls(LIST)).toHaveLength(2);
    expect(main().querySelectorAll('tbody tr')).toHaveLength(1);
  });

  it('changing the language keeps the typed search and the filters', async () => {
    stub();
    app = await renderApp('/users?role=tutor');
    typeInto(field('Search by name or username'), 'an');

    act(() => applyLanguage('es'));
    await settle();

    expect(fieldByLabel(main(), 'Buscar por nombre o usuario').value).toBe(
      'an',
    );
    const role = [...main().querySelectorAll('label')].find(
      (l) => l.textContent === 'Rol',
    );
    expect(
      (document.getElementById(role!.htmlFor) as HTMLSelectElement).value,
    ).toBe('tutor');
  });
});

// --- K1: detail ---------------------------------------------------------------------

describe('the detail page (K1)', () => {
  it('shows the name, username, state, roles and the document title', async () => {
    stub();

    app = await renderApp(`/users/${TUTOR_ID}`);

    expect(main().querySelector('h1')?.textContent).toBe('Ana Tutor');
    expect(document.title).toBe('Ana Tutor · Classora');
    expect(main().textContent).toContain('ana.tutor');
    expect(main().textContent).toContain('Active');
    expect(main().textContent).toContain('Tutor, Parent or guardian');
  });

  it.each([
    [false, 'Invitation not redeemed yet'],
    [true, 'Account request awaiting review'],
  ])('a pending user with password_set=%s says %s', async (set, text) => {
    stub(OWNER, {
      [DETAIL]: () =>
        jsonResponse(200, { ...TUTOR, state: 'pending', password_set: set }),
    });

    app = await renderApp(`/users/${TUTOR_ID}`);

    expect(main().textContent).toContain(text);
  });

  it('404 says the user does not exist and links back', async () => {
    stub(OWNER, { [DETAIL]: () => problem(404, 'not_found') });

    app = await renderApp(`/users/${TUTOR_ID}`);

    expect(main().querySelector('h1')?.textContent).toBe(
      "This user doesn't exist.",
    );
    const back = [...main().querySelectorAll('a')].find(
      (a) => a.textContent === 'Back to users',
    );
    expect(back?.getAttribute('href')).toBe('/users');
  });
});

// --- K7: buttons follow the flags ---------------------------------------------------

describe('buttons follow allowed_actions (K7)', () => {
  const buttons = () =>
    [...main().querySelectorAll('button')].map((b) => b.textContent);

  it.each([
    ['suspend', 'Suspend'],
    ['archive', 'Archive'],
    ['reactivate', 'Reactivate'],
    ['reset_password', 'Create password reset link'],
  ] as const)('%s → %s exactly when true', async (flag, label) => {
    const none = {
      edit: true,
      suspend: false,
      archive: false,
      reactivate: false,
      reset_password: false,
      assignable_roles: [],
      manage_delegations: false,
    };
    stub(OWNER, {
      [DETAIL]: [
        () =>
          jsonResponse(200, {
            ...TUTOR,
            allowed_actions: { ...none, [flag]: true },
          }),
      ],
    });
    app = await renderApp(`/users/${TUTOR_ID}`);
    expect(buttons()).toContain(label);
    app.unmount();

    stub(OWNER, {
      [DETAIL]: () => jsonResponse(200, { ...TUTOR, allowed_actions: none }),
    });
    app = await renderApp(`/users/${TUTOR_ID}`);
    expect(buttons()).not.toContain(label);
  });

  it('own record: only the profile form, no Suspend, Archive or reset', async () => {
    stub(OWNER, {
      [`GET /api/v1/users/${TEST_USER.id}`]: () =>
        jsonResponse(200, {
          ...TUTOR,
          id: TEST_USER.id,
          username: TEST_USER.username,
          display_name: TEST_USER.display_name,
          roles: ['owner'],
          allowed_actions: {
            edit: true,
            suspend: false,
            archive: false,
            reactivate: false,
            reset_password: false,
            assignable_roles: [],
            manage_delegations: false,
          },
        }),
    });

    app = await renderApp(`/users/${TEST_USER.id}`);

    expect(field('Name').value).toBe('Demo Owner');
    expect(buttons()).toEqual(['Save']);
    expect(main().textContent).not.toContain('Account status');
    expect(main().textContent).not.toContain('Change password');
  });

  it('without edit: the profile as text, no form', async () => {
    stub(OWNER, {
      [DETAIL]: () =>
        jsonResponse(200, {
          ...TUTOR,
          allowed_actions: {
            edit: false,
            suspend: false,
            archive: false,
            reactivate: false,
            reset_password: false,
            assignable_roles: [],
            manage_delegations: false,
          },
        }),
    });

    app = await renderApp(`/users/${TUTOR_ID}`);

    expect(main().querySelector('form')).toBeNull();
    expect(main().textContent).toContain('ana@example.com');
    expect(main().textContent).toContain('Not set');
  });
});

// --- K2, K7: the profile form -------------------------------------------------------

describe('the profile form (K2, K6, K7)', () => {
  it('has labelled fields with the contact help and the right types', async () => {
    stub();
    app = await renderApp(`/users/${TUTOR_ID}`);

    expect(field('Name').value).toBe('Ana Tutor');
    expect(field('Email (optional)').type).toBe('email');
    expect(field('Email (optional)').autocomplete).toBe('off');
    expect(field('Phone (optional)').type).toBe('tel');
    expect(field('Phone (optional)').autocomplete).toBe('off');
    expect(main().textContent).toContain(
      'For contact only. Never used to sign in.',
    );
  });

  it('sends only the changed fields with the loaded version, then says saved', async () => {
    const saved = { ...TUTOR, phone: '55 1234 5678', version: 4 };
    const api = stub(OWNER, { [PATCH]: () => jsonResponse(200, saved) });
    app = await renderApp(`/users/${TUTOR_ID}`);

    typeInto(field('Phone (optional)'), ' 55 1234 5678 ');
    await click(buttonNamed(main(), 'Save'));

    const [call] = api.calls(PATCH);
    expect(call!.body).toEqual({ version: 3, phone: '55 1234 5678' });
    expect(call!.headers.get(CSRF_HEADER)).toBe(TEST_USER.csrf_token);
    expect(main().textContent).toContain('Changes saved.');
    expect(field('Phone (optional)').value).toBe('55 1234 5678');
    // The detail is replaced from the response (no refetch).
    expect(api.calls(DETAIL)).toHaveLength(1);
  });

  it('clearing a contact field sends null', async () => {
    const api = stub(OWNER, {
      [PATCH]: () => jsonResponse(200, { ...TUTOR, email: null, version: 4 }),
    });
    app = await renderApp(`/users/${TUTOR_ID}`);

    typeInto(field('Email (optional)'), '  ');
    await click(buttonNamed(main(), 'Save'));

    expect(api.calls(PATCH)[0]!.body).toEqual({ version: 3, email: null });
  });

  it('validates on the client without a request', async () => {
    const api = stub();
    app = await renderApp(`/users/${TUTOR_ID}`);

    typeInto(field('Name'), '   ');
    typeInto(field('Email (optional)'), 'ab');
    typeInto(field('Phone (optional)'), 'phone-ext');
    await click(buttonNamed(main(), 'Save'));

    expect(api.calls(PATCH)).toHaveLength(0);
    for (const label of ['Name', 'Email (optional)', 'Phone (optional)']) {
      expect(field(label).getAttribute('aria-invalid')).toBe('true');
    }
    expect(main().textContent).toContain('Enter a name.');
    expect(document.activeElement).toBe(field('Name'));
  });

  it('server field errors go on their field', async () => {
    stub(OWNER, {
      [PATCH]: () =>
        new Response(
          JSON.stringify({
            type: 'about:blank',
            title: 'Unprocessable',
            status: 422,
            code: 'validation_error',
            request_id: 'req-422',
            errors: [
              {
                path: ['body', 'email', 'constrained-str'],
                code: 'string_pattern_mismatch',
                message: 'x',
              },
            ],
          }),
          {
            status: 422,
            headers: { 'Content-Type': 'application/problem+json' },
          },
        ),
    });
    app = await renderApp(`/users/${TUTOR_ID}`);

    typeInto(field('Email (optional)'), 'a@b.co');
    await click(buttonNamed(main(), 'Save'));

    expect(field('Email (optional)').getAttribute('aria-invalid')).toBe('true');
    expect(alerts(main())).toEqual([]);
  });

  it('version_conflict shows Reload, which refetches and resets the form', async () => {
    const api = stub(OWNER, {
      [PATCH]: () => problem(409, 'version_conflict'),
      [DETAIL]: [
        () => jsonResponse(200, TUTOR),
        () =>
          jsonResponse(200, {
            ...TUTOR,
            display_name: 'Ana Server',
            version: 5,
          }),
      ],
    });
    app = await renderApp(`/users/${TUTOR_ID}`);
    typeInto(field('Name'), 'Mine');

    await click(buttonNamed(main(), 'Save'));

    expect(alerts(main()).join(' ')).toContain('Someone else changed this');
    await click(buttonNamed(main(), 'Reload'));

    expect(api.calls(DETAIL)).toHaveLength(2);
    expect(field('Name').value).toBe('Ana Server');
    expect(alerts(main())).toEqual([]);
    expect(main().querySelector('h1')?.textContent).toBe('Ana Server');
  });
});

// --- K3, K4, K6, K7: account status -------------------------------------------------

describe('account status (K3, K4, K6, K7)', () => {
  it('suspend: confirm → password dialog → retried once → updated badge', async () => {
    const api = stub(OWNER, {
      [action('suspend')]: [
        () => problem(403, 'reauth_required'),
        () => jsonResponse(200, SUSPENDED),
      ],
    });
    app = await renderApp(`/users/${TUTOR_ID}`);

    await click(buttonNamed(main(), 'Suspend'));
    const confirm = dialogTitled('Suspend account');
    expect(confirm?.textContent).toContain(
      "Suspend Ana Tutor? All their sessions end and they can't sign in until you reactivate them. We'll ask for your password.",
    );
    await click(buttonNamed(confirm!, 'Suspend'));
    await confirmPassword();

    const calls = api.calls(action('suspend'));
    expect(calls).toHaveLength(2);
    expect(calls.map((c) => c.body)).toEqual([{ version: 3 }, { version: 3 }]);
    expect(api.calls(REAUTH)).toHaveLength(1);
    expect(dialogs()).toHaveLength(0);
    expect(main().textContent).toContain('Suspended');
    expect(main().textContent).toContain('The account is suspended.');
    expect(buttonNamed(main(), 'Reactivate')).toBeTruthy();
    expect(
      [...main().querySelectorAll('button')].map((b) => b.textContent),
    ).not.toContain('Suspend');
  });

  it('reactivate needs no password and says they can sign in', async () => {
    const api = stub(OWNER, {
      [DETAIL]: () => jsonResponse(200, SUSPENDED),
      [action('reactivate')]: () => jsonResponse(200, { ...TUTOR, version: 5 }),
    });
    app = await renderApp(`/users/${TUTOR_ID}`);

    await click(buttonNamed(main(), 'Reactivate'));
    await click(buttonNamed(dialogTitled('Reactivate account')!, 'Reactivate'));

    expect(api.calls(action('reactivate'))[0]!.body).toEqual({ version: 4 });
    expect(api.calls(REAUTH)).toHaveLength(0);
    expect(main().textContent).toContain(
      'They can sign in with their existing password.',
    );
  });

  it.each([
    [
      'student_profile_required',
      "This student account isn't linked to a student profile yet. Ask the center to link it.",
    ],
    [
      'guardian_consent_required',
      'The student is a minor and needs consent from a linked guardian.',
    ],
  ])(
    'reactivating a student refused by the gate (%s) shows its message (#35 I3)',
    async (code, message) => {
      stub(OWNER, {
        [DETAIL]: () => jsonResponse(200, SUSPENDED),
        [action('reactivate')]: () => problem(409, code),
      });
      app = await renderApp(`/users/${TUTOR_ID}`);

      await click(buttonNamed(main(), 'Reactivate'));
      await click(
        buttonNamed(dialogTitled('Reactivate account')!, 'Reactivate'),
      );

      expect(document.body.textContent).toContain(message);
    },
  );

  it('reactivating someone without a password says to issue a reset link', async () => {
    stub(OWNER, {
      [DETAIL]: () =>
        jsonResponse(200, {
          ...SUSPENDED,
          state: 'archived',
          password_set: false,
        }),
      [action('reactivate')]: () =>
        jsonResponse(200, { ...TUTOR, password_set: false, version: 5 }),
    });
    app = await renderApp(`/users/${TUTOR_ID}`);

    await click(buttonNamed(main(), 'Reactivate'));
    await click(buttonNamed(dialogTitled('Reactivate account')!, 'Reactivate'));

    expect(main().textContent).toContain(
      'This person has no password yet. Issue a password reset link.',
    );
  });

  it('archiving an open invitation says it cancels the invitation', async () => {
    stub(OWNER, {
      [DETAIL]: () =>
        jsonResponse(200, {
          ...TUTOR,
          state: 'pending',
          password_set: false,
          allowed_actions: {
            ...ALL_ACTIONS,
            suspend: false,
            reset_password: false,
          },
        }),
    });
    app = await renderApp(`/users/${TUTOR_ID}`);

    await click(buttonNamed(main(), 'Archive'));

    expect(dialogTitled('Cancel invitation')?.textContent).toContain(
      "Cancel Ana Tutor's invitation? The link stops working.",
    );
  });

  it('cancelling the confirmation sends nothing and gives focus back', async () => {
    const api = stub();
    app = await renderApp(`/users/${TUTOR_ID}`);
    const suspend = buttonNamed(main(), 'Suspend');
    act(() => suspend.focus());

    await click(suspend);
    await click(buttonNamed(dialogTitled('Suspend account')!, 'Cancel'));

    expect(dialogs()).toHaveLength(0);
    expect(api.calls(action('suspend'))).toHaveLength(0);
    expect(document.activeElement).toBe(suspend);
  });

  it('a cancelled password dialog shows reauth_required in the section', async () => {
    stub(OWNER, {
      [action('archive')]: () => problem(403, 'reauth_required'),
    });
    app = await renderApp(`/users/${TUTOR_ID}`);

    await click(buttonNamed(main(), 'Archive'));
    await click(buttonNamed(dialogTitled('Archive account')!, 'Archive'));
    await click(buttonNamed(dialogTitled('Confirm your password')!, 'Cancel'));

    expect(dialogs()).toHaveLength(0);
    expect(alerts(main()).join(' ')).toContain(
      'Confirm your password to continue.',
    );
    expect(main().textContent).toContain('Active');
  });

  it.each([
    ['last_active_owner', 'This is the only active Owner account.'],
    ['user_state_transition_invalid', "The account can't be moved"],
    ['permission_denied', "You don't have permission to do this."],
  ])('409/403 %s shows in the section alert', async (code, text) => {
    stub(OWNER, {
      [action('suspend')]: [
        () => problem(403, 'reauth_required'),
        () => problem(code === 'permission_denied' ? 403 : 409, code),
      ],
    });
    app = await renderApp(`/users/${TUTOR_ID}`);

    await click(buttonNamed(main(), 'Suspend'));
    await click(buttonNamed(dialogTitled('Suspend account')!, 'Suspend'));
    await confirmPassword();

    expect(alerts(main()).join(' ')).toContain(text);
  });

  it('version_conflict on a state change offers Reload with the new version', async () => {
    const api = stub(OWNER, {
      [DETAIL]: [
        () => jsonResponse(200, SUSPENDED),
        () => jsonResponse(200, { ...SUSPENDED, version: 9 }),
      ],
      [action('reactivate')]: [
        () => problem(409, 'version_conflict'),
        () => jsonResponse(200, { ...TUTOR, version: 10 }),
      ],
    });
    app = await renderApp(`/users/${TUTOR_ID}`);

    await click(buttonNamed(main(), 'Reactivate'));
    await click(buttonNamed(dialogTitled('Reactivate account')!, 'Reactivate'));
    await click(buttonNamed(main(), 'Reload'));
    await click(buttonNamed(main(), 'Reactivate'));
    await click(buttonNamed(dialogTitled('Reactivate account')!, 'Reactivate'));

    expect(api.calls(action('reactivate')).map((c) => c.body)).toEqual([
      { version: 4 },
      { version: 9 },
    ]);
  });
});

// --- K5: the reset link -------------------------------------------------------------

describe('the password reset link (K5)', () => {
  const ISSUED: PasswordResetIssued = {
    user_id: TUTOR_ID,
    username: 'ana.tutor',
    display_name: 'Ana Tutor',
    expires_at: '2026-10-20T18:00:00Z',
    reset_url: `http://127.0.0.1:5173/reset#token=${TOKEN}`,
  };

  it('issues through the #30 route with the password dialog; Done leaves no token anywhere', async () => {
    const api = stub(OWNER, {
      [RESET]: [
        () => problem(403, 'reauth_required'),
        () => jsonResponse(201, ISSUED),
      ],
    });
    app = await renderApp(`/users/${TUTOR_ID}`);

    await click(buttonNamed(main(), 'Create password reset link'));
    await confirmPassword();

    expect(api.calls(RESET).map((c) => c.body)).toEqual([
      { username: 'ana.tutor' },
      { username: 'ana.tutor' },
    ]);
    expect(field('Password reset link').value).toBe(ISSUED.reset_url);
    expect(cacheText()).not.toContain(TOKEN);

    await click(buttonNamed(main(), 'Done'));

    expect(main().textContent).not.toContain(TOKEN);
    expect(cacheText()).not.toContain(TOKEN);
    const storage = JSON.stringify({
      local: { ...window.localStorage },
      session: { ...window.sessionStorage },
    });
    expect(storage).not.toContain(TOKEN);
    expect(app.location()).not.toContain(TOKEN);
    expect(buttonNamed(main(), 'Create password reset link')).toBeTruthy();
  });

  it('an error shows in the section', async () => {
    stub(OWNER, { [RESET]: () => problem(409, 'user_not_active') });
    app = await renderApp(`/users/${TUTOR_ID}`);

    await click(buttonNamed(main(), 'Create password reset link'));

    expect(alerts(main()).join(' ')).toContain("This account isn't active.");
  });
});
