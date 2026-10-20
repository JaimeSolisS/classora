import { act, useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SESSION_QUERY_KEY, type SessionData } from '@/features/auth/session';
import { applyLanguage } from '@/i18n';
import {
  meAs,
  OTHER_USER,
  problem,
  stubSignedIn,
  stubSignedOut,
  TEST_USER,
  type ApiStub,
  type Responder,
} from '@/test/api-stub';
import { routesWith } from '@/test/auth-routes';
import { alerts, click, fieldByLabel, typeInto } from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  settle,
  type RenderedApp,
} from '@/test/render-app';
import type { UserPreferences } from './api';

// #36 F8: the account's language and theme in the real App (stubbed fetch).

const PATCH = 'PATCH /api/v1/auth/me/preferences';
const ME = 'GET /api/v1/auth/me';
const LANGUAGE_KEY = 'classora.language';
const THEME_KEY = 'classora.theme';
const PASSWORD = 'correct-horse-battery-staple';

let app: RenderedApp | undefined;

/** What the probe page saw on its first render: `lang|dark`. */
let firstRender: string | null = null;

function ProbePage() {
  useState(() => {
    const root = document.documentElement;
    firstRender ??= `${root.lang}|${root.classList.contains('dark')}`;
    return null;
  });
  return <h1>Probe</h1>;
}

const ROUTES = routesWith([{ path: 'probe', element: <ProbePage /> }]);

function withPreferences(
  preferences: UserPreferences,
  user = TEST_USER,
): typeof TEST_USER {
  return { ...user, preferences };
}

/** The local (M1) values, as a browser that used the switchers has them. */
function setLocal(language: 'es' | 'en', theme: 'light' | 'dark' | 'system') {
  window.localStorage.setItem(LANGUAGE_KEY, language);
  window.localStorage.setItem(THEME_KEY, theme);
  applyLanguage(language);
}

const html = () => document.documentElement;
const isDark = () => html().classList.contains('dark');
const header = () => app!.container.querySelector('header')!;
/** The server's stored row: each PATCH merges into it and returns it. */
let stored: UserPreferences = { language: 'en', theme: 'system' };
const echo: Responder = (_request, body) => {
  stored = { ...stored, ...(body as Partial<UserPreferences>) };
  return jsonResponse(200, stored);
};

/** A response held until the test releases it. */
function held(): { responder: Responder; release: (r: Response) => void } {
  let release: (r: Response) => void = () => {};
  const promise = new Promise<Response>((resolve) => {
    release = resolve;
  });
  return { responder: () => promise, release };
}

function cachedPreferences(): UserPreferences | undefined {
  const data = app!.queryClient.getQueryData<SessionData>(SESSION_QUERY_KEY);
  return data?.user?.preferences;
}

/** The session query refetches (as on window focus once stale). */
async function refetchSession(): Promise<void> {
  await act(async () => {
    await app!.queryClient.refetchQueries({ queryKey: SESSION_QUERY_KEY });
  });
  await settle();
}

beforeEach(() => {
  firstRender = null;
  stored = { language: 'en', theme: 'system' };
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.localStorage.clear();
  html().classList.remove('dark');
  applyLanguage('en');
});

// --- F2: the account wins at sign-in ----------------------------------------------

describe('applying the account values (F2)', () => {
  it('a login response applies them before the page renders, stores them and sends nothing', async () => {
    setLocal('es', 'light');
    const api = stubSignedOut({
      'POST /api/v1/auth/login': () =>
        jsonResponse(200, withPreferences({ language: 'en', theme: 'dark' })),
    });
    app = await renderApp('/login?next=%2Fprobe', ROUTES);
    expect(html().lang).toBe('es');
    expect(isDark()).toBe(false);

    const main = app.container.querySelector('main')!;
    typeInto(fieldByLabel(main, 'Nombre de usuario'), 'demo.owner');
    typeInto(fieldByLabel(main, 'Contraseña'), PASSWORD);
    await click(buttonNamed(main, 'Iniciar sesión'));

    expect(app.location()).toBe('/probe');
    expect(firstRender).toBe('en|true');
    expect(html().lang).toBe('en');
    expect(isDark()).toBe(true);
    expect(window.localStorage.getItem(LANGUAGE_KEY)).toBe('en');
    expect(window.localStorage.getItem(THEME_KEY)).toBe('dark');
    expect(api.calls(PATCH)).toHaveLength(0);
  });

  it('a bootstrap /auth/me does the same', async () => {
    setLocal('es', 'light');
    const api = stubSignedIn({
      [ME]: meAs(withPreferences({ language: 'en', theme: 'dark' })),
    });

    app = await renderApp('/probe', ROUTES);

    expect(firstRender).toBe('en|true');
    expect(html().lang).toBe('en');
    expect(isDark()).toBe(true);
    expect(window.localStorage.getItem(LANGUAGE_KEY)).toBe('en');
    expect(window.localStorage.getItem(THEME_KEY)).toBe('dark');
    expect(api.calls(PATCH)).toHaveLength(0);
    expect(header().textContent).toContain('Signed in as Demo Owner');
  });
});

// --- F3: migrating the local values -------------------------------------------------

describe('filling values the account never stored (F3)', () => {
  it('sends both local values in one PATCH and caches the answer without a refetch', async () => {
    setLocal('en', 'dark');
    const api = stubSignedIn({ [PATCH]: echo });

    app = await renderApp('/probe', ROUTES);

    const [call] = api.calls(PATCH);
    expect(api.calls(PATCH)).toHaveLength(1);
    expect(call!.body).toEqual({ language: 'en', theme: 'dark' });
    expect(call!.headers.get('X-CSRF-Token')).toBe(TEST_USER.csrf_token);
    expect(cachedPreferences()).toEqual({ language: 'en', theme: 'dark' });
    expect(api.calls(ME)).toHaveLength(1);
    expect(html().lang).toBe('en');
    expect(isDark()).toBe(true);
  });

  it('applies the stored language and sends only the missing theme', async () => {
    setLocal('en', 'dark');
    const api = stubSignedIn({
      [ME]: meAs(withPreferences({ language: 'es', theme: null })),
      [PATCH]: echo,
    });

    app = await renderApp('/probe', ROUTES);

    expect(api.calls(PATCH).map((c) => c.body)).toEqual([{ theme: 'dark' }]);
    expect(html().lang).toBe('es');
    expect(isDark()).toBe(true);
  });

  it('uses the defaults when nothing is stored locally', async () => {
    applyLanguage('es');
    const api = stubSignedIn({ [PATCH]: echo });

    app = await renderApp('/probe', ROUTES);

    expect(api.calls(PATCH).map((c) => c.body)).toEqual([
      { language: 'es', theme: 'system' },
    ]);
  });

  it('a failing migration shows nothing and is not retried in this page load', async () => {
    setLocal('en', 'dark');
    const api = stubSignedIn({ [PATCH]: () => problem(500, 'internal_error') });

    app = await renderApp('/probe', ROUTES);
    await refetchSession();

    expect(api.calls(PATCH)).toHaveLength(1);
    expect(alerts(header())).toEqual([]);
    expect(cachedPreferences()).toEqual({ language: null, theme: null });
  });

  it('signed out, nothing is sent', async () => {
    setLocal('en', 'dark');
    const api = stubSignedOut({ [PATCH]: echo });

    app = await renderApp('/login');
    await click(buttonNamed(header(), 'Light'));
    await click(buttonNamed(header(), 'Español'));

    expect(api.calls(PATCH)).toHaveLength(0);
    expect(isDark()).toBe(false);
    expect(html().lang).toBe('es');
    expect(window.localStorage.getItem(THEME_KEY)).toBe('light');
    expect(window.localStorage.getItem(LANGUAGE_KEY)).toBe('es');
  });
});

// --- F4: switching while signed in --------------------------------------------------

const STORED: UserPreferences = { language: 'en', theme: 'system' };

describe('switching while signed in (F4)', () => {
  it('a theme click sends only the theme; a language click only the language', async () => {
    const api = stubSignedIn({
      [ME]: meAs(withPreferences(STORED)),
      [PATCH]: echo,
    });
    app = await renderApp('/probe', ROUTES);

    await click(buttonNamed(header(), 'Dark'));

    expect(isDark()).toBe(true);
    expect(window.localStorage.getItem(THEME_KEY)).toBe('dark');
    expect(api.calls(PATCH).map((c) => c.body)).toEqual([{ theme: 'dark' }]);

    await click(buttonNamed(header(), 'Español'));

    expect(html().lang).toBe('es');
    expect(window.localStorage.getItem(LANGUAGE_KEY)).toBe('es');
    expect(api.calls(PATCH).map((c) => c.body)).toEqual([
      { theme: 'dark' },
      { language: 'es' },
    ]);
    expect(cachedPreferences()).toEqual({ language: 'es', theme: 'dark' });
  });

  it('serializes rapid saves and never applies an older response', async () => {
    const first = held();
    const api = stubSignedIn({
      [ME]: meAs(withPreferences(STORED)),
      [PATCH]: [first.responder, echo],
    });
    app = await renderApp('/probe', ROUTES);

    await click(buttonNamed(header(), 'Dark'));
    await click(buttonNamed(header(), 'Light'));

    // The second request waits for the first.
    expect(api.calls(PATCH).map((c) => c.body)).toEqual([{ theme: 'dark' }]);
    expect(isDark()).toBe(false);

    first.release(jsonResponse(200, { language: 'en', theme: 'dark' }));
    await settle();

    expect(api.calls(PATCH).map((c) => c.body)).toEqual([
      { theme: 'dark' },
      { theme: 'light' },
    ]);
    expect(isDark()).toBe(false);
    expect(window.localStorage.getItem(THEME_KEY)).toBe('light');
    expect(cachedPreferences()).toEqual({ language: 'en', theme: 'light' });
  });

  it('a refetch with a value changed elsewhere applies it', async () => {
    const api = stubSignedIn({ [ME]: meAs(withPreferences(STORED)) });
    app = await renderApp('/probe', ROUTES);
    expect(isDark()).toBe(false);

    // Another device chose Dark.
    api.set({ [ME]: meAs(withPreferences({ language: 'en', theme: 'dark' })) });
    await refetchSession();

    expect(isDark()).toBe(true);
    expect(window.localStorage.getItem(THEME_KEY)).toBe('dark');
    expect(api.calls(PATCH)).toHaveLength(0);
  });

  it('a refetch with the old value while a save is pending changes nothing', async () => {
    const save = held();
    stubSignedIn({
      [ME]: meAs(withPreferences(STORED)),
      [PATCH]: save.responder,
    });
    app = await renderApp('/probe', ROUTES);

    await click(buttonNamed(header(), 'Dark'));
    await refetchSession();

    expect(isDark()).toBe(true);
    expect(window.localStorage.getItem(THEME_KEY)).toBe('dark');

    save.release(jsonResponse(200, { language: 'en', theme: 'dark' }));
    await settle();
    expect(isDark()).toBe(true);
    expect(cachedPreferences()).toEqual({ language: 'en', theme: 'dark' });
  });
});

// --- F5: a failed save --------------------------------------------------------------

describe('a failed save (F5)', () => {
  it('keeps the choice, shows the alert with "Try again", and a retry clears it', async () => {
    const api = stubSignedIn({
      [ME]: meAs(withPreferences(STORED)),
      [PATCH]: [() => problem(500, 'internal_error', 'req-prefs-1'), echo],
    });
    app = await renderApp('/probe', ROUTES);

    await click(buttonNamed(header(), 'Dark'));

    expect(isDark()).toBe(true);
    expect(window.localStorage.getItem(THEME_KEY)).toBe('dark');
    const [alert] = alerts(header());
    expect(alert).toContain(
      "Your preference couldn't be saved to your account. For now it applies only on this device.",
    );
    expect(alert).toContain('req-prefs-1');
    const retry = buttonNamed(header(), 'Try again');

    await click(retry);

    expect(api.calls(PATCH).map((c) => c.body)).toEqual([
      { theme: 'dark' },
      { theme: 'dark' },
    ]);
    expect(alerts(header())).toEqual([]);
    expect(cachedPreferences()).toEqual({ language: 'en', theme: 'dark' });
  });

  it('a network error shows the alert too; "Try again" sends the latest value', async () => {
    const api = stubSignedIn({
      [ME]: meAs(withPreferences(STORED)),
      [PATCH]: [
        () => Promise.reject(new TypeError('Failed to fetch')),
        () => Promise.reject(new TypeError('Failed to fetch')),
        echo,
      ],
    });
    app = await renderApp('/probe', ROUTES);

    await click(buttonNamed(header(), 'Dark'));
    await click(buttonNamed(header(), 'Light'));
    expect(alerts(header())).toHaveLength(1);

    await click(buttonNamed(header(), 'Try again'));

    expect(api.calls(PATCH).at(-1)!.body).toEqual({ theme: 'light' });
    expect(alerts(header())).toEqual([]);
  });
});

// --- Session changes -----------------------------------------------------------------

describe('session changes', () => {
  it('another user in a refetch (A5): their preferences are applied', async () => {
    const api = stubSignedIn({ [ME]: meAs(withPreferences(STORED)) });
    app = await renderApp('/probe', ROUTES);
    expect(html().lang).toBe('en');

    api.set({
      [ME]: meAs(
        withPreferences({ language: 'es', theme: 'dark' }, OTHER_USER),
      ),
    });
    await refetchSession();

    expect(header().textContent).toContain('Sesión iniciada como Other User');
    expect(html().lang).toBe('es');
    expect(isDark()).toBe(true);
    expect(window.localStorage.getItem(LANGUAGE_KEY)).toBe('es');
    expect(api.calls(PATCH)).toHaveLength(0);
  });

  it('a password change response with the same preferences applies nothing and sends nothing', async () => {
    const api: ApiStub = stubSignedIn({
      [ME]: meAs(withPreferences(STORED)),
      'POST /api/v1/auth/password': () =>
        jsonResponse(
          200,
          withPreferences(STORED, {
            ...TEST_USER,
            csrf_token: 'csrf-token-ccccccccccccccccccccccccccccccccccc',
          }),
        ),
    });
    app = await renderApp('/account/password', ROUTES);
    const setItem = vi.spyOn(Storage.prototype, 'setItem');
    const main = app.container.querySelector('main')!;

    typeInto(fieldByLabel(main, 'Current password'), 'my-current-password');
    typeInto(fieldByLabel(main, 'New password'), 'my-brand-new-password');
    typeInto(
      fieldByLabel(main, 'Confirm new password'),
      'my-brand-new-password',
    );
    await click(buttonNamed(main, 'Change password'));

    expect(api.calls('POST /api/v1/auth/password')).toHaveLength(1);
    expect(
      setItem.mock.calls.filter(([key]) =>
        [LANGUAGE_KEY, THEME_KEY].includes(key),
      ),
    ).toEqual([]);
    expect(api.calls(PATCH)).toHaveLength(0);
  });

  it('after signing out, the switchers are local only and the values stay', async () => {
    const api = stubSignedIn({
      [ME]: meAs(withPreferences({ language: 'en', theme: 'dark' })),
      'POST /api/v1/auth/logout': () => new Response(null, { status: 204 }),
    });
    app = await renderApp('/probe', ROUTES);

    await click(buttonNamed(header(), 'Sign out'));

    expect(app.location()).toBe('/login');
    expect(html().lang).toBe('en');
    expect(isDark()).toBe(true);
    expect(window.localStorage.getItem(LANGUAGE_KEY)).toBe('en');
    expect(window.localStorage.getItem(THEME_KEY)).toBe('dark');

    await click(buttonNamed(header(), 'Light'));

    expect(api.calls(PATCH)).toHaveLength(0);
    expect(isDark()).toBe(false);
  });
});
