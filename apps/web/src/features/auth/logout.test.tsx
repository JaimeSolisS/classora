import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { applyLanguage } from '@/i18n';
import { useUiStore } from '@/stores/ui';
import {
  noContent,
  problem,
  stubSignedIn,
  TEST_USER,
  type Responder,
} from '@/test/api-stub';
import { QueryPage } from '@/test/auth-pages';
import { routesWith } from '@/test/auth-routes';
import { alerts, click, fieldByLabel, typeInto } from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  settle,
  type RenderedApp,
} from '@/test/render-app';
import { CSRF_HEADER, getCsrfToken } from './csrf';

// E1–E4 (and C2 after a logout): signing out.

const LOGOUT = 'POST /api/v1/auth/logout';

let app: RenderedApp | undefined;

const pages = routesWith([
  {
    path: 'data',
    element: (
      <>
        <QueryPage path="/api/v1/test/one" />
        <QueryPage path="/api/v1/test/two" />
      </>
    ),
  },
]);

const h1 = () => app?.container.querySelector('main h1')?.textContent;
const header = () => app!.container.querySelector('header')!;

function stubLogout(...logout: Responder[]) {
  return stubSignedIn({
    [LOGOUT]: logout,
    'GET /api/v1/test/one': () => jsonResponse(200, { one: 1 }),
    'GET /api/v1/test/two': () => jsonResponse(200, { two: 2 }),
    'POST /api/v1/auth/login': () => jsonResponse(200, TEST_USER),
  });
}

async function signOut() {
  await click(buttonNamed(header(), 'Sign out'));
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
});

describe('the header (E1)', () => {
  it('shows the user name as text and a Sign out button', async () => {
    stubLogout(noContent);
    app = await renderApp('/', pages);

    expect(header().textContent).toContain('Signed in as Demo Owner');
    expect(header().querySelector('a[href*="Demo"]')).toBeNull();
    // In the header menu that collapses below lg, with the switchers.
    const menu = header().querySelector('#app-header-menu')!;
    expect(buttonNamed(menu, 'Sign out')).toBeTruthy();
  });
});

describe('sign out (E2, E4)', () => {
  it.each([
    ['204', noContent],
    ['401 (already gone)', () => problem(401, 'authentication_required')],
  ])(
    'on %s: token, caches and UI store cleared, /login without next or notice',
    async (_name, answer) => {
      const stub = stubLogout(answer);
      app = await renderApp('/data', pages);
      expect(app.queryClient.getQueryCache().getAll().length).toBeGreaterThan(
        2,
      );
      act(() => useUiStore.getState().setNavOpen(true));

      await signOut();

      const [logout] = stub.calls(LOGOUT);
      expect(logout!.headers.get(CSRF_HEADER)).toBe(TEST_USER.csrf_token);
      expect(app.location()).toBe('/login');
      expect(h1()).toBe('Sign in');
      expect(
        app.container.querySelector('main [role="status"]')?.textContent,
      ).toBe('');
      expect(getCsrfToken()).toBeNull();
      expect(useUiStore.getState().navOpen).toBe(false);
      const queries = app.queryClient.getQueryCache().getAll();
      expect(queries.map((query) => query.queryKey.join('/'))).toEqual([
        'auth/me',
      ]);
      expect(queries[0]!.state.data).toEqual({
        user: null,
        reason: 'signed_out',
      });
      expect(app.queryClient.getMutationCache().getAll()).toHaveLength(0);
      expect(header().textContent).not.toContain('Sign out');
    },
  );

  it('the next unsafe request carries no X-CSRF-Token', async () => {
    const stub = stubLogout(noContent);
    app = await renderApp('/', pages);
    await signOut();

    typeInto(fieldByLabel(app.container, 'Username'), 'demo.owner');
    typeInto(fieldByLabel(app.container, 'Password'), 'secret-password');
    await click(
      app.container.querySelector<HTMLButtonElement>(
        'main button[type="submit"]',
      )!,
    );

    const [login] = stub.calls('POST /api/v1/auth/login');
    expect(login!.headers.get(CSRF_HEADER)).toBeNull();
  });

  it('back after sign out shows the login page again (C2)', async () => {
    stubLogout(noContent);
    app = await renderApp('/', pages);
    await act(async () => {
      await app!.router.navigate('/data');
    });
    await settle();

    await signOut();
    expect(app.location()).toBe('/login');
    await act(async () => {
      await app!.router.navigate(-1);
    });
    await settle();

    expect(h1()).toBe('Sign in');
    expect(app.location()).toBe('/login');
    expect(app.container.textContent).not.toContain('loaded');
  });
});

describe('failure (E3)', () => {
  it.each([
    ['503', () => problem(503, 'database_unavailable')],
    ['a network error', () => Promise.reject(new TypeError('Failed to fetch'))],
  ])('on %s: shows the error and stays signed in', async (_name, answer) => {
    const stub = stubLogout(answer, noContent);
    app = await renderApp('/data', pages);

    await signOut();

    expect(app.location()).toBe('/data');
    expect(alerts(header())).toHaveLength(1);
    expect(getCsrfToken()).toBe(TEST_USER.csrf_token);
    expect(header().textContent).toContain('Signed in as Demo Owner');

    // The button can be pressed again.
    await signOut();
    expect(stub.calls(LOGOUT)).toHaveLength(2);
    expect(app.location()).toBe('/login');
  });

  it('csrf_failed twice (after C3): shows the error and stays signed in', async () => {
    const stub = stubLogout(() => problem(403, 'csrf_failed'));
    app = await renderApp('/data', pages);

    await signOut();

    expect(stub.calls(LOGOUT)).toHaveLength(2);
    expect(alerts(header())[0]).toContain("We couldn't verify the request.");
    expect(app.location()).toBe('/data');
  });
});
