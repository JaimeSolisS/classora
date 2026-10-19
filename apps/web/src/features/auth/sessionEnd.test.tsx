import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { applyLanguage } from '@/i18n';
import {
  meAs,
  OTHER_USER,
  problem,
  stubSignedIn,
  TEST_USER,
  type Responder,
} from '@/test/api-stub';
import { ActionPage, QueryPage } from '@/test/auth-pages';
import { routesWith } from '@/test/auth-routes';
import { alerts, click } from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  settle,
  type RenderedApp,
} from '@/test/render-app';
import { CSRF_HEADER, getCsrfToken } from './csrf';

// C1–C3 (and A5 through C3): the reactions to a session that ends and to a
// CSRF failure, for any request made through apiCall.

let app: RenderedApp | undefined;

const ACTION = 'POST /api/v1/test/action';
const ME = 'GET /api/v1/auth/me';

const pages = routesWith([
  { path: 'q', element: <QueryPage /> },
  {
    path: 'two',
    element: (
      <>
        <QueryPage path="/api/v1/test/one" />
        <QueryPage path="/api/v1/test/two" />
      </>
    ),
  },
  { path: 'action', element: <ActionPage /> },
]);

const h1 = () => app?.container.querySelector('main h1')?.textContent;
const statusRegion = () =>
  app?.container.querySelector('main [role="status"]')?.textContent;
const result = () =>
  app?.container.querySelector('[data-testid="result"]')?.textContent;

async function runAction() {
  await click(buttonNamed(app!.container, 'Run action'));
}

/** A response that waits until `release()` is called. */
function deferred(response: () => Response) {
  let release: () => void = () => {};
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const responder: Responder = async () => {
    await gate;
    return response();
  };
  return { responder, release };
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

describe('401 from any request (C1)', () => {
  it('session_expired from a query: clears token and cache, login with next and the notice', async () => {
    stubSignedIn({
      'GET /api/v1/test/data': () => problem(401, 'session_expired'),
    });

    app = await renderApp('/q?a=1#h', pages);

    expect(app.location()).toBe(
      `/login?next=${encodeURIComponent('/q?a=1#h')}`,
    );
    expect(h1()).toBe('Sign in');
    expect(statusRegion()).toBe('Your session has expired. Sign in again.');
    expect(getCsrfToken()).toBeNull();
    const keys = app.queryClient
      .getQueryCache()
      .getAll()
      .map((query) => query.queryKey.join('/'));
    expect(keys).toEqual(['auth/me']);
  });

  it('authentication_required from a mutation: login with next, no notice', async () => {
    stubSignedIn({ [ACTION]: () => problem(401, 'authentication_required') });
    app = await renderApp('/action', pages);

    await runAction();

    expect(app.location()).toBe('/login?next=%2Faction');
    expect(statusRegion()).toBe('');
    expect(app.queryClient.getMutationCache().getAll()).toHaveLength(0);
  });

  it('two requests failing together end the session once', async () => {
    const one = deferred(() => problem(401, 'session_expired'));
    const two = deferred(() => problem(401, 'session_expired'));
    stubSignedIn({
      'GET /api/v1/test/one': one.responder,
      'GET /api/v1/test/two': two.responder,
    });
    app = await renderApp('/two', pages);
    const clear = vi.spyOn(app.queryClient, 'clear');
    const navigate = vi.spyOn(app.router, 'navigate');

    one.release();
    two.release();
    await settle();

    expect(clear).toHaveBeenCalledTimes(1);
    expect(navigate).toHaveBeenCalledTimes(1);
    expect(app.location()).toBe('/login?next=%2Ftwo');
  });

  it('on /login it does not navigate again', async () => {
    stubSignedIn({ [ACTION]: () => problem(401, 'session_expired') });
    app = await renderApp('/action', pages);
    await runAction();
    expect(app.location()).toBe('/login?next=%2Faction');
    const navigate = vi.spyOn(app.router, 'navigate');

    // A late 401 from another request while on /login.
    await act(async () => {
      await app!.queryClient
        .fetchQuery({
          queryKey: ['late'],
          queryFn: () =>
            import('@/app/api').then(({ apiCall }) =>
              apiCall(() =>
                Promise.resolve({
                  response: problem(401, 'session_expired'),
                  error: {
                    type: 'about:blank',
                    title: 'x',
                    status: 401,
                    code: 'session_expired',
                    request_id: 'r',
                  },
                }),
              ),
            ),
          retry: false,
        })
        .catch(() => {});
    });
    await settle();

    expect(navigate).not.toHaveBeenCalled();
    expect(app.location()).toBe('/login?next=%2Faction');
  });
});

describe('back button (C2)', () => {
  it('after a session end, going back to an app page shows the login page', async () => {
    stubSignedIn({ [ACTION]: () => problem(401, 'session_expired') });
    app = await renderApp('/', pages);
    await act(async () => {
      await app!.router.navigate('/action');
    });
    await settle();
    await runAction();
    expect(app.location()).toBe('/login?next=%2Faction');

    await act(async () => {
      await app!.router.navigate(-1);
    });
    await settle();

    expect(app.location()).toBe('/login?next=%2F');
    expect(h1()).toBe('Sign in');
  });
});

describe('csrf_failed (C3)', () => {
  it('refetches /auth/me once and retries once with the new token', async () => {
    const renewed = { ...TEST_USER, csrf_token: 'renewed-token' };
    const stub = stubSignedIn({
      [ACTION]: [
        () => problem(403, 'csrf_failed'),
        () => jsonResponse(200, {}),
      ],
    });
    stub.set({ [ME]: [meAs(), meAs(renewed)] });
    app = await renderApp('/action', pages);

    await runAction();

    expect(result()).toBe('done');
    const actions = stub.calls(ACTION);
    expect(actions).toHaveLength(2);
    expect(actions[0]!.headers.get(CSRF_HEADER)).toBe(TEST_USER.csrf_token);
    expect(actions[1]!.headers.get(CSRF_HEADER)).toBe('renewed-token');
    expect(stub.calls(ME)).toHaveLength(2);
    expect(app.location()).toBe('/action');
  });

  it('a second csrf_failed reaches the caller (sent twice, never more)', async () => {
    const stub = stubSignedIn({ [ACTION]: () => problem(403, 'csrf_failed') });
    app = await renderApp('/action', pages);

    await runAction();

    expect(stub.calls(ACTION)).toHaveLength(2);
    expect(stub.calls(ME)).toHaveLength(2);
    expect(alerts(app.container)[0]).toContain(
      "We couldn't verify the request.",
    );
    expect(result()).toBe('idle');
  });

  it('a 401 from the refetch ends the session (C1), without a retry', async () => {
    const stub = stubSignedIn({ [ACTION]: () => problem(403, 'csrf_failed') });
    stub.set({ [ME]: [meAs(), () => problem(401, 'session_expired')] });
    app = await renderApp('/action', pages);

    await runAction();

    expect(stub.calls(ACTION)).toHaveLength(1);
    expect(app.location()).toBe('/login?next=%2Faction');
    expect(statusRegion()).toBe('Your session has expired. Sign in again.');
  });

  it('another user from the refetch: no retry, cache cleared, home (A5)', async () => {
    const stub = stubSignedIn({
      [ACTION]: () => problem(403, 'csrf_failed'),
      'GET /health/live': () => jsonResponse(200, { status: 'ok' }),
    });
    stub.set({ [ME]: [meAs(), meAs(OTHER_USER)] });
    app = await renderApp('/action', pages);
    app.queryClient.setQueryData(['old', 'user', 'data'], { secret: 1 });

    await runAction();

    expect(stub.calls(ACTION)).toHaveLength(1);
    expect(app.location()).toBe('/');
    expect(app.container.textContent).toContain('Signed in as Other User');
    expect(
      app.queryClient.getQueryData(['old', 'user', 'data']),
    ).toBeUndefined();
    expect(getCsrfToken()).toBe(OTHER_USER.csrf_token);
  });

  it('a network error on the refetch gives the original error, no retry', async () => {
    const stub = stubSignedIn({ [ACTION]: () => problem(403, 'csrf_failed') });
    stub.set({
      [ME]: [meAs(), () => Promise.reject(new TypeError('Failed to fetch'))],
    });
    app = await renderApp('/action', pages);

    await runAction();

    expect(stub.calls(ACTION)).toHaveLength(1);
    expect(alerts(app.container)[0]).toContain(
      "We couldn't verify the request.",
    );
    expect(app.location()).toBe('/action');
  });
});
