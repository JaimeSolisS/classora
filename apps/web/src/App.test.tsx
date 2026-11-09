import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { applyLanguage, i18n } from './i18n';
import { LANGUAGE_STORAGE_KEY } from './i18n/language';
import { stubSignedIn } from './test/api-stub';
import { buttonNamed, renderApp, type RenderedApp } from './test/render-app';

// Home page (C5; HF-06 F4): the role home, rendered through the router
// with a fresh query client. Signed in through a stubbed /auth/me (#28 I2):
// the home page is behind the guard. TEST_USER (owner role, user.view and
// audit.view only) has no agenda and no pending source, so the role home
// shows its quick links and makes no request of its own: the session read
// is the request these tests count (same origin, no remount).

let app: RenderedApp | undefined;

/** The shell's own requests, made on every page. */
const SHELL_PATHS = [
  '/api/v1/auth/me',
  '/api/v1/auth/me/preferences',
  '/api/v1/notifications/unread-count',
];

function heading(): string | null {
  return app?.container.querySelector('main h1')?.textContent ?? null;
}

beforeEach(() => {
  // Each test sets its language explicitly; these tests read English text.
  applyLanguage('en');
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
  window.localStorage.clear();
});

describe('Home page requests', () => {
  it('greets the user and reads the session on the same origin; no health check', async () => {
    const stub = stubSignedIn();

    app = await renderApp('/');

    expect(heading()).toBe('Hello, Demo Owner');
    expect(document.title).toBe('Classora');
    expect(stub.calls('GET /api/v1/auth/me')).toHaveLength(1);
    expect(stub.calls('GET /health/live')).toHaveLength(0);
    // The API client passes one Request object to fetch.
    const request = stub.fetch.mock.calls
      .map(([r]) => r)
      .find((r) => r.url.endsWith('/api/v1/auth/me'))!;
    const url = new URL(request.url);
    expect(request.method).toBe('GET');
    expect(url.pathname).toBe('/api/v1/auth/me');
    expect(url.origin).toBe(window.location.origin);
    expect(request.signal).toBeInstanceOf(AbortSignal);
    // The quick links (D3): the page itself asks nothing; only the shell's
    // reads (the session, the bell, the stored preferences) are sent.
    expect(
      app.container.querySelector('[data-home-section="fallback"]'),
    ).not.toBeNull();
    expect(stub.requests.filter((r) => !SHELL_PATHS.includes(r.path))).toEqual(
      [],
    );
  });
});

describe('Home page language', () => {
  it.each([
    ['es', 'Hola, Demo Owner', 'Accesos rápidos', 'Tema', 'Idioma'],
    ['en', 'Hello, Demo Owner', 'Quick links', 'Theme', 'Language'],
  ] as const)(
    'renders the page and both switchers in %s on the first render',
    async (language, greeting, links, themeLabel, languageLabel) => {
      applyLanguage(language);
      stubSignedIn();

      app = await renderApp('/');

      expect(heading()).toBe(greeting);
      expect(app.container.querySelector('main h2')?.textContent).toBe(links);
      const groups = [
        ...app.container.querySelectorAll('header [role="group"]'),
      ].map((g) => g.getAttribute('aria-label'));
      expect(groups).toEqual([themeLabel, languageLabel]);
      expect(document.documentElement.lang).toBe(language);
      // No raw catalog key ever reaches the screen.
      expect(app.container.textContent).not.toMatch(
        /home\.|theme\.|language\.|nav\.|shell\.|common:|errors:/,
      );
    },
  );

  it('switches language in place: no remount, stored, no second session read', async () => {
    applyLanguage('es');
    const stub = stubSignedIn();
    app = await renderApp('/');
    const { container } = app;
    const h1 = container.querySelector('main h1');
    expect(heading()).toBe('Hola, Demo Owner');
    expect(buttonNamed(container, 'Claro')).toBeTruthy();

    act(() => buttonNamed(container, 'English').click());

    expect(i18n.language).toBe('en');
    expect(heading()).toBe('Hello, Demo Owner');
    expect(buttonNamed(container, 'Light')).toBeTruthy();
    expect(document.documentElement.lang).toBe('en');
    expect(window.localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe('en');
    // Same DOM node and no second session read: the app was not remounted.
    expect(container.querySelector('main h1')).toBe(h1);
    expect(stub.calls('GET /api/v1/auth/me')).toHaveLength(1);

    act(() => buttonNamed(container, 'Español').click());
    expect(heading()).toBe('Hola, Demo Owner');
    expect(document.documentElement.lang).toBe('es');
  });
});
