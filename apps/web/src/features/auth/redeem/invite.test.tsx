import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { applyLanguage } from '@/i18n';
import {
  noContent,
  problem,
  stubSignedIn,
  stubSignedOut,
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
import { tokenFromFragment } from '../useFragmentToken';

// #29 J1–J6 and K2: the public /invite page and the login notice.

const LOOKUP = 'POST /api/v1/auth/invitation/lookup';
const REDEEM = 'POST /api/v1/auth/invitation/redeem';
const TOKEN = 'AbCdEfGhIjKlMnOpXyStUvWxYz0123456789-_AbCdE';
const PASSWORD = 'a-long-enough-password';
const FOUND = {
  username: 'ana.tutor',
  display_name: 'Ana Tutor',
  roles: ['tutor'],
  expires_at: '2026-10-20T18:00:00Z',
};

let app: RenderedApp | undefined;

const main = () => app!.container.querySelector('main')!;
const h1 = () => main().querySelector('h1')?.textContent;
const field = (label: string) => fieldByLabel(main(), label);
const form = () => main().querySelector('form');

function stubInvite(
  lookup: Responder = () => jsonResponse(200, FOUND),
  redeem: Responder | Responder[] = noContent,
): ApiStub {
  return stubSignedOut({ [LOOKUP]: lookup, [REDEEM]: redeem });
}

async function setPassword(password = PASSWORD, confirm = password) {
  typeInto(field('New password'), password);
  typeInto(field('Confirm password'), confirm);
  await click(buttonNamed(main(), 'Set password'));
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

describe('tokenFromFragment', () => {
  it.each([
    ['#token=abc', 'abc'],
    ['token=abc', 'abc'],
    ['#token=abc&x=1', 'abc'],
    ['', null],
    ['#', null],
    ['#token=', null],
    ['#other=abc', null],
  ])('%s → %s', (hash, expected) => {
    expect(tokenFromFragment(hash)).toBe(expected);
  });
});

describe('the fragment (J1, J2)', () => {
  it('is a public page: reads the token, removes the fragment, looks it up', async () => {
    const api = stubInvite();

    app = await renderApp(`/invite?x=1#token=${TOKEN}`);

    expect(app.location()).toBe('/invite?x=1');
    const [call] = api.calls(LOOKUP);
    expect(call!.body).toEqual({ token: TOKEN });
    expect(app.location()).not.toContain(TOKEN);
    expect(main().querySelectorAll('h1')).toHaveLength(1);
    expect(h1()).toBe('Set your password');
    expect(document.title).toBe('Set your password · Classora');
    // Brand and switchers only: no navigation, no user menu.
    expect(app.container.querySelector('header nav')).toBeNull();
    expect(app.container.textContent).not.toContain('Sign out');
  });

  it('without a token shows "incomplete" and makes no request', async () => {
    const api = stubInvite();

    app = await renderApp('/invite');

    expect(main().textContent).toContain(
      'This link is incomplete. Open the link you were given again.',
    );
    expect(form()).toBeNull();
    expect(api.calls(LOOKUP)).toHaveLength(0);
  });

  it('a query-string token is ignored (the token belongs in the fragment)', async () => {
    const api = stubInvite();

    app = await renderApp(`/invite?token=${TOKEN}`);

    expect(main().textContent).toContain('This link is incomplete.');
    expect(api.calls(LOOKUP)).toHaveLength(0);
  });
});

describe('signed in (J3)', () => {
  it('makes no API call and shows the notice with "Sign out"', async () => {
    const api = stubSignedIn({
      [LOOKUP]: () => jsonResponse(200, FOUND),
      'POST /api/v1/auth/logout': noContent,
    });

    app = await renderApp(`/invite#token=${TOKEN}`);

    expect(app.location()).toBe('/invite');
    expect(api.calls(LOOKUP)).toHaveLength(0);
    expect(h1()).toBe('Set your password');
    expect(main().textContent).toContain(
      "You're signed in as Demo Owner. This link is for the person who was invited. Sign out, then open the link again.",
    );
    expect(form()).toBeNull();

    await click(buttonNamed(main(), 'Sign out'));

    expect(api.calls('POST /api/v1/auth/logout')).toHaveLength(1);
    expect(app.location()).toBe('/login');
    expect(api.calls(LOOKUP)).toHaveLength(0);
  });
});

describe('the lookup (J4)', () => {
  it('shows the welcome, the username and the password fields', async () => {
    stubInvite();

    app = await renderApp(`/invite#token=${TOKEN}`);

    expect(main().textContent).toContain('Hi Ana Tutor. Your username is:');
    const shown = [...main().querySelectorAll('p')].find(
      (p) => p.textContent === 'ana.tutor',
    );
    expect(shown?.className).toContain('select-all');
    const username = field('Username');
    expect(username.readOnly).toBe(true);
    expect(username.value).toBe('ana.tutor');
    expect(username.getAttribute('autocomplete')).toBe('username');
    expect(field('New password').getAttribute('autocomplete')).toBe(
      'new-password',
    );
    expect(field('Confirm password').getAttribute('autocomplete')).toBe(
      'new-password',
    );
    expect(field('New password').type).toBe('password');
  });

  it.each([
    [
      'token_invalid',
      410,
      'This link is no longer valid. Ask the center for a new one.',
    ],
    [
      'rate_limited',
      429,
      'Too many attempts. Wait a few minutes and try again.',
    ],
  ])('%s shows its message and no form', async (code, status, text) => {
    stubInvite(() => problem(status, code));

    app = await renderApp(`/invite#token=${TOKEN}`);

    expect(main().textContent).toContain(text);
    expect(form()).toBeNull();
  });

  it('a server error offers a retry that looks the link up again', async () => {
    const api = stubSignedOut({
      [LOOKUP]: [
        () => problem(400, 'bad_request'),
        () => jsonResponse(200, FOUND),
      ],
    });
    app = await renderApp(`/invite#token=${TOKEN}`);
    expect(alerts(main())).toHaveLength(1);

    await click(buttonNamed(main(), 'Try again'));

    expect(api.calls(LOOKUP)).toHaveLength(2);
    expect(api.calls(LOOKUP)[1]!.body).toEqual({ token: TOKEN });
    expect(form()).not.toBeNull();
  });
});

describe('setting the password (J4, J5)', () => {
  it('validates length and confirmation without a request', async () => {
    const api = stubInvite();
    app = await renderApp(`/invite#token=${TOKEN}`);

    await setPassword('too-short', 'too-short');
    expect(field('New password').getAttribute('aria-invalid')).toBe('true');
    expect(main().textContent).toContain('Use at least 12 characters.');
    expect(document.activeElement).toBe(field('New password'));

    await setPassword(PASSWORD, `${PASSWORD}x`);
    expect(field('Confirm password').getAttribute('aria-invalid')).toBe('true');
    expect(main().textContent).toContain("The passwords don't match.");

    await setPassword('x'.repeat(129));
    expect(main().textContent).toContain('Use at most 128 characters.');
    expect(api.calls(REDEEM)).toHaveLength(0);
  });

  it('attaches a server password error to the new-password field', async () => {
    stubInvite(undefined, () => problem(422, 'password_equals_username'));
    app = await renderApp(`/invite#token=${TOKEN}`);

    await setPassword();

    const input = field('New password');
    expect(input.getAttribute('aria-invalid')).toBe('true');
    const described = input
      .getAttribute('aria-describedby')!
      .split(' ')
      .map((id) => document.getElementById(id)?.textContent);
    expect(described).toContain(
      "The password can't be the same as the username.",
    );
    expect(alerts(main())).toEqual([]);
  });

  it('a 410 on redeem replaces the form with the token_invalid message', async () => {
    stubInvite(undefined, () => problem(410, 'token_invalid'));
    app = await renderApp(`/invite#token=${TOKEN}`);

    await setPassword();

    expect(form()).toBeNull();
    expect(main().textContent).toContain('This link is no longer valid.');
  });

  it('a 429 on redeem shows rate_limited in the alert and keeps the form', async () => {
    stubInvite(undefined, () => problem(429, 'rate_limited'));
    app = await renderApp(`/invite#token=${TOKEN}`);

    await setPassword();

    expect(alerts(main()).join(' ')).toContain('Too many attempts.');
    expect(form()).not.toBeNull();
  });

  it('redeems, then signs in on /login with the notice and the username', async () => {
    const api = stubInvite();
    app = await renderApp(`/invite#token=${TOKEN}`);

    await setPassword();

    const [call] = api.calls(REDEEM);
    expect(call!.body).toEqual({ token: TOKEN, password: PASSWORD });
    // Router state, never the URL.
    expect(app.location()).toBe('/login');
    expect(main().querySelector('[role="status"]')?.textContent).toBe(
      'Your password is set. Sign in to continue.',
    );
    expect(fieldByLabel(main(), 'Username').value).toBe('ana.tutor');
    expect(document.activeElement).toBe(fieldByLabel(main(), 'Password'));
    // The state is dropped from the history entry: a reload shows no notice.
    expect(app.router.state.location.state).toBeNull();
    // Replace: Back does not return to /invite.
    expect(app.router.state.historyAction).toBe('REPLACE');
  });

  it('never retains the token or the password (J6)', async () => {
    stubInvite();
    app = await renderApp(`/invite#token=${TOKEN}`);
    expect(cacheText()).not.toContain(TOKEN);

    await setPassword();

    const text = cacheText();
    expect(text).not.toContain(TOKEN);
    expect(text).not.toContain(PASSWORD);
    const storage = JSON.stringify({
      local: { ...window.localStorage },
      session: { ...window.sessionStorage },
    });
    expect(storage).not.toContain(TOKEN);
    expect(storage).not.toContain(PASSWORD);
    expect(app.location()).not.toContain('ana.tutor');
  });
});

describe('language switch (K2)', () => {
  it('re-renders in place without losing typed values or the token', async () => {
    const api = stubInvite();
    app = await renderApp(`/invite#token=${TOKEN}`);
    typeInto(field('New password'), PASSWORD);

    act(() => applyLanguage('es'));

    expect(h1()).toBe('Crea tu contraseña');
    expect(fieldByLabel(main(), 'Nueva contraseña').value).toBe(PASSWORD);
    typeInto(fieldByLabel(main(), 'Confirma la contraseña'), PASSWORD);
    await click(buttonNamed(main(), 'Guardar contraseña'));
    expect(api.calls(LOOKUP)).toHaveLength(1);
    expect(api.calls(REDEEM)[0]!.body).toEqual({
      token: TOKEN,
      password: PASSWORD,
    });
  });
});

describe('the login page notice', () => {
  it('ignores router state that is not a known notice', async () => {
    stubSignedOut();
    app = await renderApp('/login');

    await act(() =>
      app!.router.navigate('/login', {
        state: { notice: 'something_else', username: 'x' },
      }),
    );

    expect(main().querySelector('[role="status"]')?.textContent).toBe('');
    expect(fieldByLabel(main(), 'Username').value).toBe('');
  });

  it('is not shown to a signed-in user (they go to the app)', async () => {
    stubSignedIn();
    app = await renderApp('/login');

    expect(app.location()).toBe('/');
  });
});
