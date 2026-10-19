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
import type { PasswordResetIssued } from './api';

// #30 I1–I5: the /password-resets page, its navigation item, the one-time
// link panel and the rule that the link is never retained (I5). The token is
// a low-entropy placeholder.

const ISSUE = 'POST /api/v1/password-resets';
const REAUTH = 'POST /api/v1/auth/reauthenticate';
const TOKEN = 'p'.repeat(43);
const LINK = `http://127.0.0.1:5173/reset#token=${TOKEN}`;

/** An Owner who may issue resets. */
const OWNER: SessionUserBody = {
  ...TEST_USER,
  permissions: ['user.invite', 'user.password_reset.issue'],
};

const ISSUED: PasswordResetIssued = {
  user_id: '00000000-0000-4000-8000-0000000000aa',
  username: 'ana.tutor',
  display_name: 'Ana Tutor',
  expires_at: '2026-10-20T18:00:00Z',
  reset_url: LINK,
};

let app: RenderedApp | undefined;

const main = () => app!.container.querySelector('main')!;
const field = (label: string) => fieldByLabel(main(), label);
const panel = () =>
  [...main().querySelectorAll('section')].find(
    (s) => s.querySelector('h2')?.textContent === 'Reset link ready',
  ) ?? null;
const navItems = () =>
  [...app!.container.querySelectorAll('header nav a')].map(
    (a) => a.textContent,
  );

/** The issue goes through the reauthentication dialog (always required). */
const reauthenticated: Responder[] = [
  () => problem(403, 'reauth_required'),
  () => jsonResponse(201, ISSUED),
];

function stub(
  user: SessionUserBody = OWNER,
  routes: Record<string, Responder | Responder[]> = {},
): ApiStub {
  return stubApi({
    'GET /api/v1/auth/me': meAs(user),
    [REAUTH]: () =>
      jsonResponse(200, { reauthenticated_until: '2026-10-19T18:05:00Z' }),
    ...routes,
  });
}

async function submit(username = 'ana.tutor') {
  typeInto(field('Username'), username);
  await click(buttonNamed(main(), 'Create reset link'));
}

async function confirmPassword() {
  expect(openDialog()).not.toBeNull();
  typeInto(fieldByLabel(openDialog()!, 'Password'), 'my-own-password');
  await click(buttonNamed(openDialog()!, 'Confirm'));
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

    app = await renderApp('/password-resets');

    expect(main().querySelectorAll('h1')).toHaveLength(1);
    expect(main().querySelector('h1')?.textContent).toBe('Password resets');
    expect(document.title).toBe('Password resets · Classora');
    expect(navItems()).toEqual(['Home', 'Invitations', 'Password resets']);
  });

  it('without user.password_reset.issue: no item and the forbidden page', async () => {
    stub({ ...TEST_USER, permissions: ['user.view'] });

    app = await renderApp('/password-resets');

    expect(navItems()).toEqual(['Home']);
    expect(main().querySelector('h1')?.textContent).toBe(
      "You don't have access to this page",
    );
  });
});

describe('the form (I2, I3)', () => {
  it('has a labelled username field and the help text', async () => {
    stub();
    app = await renderApp('/password-resets');

    const username = field('Username');
    expect(username.getAttribute('aria-describedby')).toBeTruthy();
    expect(main().textContent).toContain(
      "We'll ask for your password. The person's sessions end when they set the new password.",
    );
  });

  it('validates an empty username without a request', async () => {
    const api = stub();
    app = await renderApp('/password-resets');

    await click(buttonNamed(main(), 'Create reset link'));

    expect(field('Username').getAttribute('aria-invalid')).toBe('true');
    expect(main().textContent).toContain('Enter a username.');
    expect(api.calls(ISSUE)).toHaveLength(0);
  });

  it('the own username shows the Change password hint and sends nothing', async () => {
    const api = stub();
    app = await renderApp('/password-resets');

    await submit(' Demo.Owner ');

    expect(main().textContent).toContain(
      'To change your own password, use Change password.',
    );
    const link = [...main().querySelectorAll('a')].find(
      (a) => a.textContent === 'Change password',
    );
    expect(link?.getAttribute('href')).toBe('/account/password');
    expect(api.calls(ISSUE)).toHaveLength(0);
    expect(openDialog()).toBeNull();
  });

  it('opens the password dialog, retries once and shows the panel', async () => {
    const api = stub(OWNER, { [ISSUE]: reauthenticated });
    app = await renderApp('/password-resets');

    await submit();
    await confirmPassword();

    const calls = api.calls(ISSUE);
    expect(calls).toHaveLength(2);
    expect(calls[1]!.body).toEqual({ username: 'ana.tutor' });
    expect(calls[1]!.headers.get(CSRF_HEADER)).toBe(TEST_USER.csrf_token);
    expect(panel()).not.toBeNull();
    expect(panel()!.textContent).toContain('Ana Tutor');
    expect(panel()!.textContent).toContain('ana.tutor');
    expect(field('Password reset link').value).toBe(LINK);
    expect(field('Password reset link').readOnly).toBe(true);
    expect(panel()!.textContent).toContain('This link is shown only once.');
    expect(panel()!.querySelector('img, canvas, svg[role="img"]')).toBeNull();
    expect(field('Username').value).toBe('');
  });

  it.each([
    ['not_found', 404, 'No user has this username.'],
    [
      'user_not_active',
      409,
      "This account isn't active. If it's a pending invitation, create a new link in Invitations.",
    ],
  ])('%s attaches to the username field', async (code, status, text) => {
    stub(OWNER, { [ISSUE]: () => problem(status, code) });
    app = await renderApp('/password-resets');

    await submit();

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

  it('permission_denied explains the Owner rule in the alert region', async () => {
    stub(OWNER, { [ISSUE]: () => problem(403, 'permission_denied') });
    app = await renderApp('/password-resets');

    await submit('admin.user');

    expect(alerts(main()).join(' ')).toContain(
      "You can't issue a reset for this account. Only an Owner can reset Owner and Administrator accounts.",
    );
  });

  it('other errors show their catalog message in the alert region', async () => {
    stub(OWNER, { [ISSUE]: () => problem(429, 'rate_limited') });
    app = await renderApp('/password-resets');

    await submit();

    expect(alerts(main()).join(' ')).toContain('Too many attempts.');
  });
});

describe('the one-time link (I4, I5)', () => {
  it('copies the link and confirms politely', async () => {
    const writeText = vi.fn(() => Promise.resolve());
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } });
    stub(OWNER, { [ISSUE]: () => jsonResponse(201, ISSUED) });
    app = await renderApp('/password-resets');
    await submit();

    await click(buttonNamed(panel()!, 'Copy link'));

    expect(writeText).toHaveBeenCalledWith(LINK);
    expect(panel()!.querySelector('[role="status"]')?.textContent).toBe(
      'Link copied',
    );
  });

  it('"Done" drops the link; no cache entry or storage ever holds the token', async () => {
    stub(OWNER, { [ISSUE]: reauthenticated });
    app = await renderApp('/password-resets');
    await submit();
    await confirmPassword();
    expect(panel()).not.toBeNull();
    expect(cacheText()).not.toContain(TOKEN);

    await click(buttonNamed(panel()!, 'Done'));

    expect(panel()).toBeNull();
    expect(main().textContent).not.toContain(TOKEN);
    expect(cacheText()).not.toContain(TOKEN);
    const storage = JSON.stringify({
      local: { ...window.localStorage },
      session: { ...window.sessionStorage },
    });
    expect(storage).not.toContain(TOKEN);
    expect(app.location()).not.toContain(TOKEN);
  });
});
