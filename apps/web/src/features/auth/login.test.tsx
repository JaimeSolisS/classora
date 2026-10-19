import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { applyLanguage } from '@/i18n';
import {
  problem,
  stubApi,
  stubSignedIn,
  stubSignedOut,
  TEST_USER,
  type Responder,
} from '@/test/api-stub';
import { alerts, click, fieldByLabel, typeInto } from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  settle,
  type RenderedApp,
} from '@/test/render-app';
import { CSRF_HEADER, getCsrfToken } from './csrf';
import { SESSION_QUERY_KEY } from './session';

// B1–B8 and G2: the login page.

const PASSWORD = 'correct-horse-battery-staple';

let app: RenderedApp | undefined;

const main = () => app!.container.querySelector('main')!;
const username = () => fieldByLabel(main(), 'Username');
const password = () => fieldByLabel(main(), 'Password');
const submitButton = () =>
  main().querySelector<HTMLButtonElement>('button[type="submit"]')!;
const statusRegion = () => main().querySelector('[role="status"]')?.textContent;

async function fillAndSubmit(user = 'demo.owner', pass = PASSWORD) {
  typeInto(username(), user);
  typeInto(password(), pass);
  await click(submitButton());
}

/** Signed out; POST /auth/login answers with `login`. */
function stubLogin(login: Responder) {
  return stubSignedOut({ 'POST /api/v1/auth/login': login });
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

describe('the page (B1, B2)', () => {
  it('is outside the guard: one h1, the title, switchers and no navigation or user menu', async () => {
    stubSignedOut();

    app = await renderApp('/login');

    expect(app.location()).toBe('/login');
    expect(app.container.querySelectorAll('h1')).toHaveLength(1);
    expect(main().querySelector('h1')?.textContent).toBe('Sign in');
    expect(document.title).toBe('Sign in · Classora');
    const groups = [
      ...app.container.querySelectorAll('header [role="group"]'),
    ].map((g) => g.getAttribute('aria-label'));
    expect(groups).toEqual(['Theme', 'Language']);
    expect(app.container.querySelector('header nav')).toBeNull();
    expect(app.container.textContent).not.toContain('Sign out');
  });

  it('has labelled fields with the right autocomplete and a post form without native validation', async () => {
    stubSignedOut();
    app = await renderApp('/login');

    const form = main().querySelector('form')!;
    expect(form.getAttribute('method')).toBe('post');
    expect(form.noValidate).toBe(true);
    expect(username().getAttribute('autocomplete')).toBe('username');
    expect(username().getAttribute('autocapitalize')).toBe('none');
    expect(username().getAttribute('spellcheck')).toBe('false');
    expect(password().type).toBe('password');
    expect(password().getAttribute('autocomplete')).toBe('current-password');
    expect(username().placeholder).toBe('');
  });

  it('blocks an empty submit without a request, links the errors and focuses the first field', async () => {
    const stub = stubLogin(() => jsonResponse(200, TEST_USER));
    app = await renderApp('/login');

    await click(submitButton());

    expect(stub.calls('POST /api/v1/auth/login')).toHaveLength(0);
    for (const [field, message] of [
      [username(), 'Enter your username.'],
      [password(), 'Enter your password.'],
    ] as const) {
      expect(field.getAttribute('aria-invalid')).toBe('true');
      const describedBy = field.getAttribute('aria-describedby');
      expect(describedBy).toBeTruthy();
      expect(document.getElementById(describedBy!)?.textContent).toBe(message);
    }
    expect(document.activeElement).toBe(username());
  });

  it('mirrors the server bounds (username 256, password 1024)', async () => {
    const stub = stubLogin(() => jsonResponse(200, TEST_USER));
    app = await renderApp('/login');

    await fillAndSubmit('u'.repeat(257), 'p'.repeat(1025));

    expect(stub.calls('POST /api/v1/auth/login')).toHaveLength(0);
    expect(main().textContent).toContain('This is too long.');
    expect(username().getAttribute('aria-invalid')).toBe('true');
    expect(password().getAttribute('aria-invalid')).toBe('true');
  });
});

describe('submit (B3, B5, B6)', () => {
  it('signs in, clears the old cache, stores the token, sets the session without /auth/me and goes to next', async () => {
    const stub = stubLogin(() => jsonResponse(200, TEST_USER));
    app = await renderApp('/login?next=%2Fstudents%3Fx%3D1%23a');
    app.queryClient.setQueryData(['stale', 'from', 'before'], { secret: 1 });

    await fillAndSubmit('Demo.Owner ', PASSWORD);

    const [login] = stub.calls('POST /api/v1/auth/login');
    // As typed: the server normalizes the username.
    expect(login!.body).toEqual({
      username: 'Demo.Owner ',
      password: PASSWORD,
    });
    expect(app.location()).toBe('/students?x=1#a');
    expect(stub.calls('GET /api/v1/auth/me')).toHaveLength(1);
    expect(getCsrfToken()).toBe(TEST_USER.csrf_token);
    expect(
      app.queryClient.getQueryData(['stale', 'from', 'before']),
    ).toBeUndefined();
    expect(app.queryClient.getQueryData(SESSION_QUERY_KEY)).toMatchObject({
      user: { id: TEST_USER.id },
    });
    expect(app.container.textContent).toContain('Signed in as Demo Owner');
  });

  it('goes to / without next, and to / for an unsafe next', async () => {
    stubLogin(() => jsonResponse(200, TEST_USER));
    app = await renderApp('/login?next=%2F%2Fevil.example');

    await fillAndSubmit();

    expect(app.location()).toBe('/');
  });

  it('sends one request on a double click and shows the spinner while pending', async () => {
    let answer: (response: Response) => void = () => {};
    const stub = stubLogin(
      () =>
        new Promise<Response>((resolve) => {
          answer = resolve;
        }),
    );
    app = await renderApp('/login');
    typeInto(username(), 'demo.owner');
    typeInto(password(), PASSWORD);

    act(() => {
      submitButton().click();
      submitButton().click();
    });
    await settle();
    await click(submitButton());

    expect(stub.calls('POST /api/v1/auth/login')).toHaveLength(1);
    expect(submitButton().disabled).toBe(true);
    expect(submitButton().textContent).toBe('Signing in…');

    answer(jsonResponse(200, TEST_USER));
    await settle();
    expect(app.location()).toBe('/');
  });

  it('a signed-in visitor of /login goes to the validated next', async () => {
    stubSignedIn();
    app = await renderApp('/login?next=%2Fx%3Fy%3D1');
    expect(app.location()).toBe('/x?y=1');
  });

  it('a signed-in visitor of /login with an unsafe next goes home', async () => {
    stubSignedIn();
    app = await renderApp('/login?next=%2F%2Fevil.example');
    expect(app.location()).toBe('/');
  });
});

describe('errors (B4)', () => {
  it('invalid_credentials: one alert, password cleared and focused, username kept', async () => {
    stubLogin(() => problem(401, 'invalid_credentials'));
    app = await renderApp('/login');

    await fillAndSubmit('demo.owner', 'wrong-password');

    expect(app.location()).toBe('/login');
    expect(alerts(main())).toHaveLength(1);
    expect(alerts(main())[0]).toContain(
      'The username or password is incorrect.',
    );
    expect(alerts(main())[0]).not.toContain('Server detail');
    expect(password().value).toBe('');
    expect(document.activeElement).toBe(password());
    expect(username().value).toBe('demo.owner');
  });

  it('rate_limited: the message, no automatic retry', async () => {
    const stub = stubLogin(() => problem(429, 'rate_limited'));
    app = await renderApp('/login');

    await fillAndSubmit();
    await settle();

    expect(alerts(main())[0]).toContain(
      'Too many attempts. Wait a few minutes and try again.',
    );
    expect(stub.calls('POST /api/v1/auth/login')).toHaveLength(1);
  });

  it('a network error shows its message', async () => {
    stubLogin(() => Promise.reject(new TypeError('Failed to fetch')));
    app = await renderApp('/login');

    await fillAndSubmit();

    expect(alerts(main())[0]).toContain("Can't reach the server.");
    expect(app.location()).toBe('/login');
  });

  it('a 5xx shows its message with the request reference', async () => {
    stubLogin(() => problem(500, 'internal_error', 'req-login-500'));
    app = await renderApp('/login');

    await fillAndSubmit();

    expect(alerts(main())[0]).toContain('Something went wrong on the server.');
    expect(alerts(main())[0]).toContain('Reference: req-login-500');
  });

  it('csrf_failed on login is not retried and shows its message', async () => {
    const stub = stubLogin(() => problem(403, 'csrf_failed'));
    app = await renderApp('/login');

    await fillAndSubmit();

    expect(alerts(main())[0]).toContain("We couldn't verify the request.");
    expect(stub.calls('POST /api/v1/auth/login')).toHaveLength(1);
    expect(stub.calls('GET /api/v1/auth/me')).toHaveLength(1);
  });
});

describe('session-expired notice (B7)', () => {
  it('shows for session_expired and disappears after the next submit', async () => {
    stubApi({
      'GET /api/v1/auth/me': () => problem(401, 'session_expired'),
      'POST /api/v1/auth/login': () => problem(401, 'invalid_credentials'),
    });
    app = await renderApp('/');
    expect(statusRegion()).toBe('Your session has expired. Sign in again.');

    await fillAndSubmit();

    expect(statusRegion()).toBe('');
  });

  it('does not show for authentication_required or a first visit', async () => {
    stubSignedOut();
    app = await renderApp('/');
    expect(app.location()).toBe('/login?next=%2F');
    expect(statusRegion()).toBe('');
  });
});

describe('the password is not retained (B8)', () => {
  it('is in no query or mutation cache entry and no storage after logins', async () => {
    stubApi({
      'GET /api/v1/auth/me': () => problem(401, 'authentication_required'),
      'POST /api/v1/auth/login': [
        () => problem(401, 'invalid_credentials'),
        () => jsonResponse(200, TEST_USER),
      ],
    });
    app = await renderApp('/login');

    await fillAndSubmit('demo.owner', PASSWORD);
    await fillAndSubmit('demo.owner', PASSWORD);
    expect(app.location()).toBe('/');

    const queries = app.queryClient
      .getQueryCache()
      .getAll()
      .map((query) => ({ key: query.queryKey, state: query.state }));
    const mutations = app.queryClient
      .getMutationCache()
      .getAll()
      .map((mutation) => mutation.state);
    expect(JSON.stringify(queries)).not.toContain(PASSWORD);
    expect(JSON.stringify(mutations)).not.toContain(PASSWORD);
    expect(JSON.stringify({ ...window.localStorage })).not.toContain(PASSWORD);
    expect(JSON.stringify({ ...window.sessionStorage })).not.toContain(
      PASSWORD,
    );
    const logged = vi.mocked(console.error).mock.calls.flat().map(String);
    expect(logged.join('\n')).not.toContain(PASSWORD);
  });
});

describe('language (G2)', () => {
  it('switches labels, errors and title in place and keeps the typed values', async () => {
    stubSignedOut();
    applyLanguage('es');
    app = await renderApp('/login');
    const user = fieldByLabel(main(), 'Nombre de usuario');
    typeInto(user, 'demo.owner');
    typeInto(fieldByLabel(main(), 'Contraseña'), '');
    await click(submitButton());
    expect(main().textContent).toContain('Escribe tu contraseña.');
    expect(document.title).toBe('Iniciar sesión · Classora');

    act(() => buttonNamed(app!.container, 'English').click());

    expect(username()).toBe(user);
    expect(username().value).toBe('demo.owner');
    expect(main().textContent).toContain('Enter your password.');
    expect(main().querySelector('h1')?.textContent).toBe('Sign in');
    expect(document.title).toBe('Sign in · Classora');
  });
});

describe('the token header on login', () => {
  it('a login after a sign-out carries no X-CSRF-Token', async () => {
    const stub = stubLogin(() => jsonResponse(200, TEST_USER));
    app = await renderApp('/login');

    await fillAndSubmit();

    const [login] = stub.calls('POST /api/v1/auth/login');
    expect(login!.headers.get(CSRF_HEADER)).toBeNull();
    expect(stub.calls('GET /api/v1/auth/me')).toHaveLength(1);
  });
});
