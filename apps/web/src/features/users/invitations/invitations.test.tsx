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
import { alerts, click, fieldByLabel, openDialog, typeInto } from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  type RenderedApp,
} from '@/test/render-app';
import type { InvitationIssued, InvitationListItem } from './api';

// #29 I1–I5: the /invitations page, its navigation item, the one-time link
// panel and the rule that the link is never retained (I4).

const LIST = 'GET /api/v1/invitations';
const CREATE = 'POST /api/v1/invitations';
const REAUTH = 'POST /api/v1/auth/reauthenticate';
const TOKEN = 'tok3n-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA';
const NEW_TOKEN = 'n3w-tokenBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB';
const LINK = `http://127.0.0.1:5173/invite#token=${TOKEN}`;
const NEW_LINK = `http://127.0.0.1:5173/invite#token=${NEW_TOKEN}`;

/** An Owner: every role may be assigned. */
const OWNER: SessionUserBody = {
  ...TEST_USER,
  permissions: [
    'user.invite',
    'user.role.assign',
    'user.admin_role.assign',
    'user.owner_role.assign',
  ],
};

/** An Administrator without delegations. */
const ADMIN: SessionUserBody = {
  ...TEST_USER,
  id: '00000000-0000-4000-8000-000000000003',
  username: 'admin.user',
  display_name: 'Admin User',
  roles: ['administrator'],
  permissions: ['user.invite', 'user.role.assign'],
};

const ISSUED: InvitationIssued = {
  user_id: '00000000-0000-4000-8000-0000000000aa',
  username: 'ana.tutor',
  display_name: 'Ana Tutor',
  roles: ['tutor'],
  expires_at: '2026-10-20T18:00:00Z',
  invitation_url: LINK,
};

const ROW: InvitationListItem = {
  user_id: ISSUED.user_id,
  username: 'ana.tutor',
  display_name: 'Ana Tutor',
  roles: ['tutor'],
  status: 'active',
  expires_at: '2026-10-20T18:00:00Z',
  issued_at: '2026-10-19T18:00:00Z',
};

const OWNER_ROW: InvitationListItem = {
  ...ROW,
  user_id: '00000000-0000-4000-8000-0000000000bb',
  username: 'olga.owner',
  display_name: 'Olga Owner',
  roles: ['owner'],
  status: 'expired',
};

const page = (items: InvitationListItem[], total = items.length, offset = 0) =>
  jsonResponse(200, { items, total, limit: 10, offset });

let app: RenderedApp | undefined;

const main = () => app!.container.querySelector('main')!;
const field = (label: string) => fieldByLabel(main(), label);
const roleSelect = () => {
  const label = [...main().querySelectorAll('label')].find(
    (l) => l.textContent === 'Role',
  );
  return document.getElementById(label!.htmlFor) as HTMLSelectElement;
};
const panel = () =>
  [...main().querySelectorAll('section')].find(
    (s) => s.querySelector('h2')?.textContent === 'Invitation link ready',
  ) ?? null;
const linkField = () => field('Invitation link');

function choose(select: HTMLSelectElement, value: string) {
  // eslint-disable-next-line @typescript-eslint/unbound-method
  const setter = Object.getOwnPropertyDescriptor(
    HTMLSelectElement.prototype,
    'value',
  )?.set;
  act(() => {
    setter?.call(select, value);
    select.dispatchEvent(new Event('change', { bubbles: true }));
  });
}

async function fillAndSubmit(role = 'tutor', username = 'ana.tutor') {
  typeInto(field('Username'), username);
  typeInto(field('Display name'), 'Ana Tutor');
  choose(roleSelect(), role);
  await click(buttonNamed(main(), 'Create invitation'));
}

function stub(
  user: SessionUserBody = OWNER,
  routes: Record<string, Responder | Responder[]> = {},
): ApiStub {
  return stubApi({
    'GET /api/v1/auth/me': meAs(user),
    [LIST]: () => page([ROW]),
    ...routes,
  });
}

/** Every cached query and mutation, serialized. */
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

describe('route and navigation (I1)', () => {
  it('shows the page with one h1, the title and the navigation item', async () => {
    stub();

    app = await renderApp('/invitations');

    expect(main().querySelectorAll('h1')).toHaveLength(1);
    expect(main().querySelector('h1')?.textContent).toBe('Invitations');
    expect(document.title).toBe('Invitations · Classora');
    const nav = [...app.container.querySelectorAll('header nav a')].map(
      (a) => a.textContent,
    );
    expect(nav).toEqual(['Home', 'Invitations']);
  });

  it('a user without user.invite gets no navigation item and the forbidden page', async () => {
    stub({ ...TEST_USER, permissions: ['user.view'] });

    app = await renderApp('/invitations');

    const nav = [...app.container.querySelectorAll('header nav a')].map(
      (a) => a.textContent,
    );
    expect(nav).toEqual(['Home']);
    expect(main().querySelector('h1')?.textContent).toBe(
      "You don't have access to this page",
    );
  });
});

describe('the invite form (I2)', () => {
  it('offers only the roles the user may assign, with translated names', async () => {
    stub(ADMIN);
    app = await renderApp('/invitations');

    const options = [...roleSelect().options].map((o) => o.textContent);
    expect(options).toEqual([
      'Choose a role',
      'Academic coordinator',
      'Finance staff',
      'Tutor',
      'Student',
      'Parent or guardian',
    ]);
  });

  it('an Owner may choose every role', async () => {
    stub();
    app = await renderApp('/invitations');

    expect([...roleSelect().options].map((o) => o.value)).toEqual([
      '',
      'owner',
      'administrator',
      'academic_coordinator',
      'finance_staff',
      'tutor',
      'student',
      'guardian',
    ]);
  });

  it('validates without a request and focuses the first invalid field', async () => {
    const api = stub();
    app = await renderApp('/invitations');

    await click(buttonNamed(main(), 'Create invitation'));

    expect(field('Username').getAttribute('aria-invalid')).toBe('true');
    expect(field('Display name').getAttribute('aria-invalid')).toBe('true');
    expect(roleSelect().getAttribute('aria-invalid')).toBe('true');
    expect(main().textContent).toContain('Enter a username.');
    expect(main().textContent).toContain('Choose a role.');
    expect(document.activeElement).toBe(field('Username'));
    expect(api.calls(CREATE)).toHaveLength(0);
  });

  it('shows the staff note for a staff role only', async () => {
    stub();
    app = await renderApp('/invitations');
    const note = "For a staff role you'll be asked for your password.";

    choose(roleSelect(), 'tutor');
    expect(main().textContent).not.toContain(note);
    choose(roleSelect(), 'finance_staff');
    expect(main().textContent).toContain(note);
    expect(roleSelect().getAttribute('aria-describedby')).toBeTruthy();
  });

  it('sends the invitation with the CSRF token and shows the panel', async () => {
    const api = stub(OWNER, { [CREATE]: () => jsonResponse(201, ISSUED) });
    app = await renderApp('/invitations');

    await fillAndSubmit();

    const [call] = api.calls(CREATE);
    expect(call!.body).toEqual({
      username: 'ana.tutor',
      display_name: 'Ana Tutor',
      role: 'tutor',
    });
    expect(call!.headers.get(CSRF_HEADER)).toBe(OWNER.csrf_token);
    expect(panel()).not.toBeNull();
    expect(linkField().value).toBe(LINK);
    expect(linkField().readOnly).toBe(true);
    expect(panel()!.textContent).toContain(
      'This link is shown only once. Give it to the person in person or over the phone. It expires in 24 hours.',
    );
    expect(panel()!.textContent).toContain('Ana Tutor');
    expect(panel()!.textContent).toContain('ana.tutor');
    expect(panel()!.textContent).toContain('Tutor');
    expect(document.activeElement).toBe(panel()!.querySelector('h2'));
    // The form is cleared for the next invitation; the list is refreshed.
    expect(field('Username').value).toBe('');
    expect(api.calls(LIST)).toHaveLength(2);
    // A link only: no image or canvas (ADR-0007 D11 as amended by #29).
    expect(main().querySelector('img, canvas, svg[role="img"]')).toBeNull();
  });

  it('a staff role opens the password dialog and retries once', async () => {
    const api = stub(OWNER, {
      [CREATE]: [
        () => problem(403, 'reauth_required'),
        () => jsonResponse(201, { ...ISSUED, roles: ['finance_staff'] }),
      ],
      [REAUTH]: () =>
        jsonResponse(200, { reauthenticated_until: '2026-10-19T18:05:00Z' }),
    });
    app = await renderApp('/invitations');

    await fillAndSubmit('finance_staff');
    expect(openDialog()).not.toBeNull();
    typeInto(fieldByLabel(openDialog()!, 'Password'), 'my-own-password');
    await click(buttonNamed(openDialog()!, 'Confirm'));

    expect(api.calls(CREATE)).toHaveLength(2);
    expect(panel()!.textContent).toContain('Finance staff');
  });

  it.each([
    ['username_taken', 'That username is already taken.'],
    [
      'username_invalid',
      'Use 3 to 64 characters: letters, digits, dot, hyphen or underscore.',
    ],
  ])('%s attaches to the username field', async (code, text) => {
    stub(OWNER, {
      [CREATE]: () => problem(code === 'username_taken' ? 409 : 422, code),
    });
    app = await renderApp('/invitations');

    await fillAndSubmit();

    const username = field('Username');
    expect(username.getAttribute('aria-invalid')).toBe('true');
    const described = username
      .getAttribute('aria-describedby')!
      .split(' ')
      .map((id) => document.getElementById(id)?.textContent);
    expect(described).toContain(text);
    expect(alerts(main())).toEqual([]);
    expect(document.activeElement).toBe(username);
    expect(panel()).toBeNull();
  });

  it('other errors show in the alert region', async () => {
    stub(OWNER, { [CREATE]: () => problem(403, 'permission_denied') });
    app = await renderApp('/invitations');

    await fillAndSubmit('owner');

    expect(alerts(main()).join(' ')).toContain(
      "You don't have permission to do this.",
    );
  });
});

describe('the one-time link (I3, I4)', () => {
  it('copies the link and confirms politely', async () => {
    const writeText = vi.fn(() => Promise.resolve());
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } });
    stub(OWNER, { [CREATE]: () => jsonResponse(201, ISSUED) });
    app = await renderApp('/invitations');
    await fillAndSubmit();

    await click(buttonNamed(panel()!, 'Copy link'));

    expect(writeText).toHaveBeenCalledWith(LINK);
    expect(panel()!.querySelector('[role="status"]')?.textContent).toBe(
      'Link copied',
    );
  });

  it('selects the link when the copy fails', async () => {
    const writeText = vi.fn(() => Promise.reject(new Error('denied')));
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } });
    stub(OWNER, { [CREATE]: () => jsonResponse(201, ISSUED) });
    app = await renderApp('/invitations');
    await fillAndSubmit();

    await click(buttonNamed(panel()!, 'Copy link'));

    expect(document.activeElement).toBe(linkField());
    expect(linkField().selectionStart).toBe(0);
    expect(linkField().selectionEnd).toBe(LINK.length);
    expect(panel()!.querySelector('[role="status"]')?.textContent).toBe(
      "Couldn't copy. The link is selected: copy it by hand.",
    );
  });

  it('"Done" drops the link; no cache entry or storage ever holds the token', async () => {
    stub(OWNER, { [CREATE]: () => jsonResponse(201, ISSUED) });
    app = await renderApp('/invitations');
    await fillAndSubmit();
    expect(cacheText()).not.toContain(TOKEN);

    await click(buttonNamed(panel()!, 'Done'));

    expect(panel()).toBeNull();
    expect(app.container.textContent).not.toContain(TOKEN);
    expect(cacheText()).not.toContain(TOKEN);
    expect(app.location()).toBe('/invitations');
    expect(JSON.stringify({ ...window.localStorage })).not.toContain(TOKEN);
    expect(JSON.stringify({ ...window.sessionStorage })).not.toContain(TOKEN);
  });
});

describe('the open invitations (I5)', () => {
  const rows = () => [...main().querySelectorAll('tbody tr')];

  it('lists username, display name, role and status', async () => {
    stub(OWNER, { [LIST]: () => page([ROW, OWNER_ROW]) });
    app = await renderApp('/invitations');

    const cells = rows().map((row) =>
      [...row.querySelectorAll('td')].slice(0, 4).map((c) => c.textContent),
    );
    expect(cells[0]!.slice(0, 3)).toEqual(['ana.tutor', 'Ana Tutor', 'Tutor']);
    expect(cells[0]![3]).toMatch(/^Active until /);
    expect(cells[1]).toEqual(['olga.owner', 'Olga Owner', 'Owner', 'Expired']);
    expect(
      [...main().querySelectorAll('thead th')].map((th) => th.textContent),
    ).toEqual(['Username', 'Display name', 'Role', 'Status', 'Actions']);
  });

  it('"New link" reissues and shows the new link in the panel', async () => {
    const api = stub(OWNER, {
      [`POST /api/v1/invitations/${ROW.user_id}/reissue`]: () =>
        jsonResponse(200, { ...ISSUED, invitation_url: NEW_LINK }),
    });
    app = await renderApp('/invitations');

    await click(buttonNamed(main(), 'New link for ana.tutor'));

    expect(
      api.calls(`POST /api/v1/invitations/${ROW.user_id}/reissue`),
    ).toHaveLength(1);
    expect(linkField().value).toBe(NEW_LINK);
    expect(cacheText()).not.toContain(NEW_TOKEN);
    expect(api.calls(LIST)).toHaveLength(2);
  });

  it('hides "New link" for a row with a role the user cannot assign', async () => {
    stub(ADMIN, { [LIST]: () => page([ROW, OWNER_ROW]) });
    app = await renderApp('/invitations');

    expect(rows()[0]!.querySelector('button')).not.toBeNull();
    expect(rows()[1]!.querySelector('button')).toBeNull();
  });

  it('shows the empty state', async () => {
    stub(OWNER, { [LIST]: () => page([]) });
    app = await renderApp('/invitations');

    expect(main().textContent).toContain('There are no open invitations.');
    expect(main().querySelector('table')).toBeNull();
  });

  it('shows the error with a retry', async () => {
    const api = stub(OWNER, {
      // A 4xx is never retried automatically.
      [LIST]: [() => problem(400, 'bad_request'), () => page([ROW])],
    });
    app = await renderApp('/invitations');

    expect(alerts(main()).join(' ')).toContain(
      "We couldn't process the request.",
    );
    await click(buttonNamed(main(), 'Try again'));

    expect(rows()).toHaveLength(1);
    expect(api.calls(LIST)).toHaveLength(2);
  });

  it('pages with previous and next', async () => {
    const offsets: string[] = [];
    stub(OWNER, {
      [LIST]: (request) => {
        offsets.push(new URL(request.url).search);
        const offset = Number(new URL(request.url).searchParams.get('offset'));
        return page(offset === 0 ? [ROW] : [OWNER_ROW], 11, offset);
      },
    });
    app = await renderApp('/invitations');
    expect(main().textContent).toContain('1–1 of 11');
    expect(buttonNamed(main(), 'Previous').disabled).toBe(true);

    await click(buttonNamed(main(), 'Next'));

    expect(offsets).toEqual(['?limit=10&offset=0', '?limit=10&offset=10']);
    expect(main().textContent).toContain('11–11 of 11');
    expect(rows()[0]!.textContent).toContain('olga.owner');
    expect(buttonNamed(main(), 'Next').disabled).toBe(true);
  });
});
