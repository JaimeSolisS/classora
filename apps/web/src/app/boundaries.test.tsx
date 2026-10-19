import { act } from 'react';
import { createRoot } from 'react-dom/client';
import type { RouteObject } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { applyLanguage } from '@/i18n';
import { stubSignedIn } from '@/test/api-stub';
import { renderApp, type RenderedApp } from '@/test/render-app';
import { ApiError } from './api';
import { AppErrorBoundary } from './AppErrorBoundary';
import { reportError } from './errors';
import { routes } from './router';

// E3/E4 (H6): render errors never leave a blank screen, never show the error
// text or stack, and are reported once through reportError. Signed in through
// a stubbed /auth/me (#28 I2), so pages under the guard render.

vi.mock('./errors', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./errors')>();
  return { ...actual, reportError: vi.fn() };
});

const SECRET = 'SECRET internal failure';

function Boom(): never {
  throw new Error(SECRET);
}

function ApiBoom(): never {
  throw new ApiError({
    code: 'internal_error',
    status: 500,
    requestId: 'req-boom-1',
  });
}

// The real route tree with one extra page under the guard route, so the real
// errorElements are exercised.
function routesWithPage(path: string, element: React.ReactNode): RouteObject[] {
  const [root] = routes;
  const children = root!.children!.map((child) =>
    child.id === 'guard'
      ? { ...child, children: [...child.children!, { path, element }] }
      : child,
  );
  return [{ ...root, children } as RouteObject];
}

let app: RenderedApp | undefined;

function expectNoErrorDetails(container: HTMLElement) {
  expect(container.textContent).not.toContain(SECRET);
  expect(container.textContent).not.toMatch(/at Boom|at ApiBoom|\.tsx:/);
}

beforeEach(() => {
  applyLanguage('en');
  stubSignedIn();
  // React and React Router log caught errors; silenced in these tests only.
  vi.spyOn(console, 'error').mockImplementation(() => {});
  vi.spyOn(console, 'warn').mockImplementation(() => {});
  vi.mocked(reportError).mockClear();
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('route-level boundary', () => {
  it('renders RouteErrorPage inside the layout when a page throws', async () => {
    app = await renderApp('/boom', routesWithPage('boom', <Boom />));
    const { container } = app;

    expect(container.querySelector('header')).not.toBeNull();
    expect(container.querySelector('nav')).not.toBeNull();
    expect(container.querySelector('main#main h1')?.textContent).toBe(
      "We couldn't show this page",
    );
    expect(container.textContent).toContain('Go to home');
    expect(container.textContent).toContain('Reload');
    expect(container.textContent).not.toContain('Reference');
    expectNoErrorDetails(container);
    expect(document.title).toBe("We couldn't show this page · Classora");

    expect(reportError).toHaveBeenCalledTimes(1);
    expect(reportError).toHaveBeenCalledWith(expect.any(Error), {
      source: 'route',
    });
  });

  it('shows the reference ID of an ApiError thrown while rendering', async () => {
    app = await renderApp('/api-boom', routesWithPage('api-boom', <ApiBoom />));

    expect(app.container.querySelector('main code')?.textContent).toBe(
      'req-boom-1',
    );
    expect(app.container.textContent).toContain('Reference: req-boom-1');
    expect(reportError).toHaveBeenCalledTimes(1);
  });

  it('keeps the header switchers usable after a page error', async () => {
    app = await renderApp('/boom', routesWithPage('boom', <Boom />));
    const spanish = [...app.container.querySelectorAll('header button')].find(
      (b) => b.textContent === 'Español',
    ) as HTMLButtonElement;

    act(() => spanish.click());

    expect(app.container.querySelector('main h1')?.textContent).toBe(
      'No pudimos mostrar esta página',
    );
    window.localStorage.clear();
  });
});

describe('root-level boundary', () => {
  it('renders the root fallback without the layout when the layout throws', async () => {
    const [root] = routes;
    app = await renderApp('/', [{ ...root!, element: <Boom /> }]);
    const { container } = app;

    expect(container.querySelector('header')).toBeNull();
    expect(container.querySelector('h1')?.textContent).toBe(
      'Something went wrong',
    );
    expect(container.textContent).toContain('Reload');
    expectNoErrorDetails(container);
    expect(reportError).toHaveBeenCalledTimes(1);
    expect(reportError).toHaveBeenCalledWith(expect.any(Error), {
      source: 'root',
    });
  });
});

describe('AppErrorBoundary', () => {
  it('catches a throwing child and renders its translated fallback', () => {
    for (const [language, title, reload] of [
      ['en', 'Something went wrong', 'Reload'],
      ['es', 'Algo salió mal', 'Recargar'],
    ] as const) {
      vi.mocked(reportError).mockClear();
      applyLanguage(language);
      const container = document.createElement('div');
      document.body.appendChild(container);
      const root = createRoot(container);

      act(() =>
        root.render(
          <AppErrorBoundary>
            <Boom />
          </AppErrorBoundary>,
        ),
      );

      expect(container.querySelector('h1')?.textContent).toBe(title);
      expect(container.querySelector('button')?.textContent).toBe(reload);
      expectNoErrorDetails(container);
      expect(reportError).toHaveBeenCalledTimes(1);
      expect(reportError).toHaveBeenCalledWith(
        expect.any(Error),
        expect.objectContaining({ source: 'app' }),
      );
      act(() => root.unmount());
      container.remove();
    }
  });

  it('renders its children when nothing throws', () => {
    const container = document.createElement('div');
    const root = createRoot(container);
    act(() =>
      root.render(
        <AppErrorBoundary>
          <p>fine</p>
        </AppErrorBoundary>,
      ),
    );
    expect(container.textContent).toBe('fine');
    act(() => root.unmount());
  });
});
