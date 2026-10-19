import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { applyLanguage } from '@/i18n';
import { CSRF_HEADER } from '@/features/auth/csrf';
import { SESSION_QUERY_KEY } from '@/features/auth/session';
import {
  noContent,
  problem,
  stubSignedIn,
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

// #30 K1–K4: /account/password, the header link, storing the rotated CSRF
// token and never retaining a password.

const CHANGE = 'POST /api/v1/auth/password';
const LOGOUT = 'POST /api/v1/auth/logout';
const CURRENT = 'my-current-password';
const NEW = 'my-brand-new-password';
const NEW_CSRF = 'csrf-token-ccccccccccccccccccccccccccccccccccc';
const ROTATED = { ...TEST_USER, csrf_token: NEW_CSRF };

let app: RenderedApp | undefined;

const main = () => app!.container.querySelector('main')!;
const field = (label: string) => fieldByLabel(main(), label);
const status = () => main().querySelector('[role="status"]')?.textContent ?? '';

function stub(
  change: Responder | Responder[] = () => jsonResponse(200, ROTATED),
): ApiStub {
  return stubSignedIn({ [CHANGE]: change, [LOGOUT]: noContent });
}

async function fillAndSubmit(current = CURRENT, next = NEW, confirm = next) {
  typeInto(field('Current password'), current);
  typeInto(field('New password'), next);
  typeInto(field('Confirm new password'), confirm);
  await click(buttonNamed(main(), 'Change password'));
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

describe('route and header link (K1)', () => {
  it('the header links to the page; one h1 and a title; no nav item', async () => {
    stub();
    app = await renderApp('/');

    const link = [...app.container.querySelectorAll('header a')].find(
      (a) => a.textContent === 'Change password',
    );
    expect(link?.getAttribute('href')).toBe('/account/password');
    const nav = [...app.container.querySelectorAll('header nav a')].map(
      (a) => a.textContent,
    );
    expect(nav).not.toContain('Change password');

    await click(link as HTMLElement);

    expect(app.location()).toBe('/account/password');
    expect(main().querySelectorAll('h1')).toHaveLength(1);
    expect(main().querySelector('h1')?.textContent).toBe('Change password');
    expect(document.title).toBe('Change password · Classora');
  });

  it('signed out, the guard sends the visitor to /login', async () => {
    stubSignedIn({
      'GET /api/v1/auth/me': () => problem(401, 'authentication_required'),
    });

    app = await renderApp('/account/password');

    expect(app.location()).toContain('/login');
  });
});

describe('the form (K2)', () => {
  it('has the autocomplete tokens and the signed-in username', async () => {
    stub();
    app = await renderApp('/account/password');

    const username = field('Username');
    expect(username.readOnly).toBe(true);
    expect(username.value).toBe('demo.owner');
    expect(username.getAttribute('autocomplete')).toBe('username');
    expect(field('Current password').getAttribute('autocomplete')).toBe(
      'current-password',
    );
    expect(field('New password').getAttribute('autocomplete')).toBe(
      'new-password',
    );
    expect(field('Confirm new password').getAttribute('autocomplete')).toBe(
      'new-password',
    );
  });

  it('validates without a request', async () => {
    const api = stub();
    app = await renderApp('/account/password');

    await fillAndSubmit('', 'too-short');
    expect(field('Current password').getAttribute('aria-invalid')).toBe('true');
    expect(main().textContent).toContain('Enter your current password.');
    expect(main().textContent).toContain('Use at least 12 characters.');

    await fillAndSubmit(CURRENT, NEW, `${NEW}x`);
    expect(main().textContent).toContain("The passwords don't match.");
    expect(api.calls(CHANGE)).toHaveLength(0);
  });

  it('current_password_invalid attaches to the current-password field', async () => {
    stub(() => problem(403, 'current_password_invalid'));
    app = await renderApp('/account/password');

    await fillAndSubmit();

    const current = field('Current password');
    expect(current.getAttribute('aria-invalid')).toBe('true');
    const described = current
      .getAttribute('aria-describedby')!
      .split(' ')
      .map((id) => document.getElementById(id)?.textContent);
    expect(described).toContain('The current password is incorrect.');
    expect(document.activeElement).toBe(current);
    expect(current.value).toBe('');
    expect(alerts(main())).toEqual([]);
    expect(app.location()).toBe('/account/password');
  });

  it.each(['password_too_short', 'password_equals_username'])(
    '%s attaches to the new-password field',
    async (code) => {
      stub(() => problem(422, code));
      app = await renderApp('/account/password');

      await fillAndSubmit();

      expect(field('New password').getAttribute('aria-invalid')).toBe('true');
      expect(alerts(main())).toEqual([]);
    },
  );

  it('rate_limited shows in the alert region and empties the passwords', async () => {
    stub(() => problem(429, 'rate_limited'));
    app = await renderApp('/account/password');

    await fillAndSubmit();

    expect(alerts(main()).join(' ')).toContain('Too many attempts.');
    expect(field('Current password').value).toBe('');
    expect(field('New password').value).toBe('');
    expect(field('Confirm new password').value).toBe('');
    expect(field('Username').value).toBe('demo.owner');
  });
});

describe('after a change (K3, K4)', () => {
  it('stores the new CSRF token: the next unsafe request carries it', async () => {
    const api = stub();
    app = await renderApp('/account/password');

    await fillAndSubmit();

    const [call] = api.calls(CHANGE);
    expect(call!.body).toEqual({
      current_password: CURRENT,
      new_password: NEW,
    });
    expect(call!.headers.get(CSRF_HEADER)).toBe(TEST_USER.csrf_token);
    expect(app.location()).toBe('/account/password');
    expect(status()).toBe(
      'Your password was changed. You were signed out on your other devices.',
    );
    expect(field('Current password').value).toBe('');
    expect(field('New password').value).toBe('');
    // The session cache holds the user, never the CSRF token.
    const cached = app.queryClient.getQueryData(SESSION_QUERY_KEY);
    expect(cached).toEqual({
      user: {
        id: TEST_USER.id,
        username: TEST_USER.username,
        display_name: TEST_USER.display_name,
        roles: TEST_USER.roles,
        permissions: TEST_USER.permissions,
        privacy_notice_required: false,
      },
    });
    expect(JSON.stringify(cached)).not.toContain(NEW_CSRF);

    await click(
      buttonNamed(app.container.querySelector('header')!, 'Sign out'),
    );

    const [logout] = api.calls(LOGOUT);
    expect(logout!.headers.get(CSRF_HEADER)).toBe(NEW_CSRF);
    expect(
      api.requests.filter((r) => r.path === '/api/v1/auth/me'),
    ).toHaveLength(1);
    expect(app.location()).toBe('/login');
  });

  it('never retains a password (K4)', async () => {
    stub();
    app = await renderApp('/account/password');

    await fillAndSubmit();

    const text = cacheText();
    for (const secret of [CURRENT, NEW]) {
      expect(text).not.toContain(secret);
      expect(
        JSON.stringify({ ...window.localStorage, ...window.sessionStorage }),
      ).not.toContain(secret);
    }
  });

  it('switching the language keeps typed values (L2)', async () => {
    const api = stub();
    app = await renderApp('/account/password');
    typeInto(field('Current password'), CURRENT);

    act(() => applyLanguage('es'));

    expect(main().querySelector('h1')?.textContent).toBe('Cambiar contraseña');
    expect(fieldByLabel(main(), 'Contraseña actual').value).toBe(CURRENT);
    expect(api.calls(CHANGE)).toHaveLength(0);
  });
});
