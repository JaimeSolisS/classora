import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
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
import {
  KPI_FINANCE_ACCESS,
  KPI_OPERATIONS_ACCESS,
  KPI_PAGE_ACCESS,
  KPI_TUTOR_ACCESS,
} from '../access';
import {
  COURSES,
  COURSES_EMPTY,
  KPI_BALANCES,
  KPI_BALANCES_EMPTY,
  RETENTION,
  RETENTION_EMPTY,
  ROOMS,
  ROOMS_EDGES,
  ROOMS_EMPTY,
  TREND_ACCRUAL,
  TREND_CASH,
  TREND_EMPTY,
  TUTORS,
  TUTORS_EMPTY,
} from './fixtures';
import { isPartialMonth, kpiMonthLabel, monthBounds } from './format';

// M8-09b E1: the KPI page per role (which sections show and which routes
// are asked), the redirect, the range and basis in the URL, the server's
// range refusal, each section from #126's worked examples B1–B5 (the same
// figures as _docs/kpis.md), null percentages, partial months, and the
// empty, error, loading and 403 states.

const user = (
  roles: SessionUserBody['roles'],
  permissions: SessionUserBody['permissions'],
): SessionUserBody => ({ ...TEST_USER, roles, permissions });

const OWNER = user(
  ['owner'],
  [
    'finance.report.view',
    'schedule.view',
    'tutor.view',
    'billing.view',
    'expense.view',
  ],
);
const FINANCE = user(
  ['finance_staff'],
  ['finance.report.view', 'schedule.view', 'expense.view'],
);
const ADMIN = user(
  ['administrator'],
  ['user.view', 'schedule.view', 'tutor.view', 'expense.configure'],
);
const ADMIN_FINALIZE = user(
  ['administrator'],
  [
    'user.view',
    'schedule.view',
    'tutor.view',
    'expense.configure',
    'payroll.finalize',
  ],
);
const COORDINATOR = user(
  ['academic_coordinator'],
  ['schedule.view', 'tutor.view'],
);
const COORDINATOR_FINANCE = user(
  ['academic_coordinator', 'finance_staff'],
  ['schedule.view', 'tutor.view', 'finance.report.view'],
);
const TUTOR = user(['tutor'], ['tutor.view']);
const STUDENT = user(['student'], ['student.view']);
const GUARDIAN = user(['guardian'], ['student.view', 'guardian.view']);

const R = {
  trend: 'GET /api/v1/reports/kpis/revenue-trend',
  courses: 'GET /api/v1/reports/kpis/course-profitability',
  rooms: 'GET /api/v1/reports/kpis/room-utilization',
  retention: 'GET /api/v1/reports/kpis/student-retention',
  tutors: 'GET /api/v1/reports/kpis/tutor-performance',
  balances: 'GET /api/v1/reports/finance/balances',
} as const;

/** Section codes in page order. */
const ALL = [
  'revenue-trend',
  'balances',
  'course-profitability',
  'room-utilization',
  'student-retention',
  'tutor-performance',
];

let app: RenderedApp | undefined;
let asked: Record<string, URLSearchParams[]> = {};
const main = () => app!.container.querySelector('main')!;
const h1 = () => main().querySelector('h1')?.textContent;
const search = () => new URLSearchParams(app!.router.state.location.search);
const section = (code: string) =>
  main().querySelector<HTMLElement>(`[data-kpi-section="${code}"]`);
const sections = () =>
  [...main().querySelectorAll('[data-kpi-section]')].map((node) =>
    node.getAttribute('data-kpi-section'),
  );

/** Answers with `body`, recording the query. */
const answer =
  (route: string, body: unknown): Responder =>
  (request) => {
    (asked[route] ??= []).push(new URL(request.url).searchParams);
    return jsonResponse(200, body);
  };

function stub(
  as: SessionUserBody = OWNER,
  routes: Record<string, Responder | Responder[]> = {},
): ApiStub {
  return stubApi({
    'GET /api/v1/auth/me': meAs(as),
    [R.trend]: answer(R.trend, TREND_ACCRUAL),
    [R.courses]: answer(R.courses, COURSES),
    [R.rooms]: answer(R.rooms, ROOMS),
    [R.retention]: answer(R.retention, RETENTION),
    [R.tutors]: answer(R.tutors, TUTORS),
    [R.balances]: answer(R.balances, KPI_BALANCES),
    ...routes,
  });
}

async function open(path = '/reports/kpis') {
  app = await renderApp(path);
  await settleUntil(() => main().querySelector('h1'));
}

/** Every listed section has left its loading state. */
const ready = (codes: readonly string[]) =>
  settleUntil(() =>
    codes.every(
      (code) => section(code)?.getAttribute('data-kpi-status') === 'ready',
    ),
  );

/** The table rows' cells (header first) of a section, from `sm` up. */
const tableRows = (code: string) =>
  [
    ...main().querySelectorAll(`[data-kpi-table="${code}"] tr[data-kpi-row]`),
  ].map((row) =>
    [...row.querySelectorAll('th, td')].map((cell) => cell.textContent),
  );

beforeEach(() => {
  applyLanguage('en');
  asked = {};
  vi.useFakeTimers({ toFake: ['Date'] });
  // 12:00 on 2031-04-02 in Mexico City: the fixtures' today.
  vi.setSystemTime(new Date('2031-04-02T18:00:00Z'));
  // Recharts warns that a ResponsiveContainer has no size in jsdom.
  vi.spyOn(console, 'warn').mockImplementation(() => {});
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('rules', () => {
  it('finds partial months and labels months in both languages', () => {
    expect(monthBounds('2031-02')).toEqual({
      from: '2031-02-01',
      to: '2031-02-28',
    });
    expect(
      isPartialMonth({
        month: '2031-01',
        from: '2031-01-15',
        to: '2031-01-31',
      }),
    ).toBe(true);
    expect(
      isPartialMonth({
        month: '2031-02',
        from: '2031-02-01',
        to: '2031-02-28',
      }),
    ).toBe(false);
    expect(
      isPartialMonth({
        month: '2031-03',
        from: '2031-03-01',
        to: '2031-03-10',
      }),
    ).toBe(true);
    expect(kpiMonthLabel('2031-03', 'en')).toBe('Mar 2031');
    expect(kpiMonthLabel('2031-03', 'es')).toBe('mar 2031');
  });
});

describe('access (A1)', () => {
  it('requires both schedule.view and tutor.view for tutor performance', () => {
    expect(canAccess(COORDINATOR, KPI_TUTOR_ACCESS)).toBe(true);
    expect(canAccess(TUTOR, KPI_TUTOR_ACCESS)).toBe(false);
    expect(canAccess(FINANCE, KPI_TUTOR_ACCESS)).toBe(false);
    expect(
      canAccess(user(['administrator'], ['tutor.view']), KPI_TUTOR_ACCESS),
    ).toBe(false);
  });

  it.each([
    ['the Owner', OWNER, [true, true, true, true]],
    ['Finance Staff', FINANCE, [true, true, true, false]],
    ['an Administrator', ADMIN, [true, false, true, true]],
    ['a Coordinator', COORDINATOR, [true, false, true, true]],
    ['a tutor', TUTOR, [false, false, false, false]],
    ['a student', STUDENT, [false, false, false, false]],
    ['a guardian', GUARDIAN, [false, false, false, false]],
  ] as const)('%s: page, finance, operations, tutors', (_who, as, expected) => {
    expect(
      [
        KPI_PAGE_ACCESS,
        KPI_FINANCE_ACCESS,
        KPI_OPERATIONS_ACCESS,
        KPI_TUTOR_ACCESS,
      ].map((requirement) => canAccess(as, requirement)),
    ).toEqual(expected);
  });
});

describe('sections per role (E2, #126 E3)', () => {
  it.each([
    ['the Owner', OWNER, ALL],
    [
      'Finance Staff',
      FINANCE,
      [
        'revenue-trend',
        'balances',
        'course-profitability',
        'room-utilization',
        'student-retention',
      ],
    ],
    [
      'an Administrator',
      ADMIN,
      ['room-utilization', 'student-retention', 'tutor-performance'],
    ],
    [
      'an Administrator with payroll.finalize',
      ADMIN_FINALIZE,
      ['room-utilization', 'student-retention', 'tutor-performance'],
    ],
    [
      'a Coordinator',
      COORDINATOR,
      ['room-utilization', 'student-retention', 'tutor-performance'],
    ],
    ['a Coordinator who is also Finance', COORDINATOR_FINANCE, ALL],
  ] as const)(
    '%s sees exactly its sections, asking only their routes',
    async (_who, as, expected) => {
      const api = stub(as);
      await open();
      await ready(expected);
      await settle();
      expect(sections()).toEqual(expected);
      const routeOf: Record<string, string> = {
        'revenue-trend': R.trend,
        balances: R.balances,
        'course-profitability': R.courses,
        'room-utilization': R.rooms,
        'student-retention': R.retention,
        'tutor-performance': R.tutors,
      };
      for (const code of ALL) {
        const calls = api.calls(routeOf[code]!);
        // One request per section, shared by the page and the section.
        expect(calls, code).toHaveLength(
          (expected as readonly string[]).includes(code) ? 1 : 0,
        );
      }
    },
  );

  it.each([
    ['a tutor', TUTOR],
    ['a student', STUDENT],
    ['a guardian', GUARDIAN],
  ] as const)('%s is forbidden with no report request', async (_who, as) => {
    for (const path of ['/reports', '/reports/kpis']) {
      const api = stub(as);
      await open(path);
      expect(h1()).toBe("You don't have access to this page");
      expect(
        api.requests.filter((r) => r.path.startsWith('/api/v1/reports')),
      ).toEqual([]);
      app!.unmount();
      app = undefined;
    }
  });

  it('/reports opens the KPIs', async () => {
    stub();
    await open('/reports');
    await settleUntil(() => h1() === 'KPIs');
    expect(app!.router.state.location.pathname).toBe('/reports/kpis');
    expect(document.title).toContain('KPIs');
  });

  it('shows both sub-navigation links to Finance, KPIs current', async () => {
    applyLanguage('es');
    stub(FINANCE);
    await open();
    const links = [
      ...main().querySelectorAll<HTMLAnchorElement>('[data-reports-subnav] a'),
    ];
    expect(links.map((a) => [a.getAttribute('href'), a.textContent])).toEqual([
      ['/reports/kpis', 'Indicadores'],
      ['/reports/finance', 'Reporte financiero'],
    ]);
    expect(links[0]!.getAttribute('aria-current')).toBe('page');
    expect(h1()).toBe('Indicadores');
  });

  it.each([
    ['an Administrator', ADMIN],
    ['an Administrator with payroll.finalize', ADMIN_FINALIZE],
    ['a Coordinator', COORDINATOR],
  ] as const)(
    '%s gets no financial-report link and no money section',
    async (_who, as) => {
      stub(as);
      await open();
      await ready(['room-utilization']);
      expect(main().querySelector('[data-reports-subnav]')).toBeNull();
      expect(main().querySelector('a[href="/reports/finance"]')).toBeNull();
      expect(main().querySelector('[data-money]')).toBeNull();
    },
  );

  it('an Administrator opening /reports/finance gets the forbidden page', async () => {
    const api = stub(ADMIN);
    await open('/reports/finance');
    expect(h1()).toBe("You don't have access to this page");
    expect(
      api.requests.filter((r) => r.path.startsWith('/api/v1/reports')),
    ).toEqual([]);
  });
});

describe('range and basis (B1, B2)', () => {
  it('asks for the last 3 months by default, on the accrual basis', async () => {
    stub();
    await open();
    await ready(ALL);
    // 90 days ending on the center's today, both included.
    for (const route of [R.courses, R.rooms, R.retention, R.tutors]) {
      expect(
        asked[route]?.map((q) => q.toString()),
        route,
      ).toEqual(['from=2031-01-03&to=2031-04-02']);
    }
    expect(asked[R.trend]?.map((q) => q.toString())).toEqual([
      'basis=accrual&from=2031-01-03&to=2031-04-02',
    ]);
    expect(
      buttonNamed(main(), 'Last 3 months').getAttribute('aria-pressed'),
    ).toBe('true');
    expect(labelled<HTMLInputElement>(main(), 'From').value).toBe('2031-01-03');
    // The basis switch sits on K1 only.
    expect(main().querySelectorAll('[data-report-basis]')).toHaveLength(1);
    expect(
      section('revenue-trend')!.querySelector('[data-report-basis]'),
    ).not.toBeNull();
    expect(main().querySelector('[data-kpi-range]')?.textContent).toBe(
      'KPIs from Jan 3, 2031 to Apr 2, 2031',
    );
  });

  it("re-anchors the default range on the API's today", async () => {
    // The browser is already in April; the center's today is Mar 31.
    stub(OWNER, {
      [R.balances]: answer(R.balances, {
        ...KPI_BALANCES,
        today: '2031-03-31',
      }),
    });
    await open();
    await settleUntil(() =>
      asked[R.rooms]?.some((q) => q.get('to') === '2031-03-31'),
    );
    expect(asked[R.rooms]?.at(-1)?.toString()).toBe(
      'from=2031-01-01&to=2031-03-31',
    );
    expect(labelled<HTMLInputElement>(main(), 'To').max).toBe('2031-03-31');
  });

  it('writes a preset and the basis to the URL; only K1 asks again for a basis', async () => {
    stub(OWNER, {
      [R.trend]: [
        answer(R.trend, TREND_ACCRUAL),
        answer(R.trend, TREND_ACCRUAL),
        answer(R.trend, TREND_CASH),
      ],
    });
    await open();
    await ready(ALL);
    await click(buttonNamed(main(), 'Last month'));
    expect(search().toString()).toBe('from=2031-03-01&to=2031-03-31');
    await ready(ALL);
    expect(asked[R.rooms]?.at(-1)?.toString()).toBe(
      'from=2031-03-01&to=2031-03-31',
    );
    expect(main().querySelector('[data-kpi-range]')?.textContent).toBe(
      'KPIs from Mar 1, 2031 to Mar 31, 2031',
    );
    const roomRequests = asked[R.rooms]!.length;
    await click(buttonNamed(section('revenue-trend')!, 'Cash'));
    expect(search().toString()).toBe(
      'from=2031-03-01&to=2031-03-31&basis=cash',
    );
    await settleUntil(
      () =>
        section('revenue-trend')
          ?.querySelector('[data-kpi-basis-shown]')
          ?.getAttribute('data-kpi-basis-shown') === 'cash',
    );
    expect(asked[R.trend]?.at(-1)?.toString()).toBe(
      'basis=cash&from=2031-03-01&to=2031-03-31',
    );
    expect(asked[R.rooms]).toHaveLength(roomRequests);
    expect(
      buttonNamed(section('revenue-trend')!, 'Cash').getAttribute(
        'aria-pressed',
      ),
    ).toBe('true');
  });

  it('writes a typed date after a pause, keeping a basis switched meanwhile', async () => {
    stub();
    await open('/reports/kpis?from=2031-03-01&to=2031-03-31');
    await ready(ALL);
    setFieldValue(labelled(main(), 'From'), '2031-02-15');
    expect(search().get('from')).toBe('2031-03-01');
    await click(buttonNamed(section('revenue-trend')!, 'Cash'));
    await settleUntil(() => search().get('from') === '2031-02-15');
    expect(search().get('basis')).toBe('cash');
    expect(search().get('to')).toBe('2031-03-31');
    await settleUntil(
      () =>
        asked[R.trend]?.at(-1)?.toString() ===
        'basis=cash&from=2031-02-15&to=2031-03-31',
    );
  });

  it('a range over 366 days asks nothing and shows the bound', async () => {
    stub();
    await open('/reports/kpis?from=2030-03-01&to=2031-04-01');
    await settle();
    expect(asked[R.rooms]).toBeUndefined();
    expect(asked[R.trend]).toBeUndefined();
    const from = labelled<HTMLInputElement>(main(), 'From');
    expect(from.getAttribute('aria-invalid')).toBe('true');
    expect(
      document.getElementById(from.getAttribute('aria-describedby')!)
        ?.textContent,
    ).toBe('366 days at most');
    // Only today's balances stay (they don't depend on the range).
    expect(sections()).toEqual(['balances']);
  });

  it('shows a server 422 report_range_invalid next to the field it names', async () => {
    const refusal = () =>
      problemWith(422, 'report_range_invalid', {
        errors: [
          {
            path: ['query', 'from'],
            code: 'report_range_invalid',
            message: 'The range is longer than 366 days.',
          },
        ],
      });
    stub(ADMIN, {
      [R.rooms]: refusal,
      [R.retention]: refusal,
      [R.tutors]: refusal,
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
    expect(alerts(main())).toEqual([]);
  });
});

describe('K1 revenue trend (C1, #126 B1)', () => {
  it('renders the accrual example with partial months, totals and the note', async () => {
    stub(OWNER);
    await open('/reports/kpis?from=2031-01-15&to=2031-03-10');
    await ready(['revenue-trend']);
    expect(tableRows('revenue-trend')).toEqual([
      [
        'Jan 2031 (partial) from Jan 15, 2031 to Jan 31, 2031',
        '$400.00',
        '$0.00',
        '$400.00',
        '$0.00',
        '$400.00',
      ],
      ['Feb 2031', '$400.00', '$250.00', '$150.00', '$1,000.00', '-$850.00'],
      [
        'Mar 2031 (partial) from Mar 1, 2031 to Mar 10, 2031',
        '$0.00',
        '$0.00',
        '$0.00',
        '$0.00',
        '$0.00',
      ],
      ['Totals', '$800.00', '$250.00', '$550.00', '$1,000.00', '-$450.00'],
    ]);
    const k1 = section('revenue-trend')!;
    expect(
      k1.querySelector('[data-partial-month="2031-01"]')?.getAttribute('title'),
    ).toBe('from Jan 15, 2031 to Jan 31, 2031');
    // A negative amount: the minus sign and an icon, not colour alone.
    const negative = k1.querySelector(
      'tr[data-kpi-row="2031-02"] [data-negative]',
    );
    expect(negative?.querySelector('svg')).not.toBeNull();
    expect(
      k1.querySelector('[data-kpi-row="totals"]')?.closest('tfoot'),
    ).not.toBeNull();
    expect(
      k1.querySelector('[data-kpi-note="accrual"]')?.textContent,
    ).toContain('Revenue is recognized when the class is taught');
    // The chart's text alternative: a visually hidden table.
    const chartTable = k1.querySelector(
      '[data-kpi-chart="revenue-trend"] table',
    )!;
    expect(chartTable.parentElement!.className).toBe('sr-only');
    expect(chartTable.querySelector('caption')?.textContent).toBe(
      'Data of the revenue and result by month chart',
    );
    expect(
      [...chartTable.querySelectorAll('tbody tr')].map(
        (row) => row.textContent,
      ),
    ).toEqual([
      'Jan 2031$400.00$0.00$0.00$400.00',
      'Feb 2031$400.00$250.00$1,000.00-$850.00',
      'Mar 2031$0.00$0.00$0.00$0.00',
    ]);
  });

  it('renders the cash example with no accrual note', async () => {
    stub(OWNER, { [R.trend]: answer(R.trend, TREND_CASH) });
    await open('/reports/kpis?basis=cash&from=2031-01-15&to=2031-03-10');
    await ready(['revenue-trend']);
    expect(tableRows('revenue-trend').map((row) => row.slice(1))).toEqual([
      ['$400.00', '$0.00', '$400.00', '$0.00', '$400.00'],
      ['$0.00', '$0.00', '$0.00', '$1,000.00', '-$1,000.00'],
      ['$300.00', '$250.00', '$50.00', '$0.00', '$50.00'],
      ['$700.00', '$250.00', '$450.00', '$1,000.00', '-$550.00'],
    ]);
    expect(
      section('revenue-trend')!.querySelector('[data-kpi-note]'),
    ).toBeNull();
  });

  it('labels the months in Spanish', async () => {
    applyLanguage('es');
    stub(OWNER);
    await open('/reports/kpis?from=2031-01-15&to=2031-03-10');
    await ready(['revenue-trend']);
    expect(tableRows('revenue-trend')[1]![0]).toBe('feb 2031');
    expect(tableRows('revenue-trend')[0]![0]).toContain('ene 2031 (parcial)');
  });
});

describe('K3 balances (C2)', () => {
  it('shows unpaid payroll prominently, as of today, with the balance cards', async () => {
    stub(OWNER);
    await open();
    await ready(['balances']);
    const k3 = section('balances')!;
    expect(k3.textContent).toContain('As of today');
    const payroll = k3.querySelector('[data-kpi-payroll-unpaid]')!;
    expect(payroll.textContent).toBe('Unpaid payroll$3,830.002 periods');
    expect(
      [...k3.querySelectorAll('[data-balance-card]')].map((card) =>
        card.getAttribute('data-balance-card'),
      ),
    ).toEqual(['receivables', 'payables', 'deferred']);
    expect(k3.querySelectorAll('[data-balance-link]')).toHaveLength(2);
  });
});

describe('K2 course profitability (C3, #126 B2)', () => {
  it("lists the cohorts in the API's order, the unattributed row and the total", async () => {
    stub(OWNER);
    await open('/reports/kpis?from=2031-03-01&to=2031-03-31');
    await ready(['course-profitability']);
    expect(tableRows('course-profitability')).toEqual([
      ['C2', '$666.67', '$0.00', '$666.67'],
      ['C1', '$1,000.00', '$750.00', '$250.00'],
      ['No cohort assigned', '$800.00', '$0.00', '$800.00'],
      ['Total', '$2,466.67', '$750.00', '$1,716.67'],
    ]);
    expect(
      section('course-profitability')!.querySelector('[data-kpi-note]')
        ?.textContent,
    ).toBe(
      "Accrual basis only; no overhead (spec). The total matches the financial report's “Courses” row.",
    );
  });

  it('has no unattributed row when it is zero', async () => {
    stub(OWNER, {
      [R.courses]: answer(R.courses, {
        ...COURSES,
        not_attributed: {
          revenue: '0.00',
          direct_tutor_cost: '0.00',
          gross_profit: '0.00',
        },
      }),
    });
    await open();
    await ready(['course-profitability']);
    expect(tableRows('course-profitability').map((row) => row[0])).toEqual([
      'C2',
      'C1',
      'Total',
    ]);
  });
});

describe('K4 room utilization (C4, #126 B3)', () => {
  it('renders rooms A and B and the total', async () => {
    stub(ADMIN);
    await open('/reports/kpis?from=2031-03-03&to=2031-03-09');
    await ready(['room-utilization']);
    expect(tableRows('room-utilization')).toEqual([
      ['A', '3,180', '210', '6.60%'],
      ['B', '300', '120', '40.00%'],
      ['Total', '3,480', '330', '9.48%'],
    ]);
    expect(
      section('room-utilization')!.querySelector('[data-kpi-note]'),
    ).toBeNull();
  });

  it('shows a null percent as a dash with screen-reader text, and the over-100 note', async () => {
    stub(ADMIN, { [R.rooms]: answer(R.rooms, ROOMS_EDGES) });
    await open();
    await ready(['room-utilization']);
    const k4 = section('room-utilization')!;
    const none = k4.querySelector(
      '[data-kpi-table] tr[data-kpi-row="12600000-0000-4000-8000-00000000000b"] [data-percent="none"]',
    )!;
    expect(none.querySelector('[aria-hidden="true"]')?.textContent).toBe('—');
    expect(none.querySelector('.sr-only')?.textContent).toBe(
      'No schedule available',
    );
    expect(tableRows('room-utilization')[0]).toEqual([
      'A',
      '60',
      '120',
      '200.00%',
    ]);
    expect(k4.querySelector('[data-kpi-note="over-100"]')?.textContent).toBe(
      "Includes classes outside the room's hours",
    );
  });

  it('formats minutes and percentages for Spanish', async () => {
    applyLanguage('es');
    stub(ADMIN);
    await open();
    await ready(['room-utilization']);
    expect(tableRows('room-utilization')[2]).toEqual([
      'Total',
      '3,480',
      '330',
      '9.48 %',
    ]);
  });
});

describe('K5 student retention (C5, #126 B4)', () => {
  it('shows the tiles, the previous window, the chart table and the note', async () => {
    stub(ADMIN);
    await open('/reports/kpis?from=2031-03-01&to=2031-03-31');
    await ready(['student-retention']);
    const k5 = section('student-retention')!;
    const tile = (key: string) =>
      k5.querySelector(`[data-kpi-tile="${key}"] dd`)?.textContent;
    expect(
      [
        'active_previous',
        'active_current',
        'retained',
        'new',
        'lost',
        'retention_percent',
      ].map(tile),
    ).toEqual(['5', '5', '2', '3', '3', '40.00%']);
    expect(k5.textContent).toContain(
      'Previous period: Jan 29, 2031 to Feb 28, 2031',
    );
    const chartTable = k5.querySelector('[data-kpi-chart] table')!;
    expect(chartTable.parentElement!.className).toBe('sr-only');
    expect(
      [...chartTable.querySelectorAll('tbody tr')].map(
        (row) => row.textContent,
      ),
    ).toEqual(['Mar 20315']);
    expect(k5.querySelector('[data-kpi-note="active"]')?.textContent).toBe(
      'A student is active with at least one confirmed or completed class in the period, whether they attended or not.',
    );
  });

  it('shows a null retention as a dash with screen-reader text', async () => {
    stub(ADMIN, {
      [R.retention]: answer(R.retention, {
        ...RETENTION,
        active_previous: 0,
        retained: 0,
        lost: 0,
        new: 5,
        retention_percent: null,
      }),
    });
    await open();
    await ready(['student-retention']);
    const percent = section('student-retention')!.querySelector(
      '[data-kpi-tile="retention_percent"] [data-percent="none"]',
    )!;
    expect(percent.textContent).toBe('—No activity in the previous period');
  });
});

describe('K6 tutor performance (C6, #126 B5)', () => {
  it('renders T1, T2 and the center row, hours with one decimal', async () => {
    stub(COORDINATOR);
    await open('/reports/kpis?from=2031-03-01&to=2031-03-31');
    await ready(['tutor-performance']);
    expect(tableRows('tutor-performance')).toEqual([
      ['T1', '3', '2', '1', '1', '3.5', '4', '75.00%'],
      ['T2', '1', '1', '0', '0', '1.0', '1', '100.00%'],
      ['Center', '4', '3', '1', '1', '4.5', '5', '80.00%'],
    ]);
    expect(
      section('tutor-performance')!.querySelector('[data-kpi-note]')
        ?.textContent,
    ).toBe(
      'Cancelled: only classes that had been confirmed. A substitution counts for the tutor who taught it.',
    );
    // No sorting control.
    expect(
      section('tutor-performance')!.querySelectorAll('th button, [aria-sort]'),
    ).toHaveLength(0);
  });
});

describe('states (C7, C8)', () => {
  it('shows a skeleton per section while loading', async () => {
    const pending = () => new Promise<Response>(() => {});
    stub(OWNER, Object.fromEntries(Object.values(R).map((r) => [r, pending])));
    await open();
    await settle();
    expect(
      main().querySelectorAll(
        '[data-kpi-section] [data-slot="loading-state"][aria-busy="true"]',
      ),
    ).toHaveLength(6);
  });

  it('shows "No activity in this period" for an all-zero section', async () => {
    stub(OWNER, {
      [R.trend]: answer(R.trend, TREND_EMPTY),
      [R.courses]: answer(R.courses, COURSES_EMPTY),
      [R.rooms]: answer(R.rooms, ROOMS_EMPTY),
      [R.retention]: answer(R.retention, RETENTION_EMPTY),
      [R.tutors]: answer(R.tutors, TUTORS_EMPTY),
      [R.balances]: answer(R.balances, KPI_BALANCES_EMPTY),
    });
    await open();
    await ready(ALL);
    for (const code of ALL) {
      expect(
        section(code)!.querySelector('[data-kpi-empty]')?.textContent,
        code,
      ).toBe('No activity in this period');
      expect(section(code)!.querySelector('[data-kpi-table]'), code).toBeNull();
    }
    // The basis switch stays on an empty K1.
    expect(
      section('revenue-trend')!.querySelector('[data-report-basis]'),
    ).not.toBeNull();
  });

  it("one section's error has its own retry; the others stay", async () => {
    const api = stub(ADMIN, {
      [R.rooms]: [() => problem(404, 'not_found'), answer(R.rooms, ROOMS)],
    });
    await open();
    await settleUntil(() =>
      section('room-utilization')?.querySelector('[role="alert"]'),
    );
    await ready(['student-retention', 'tutor-performance']);
    expect(alerts(section('student-retention')!)).toEqual([]);
    await click(buttonNamed(section('room-utilization')!, 'Try again'));
    await ready(['room-utilization']);
    expect(api.calls(R.rooms)).toHaveLength(2);
  });

  it('a 403 on one section hides it and keeps the page', async () => {
    stub(OWNER, { [R.tutors]: () => problem(403, 'permission_denied') });
    await open();
    await ready(ALL.filter((code) => code !== 'tutor-performance'));
    await settleUntil(() => section('tutor-performance') === null);
    expect(sections()).toEqual(ALL.slice(0, 5));
    expect(h1()).toBe('KPIs');
    expect(alerts(main())).toEqual([]);
  });

  it('renders the tables as cards below sm', async () => {
    stub(OWNER);
    await open();
    await ready(ALL);
    for (const code of [
      'revenue-trend',
      'course-profitability',
      'room-utilization',
      'tutor-performance',
    ]) {
      const table = main().querySelector(`[data-kpi-table="${code}"]`)!;
      const cards = main().querySelector(`[data-kpi-cards="${code}"]`)!;
      expect(table.className, code).toContain('hidden');
      expect(table.className, code).toContain('sm:block');
      expect(cards.className, code).toContain('sm:hidden');
    }
    const cards = main().querySelector('[data-kpi-cards="tutor-performance"]')!;
    expect(
      [...cards.querySelectorAll('[data-kpi-card]')].map((card) =>
        card.getAttribute('data-kpi-card'),
      ),
    ).toEqual([
      '12600000-0000-4000-8000-000000000101',
      '12600000-0000-4000-8000-000000000102',
      'center',
    ]);
    expect(
      cards.querySelector('[data-kpi-card="center"]')?.textContent,
    ).toContain('Hours4.5');
  });
});
