import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { NAV_ITEMS } from '@/app/navigation';
import { canAccess } from '@/features/auth/access';
import type { SessionUserBody } from '@/features/auth/session';
import { applyLanguage } from '@/i18n';
import { meAs, problem, stubApi } from '@/test/api-stub';
import {
  jsonResponse,
  renderApp,
  settleUntil,
  type RenderedApp,
} from '@/test/render-app';
import {
  expensePage,
  SEEDED_CATEGORIES,
  TEMPLATES as TEMPLATE_ROWS,
  vendorPage,
} from './fixtures';
import {
  ADMIN,
  ADMIN_REFUNDS,
  CATEGORIES,
  COORDINATOR,
  EXPENSES,
  FINANCE,
  GUARDIAN,
  OWNER,
  STUDENT,
  TEMPLATES,
  TUTOR,
  VENDORS,
} from './testing';

// M7-11b A: the Expenses nav item, the routes and the tabs for each role, and
// the forbidden page on a 403.

let app: RenderedApp | undefined;
const main = () => app!.container.querySelector('main')!;
const h1 = () => main().querySelector('h1')?.textContent;

function stub(as: SessionUserBody, routes = {}) {
  return stubApi({
    'GET /api/v1/auth/me': meAs(as),
    [CATEGORIES]: () => jsonResponse(200, SEEDED_CATEGORIES),
    [VENDORS]: () => jsonResponse(200, vendorPage()),
    [EXPENSES]: () => jsonResponse(200, expensePage()),
    [TEMPLATES]: () => jsonResponse(200, TEMPLATE_ROWS),
    ...routes,
  });
}

async function open(path: string) {
  app = await renderApp(path);
  await settleUntil(() => main().querySelector('h1'));
}

beforeEach(() => {
  applyLanguage('en');
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.unstubAllGlobals();
});

describe('nav (A2)', () => {
  it.each([
    ['the Owner', OWNER, true],
    ['an Administrator', ADMIN, true],
    ['Finance Staff', FINANCE, true],
    ['a Coordinator', COORDINATOR, false],
    ['a tutor', TUTOR, false],
    ['a student', STUDENT, false],
    ['a guardian', GUARDIAN, false],
  ] as const)('%s: Expenses shown %s', (_who, as, shown) => {
    const items = NAV_ITEMS.filter((item) => canAccess(as, item));
    expect(items.some((item) => item.to === '/expenses')).toBe(shown);
  });

  it('puts Expenses after Payroll, labelled in both languages', () => {
    const index = NAV_ITEMS.findIndex((item) => item.to === '/expenses');
    expect(NAV_ITEMS[index - 1]?.to).toBe('/payroll');
    expect(NAV_ITEMS[index]?.labelKey).toBe('nav.expenses');
  });
});

describe('routes (A3, A4)', () => {
  it.each([
    ['a Coordinator', COORDINATOR],
    ['a tutor', TUTOR],
    ['a student', STUDENT],
    ['a guardian', GUARDIAN],
  ] as const)(
    '%s is forbidden on /expenses/* with no request',
    async (_who, as) => {
      for (const path of [
        '/expenses',
        '/expenses/records',
        '/expenses/records/84000000-0000-4000-8000-000000000001',
        '/expenses/templates',
        '/expenses/templates/85000000-0000-4000-8000-000000000001',
        '/expenses/categories',
        '/expenses/vendors',
      ]) {
        const api = stub(as);
        await open(path);
        expect(h1()).toBe("You don't have access to this page");
        expect(
          api.requests.filter((request) => request.path.includes('/finance')),
        ).toEqual([]);
        app!.unmount();
        app = undefined;
      }
    },
  );

  it.each([
    ['the Owner', OWNER],
    ['Finance Staff', FINANCE],
  ] as const)(
    'M7-12b A1: /expenses opens the expense list for %s, first tab',
    async (_who, as) => {
      stub(as);
      await open('/expenses');
      await settleUntil(() => h1() === 'Expenses');
      expect(app!.router.state.location.pathname).toBe('/expenses/records');
      const tabs = [
        ...main().querySelectorAll<HTMLAnchorElement>(
          'nav[aria-label="Expense sections"] a',
        ),
      ].map((a) => [a.textContent, a.getAttribute('aria-current')]);
      expect(tabs).toEqual([
        ['Expenses', 'page'],
        ['Recurring', null],
        ['Categories', null],
        ['Vendors', null],
      ]);
    },
  );

  it.each([
    ['an Administrator', ADMIN],
    ['an Administrator delegated refunds', ADMIN_REFUNDS],
  ] as const)(
    'M7-12b A1: /expenses opens the categories for %s, with no expense tab',
    async (_who, as) => {
      const api = stub(as);
      await open('/expenses');
      await settleUntil(() => h1() === 'Expense categories');
      expect(app!.router.state.location.pathname).toBe('/expenses/categories');
      const tabs = [
        ...main().querySelectorAll<HTMLAnchorElement>(
          'nav[aria-label="Expense sections"] a',
        ),
      ].map((a) => [a.textContent, a.getAttribute('aria-current')]);
      expect(tabs).toEqual([
        ['Categories', 'page'],
        ['Vendors', null],
      ]);
      expect(api.calls(EXPENSES)).toHaveLength(0);
    },
  );

  it.each([
    ['an Administrator', ADMIN],
    ['an Administrator delegated refunds', ADMIN_REFUNDS],
  ] as const)(
    'M7-12b A2: %s is forbidden on the expense records with no request',
    async (_who, as) => {
      const api = stub(as);
      await open('/expenses/records');
      expect(h1()).toBe("You don't have access to this page");
      expect(api.calls(EXPENSES)).toHaveLength(0);
    },
  );

  it.each([
    ['an Administrator', ADMIN],
    ['an Administrator delegated refunds', ADMIN_REFUNDS],
  ] as const)(
    'M7-13b A2: %s is forbidden on the recurring templates with no request',
    async (_who, as) => {
      for (const path of [
        '/expenses/templates',
        '/expenses/templates/85000000-0000-4000-8000-000000000001',
      ]) {
        const api = stub(as);
        await open(path);
        expect(h1()).toBe("You don't have access to this page");
        expect(
          api.requests.filter((r) => r.path.includes('expense-templates')),
        ).toEqual([]);
        app!.unmount();
        app = undefined;
      }
    },
  );

  it.each([
    ['the Owner', OWNER],
    ['Finance Staff', FINANCE],
  ] as const)(
    'M7-13b A1: the Recurring tab is second and current on its page for %s',
    async (_who, as) => {
      stub(as);
      await open('/expenses/templates');
      await settleUntil(() => h1() === 'Recurring expenses');
      const tabs = [
        ...main().querySelectorAll<HTMLAnchorElement>(
          'nav[aria-label="Expense sections"] a',
        ),
      ].map((a) => [a.textContent, a.getAttribute('aria-current')]);
      expect(tabs).toEqual([
        ['Expenses', null],
        ['Recurring', 'page'],
        ['Categories', null],
        ['Vendors', null],
      ]);
    },
  );

  it('M7-13b: the Recurring tab reads "Recurrentes" in Spanish', async () => {
    applyLanguage('es');
    stub(OWNER);
    await open('/expenses/templates');
    await settleUntil(() => h1() === 'Gastos recurrentes');
    expect(
      main().querySelector('nav a[aria-current="page"]')?.textContent,
    ).toBe('Recurrentes');
  });

  it('the vendors tab is marked current on its page', async () => {
    stub(ADMIN);
    await open('/expenses/vendors');
    await settleUntil(() => h1() === 'Vendors');
    expect(
      main().querySelector('nav a[aria-current="page"]')?.textContent,
    ).toBe('Vendors');
  });

  it.each([
    ['/expenses/categories', CATEGORIES],
    ['/expenses/vendors', VENDORS],
    ['/expenses/records', EXPENSES],
    ['/expenses/templates', TEMPLATES],
  ])('a 403 on %s shows the forbidden page', async (path, route) => {
    stub(FINANCE, { [route]: () => problem(403, 'permission_denied') });
    await open(path);
    await settleUntil(() => h1() === "You don't have access to this page");
  });

  it('labels the nav item in Spanish', async () => {
    applyLanguage('es');
    stub(OWNER);
    await open('/expenses/categories');
    const link = [
      ...app!.container.querySelectorAll<HTMLAnchorElement>('header a'),
    ].find((a) => a.getAttribute('href') === '/expenses');
    expect(link?.textContent).toBe('Gastos');
  });
});
