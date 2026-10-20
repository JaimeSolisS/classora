import { focusManager } from '@tanstack/react-query';
import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { applyLanguage } from '@/i18n';
import {
  meAs,
  OTHER_USER,
  problem,
  stubApi,
  stubSignedIn,
  stubSignedOut,
  TEST_USER,
} from '@/test/api-stub';
import { QueryPage } from '@/test/auth-pages';
import { routesWith } from '@/test/auth-routes';
import {
  buttonNamed,
  jsonResponse,
  mountApp,
  renderApp,
  settle,
  type RenderedApp,
} from '@/test/render-app';
import { getCsrfToken } from './csrf';
import { SESSION_QUERY_KEY } from './session';

// A1–A5: the session query, bootstrap, no polling, the token kept out of the
// cache, and a user who changed under the app.

let app: RenderedApp | undefined;

const h1 = () => app?.container.querySelector('main h1')?.textContent;
const statusRegion = () =>
  app?.container.querySelector('main [role="status"]')?.textContent;

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
  focusManager.setFocused(undefined);
  window.localStorage.clear();
  window.sessionStorage.clear();
});

describe('bootstrap (A1)', () => {
  it('signed out: goes to /login with the page as next, without a notice', async () => {
    stubSignedOut();

    app = await renderApp('/e2e/deep?x=1#top');

    expect(app.location()).toBe(
      `/login?next=${encodeURIComponent('/e2e/deep?x=1#top')}`,
    );
    expect(h1()).toBe('Sign in');
    expect(statusRegion()).toBe('');
  });

  it('session_expired: goes to /login with the "session expired" notice', async () => {
    stubApi({
      'GET /api/v1/auth/me': () => problem(401, 'session_expired'),
    });

    app = await renderApp('/students');

    expect(app.location()).toBe('/login?next=%2Fstudents');
    expect(statusRegion()).toBe('Your session has expired. Sign in again.');
  });

  it('signed in: shows the page', async () => {
    stubSignedIn();

    app = await renderApp('/');

    expect(app.location()).toBe('/');
    expect(h1()).toBe('Classora');
  });

  it('loading: shows the busy session state, never the page or a redirect', async () => {
    stubApi(); // /auth/me never answers

    app = await renderApp('/somewhere');

    expect(app.location()).toBe('/somewhere');
    const busy = app.container.querySelector('main [aria-busy="true"]');
    expect(busy?.textContent).toBe('Loading your session…');
    expect(app.container.querySelector('main h1')).toBeNull();
  });

  it('a 5xx is retried by the query policy, then shows the error with "Try again" (no redirect)', async () => {
    vi.useFakeTimers();
    const stub = stubApi({
      'GET /api/v1/auth/me': [
        () => problem(503, 'database_unavailable'),
        () => problem(503, 'database_unavailable'),
        () => problem(503, 'database_unavailable'),
        meAs(),
      ],
    });

    app = mountApp('/page');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });

    expect(stub.calls('GET /api/v1/auth/me')).toHaveLength(3);
    expect(app.location()).toBe('/page');
    const alert = app.container.querySelector('main [role="alert"]');
    expect(alert?.textContent).toContain(
      'The service is temporarily unavailable.',
    );

    act(() => buttonNamed(app!.container, 'Try again').click());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });
    expect(stub.calls('GET /api/v1/auth/me')).toHaveLength(4);
    expect(h1()).toBe('Page not found');
  });

  it('a network error is retried, then shows the error', async () => {
    vi.useFakeTimers();
    const stub = stubApi({
      'GET /api/v1/auth/me': () =>
        Promise.reject(new TypeError('Failed to fetch')),
    });

    app = mountApp('/');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });

    expect(stub.calls('GET /api/v1/auth/me')).toHaveLength(3);
    expect(
      app.container.querySelector('main [role="alert"]')?.textContent,
    ).toContain("Can't reach the server.");
    expect(app.location()).toBe('/');
  });
});

describe('no polling (A2)', () => {
  it('an idle tab makes no /auth/me call after the first, for 31 minutes', async () => {
    vi.useFakeTimers();
    const stub = stubSignedIn({
      'GET /health/live': () => jsonResponse(200, { status: 'ok' }),
    });

    app = mountApp('/');
    for (let minute = 0; minute < 31; minute += 1) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(60_000);
      });
    }

    expect(h1()).toBe('Classora');
    expect(stub.calls('GET /api/v1/auth/me')).toHaveLength(1);
  });
});

describe('CSRF token in memory only (A3)', () => {
  it('keeps the token out of the cached session, the query cache and storage', async () => {
    stubSignedIn();

    app = await renderApp('/');

    expect(getCsrfToken()).toBe(TEST_USER.csrf_token);
    const cached = app.queryClient.getQueryData(SESSION_QUERY_KEY) as {
      user: Record<string, unknown>;
    };
    expect(cached.user).toEqual({
      id: TEST_USER.id,
      username: TEST_USER.username,
      display_name: TEST_USER.display_name,
      roles: TEST_USER.roles,
      permissions: TEST_USER.permissions,
      privacy_notice_required: false,
      preferences: TEST_USER.preferences,
      center_time_zone: TEST_USER.center_time_zone,
    });
    expect(cached.user).not.toHaveProperty('csrf_token');
    const everything = JSON.stringify(
      app.queryClient
        .getQueryCache()
        .getAll()
        .map((query) => query.state),
    );
    expect(everything).not.toContain(TEST_USER.csrf_token);
    expect(JSON.stringify({ ...window.localStorage })).not.toContain(
      TEST_USER.csrf_token,
    );
    expect(JSON.stringify({ ...window.sessionStorage })).not.toContain(
      TEST_USER.csrf_token,
    );
    expect(document.cookie).not.toContain(TEST_USER.csrf_token);
  });

  it('takes the new token from every /auth/me response', async () => {
    const renewed = { ...TEST_USER, csrf_token: 'renewed-token' };
    stubApi({ 'GET /api/v1/auth/me': [meAs(), meAs(renewed)] });
    app = await renderApp('/');
    expect(getCsrfToken()).toBe(TEST_USER.csrf_token);

    await act(async () => {
      await app!.queryClient.refetchQueries({ queryKey: SESSION_QUERY_KEY });
    });

    expect(getCsrfToken()).toBe('renewed-token');
  });
});

describe('user changed under the app (A5)', () => {
  it('a focus refetch with another user clears the cache, takes the session and goes home', async () => {
    const stub = stubSignedIn({
      'GET /api/v1/test/data': () => jsonResponse(200, { ok: true }),
    });
    app = await renderApp(
      '/q',
      routesWith([{ path: 'q', element: <QueryPage /> }]),
    );
    expect(
      app.container.querySelector('[data-testid="query"]')?.textContent,
    ).toBe('loaded');
    expect(app.container.textContent).toContain('Signed in as Demo Owner');

    // Another tab signed in as someone else; this tab regains focus after
    // the session data went stale.
    stub.set({ 'GET /api/v1/auth/me': meAs(OTHER_USER) });
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(Date.now() + 61_000);
    act(() => focusManager.setFocused(false));
    act(() => focusManager.setFocused(true));
    await settle();

    expect(stub.calls('GET /api/v1/auth/me')).toHaveLength(2);
    expect(app.location()).toBe('/');
    expect(app.container.textContent).toContain('Signed in as Other User');
    expect(getCsrfToken()).toBe(OTHER_USER.csrf_token);
    const keys = app.queryClient
      .getQueryCache()
      .getAll()
      .map((query) => query.queryKey[0]);
    expect(keys).not.toContain('test');
  });

  it('a focus refetch that finds the session gone signs out (login with next)', async () => {
    const stub = stubSignedIn();
    app = await renderApp('/x?y=1');
    expect(h1()).toBe('Page not found');

    stub.set({
      'GET /api/v1/auth/me': () => problem(401, 'authentication_required'),
    });
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(Date.now() + 61_000);
    act(() => focusManager.setFocused(false));
    act(() => focusManager.setFocused(true));
    await settle();

    expect(app.location()).toBe('/login?next=%2Fx%3Fy%3D1');
    expect(statusRegion()).toBe('');
    expect(getCsrfToken()).toBeNull();
  });
});
