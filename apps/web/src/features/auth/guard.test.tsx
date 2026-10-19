import type { RouteObject } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { NavItem } from '@/app/navigation';
import { routes } from '@/app/router';
import { applyLanguage } from '@/i18n';
import {
  meAs,
  OTHER_USER,
  stubApi,
  stubSignedIn,
  stubSignedOut,
} from '@/test/api-stub';
import { routesWith } from '@/test/auth-routes';
import { renderApp, type RenderedApp } from '@/test/render-app';
import { useCan } from './useSession';

// F1, F2, F6: route access (UX only; the API matrix is the authority).

let app: RenderedApp | undefined;

beforeEach(() => {
  applyLanguage('en');
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.unstubAllGlobals();
});

function AuditPage() {
  return <h1>Audit page</h1>;
}

function CanAudit() {
  return <p data-testid="can">{useCan('audit.view') ? 'yes' : 'no'}</p>;
}

const testPages: RouteObject[] = [
  {
    path: 'audit',
    element: <AuditPage />,
    handle: { permission: 'audit.view' },
  },
  {
    path: 'odd',
    element: <h1>Odd page</h1>,
    handle: { permission: 'not.a.permission' },
  },
  { path: 'open', element: <CanAudit /> },
];

const testNav: NavItem[] = [
  { to: '/', labelKey: 'nav.home', end: true },
  { to: '/audit', labelKey: 'auth.forbidden.title', permission: 'audit.view' },
  {
    to: '/odd',
    labelKey: 'notFound.title',
    permission: 'not.a.permission' as NavItem['permission'],
  },
];

const navLabels = () =>
  [...(app?.container.querySelectorAll('header nav a') ?? [])].map(
    (link) => link.textContent,
  );
const h1 = () => app?.container.querySelector('main h1')?.textContent;

describe('route tree (F1)', () => {
  it('has no page outside the guard except /login', () => {
    const outside: string[] = [];
    function walk(list: RouteObject[], underGuard: boolean, prefix: string) {
      for (const route of list) {
        const path = route.index
          ? `${prefix}(index)`
          : `${prefix}${route.path ?? ''}`;
        const guarded = underGuard || route.id === 'guard';
        if (!guarded && route.id !== 'root' && route.element) {
          outside.push(path);
        }
        if (route.children) {
          walk(route.children, guarded, path.endsWith('/') ? path : `${path}/`);
        }
      }
    }
    walk(routes, false, '');
    expect(outside).toEqual(['/login']);
  });
});

describe('permission routes (F2, F6)', () => {
  it('a user without the permission gets ForbiddenPage at the same URL, and no nav item', async () => {
    stubApi({ 'GET /api/v1/auth/me': meAs(OTHER_USER) });

    app = await renderApp('/audit?x=1', routesWith(testPages, testNav));

    expect(app.location()).toBe('/audit?x=1');
    expect(h1()).toBe("You don't have access to this page");
    expect(app.container.textContent).not.toContain('Audit page');
    expect(app.container.querySelector('main a[href="/"]')?.textContent).toBe(
      'Back to home',
    );
    expect(document.title).toBe(
      "You don't have access to this page · Classora",
    );
    expect(navLabels()).toEqual(['Home']);
  });

  it('a user with the permission sees the page and the nav item', async () => {
    stubSignedIn(); // TEST_USER holds audit.view

    app = await renderApp('/audit', routesWith(testPages, testNav));

    expect(h1()).toBe('Audit page');
    expect(navLabels()).toEqual(['Home', "You don't have access to this page"]);
  });

  it('a permission received through an Administrator delegation counts the same', async () => {
    stubApi({
      'GET /api/v1/auth/me': meAs({
        ...OTHER_USER,
        roles: ['administrator'],
        permissions: ['audit.view', 'payroll.finalize'],
      }),
    });

    app = await renderApp('/audit', routesWith(testPages, testNav));

    expect(h1()).toBe('Audit page');
  });

  it('an unknown permission string fails closed (page and nav item)', async () => {
    stubSignedIn();

    app = await renderApp('/odd', routesWith(testPages, testNav));

    expect(h1()).toBe("You don't have access to this page");
    expect(navLabels()).not.toContain('Page not found');
  });

  it('useCan follows the signed-in permissions', async () => {
    stubSignedIn();
    app = await renderApp('/open', routesWith(testPages));
    expect(
      app.container.querySelector('[data-testid="can"]')?.textContent,
    ).toBe('yes');
    app.unmount();

    stubApi({ 'GET /api/v1/auth/me': meAs(OTHER_USER) });
    app = await renderApp('/open', routesWith(testPages));
    expect(
      app.container.querySelector('[data-testid="can"]')?.textContent,
    ).toBe('no');
  });

  it('unknown paths stay under the guard: signed out goes to login first', async () => {
    stubSignedOut();

    app = await renderApp('/no-such-page');

    expect(app.location()).toBe('/login?next=%2Fno-such-page');
  });
});
