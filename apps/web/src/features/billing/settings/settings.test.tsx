import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { problemWith } from '@/features/academics/testing';
import type { SessionUserBody } from '@/features/auth/session';
import { canAccess } from '@/features/auth/access';
import { NAV_ITEMS } from '@/app/navigation';
import { applyLanguage } from '@/i18n';
import {
  meAs,
  problem,
  stubApi,
  TEST_USER,
  type ApiStub,
  type Responder,
} from '@/test/api-stub';
import { alerts, click, labelled, openDialog, setFieldValue } from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  settleUntil,
  type RenderedApp,
} from '@/test/render-app';
import type { BillingSettings } from './api';
import { SETTINGS_OFF, SETTINGS_ON } from './fixtures';

// #366 E1 (A, B, D): the Settings tab behind billing.view (the tabs and the
// header item per permission, /billing opening the first tab), the status
// off and on, the change dialog (the date, "Turn billing off", the
// past-date warning, success with the notice, 409 with Reload, 422 on the
// date), read-only without billing.manage, and a failed load with retry.

const SETTINGS = 'GET /api/v1/billing/settings';
const PATCH = 'PATCH /api/v1/billing/settings';
const ME = 'GET /api/v1/auth/me';

const user = (
  roles: SessionUserBody['roles'],
  permissions: SessionUserBody['permissions'],
): SessionUserBody => ({ ...TEST_USER, roles, permissions });

const FINANCE = user(
  ['finance_staff'],
  ['student.view', 'guardian.view', 'billing.view', 'billing.manage'],
);
const VIEWER = user(['finance_staff'], ['billing.view']);
const COORDINATOR = user(
  ['academic_coordinator'],
  ['student.view', 'catalog.manage', 'schedule.view'],
);

function stub(
  routes: Record<string, Responder | Responder[]> = {},
  {
    settings = SETTINGS_OFF,
    as = FINANCE,
  }: { settings?: BillingSettings; as?: SessionUserBody } = {},
): ApiStub {
  return stubApi({
    [ME]: meAs(as),
    [SETTINGS]: () => jsonResponse(200, settings),
    'GET /api/v1/billing/unbilled-items': () =>
      jsonResponse(200, { items: [], total: 0, limit: 25, offset: 0 }),
    'GET /api/v1/billing/service-prices': () =>
      jsonResponse(200, { items: [], total: 0, limit: 25, offset: 0 }),
    ...routes,
  });
}

let app: RenderedApp | undefined;

const main = () => app!.container.querySelector('main')!;
const h1 = () => main().querySelector('h1')?.textContent;
const dialog = () => openDialog()!;
const statusLine = () =>
  main().querySelector('[data-billing-status]')?.textContent;
const tabs = (label = 'Billing sections') =>
  [
    ...main().querySelectorAll<HTMLAnchorElement>(
      `nav[aria-label="${label}"] a`,
    ),
  ].map((a) => [a.textContent, a.getAttribute('aria-current')]);
const announced = () =>
  [...main().querySelectorAll('[role="status"]')]
    .map((s) => s.textContent)
    .join('|');
const dateInput = () => labelled<HTMLInputElement>(dialog(), 'Start date');
const warning = () => dialog().querySelector('[data-billing-warning]');

async function renderAt(path: string) {
  app = await renderApp(path);
  await settleUntil(() => main().querySelector('h1'));
}

async function renderSettings() {
  await renderAt('/billing/settings');
  await settleUntil(() => statusLine() ?? alerts(main()).length > 0);
}

async function openChange() {
  await click(buttonNamed(main(), 'Change'));
  await settleUntil(() => openDialog()?.querySelector('form'));
}

beforeEach(() => {
  applyLanguage('en');
  vi.useFakeTimers({ toFake: ['Date'] });
  // 12:00 on 2026-10-20 in Mexico City.
  vi.setSystemTime(new Date('2026-10-20T18:00:00Z'));
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// --- A: navigation and access -----------------------------------------------

describe('navigation and access (A)', () => {
  it('shows every tab to Finance, with Settings current', async () => {
    stub();
    await renderSettings();

    expect(tabs()).toEqual([
      ['Prices', null],
      ['Unbilled', null],
      ['Settings', 'page'],
    ]);
    expect(main().querySelectorAll('h1')).toHaveLength(1);
    expect(h1()).toBe('Billing settings');
    expect(document.title).toBe('Billing settings · Classora');
    const nav = [
      ...app!.container.querySelectorAll<HTMLAnchorElement>('header nav a'),
    ].find((a) => a.textContent === 'Billing');
    expect(nav?.getAttribute('href')).toBe('/billing');
  });

  it('leaves Prices out for a billing.view-only holder', async () => {
    stub({}, { as: VIEWER });
    await renderSettings();

    expect(tabs()).toEqual([
      ['Unbilled', null],
      ['Settings', 'page'],
    ]);
  });

  it.each([
    ['Finance', FINANCE, '/billing/prices', 'Prices'],
    [
      'a billing.view-only holder',
      VIEWER,
      '/billing/unbilled',
      'Unbilled sessions',
    ],
  ] as const)(
    '/billing opens the first tab %s may open',
    async (_who, as, path, title) => {
      stub({}, { as });
      await renderAt('/billing');
      await settleUntil(() => h1() === title);

      expect(app!.router.state.location.pathname).toBe(path);
    },
  );

  it.each(['/billing', '/billing/settings', '/billing/unbilled'])(
    'forbids a Coordinator at %s and reads nothing',
    async (path) => {
      const api = stub({}, { as: COORDINATOR });
      await renderAt(path);

      expect(h1()).toBe("You don't have access to this page");
      expect(api.calls(SETTINGS)).toHaveLength(0);
      expect(api.calls('GET /api/v1/billing/unbilled-items')).toHaveLength(0);
      const labels = [...app!.container.querySelectorAll('header nav a')].map(
        (a) => a.textContent,
      );
      expect(labels).not.toContain('Billing');
    },
  );

  it.each([
    ['billing.manage only', ['billing.manage'], true],
    ['billing.view only', ['billing.view'], true],
    ['neither', ['student.view'], false],
  ] as const)('the nav item with %s: %s', (_who, permissions, visible) => {
    const as = user(['finance_staff'], [...permissions]);
    const items = NAV_ITEMS.filter((item) => canAccess(as, item));
    expect(items.some((item) => item.to === '/billing')).toBe(visible);
  });
});

// --- B: the Settings tab ----------------------------------------------------

describe('the billing status (B1)', () => {
  it('says billing is off, with the rules', async () => {
    stub();
    await renderSettings();

    expect(statusLine()).toBe('Billing is off.');
    expect(main().textContent).toContain(
      'Sessions before the start date are never charged. Turning billing off stops new charges but never removes charges already posted.',
    );
  });

  it('gives the start date in the user’s language', async () => {
    stub({}, { settings: SETTINGS_ON });
    await renderSettings();

    expect(statusLine()).toBe('Billing charges sessions from Sep 1, 2026.');
  });

  it('is read-only without billing.manage (B3)', async () => {
    const api = stub({}, { settings: SETTINGS_ON, as: VIEWER });
    await renderSettings();

    expect(statusLine()).toBe('Billing charges sessions from Sep 1, 2026.');
    expect(() => buttonNamed(main(), 'Change')).toThrow();
    expect(api.calls(PATCH)).toHaveLength(0);
  });

  it('shows a failed load with Try again, which reloads', async () => {
    const api = stub({
      [SETTINGS]: [
        // A 4xx is never retried by the query client.
        () => problem(400, 'bad_request'),
        () => jsonResponse(200, SETTINGS_ON),
      ],
    });
    await renderSettings();

    expect(alerts(main())).toHaveLength(1);
    await click(buttonNamed(main(), 'Try again'));
    await settleUntil(() => statusLine());

    expect(statusLine()).toBe('Billing charges sessions from Sep 1, 2026.');
    expect(api.calls(SETTINGS)).toHaveLength(2);
  });
});

describe('the change dialog (B2, B3)', () => {
  it('turns billing on from a date: body, notice and refreshed status', async () => {
    const saved = {
      ...SETTINGS_OFF,
      billing_starts_on: '2026-11-01',
      version: 2,
    };
    const api = stub({ [PATCH]: () => jsonResponse(200, saved) });
    await renderSettings();
    await openChange();

    // Billing is off: no "Turn billing off", the date starts at today.
    expect(dialog().querySelectorAll('input[type="radio"]')).toHaveLength(0);
    expect(dateInput().value).toBe('2026-10-20');
    expect(warning()).toBeNull();
    setFieldValue(dateInput(), '2026-11-01');
    await click(buttonNamed(dialog(), 'Save'));
    await settleUntil(() => !openDialog());

    expect(api.calls(PATCH)[0]!.body).toEqual({
      version: 1,
      billing_starts_on: '2026-11-01',
    });
    expect(statusLine()).toBe('Billing charges sessions from Nov 1, 2026.');
    expect(announced()).toContain('Billing settings saved.');
  });

  it('turns billing off: sends null', async () => {
    const api = stub(
      { [PATCH]: () => jsonResponse(200, { ...SETTINGS_OFF, version: 3 }) },
      { settings: SETTINGS_ON },
    );
    await renderSettings();
    await openChange();

    expect(dateInput().value).toBe('2026-09-01');
    await click(labelled<HTMLInputElement>(dialog(), 'Turn billing off'));
    expect(() => dateInput()).toThrow();
    await click(buttonNamed(dialog(), 'Save'));
    await settleUntil(() => !openDialog());

    expect(api.calls(PATCH)[0]!.body).toEqual({
      version: 2,
      billing_starts_on: null,
    });
    expect(statusLine()).toBe('Billing is off.');
  });

  it('warns, without blocking, about a date before today', async () => {
    const api = stub({
      [PATCH]: () =>
        jsonResponse(200, { ...SETTINGS_ON, billing_starts_on: '2026-10-01' }),
    });
    await renderSettings();
    await openChange();

    setFieldValue(dateInput(), '2026-10-19');
    expect(warning()?.textContent).toBe(
      'This date is in the past: confirmed and completed sessions from that date on will be charged.',
    );
    expect(dateInput().getAttribute('aria-describedby')).toContain(
      warning()!.id,
    );
    expect(dateInput().getAttribute('aria-invalid')).toBeNull();
    setFieldValue(dateInput(), '2026-10-20');
    expect(warning()).toBeNull();
    setFieldValue(dateInput(), '2026-10-01');
    await click(buttonNamed(dialog(), 'Save'));
    await settleUntil(() => !openDialog());

    expect(api.calls(PATCH)[0]!.body).toMatchObject({
      billing_starts_on: '2026-10-01',
    });
  });

  it.each([
    ['', 'Choose a date.'],
    ['1999-12-31', 'Choose a date from 2000 to 2099.'],
    ['2100-01-01', 'Choose a date from 2000 to 2099.'],
  ])('checks the date %j before sending', async (value, message) => {
    const api = stub();
    await renderSettings();
    await openChange();

    setFieldValue(dateInput(), value);
    await click(buttonNamed(dialog(), 'Save'));

    expect(dialog().textContent).toContain(message);
    expect(dateInput().getAttribute('aria-invalid')).toBe('true');
    expect(document.activeElement).toBe(dateInput());
    expect(api.calls(PATCH)).toHaveLength(0);
  });

  it('puts a 422 on the date and keeps it', async () => {
    stub({
      [PATCH]: () =>
        problemWith(422, 'validation_error', {
          errors: [
            { path: ['body', 'billing_starts_on'], code: 'date_out_of_range' },
          ],
        }),
    });
    await renderSettings();
    await openChange();

    setFieldValue(dateInput(), '2026-12-01');
    await click(buttonNamed(dialog(), 'Save'));
    await settleUntil(() => dateInput().getAttribute('aria-invalid'));

    expect(dialog().textContent).toContain('Choose a date in this century.');
    expect(dateInput().value).toBe('2026-12-01');
    expect(openDialog()).not.toBeNull();
  });

  it('shows a 409 with Reload; the next save uses the new version', async () => {
    const api = stub({
      [SETTINGS]: [
        () => jsonResponse(200, SETTINGS_OFF),
        () => jsonResponse(200, { ...SETTINGS_ON, version: 7 }),
      ],
      [PATCH]: [
        () => problem(409, 'version_conflict'),
        () => jsonResponse(200, { ...SETTINGS_ON, version: 8 }),
      ],
    });
    await renderSettings();
    await openChange();

    setFieldValue(dateInput(), '2026-11-01');
    await click(buttonNamed(dialog(), 'Save'));
    await settleUntil(() => alerts(dialog()).length > 0);

    expect(alerts(dialog()).join('')).toContain(
      'Someone else changed this record. Reload it and try again.',
    );
    expect(dateInput().value).toBe('2026-11-01');
    await click(buttonNamed(dialog(), 'Reload'));
    await settleUntil(() => api.calls(SETTINGS).length === 2);
    expect(alerts(dialog())).toEqual([]);
    await click(buttonNamed(dialog(), 'Save'));
    await settleUntil(() => !openDialog());

    expect(api.calls(PATCH)[1]!.body).toEqual({
      version: 7,
      billing_starts_on: '2026-11-01',
    });
  });

  it('Cancel closes without sending', async () => {
    const api = stub();
    await renderSettings();
    await openChange();

    await click(buttonNamed(dialog(), 'Cancel'));

    expect(openDialog()).toBeNull();
    expect(api.calls(PATCH)).toHaveLength(0);
  });
});

// --- D: Spanish -------------------------------------------------------------

describe('Spanish (D1)', () => {
  it('names the tabs and the status in Spanish', async () => {
    applyLanguage('es');
    stub({}, { settings: SETTINGS_ON });
    await renderSettings();

    expect(tabs('Secciones de cobros').map(([label]) => label)).toEqual([
      'Precios',
      'Sin cobrar',
      'Configuración',
    ]);
    expect(h1()).toBe('Configuración de cobros');
    expect(statusLine()).toBe(
      'Se cobran las sesiones a partir del 1 sep 2026.',
    );
  });
});
