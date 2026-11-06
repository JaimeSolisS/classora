import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { NAV_ITEMS } from '@/app/navigation';
import { problemWith } from '@/features/academics/testing';
import { canAccess } from '@/features/auth/access';
import type { SessionUserBody } from '@/features/auth/session';
import { applyLanguage } from '@/i18n';
import {
  meAs,
  problem,
  stubApi,
  TEST_USER,
  type ApiStub,
  type Responder,
} from '@/test/api-stub';
import { alerts, click, labelled, setFieldValue } from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  settle,
  settleUntil,
  type RenderedApp,
} from '@/test/render-app';
import type { Profitability } from './api';
import {
  ACCRUAL_NOVEMBER,
  BALANCES,
  CASH_NOVEMBER,
  EMPTY_DAY,
} from './fixtures';
import { activePreset, presetRange, rangeError } from './rules';

// M7-14b E1: the Reports nav item and routes per role, the URL-synced basis
// and range (presets, a debounced custom date), the client and server range
// errors, the accrual and cash figures from #117's oracle, the category and
// vendor labels, the balances and their links, and the loading, error,
// out-of-date and all-zero states.

const user = (
  roles: SessionUserBody['roles'],
  permissions: SessionUserBody['permissions'],
): SessionUserBody => ({ ...TEST_USER, roles, permissions });

const OWNER = user(
  ['owner'],
  ['finance.report.view', 'billing.view', 'expense.view', 'expense.configure'],
);
/** Finance Staff without billing.view: the receivables link hides. */
const FINANCE = user(
  ['finance_staff'],
  ['finance.report.view', 'expense.view'],
);
const REPORTS_ONLY = user(['finance_staff'], ['finance.report.view']);
const ADMIN = user(
  ['administrator'],
  ['user.view', 'expense.configure', 'schedule.view', 'tutor.view'],
);
const ADMIN_FINALIZE = user(
  ['administrator'],
  [
    'user.view',
    'expense.configure',
    'schedule.view',
    'tutor.view',
    'payroll.finalize',
  ],
);
const COORDINATOR = user(
  ['academic_coordinator'],
  ['schedule.view', 'tutor.view'],
);
const TUTOR = user(['tutor'], ['tutor.view']);
const STUDENT = user(['student'], ['student.view']);
const GUARDIAN = user(['guardian'], ['student.view', 'guardian.view']);

const PROFITABILITY = 'GET /api/v1/reports/finance/profitability';
const BALANCES_ROUTE = 'GET /api/v1/reports/finance/balances';

let app: RenderedApp | undefined;
let asked: URLSearchParams[] = [];
const main = () => app!.container.querySelector('main')!;
const h1 = () => main().querySelector('h1')?.textContent;
const search = () => new URLSearchParams(app!.router.state.location.search);
const text = (selector: string) =>
  main().querySelector(selector)?.textContent ?? null;

/** Answers with `body`, re-dated to the asked basis and range. */
const report =
  (body: Profitability = ACCRUAL_NOVEMBER): Responder =>
  (request) => {
    const query = new URL(request.url).searchParams;
    asked.push(query);
    return jsonResponse(200, body);
  };

function stub(
  as: SessionUserBody = OWNER,
  routes: Record<string, Responder | Responder[]> = {},
): ApiStub {
  return stubApi({
    'GET /api/v1/auth/me': meAs(as),
    [PROFITABILITY]: report(),
    [BALANCES_ROUTE]: () => jsonResponse(200, BALANCES),
    ...routes,
  });
}

async function open(path = '/reports/finance') {
  app = await renderApp(path);
  await settleUntil(() => main().querySelector('h1'));
}

const loaded = () =>
  settleUntil(
    () =>
      main().querySelector('[data-report-basis-shown]') &&
      main().querySelector('[data-balance-card]'),
  );

const rowValues = (type: string) =>
  [
    ...main().querySelectorAll(
      `[data-report-class-table] tr[data-class-type="${type}"] [data-money]`,
    ),
  ].map((cell) => cell.textContent);

beforeEach(() => {
  applyLanguage('en');
  asked = [];
  vi.useFakeTimers({ toFake: ['Date'] });
  // 12:00 on 2026-12-10 in Mexico City: the oracle's today.
  vi.setSystemTime(new Date('2026-12-10T18:00:00Z'));
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('rules (E2)', () => {
  it('computes the presets on a given today', () => {
    expect(presetRange('thisMonth', '2026-12-10')).toEqual({
      from: '2026-12-01',
      to: '2026-12-10',
    });
    expect(presetRange('lastMonth', '2026-03-31')).toEqual({
      from: '2026-02-01',
      to: '2026-02-28',
    });
    expect(presetRange('lastMonth', '2026-01-15')).toEqual({
      from: '2025-12-01',
      to: '2025-12-31',
    });
    expect(presetRange('last3Months', '2026-12-10')).toEqual({
      from: '2026-09-12',
      to: '2026-12-10',
    });
    expect(presetRange('yearToDate', '2026-12-10')).toEqual({
      from: '2026-01-01',
      to: '2026-12-10',
    });
    expect(
      activePreset({ from: '2026-01-01', to: '2026-12-10' }, '2026-12-10'),
    ).toBe('yearToDate');
    expect(
      activePreset({ from: '2026-01-02', to: '2026-12-10' }, '2026-12-10'),
    ).toBeNull();
  });

  it.each([
    // Each bound and one day beyond.
    [{ from: '2026-12-10', to: '2026-12-10' }, null],
    [
      { from: '2026-12-10', to: '2026-12-09' },
      { field: 'to', problem: 'order' },
    ],
    [
      { from: '2026-12-01', to: '2026-12-11' },
      { field: 'to', problem: 'future' },
    ],
    [{ from: '2025-12-10', to: '2026-12-10' }, null],
    [
      { from: '2025-12-09', to: '2026-12-10' },
      { field: 'from', problem: 'tooLong' },
    ],
    [
      { from: '', to: '2026-12-10' },
      { field: 'from', problem: 'required' },
    ],
    [
      { from: '2026-12-01', to: '' },
      { field: 'to', problem: 'required' },
    ],
  ] as const)('%o → %o', (range, expected) => {
    expect(rangeError(range, '2026-12-10')).toEqual(expected);
  });
});

describe('nav and routes (A1, A2)', () => {
  // M8-09b A1: Reports opens the KPIs, so schedule.view holders see it too.
  it.each([
    ['the Owner', OWNER, true],
    ['Finance Staff', FINANCE, true],
    ['an Administrator', ADMIN, true],
    ['an Administrator with payroll.finalize', ADMIN_FINALIZE, true],
    ['a Coordinator', COORDINATOR, true],
    ['a tutor', TUTOR, false],
    ['a student', STUDENT, false],
    ['a guardian', GUARDIAN, false],
  ] as const)('%s: Reports shown %s', (_who, as, shown) => {
    const items = NAV_ITEMS.filter((item) => canAccess(as, item));
    expect(items.some((item) => item.to === '/reports')).toBe(shown);
  });

  it('puts Reports after Expenses, labelled in both languages', async () => {
    const index = NAV_ITEMS.findIndex((item) => item.to === '/reports');
    expect(NAV_ITEMS[index - 1]?.to).toBe('/expenses');
    expect(NAV_ITEMS[index]?.labelKey).toBe('nav.reports');
    applyLanguage('es');
    stub();
    await open();
    const link = [
      ...app!.container.querySelectorAll<HTMLAnchorElement>('header a'),
    ].find((a) => a.getAttribute('href') === '/reports');
    expect(link?.textContent).toBe('Reportes');
  });

  it.each([
    ['an Administrator', ADMIN, ['/reports/finance']],
    [
      'an Administrator with payroll.finalize',
      ADMIN_FINALIZE,
      ['/reports/finance'],
    ],
    ['a Coordinator', COORDINATOR, ['/reports/finance']],
    ['a tutor', TUTOR, ['/reports', '/reports/finance']],
    ['a student', STUDENT, ['/reports', '/reports/finance']],
    ['a guardian', GUARDIAN, ['/reports', '/reports/finance']],
  ] as const)(
    '%s is forbidden with no report request',
    async (_who, as, paths) => {
      for (const path of paths) {
        const api = stub(as);
        await open(path);
        expect(h1()).toBe("You don't have access to this page");
        expect(
          api.requests.filter((r) => r.path.startsWith('/api/v1/reports')),
        ).toEqual([]);
        app!.unmount();
        app = undefined;
      }
    },
  );

  it('shows the reports sub-navigation, the financial report current', async () => {
    applyLanguage('es');
    stub();
    await open();
    await loaded();
    const links = [
      ...main().querySelectorAll<HTMLAnchorElement>('[data-reports-subnav] a'),
    ];
    expect(links.map((a) => [a.getAttribute('href'), a.textContent])).toEqual([
      ['/reports/kpis', 'Indicadores'],
      ['/reports/finance', 'Reporte financiero'],
    ]);
    expect(links[1]!.getAttribute('aria-current')).toBe('page');
  });

  it.each([[PROFITABILITY], [BALANCES_ROUTE]])(
    'a 403 on %s shows the forbidden page',
    async (route) => {
      stub(FINANCE, { [route]: () => problem(403, 'permission_denied') });
      await open();
      await settleUntil(() => h1() === "You don't have access to this page");
    },
  );
});

describe('the range in the URL (B1, E2)', () => {
  it('asks for this month on the accrual basis by default', async () => {
    stub();
    await open();
    await loaded();
    expect(asked.map((q) => q.toString())).toEqual([
      'basis=accrual&from=2026-12-01&to=2026-12-10',
    ]);
    expect(buttonNamed(main(), 'Accrual').getAttribute('aria-pressed')).toBe(
      'true',
    );
    expect(buttonNamed(main(), 'This month').getAttribute('aria-pressed')).toBe(
      'true',
    );
    expect(labelled<HTMLInputElement>(main(), 'From').value).toBe('2026-12-01');
  });

  it("re-anchors the default range on the API's today", async () => {
    // The browser is already in December; the center's today is Nov 30.
    stub(OWNER, {
      [BALANCES_ROUTE]: () =>
        jsonResponse(200, { ...BALANCES, today: '2026-11-30' }),
    });
    await open();
    await settleUntil(() => asked.some((q) => q.get('from') === '2026-11-01'));
    expect(asked.at(-1)?.toString()).toBe(
      'basis=accrual&from=2026-11-01&to=2026-11-30',
    );
    await settleUntil(() => main().querySelector('[data-report-basis-shown]'));
    expect(labelled<HTMLInputElement>(main(), 'To').value).toBe('2026-11-30');
    expect(labelled<HTMLInputElement>(main(), 'To').max).toBe('2026-11-30');
  });

  it('writes the basis and a preset to the URL, keeping the other', async () => {
    stub(OWNER, { [PROFITABILITY]: [report(), report(CASH_NOVEMBER)] });
    await open();
    await loaded();
    await click(buttonNamed(main(), 'Cash'));
    expect(search().get('basis')).toBe('cash');
    await click(buttonNamed(main(), 'Last month'));
    expect(search().toString()).toBe(
      'basis=cash&from=2026-11-01&to=2026-11-30',
    );
    expect(buttonNamed(main(), 'Last month').getAttribute('aria-pressed')).toBe(
      'true',
    );
    expect(asked.at(-1)?.toString()).toBe(
      'basis=cash&from=2026-11-01&to=2026-11-30',
    );
  });

  it('writes a typed date after a pause, keeping a basis switched meanwhile', async () => {
    stub();
    await open('/reports/finance?basis=accrual&from=2026-11-01&to=2026-11-30');
    await loaded();
    setFieldValue(labelled(main(), 'From'), '2026-10-15');
    // The write waits; the basis switch lands first.
    expect(search().get('from')).toBe('2026-11-01');
    await click(buttonNamed(main(), 'Cash'));
    await settleUntil(() => search().get('from') === '2026-10-15');
    expect(search().toString()).toBe(
      'basis=cash&from=2026-10-15&to=2026-11-30',
    );
    expect(asked.at(-1)?.toString()).toBe(
      'basis=cash&from=2026-10-15&to=2026-11-30',
    );
  });

  it.each([
    [
      '2026-12-05',
      '2026-12-04',
      'To',
      'The start date must be before the end date',
    ],
    ['2026-12-01', '2026-12-11', 'To', "It can't be after today"],
    ['2025-12-09', '2026-12-10', 'From', '366 days at most'],
  ])(
    'from %s to %s: no request, the error next to %s',
    async (from, to, field, message) => {
      stub();
      await open(`/reports/finance?from=${from}&to=${to}`);
      await settleUntil(() => main().querySelector('[data-balance-card]'));
      await settle();
      expect(asked).toEqual([]);
      const input = labelled<HTMLInputElement>(main(), field);
      expect(input.getAttribute('aria-invalid')).toBe('true');
      const describedBy = input.getAttribute('aria-describedby')!;
      expect(document.getElementById(describedBy)?.textContent).toBe(message);
      expect(main().querySelector('[data-report-basis-shown]')).toBeNull();
    },
  );

  it('asks for the longest range (366 days, both ends included)', async () => {
    stub();
    await open('/reports/finance?from=2025-12-10&to=2026-12-10');
    await settleUntil(() => asked.length === 1);
    expect(asked[0]!.get('from')).toBe('2025-12-10');
  });

  it('a typed date one day too early shows the bound and asks nothing', async () => {
    stub();
    await open('/reports/finance?from=2025-12-10&to=2026-12-10');
    await loaded();
    setFieldValue(labelled(main(), 'From'), '2025-12-09');
    await settle();
    expect(alerts(main())).toEqual([]);
    const from = labelled<HTMLInputElement>(main(), 'From');
    expect(from.getAttribute('aria-invalid')).toBe('true');
    expect(main().textContent).toContain('366 days at most');
    expect(main().querySelector('[data-report-basis-shown]')).toBeNull();
    // Written after the pause, so a reload keeps it; still nothing asked.
    await settleUntil(() => search().get('from') === '2025-12-09');
    expect(asked).toHaveLength(1);
  });

  it('shows a server 422 report_range_invalid next to the field it names', async () => {
    stub(OWNER, {
      [PROFITABILITY]: () =>
        problemWith(422, 'report_range_invalid', {
          errors: [
            {
              path: ['query', 'from'],
              code: 'report_range_invalid',
              message: 'The range is longer than 366 days.',
            },
          ],
        }),
    });
    await open();
    await settleUntil(() =>
      labelled(main(), 'From').getAttribute('aria-invalid'),
    );
    const from = labelled<HTMLInputElement>(main(), 'From');
    expect(
      document.getElementById(from.getAttribute('aria-describedby')!)
        ?.textContent,
    ).toBe("The center doesn't accept this range. Check this date.");
    expect(main().textContent).not.toContain('366 days.');
    expect(main().querySelector('[data-report-basis-shown]')).toBeNull();
  });
});

describe('the figures (B2–B5)', () => {
  it("renders #117's D1 accrual oracle in the API's order", async () => {
    stub();
    await open('/reports/finance?from=2026-11-01&to=2026-11-30');
    await loaded();
    const types = [
      ...main().querySelectorAll(
        '[data-report-class-table] tbody tr[data-class-type]',
      ),
    ].map((row) => row.querySelector('th')?.textContent);
    expect(types).toEqual(['Individual', 'Group', 'Courses', 'Unallocated']);
    expect(rowValues('individual')).toEqual([
      '$4,491.67',
      '$500.00',
      '$3,991.67',
    ]);
    expect(rowValues('unallocated')).toEqual([
      '$250.00',
      '$3,200.00',
      '-$2,950.00',
    ]);
    expect(rowValues('total')).toEqual(['$7,070.34', '$4,430.00', '$2,640.34']);
    // A negative amount: the minus sign and an icon, not colour alone.
    const negative = main().querySelector(
      '[data-report-class-table] tr[data-class-type="unallocated"] [data-negative]',
    )!;
    expect(negative.querySelector('svg')).not.toBeNull();
    expect(text('[data-report-net] [data-figure="net"] dd')).toBe('-$709.66');
    expect(text('[data-figure="expenses-total"] dd')).toBe('$3,350.00');
    expect(text('[data-report-note="accrual"]')).toContain(
      'Revenue is recognized when the class is taught',
    );
    expect(text('[data-report-note="unallocated"]')).toContain(
      'not split across class types',
    );
    expect(text('[data-report-range]')).toBe('Nov 1, 2026 to Nov 30, 2026');
  });

  it('shows keyed, renamed and inactive category labels and "No vendor"', async () => {
    stub();
    await open('/reports/finance?from=2026-11-01&to=2026-11-30');
    await loaded();
    const categories = () =>
      [...main().querySelectorAll('[data-report-by-category] li')].map(
        (item) => item.textContent,
      );
    expect(categories()).toEqual([
      'Rent$1,500.00',
      'Luz y agua (oficina)$1,350.00',
      'MobiliarioInactive$500.00',
    ]);
    const vendors = [
      ...main().querySelectorAll('[data-report-by-vendor] li'),
    ].map((item) => item.textContent);
    expect(vendors).toEqual([
      'No vendor$2,850.00',
      'Muebles de PruebaInactive$500.00',
    ]);
    // Keyed labels and class types follow the language; staff names don't.
    applyLanguage('es');
    await settle();
    expect(categories()).toEqual([
      'Renta$1,500.00',
      'Luz y agua (oficina)$1,350.00',
      'MobiliarioInactiva$500.00',
    ]);
    expect(
      main().querySelector(
        '[data-report-class-table] tr[data-class-type="group"] th',
      )?.textContent,
    ).toBe('Grupal');
  });

  it("renders #117's D2 cash oracle with no class rows", async () => {
    stub(OWNER, { [PROFITABILITY]: report(CASH_NOVEMBER) });
    await open('/reports/finance?basis=cash&from=2026-11-01&to=2026-11-30');
    await loaded();
    expect(main().querySelector('[data-report-class-table]')).toBeNull();
    expect(main().querySelector('[data-report-class-cards]')).toBeNull();
    expect(text('[data-report-cash] [data-figure="revenue"] dd')).toBe(
      '$700.00',
    );
    expect(text('[data-report-cash] [data-figure="payroll"] dd')).toBe(
      '$900.00',
    );
    expect(text('[data-report-cash] [data-figure="expenses"] dd')).toBe(
      '$2,900.00',
    );
    expect(text('[data-report-cash] [data-figure="net"] dd')).toBe(
      '-$3,100.00',
    );
    expect(text('[data-report-note="cash"]')).toContain('Cash basis');
    expect(main().querySelector('[data-report-note="accrual"]')).toBeNull();
    expect(main().querySelector('[data-report-net]')).toBeNull();
  });

  it('shows every row at $0.00 and "No expenses" for an empty range', async () => {
    stub(OWNER, { [PROFITABILITY]: report(EMPTY_DAY) });
    await open('/reports/finance?from=2026-12-09&to=2026-12-09');
    await loaded();
    for (const type of ['individual', 'group', 'course', 'unallocated']) {
      expect(rowValues(type)).toEqual(['$0.00', '$0.00', '$0.00']);
    }
    expect(main().querySelector('[data-negative]')).toBeNull();
    expect(text('[data-report-expenses-empty]')).toBe(
      'No expenses in this period',
    );
  });

  it('renders the class rows as cards below sm, the table from sm up', async () => {
    stub();
    await open('/reports/finance?from=2026-11-01&to=2026-11-30');
    await loaded();
    const table = main().querySelector('[data-report-class-table]')!;
    const cards = main().querySelector('[data-report-class-cards]')!;
    expect(table.className).toContain('hidden');
    expect(table.className).toContain('sm:block');
    expect(cards.className).toContain('sm:hidden');
    expect(
      [...cards.querySelectorAll('[data-class-card]')].map((card) =>
        card.getAttribute('data-class-card'),
      ),
    ).toEqual(['individual', 'group', 'course', 'unallocated', 'total']);
    expect(
      cards.querySelector('[data-class-card="unallocated"]')?.textContent,
    ).toContain('-$2,950.00');
  });
});

describe('the balances (C1, C2)', () => {
  it('shows the three cards as of the API today, with both links', async () => {
    stub();
    await open();
    await loaded();
    const card = (name: string) =>
      main().querySelector(`[data-balance-card="${name}"]`)!;
    for (const name of ['receivables', 'payables', 'deferred']) {
      expect(
        card(name).querySelector('[data-balance-as-of]')?.textContent,
      ).toBe('as of Dec 10, 2026');
    }
    const figure = (name: string, key: string) =>
      card(name).querySelector(`[data-figure="${key}"] dd`)?.textContent;
    expect(figure('receivables', 'outstanding')).toBe('$200.00');
    expect(figure('receivables', 'overdue')).toBe('$0.00');
    expect(figure('receivables', 'credit')).toBe('$50.00');
    expect(figure('receivables', 'accounts')).toBe('1');
    expect(card('receivables').textContent).toContain(
      'Account credit (not deducted)',
    );
    expect(figure('payables', 'expenses-open')).toBe('$1,250.00');
    expect(figure('payables', 'expenses-overdue')).toBe('$500.00');
    expect(figure('payables', 'payroll-unpaid')).toBe('$3,830.00');
    expect(card('payables').textContent).toContain('(2 periods)');
    expect(figure('payables', 'payables-total')).toBe('$5,080.00');
    expect(figure('deferred', 'packages')).toBe('$600.00');
    expect(figure('deferred', 'scheduled-sessions')).toBe('$350.00');
    expect(figure('deferred', 'deferred-total')).toBe('$950.00');
    expect(
      card('receivables')
        .querySelector('[data-balance-link]')
        ?.getAttribute('href'),
    ).toBe('/billing/overdue');
    expect(
      card('payables')
        .querySelector('[data-balance-link]')
        ?.getAttribute('href'),
    ).toBe('/expenses/records?status=overdue');
    // No unpaid filter on the payroll periods: no payroll link.
    expect(main().querySelectorAll('[data-balance-link]')).toHaveLength(2);
  });

  it.each([
    ['without billing.view', FINANCE, [false, true]],
    ['with neither target permission', REPORTS_ONLY, [false, false]],
  ] as const)(
    'hides the links the viewer may not open (%s)',
    async (_who, as, shown) => {
      stub(as);
      await open();
      await loaded();
      expect(
        main().querySelector('[data-balance-link="receivables"]') !== null,
      ).toBe(shown[0]);
      expect(
        main().querySelector('[data-balance-link="payables"]') !== null,
      ).toBe(shown[1]);
    },
  );
});

describe('states (B6)', () => {
  it('shows the loading skeleton while the reports load', async () => {
    stub(OWNER, {
      [PROFITABILITY]: () => new Promise<Response>(() => {}),
      [BALANCES_ROUTE]: () => new Promise<Response>(() => {}),
    });
    await open();
    expect(
      main().querySelectorAll('[data-slot="loading-state"][aria-busy="true"]'),
    ).toHaveLength(2);
  });

  it('shows an error with a retry that asks again', async () => {
    const api = stub(OWNER, {
      [PROFITABILITY]: [() => problem(404, 'not_found'), report()],
    });
    await open();
    await settleUntil(() =>
      main().querySelector('[data-report-profitability] [role="alert"]'),
    );
    await click(
      buttonNamed(
        main().querySelector('[data-report-profitability]')!,
        'Try again',
      ),
    );
    await settleUntil(() => main().querySelector('[data-report-basis-shown]'));
    expect(api.calls(PROFITABILITY)).toHaveLength(2);
  });

  it('keeps the figures after a failed refetch and never says it reloaded', async () => {
    const api = stub(OWNER, {
      [BALANCES_ROUTE]: [
        () => jsonResponse(200, BALANCES),
        () => problem(404, 'not_found'),
        () => problem(404, 'not_found'),
        () => jsonResponse(200, { ...BALANCES, today: '2026-12-10' }),
      ],
    });
    await open();
    await loaded();
    const balances = () => main().querySelector('[data-report-balances]')!;
    await app!.queryClient.refetchQueries({
      queryKey: ['reports', 'finance', 'balances'],
    });
    await settleUntil(() => alerts(balances()).length === 1);
    expect(alerts(balances())[0]).toContain(
      "We couldn't update these figures, so they may be out of date.",
    );
    expect(balances().querySelector('[data-balance-card]')).not.toBeNull();
    // "Load again" fails again: the alert stays, nothing claims a reload.
    await click(buttonNamed(balances(), 'Load again'));
    await settleUntil(() => api.calls(BALANCES_ROUTE).length === 3);
    await settle();
    expect(alerts(balances())).toHaveLength(1);
    expect(balances().textContent).not.toMatch(/reloaded|updated/i);
    // It lands: the alert goes.
    await click(buttonNamed(balances(), 'Load again'));
    await settleUntil(() => alerts(balances()).length === 0);
    expect(balances().querySelector('[data-balance-card]')).not.toBeNull();
  });
});
