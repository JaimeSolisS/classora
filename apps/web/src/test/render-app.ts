import type { QueryClient } from '@tanstack/react-query';
import { act, createElement } from 'react';
import { createRoot } from 'react-dom/client';
import { createMemoryRouter, type RouteObject } from 'react-router';
import App from '@/App';
import { createQueryClient } from '@/app/queryClient';
import { routes } from '@/app/router';
import { clearCsrfToken } from '@/features/auth/csrf';

// Test helpers: render the real App (providers + router) on a memory router
// with a fresh query client, as main.tsx does with the browser router.

// Tell React this is a test environment so act() flushes updates.
(
  globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true;

// jsdom has no ResizeObserver; Radix's Checkbox measures itself with one
// (#170). A no-op is enough: tests never depend on element sizes.
if (typeof globalThis.ResizeObserver === 'undefined') {
  globalThis.ResizeObserver = class {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  };
}

export interface RenderedApp {
  container: HTMLDivElement;
  queryClient: QueryClient;
  router: ReturnType<typeof createMemoryRouter>;
  /** The router's current path, search and hash. */
  location: () => string;
  unmount: () => void;
}

/** Mounts the app at `path` synchronously (use with fake timers). */
export function mountApp(
  path = '/',
  routeList: RouteObject[] = routes,
): RenderedApp {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = createRoot(container);
  const router = createMemoryRouter(routeList, { initialEntries: [path] });
  const queryClient = createQueryClient();
  act(() => root.render(createElement(App, { router, queryClient })));
  return {
    container,
    queryClient,
    router,
    location: () => {
      const { pathname, search, hash } = router.state.location;
      return `${pathname}${search}${hash}`;
    },
    unmount: () => {
      act(() => root.unmount());
      container.remove();
      queryClient.clear();
      // The token is module state: never carried into the next test.
      clearCsrfToken();
    },
  };
}

/** Lets pending fetches, query updates and effects settle (real timers). */
export async function settle(): Promise<void> {
  for (let i = 0; i < 5; i += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

/** Mounts the app at `path` and lets it settle. */
export async function renderApp(
  path = '/',
  routeList: RouteObject[] = routes,
): Promise<RenderedApp> {
  const rendered = mountApp(path, routeList);
  await settle();
  return rendered;
}

export function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

export function buttonNamed(
  container: ParentNode,
  name: string,
): HTMLButtonElement {
  const found = [...container.querySelectorAll('button')].find(
    (b) => b.textContent === name || b.getAttribute('aria-label') === name,
  );
  if (!found) {
    throw new Error(`No button "${name}"`);
  }
  return found;
}
