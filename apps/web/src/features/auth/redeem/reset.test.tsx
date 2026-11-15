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

// #30 J1–J2 and L2: the public /reset page and the login notice. The token
// below is a low-entropy placeholder, not a real token.

const LOOKUP = 'POST /api/v1/auth/password-reset/lookup';
const REDEEM = 'POST /api/v1/auth/password-reset/redeem';
const INVITE_LOOKUP = 'POST /api/v1/auth/invitation/lookup';
const TOKEN = 'r'.repeat(43);
const PASSWORD = 'a-long-enough-password';
const FOUND = {
  username: 'ana.tutor',
  display_name: 'Ana Tutor',
  expires_at: '2026-10-20T18:00:00Z',
};

let app: RenderedApp | undefined;

const main = () => app!.container.querySelector('main')!;
const h1 = () => main().querySelector('h1')?.textContent;
const field = (label: string) => fieldByLabel(main(), label);
const form = () => main().querySelector('form');

function stubReset(
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

describe('the fragment (J1, J2)', () => {
  it('is a public page: reads the token, removes the fragment, looks it up', async () => {
    const api = stubReset();

    app = await renderApp(`/reset#token=${TOKEN}`);

    expect(app.location()).toBe('/reset');
    const [call] = api.calls(LOOKUP);
    expect(call!.body).toEqual({ token: TOKEN });
    expect(api.calls(INVITE_LOOKUP)).toHaveLength(0);
    expect(main().querySelectorAll('h1')).toHaveLength(1);
    expect(h1()).toBe('Reset your password');
    expect(document.title).toBe('Reset your password · Classora');
    expect(app.container.querySelector('header nav')).toBeNull();
    expect(app.container.textContent).not.toContain('Sign out');
  });

  it('without a token shows "incomplete" and makes no request', async () => {
    const api = stubReset();

    app = await renderApp('/reset');

    expect(main().textContent).toContain(
      'This link is incomplete. Open the link you were given again.',
    );
    expect(form()).toBeNull();
    expect(api.calls(LOOKUP)).toHaveLength(0);
  });

  it('a query-string token is ignored', async () => {
    const api = stubReset();

    app = await renderApp(`/reset?token=${TOKEN}`);

    expect(main().textContent).toContain('This link is incomplete.');
    expect(api.calls(LOOKUP)).toHaveLength(0);
  });
});

describe('signed in (J2)', () => {
  it('makes no API call and shows the notice with "Sign out"', async () => {
    const api = stubSignedIn({
      [LOOKUP]: () => jsonResponse(200, FOUND),
      'POST /api/v1/auth/logout': noContent,
    });

    app = await renderApp(`/reset#token=${TOKEN}`);

    expect(app.location()).toBe('/reset');
    expect(api.calls(LOOKUP)).toHaveLength(0);
    expect(h1()).toBe('Reset your password');
    expect(main().textContent).toContain(
      "You're signed in as Demo Owner. This link is for resetting another account's password. Sign out, then open the link again.",
    );
    expect(form()).toBeNull();

    await click(buttonNamed(main(), 'Sign out'));

    expect(app.location()).toBe('/login');
    expect(api.calls(LOOKUP)).toHaveLength(0);
  });
});

describe('the lookup (J2)', () => {
  it('shows the username and the password fields', async () => {
    stubReset();

    app = await renderApp(`/reset#token=${TOKEN}`);

    expect(main().textContent).toContain('Your username is:');
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
    stubReset(() => problem(status, code));

    app = await renderApp(`/reset#token=${TOKEN}`);

    expect(main().textContent).toContain(text);
    expect(form()).toBeNull();
  });
});

describe('setting the new password (J2)', () => {
  it('validates length and confirmation without a request', async () => {
    const api = stubReset();
    app = await renderApp(`/reset#token=${TOKEN}`);

    await setPassword('too-short', 'too-short');
    expect(main().textContent).toContain('Use at least 12 characters.');

    await setPassword(PASSWORD, `${PASSWORD}x`);
    expect(main().textContent).toContain("The passwords don't match.");
    expect(api.calls(REDEEM)).toHaveLength(0);
  });

  it('attaches a server password error to the new-password field', async () => {
    stubReset(undefined, () => problem(422, 'password_too_short'));
    app = await renderApp(`/reset#token=${TOKEN}`);

    await setPassword();

    expect(field('New password').getAttribute('aria-invalid')).toBe('true');
    expect(alerts(main())).toEqual([]);
  });

  it('a 410 on redeem replaces the form with the token_invalid message', async () => {
    stubReset(undefined, () => problem(410, 'token_invalid'));
    app = await renderApp(`/reset#token=${TOKEN}`);

    await setPassword();

    expect(form()).toBeNull();
    expect(main().textContent).toContain('This link is no longer valid.');
  });

  it('redeems, then signs in on /login with the reset notice and the username', async () => {
    const api = stubReset();
    app = await renderApp(`/reset#token=${TOKEN}`);

    await setPassword();

    const [call] = api.calls(REDEEM);
    expect(call!.body).toEqual({ token: TOKEN, password: PASSWORD });
    expect(app.location()).toBe('/login');
    expect(main().querySelector('[role="status"]')?.textContent).toBe(
      'Your new password is set. Sign in to continue.',
    );
    expect(fieldByLabel(main(), 'Username').value).toBe('ana.tutor');
    expect(document.activeElement).toBe(fieldByLabel(main(), 'Password'));
    expect(app.router.state.location.state).toBeNull();
    expect(app.router.state.historyAction).toBe('REPLACE');
  });

  it('never retains the token or the password', async () => {
    stubReset();
    app = await renderApp(`/reset#token=${TOKEN}`);
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
  });
});

describe('language switch (L2)', () => {
  it('keeps typed values and the token', async () => {
    const api = stubReset();
    app = await renderApp(`/reset#token=${TOKEN}`);
    typeInto(field('New password'), PASSWORD);

    act(() => applyLanguage('es'));

    expect(h1()).toBe('Restablece tu contraseña');
    expect(main().textContent).toContain('Tu usuario es:');
    expect(fieldByLabel(main(), 'Nueva contraseña').value).toBe(PASSWORD);
    typeInto(fieldByLabel(main(), 'Confirma la contraseña'), PASSWORD);
    await click(buttonNamed(main(), 'Guardar contraseña'));

    expect(api.calls(REDEEM)[0]!.body).toEqual({
      token: TOKEN,
      password: PASSWORD,
    });
  });
});
