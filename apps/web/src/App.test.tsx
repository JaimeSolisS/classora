import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { applyLanguage, i18n } from './i18n';
import { LANGUAGE_STORAGE_KEY } from './i18n/language';
import { stubSignedIn, type Responder } from './test/api-stub';
import {
  buttonNamed,
  jsonResponse,
  mountApp,
  renderApp,
  type RenderedApp,
} from './test/render-app';

// Home page (C5): the health check runs through TanStack Query and the
// central API helper, rendered through the router with a fresh query client.
// Signed in through a stubbed /auth/me (#28 I2): the home page is behind the
// guard.

/** Signed in; GET /health/live answers with `health`. */
function stubHealth(health: Responder) {
  return stubSignedIn({ 'GET /health/live': health });
}

let app: RenderedApp | undefined;

function statusText(): string | null {
  return app?.container.querySelector('[role="status"]')?.textContent ?? null;
}

beforeEach(() => {
  // Each test sets its language explicitly; these tests read English text.
  applyLanguage('en');
  // Failed health checks are reported to the console; keep the output clean.
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
  window.localStorage.clear();
});

describe('Home page API status', () => {
  it('shows the product name and checks /health/live on the same origin', async () => {
    const stub = stubHealth(() => jsonResponse(200, { status: 'ok' }));

    app = await renderApp('/');

    expect(app.container.querySelector('h1')?.textContent).toBe('Classora');
    expect(document.title).toBe('Classora');
    expect(stub.calls('GET /health/live')).toHaveLength(1);
    // The API client passes one Request object to fetch.
    const request = stub.fetch.mock.calls
      .map(([r]) => r)
      .find((r) => r.url.endsWith('/health/live'))!;
    const url = new URL(request.url);
    expect(request.method).toBe('GET');
    expect(url.pathname).toBe('/health/live');
    expect(url.origin).toBe(window.location.origin);
    expect(request.headers.get('Accept')).toBe('application/json');
    expect(request.signal).toBeInstanceOf(AbortSignal);
  });

  it('shows "API: checking…" while the request is pending', async () => {
    stubHealth(() => new Promise<Response>(() => {}));

    app = await renderApp('/');

    expect(statusText()).toBe('API: checking…');
  });

  it('shows "API: live" for 200 with {"status":"ok"}', async () => {
    stubHealth(() => jsonResponse(200, { status: 'ok' }));

    app = await renderApp('/');

    expect(statusText()).toBe('API: live');
  });

  it('shows "API: unreachable" for a 503 response, without retrying', async () => {
    const stub = stubHealth(() => jsonResponse(503, { status: 'ok' }));

    app = await renderApp('/');

    expect(statusText()).toBe('API: unreachable');
    expect(stub.calls('GET /health/live')).toHaveLength(1);
  });

  it('shows "API: unreachable" for a 200 with an unexpected body', async () => {
    stubHealth(() => jsonResponse(200, { status: 'down' }));

    app = await renderApp('/');

    expect(statusText()).toBe('API: unreachable');
  });

  it('shows "API: unreachable" when fetch rejects (network error)', async () => {
    stubHealth(() => Promise.reject(new TypeError('Failed to fetch')));

    app = await renderApp('/');

    expect(statusText()).toBe('API: unreachable');
  });

  it('shows "API: unreachable" when there is no response within 5 seconds', async () => {
    vi.useFakeTimers();
    stubHealth(
      (request) =>
        new Promise<Response>((_resolve, reject) => {
          request.signal.addEventListener('abort', () =>
            reject(new DOMException('Aborted', 'AbortError')),
          );
        }),
    );

    app = mountApp('/');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4999);
    });
    expect(statusText()).toBe('API: checking…');

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10);
    });
    expect(statusText()).toBe('API: unreachable');
  });
});

describe('Home page language', () => {
  it.each([
    ['es', 'API: activa', 'Tema', 'Idioma'],
    ['en', 'API: live', 'Theme', 'Language'],
  ] as const)(
    'renders the page and both switchers in %s on the first render',
    async (language, status, themeLabel, languageLabel) => {
      applyLanguage(language);
      stubHealth(() => jsonResponse(200, { status: 'ok' }));

      app = await renderApp('/');

      expect(statusText()).toBe(status);
      const groups = [
        ...app.container.querySelectorAll('header [role="group"]'),
      ].map((g) => g.getAttribute('aria-label'));
      expect(groups).toEqual([themeLabel, languageLabel]);
      expect(document.documentElement.lang).toBe(language);
      // No raw catalog key ever reaches the screen.
      expect(app.container.textContent).not.toMatch(
        /apiStatus\.|theme\.|language\.|nav\.|shell\.|common:|errors:/,
      );
    },
  );

  it('switches language in place: no remount, status kept, stored', async () => {
    applyLanguage('es');
    const stub = stubHealth(() => jsonResponse(200, { status: 'ok' }));
    app = await renderApp('/');
    const { container } = app;
    const heading = container.querySelector('h1');
    expect(statusText()).toBe('API: activa');
    expect(buttonNamed(container, 'Claro')).toBeTruthy();

    act(() => buttonNamed(container, 'English').click());

    expect(i18n.language).toBe('en');
    expect(statusText()).toBe('API: live');
    expect(buttonNamed(container, 'Light')).toBeTruthy();
    expect(document.documentElement.lang).toBe('en');
    expect(window.localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe('en');
    // Same DOM node and no second health check: the app was not remounted.
    expect(container.querySelector('h1')).toBe(heading);
    expect(stub.calls('GET /health/live')).toHaveLength(1);

    act(() => buttonNamed(container, 'Español').click());
    expect(statusText()).toBe('API: activa');
    expect(document.documentElement.lang).toBe('es');
  });
});
