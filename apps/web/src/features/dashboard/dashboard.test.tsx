import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { problemWith } from '@/features/academics/testing';
import { canAccess } from '@/features/auth/access';
import type { SessionUserBody } from '@/features/auth/session';
import {
  COURSES,
  KPI_BALANCES,
  RETENTION,
  ROOMS,
  TREND_ACCRUAL,
  TUTORS,
} from '@/features/reporting/kpis/fixtures';
import { applyLanguage } from '@/i18n';
import {
  meAs,
  problem,
  stubApi,
  TEST_USER,
  type ApiStub,
  type Responder,
} from '@/test/api-stub';
import { alerts, click, openDialog, pressEscape } from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  settle,
  settleUntil,
  type RenderedApp,
} from '@/test/render-app';
import { DASHBOARD_ACCESS } from './access';
import type { DashboardLayout, LayoutEntry, WidgetCode } from './api';
import { defaultLayout, WIDGETS } from './widgets';

// M8-10 (#127) D–F: the home page dashboard per role (the E3 defaults),
// the layout's order and visibility, a layout that lists a widget the
// user's permissions don't pass (F3), a 403 widget, the layout states (D5),
// the old home page for roles without widgets, and the "Customize" dialog
// (the buttons, the checkbox, Save's exact body, Cancel, Restore default
// and the 422 branch). jsdom can't drive dnd-kit's dragging; Playwright
// covers its keyboard path.

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

const LAYOUT = '/api/v1/reports/dashboard/layout';
const R: Record<WidgetCode, string> = {
  revenue_trend: 'GET /api/v1/reports/kpis/revenue-trend',
  balances: 'GET /api/v1/reports/finance/balances',
  course_profitability: 'GET /api/v1/reports/kpis/course-profitability',
  room_utilization: 'GET /api/v1/reports/kpis/room-utilization',
  student_retention: 'GET /api/v1/reports/kpis/student-retention',
  tutor_performance: 'GET /api/v1/reports/kpis/tutor-performance',
};
const SECTION: Record<WidgetCode, string> = {
  revenue_trend: 'revenue-trend',
  balances: 'balances',
  course_profitability: 'course-profitability',
  room_utilization: 'room-utilization',
  student_retention: 'student-retention',
  tutor_performance: 'tutor-performance',
};
const ALL: WidgetCode[] = [
  'revenue_trend',
  'balances',
  'course_profitability',
  'room_utilization',
  'student_retention',
  'tutor_performance',
];
const OPERATIONS: WidgetCode[] = [
  'room_utilization',
  'student_retention',
  'tutor_performance',
];
const FINANCE_SET: WidgetCode[] = [
  'revenue_trend',
  'balances',
  'course_profitability',
  'room_utilization',
  'student_retention',
];
const TITLES: Record<WidgetCode, string> = {
  revenue_trend: 'Revenue and result by month',
  balances: 'Unpaid balances and payroll',
  course_profitability: 'Profitability by course',
  room_utilization: 'Room utilization',
  student_retention: 'Student retention',
  tutor_performance: 'Tutor performance and center activity',
};

const layoutOf = (
  widgets: (WidgetCode | [WidgetCode, boolean])[],
  customized = false,
): DashboardLayout => ({
  widgets: widgets.map((entry) =>
    typeof entry === 'string'
      ? { widget: entry, visible: true }
      : { widget: entry[0], visible: entry[1] },
  ),
  customized,
});

let app: RenderedApp | undefined;
const main = () => app!.container.querySelector('main')!;
const h1 = () => main().querySelector('h1')?.textContent;
const sections = () =>
  [...main().querySelectorAll('[data-kpi-section]')].map((node) =>
    node.getAttribute('data-kpi-section'),
  );
const ready = (codes: readonly WidgetCode[]) =>
  settleUntil(() =>
    codes.every(
      (code) =>
        main()
          .querySelector(`[data-kpi-section="${SECTION[code]}"]`)
          ?.getAttribute('data-kpi-status') === 'ready',
    ),
  );

function stub(
  as: SessionUserBody,
  layout: Responder | Responder[],
  routes: Record<string, Responder | Responder[]> = {},
): ApiStub {
  return stubApi({
    'GET /api/v1/auth/me': meAs(as),
    [`GET ${LAYOUT}`]: layout,
    [R.revenue_trend]: () => jsonResponse(200, TREND_ACCRUAL),
    [R.course_profitability]: () => jsonResponse(200, COURSES),
    [R.room_utilization]: () => jsonResponse(200, ROOMS),
    [R.student_retention]: () => jsonResponse(200, RETENTION),
    [R.tutor_performance]: () => jsonResponse(200, TUTORS),
    [R.balances]: () => jsonResponse(200, KPI_BALANCES),
    'GET /health/live': () => jsonResponse(200, { status: 'ok' }),
    ...routes,
  });
}

const answer = (body: DashboardLayout): Responder => {
  return () => jsonResponse(200, body);
};

async function open() {
  app = await renderApp('/');
  await settleUntil(() => main().querySelector('h1'));
}

function reportRequests(api: ApiStub) {
  return api.requests.filter((r) => r.path.startsWith('/api/v1/reports'));
}

beforeEach(() => {
  applyLanguage('en');
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

describe('the web registry (D2)', () => {
  it('lists the six widgets in the API order with the KPI page requirements', () => {
    expect(WIDGETS.map((widget) => widget.code)).toEqual(ALL);
    expect(canAccess(OWNER, DASHBOARD_ACCESS)).toBe(true);
    expect(canAccess(FINANCE, DASHBOARD_ACCESS)).toBe(true);
    expect(canAccess(ADMIN, DASHBOARD_ACCESS)).toBe(true);
    expect(canAccess(TUTOR, DASHBOARD_ACCESS)).toBe(false);
  });

  it.each([
    ['the Owner', OWNER, ALL],
    ['Finance Staff', FINANCE, FINANCE_SET],
    ['an Administrator', ADMIN, OPERATIONS],
    ['a Coordinator', COORDINATOR, OPERATIONS],
    ['a Coordinator who is also Finance', COORDINATOR_FINANCE, ALL],
    ['a tutor', TUTOR, []],
  ] as const)('the default for %s is the E3 set', (_who, as, expected) => {
    expect(defaultLayout(as).map((entry: LayoutEntry) => entry.widget)).toEqual(
      expected,
    );
  });
});

describe('the dashboard per role (D1, D3, E3)', () => {
  it.each([
    ['the Owner', OWNER, ALL],
    ['Finance Staff', FINANCE, FINANCE_SET],
    ['an Administrator', ADMIN, OPERATIONS],
    ['a Coordinator', COORDINATOR, OPERATIONS],
  ] as const)(
    '%s sees its default widgets, asking each route once',
    async (_who, as, expected) => {
      const api = stub(as, answer(layoutOf([...expected])));
      await open();
      await ready(expected);
      await settle();

      expect(h1()).toBe('Dashboard');
      // The home page keeps the bare brand title (useDocumentTitle).
      expect(document.title).toBe('Classora');
      expect(sections()).toEqual(expected.map((code) => SECTION[code]));
      expect(
        [...main().querySelectorAll('h2')].map((node) => node.textContent),
      ).toEqual(expected.map((code) => TITLES[code]));
      expect(api.calls(`GET ${LAYOUT}`)).toHaveLength(1);
      for (const code of ALL) {
        expect(api.calls(R[code]), code).toHaveLength(
          (expected as readonly string[]).includes(code) ? 1 : 0,
        );
      }
      // The range is the KPI page's default ("Last 3 months").
      const [rooms] = api.calls(R.room_utilization);
      expect(rooms).toBeDefined();
      expect(buttonNamed(main(), 'Customize')).toBeTruthy();
    },
  );

  it('shows the visible widgets in the layout order; a hidden one asks nothing', async () => {
    const api = stub(
      OWNER,
      answer(
        layoutOf(
          [
            'tutor_performance',
            ['revenue_trend', false],
            'balances',
            ['course_profitability', false],
            'student_retention',
            'room_utilization',
          ],
          true,
        ),
      ),
    );
    await open();
    const shown: WidgetCode[] = [
      'tutor_performance',
      'balances',
      'student_retention',
      'room_utilization',
    ];
    await ready(shown);
    await settle();

    expect(sections()).toEqual(shown.map((code) => SECTION[code]));
    expect(api.calls(R.revenue_trend)).toHaveLength(0);
    expect(api.calls(R.course_profitability)).toHaveLength(0);
  });

  it('F3: a listed widget the permissions do not pass renders nothing and asks nothing', async () => {
    const api = stub(
      ADMIN,
      answer(
        layoutOf([
          'revenue_trend',
          'room_utilization',
          'balances',
          'tutor_performance',
        ]),
      ),
    );
    await open();
    await ready(['room_utilization', 'tutor_performance']);
    await settle();

    expect(sections()).toEqual(['room-utilization', 'tutor-performance']);
    expect(api.calls(R.revenue_trend)).toHaveLength(0);
    expect(api.calls(R.balances)).toHaveLength(0);
  });

  it('ignores a code this build does not know', async () => {
    stub(
      COORDINATOR,
      answer({
        widgets: [
          { widget: 'future_widget' as WidgetCode, visible: true },
          { widget: 'student_retention', visible: true },
        ],
        customized: true,
      }),
    );
    await open();
    await ready(['student_retention']);
    expect(sections()).toEqual(['student-retention']);
  });

  it('D4: a widget whose route answers 403 is hidden; the others stay', async () => {
    stub(OWNER, answer(layoutOf(ALL)), {
      [R.room_utilization]: () => problem(403, 'permission_denied'),
    });
    await open();
    await ready(ALL.filter((code) => code !== 'room_utilization'));
    await settle();

    expect(sections()).toEqual(
      ALL.filter((code) => code !== 'room_utilization').map(
        (code) => SECTION[code],
      ),
    );
  });

  it.each([
    ['a tutor', TUTOR],
    ['a student', STUDENT],
    ['a guardian', GUARDIAN],
    ['a user with no KPI permission', TEST_USER],
  ] as const)(
    '%s keeps the old home page and asks for no layout',
    async (_who, as) => {
      const api = stub(as, answer(layoutOf([])));
      await open();
      await settle();

      expect(h1()).toBe('Classora');
      expect(main().querySelector('[role="status"]')?.textContent).toBe(
        'API: live',
      );
      expect(reportRequests(api)).toEqual([]);
    },
  );
});

describe('layout states (D5)', () => {
  it('shows a skeleton while the layout loads, then the widgets', async () => {
    let release: (response: Response) => void = () => {};
    stub(
      COORDINATOR,
      () => new Promise<Response>((resolve) => (release = resolve)),
    );
    await open();

    expect(main().querySelector('[data-dashboard-loading]')).toBeTruthy();
    expect(main().querySelector('[aria-busy="true"]')).toBeTruthy();
    expect(sections()).toEqual([]);
    expect(buttonNamed(main(), 'Customize')?.disabled).toBe(true);

    release(jsonResponse(200, layoutOf(OPERATIONS)));
    await ready(OPERATIONS);
    expect(main().querySelector('[data-dashboard-loading]')).toBeNull();
  });

  it('on a failure, an alert, the role default and Customize disabled', async () => {
    // A 4xx other than 403 (a 5xx is retried first).
    const api = stub(FINANCE, [
      () => problem(400, 'bad_request'),
      () => jsonResponse(200, layoutOf(['balances'])),
    ]);
    await open();
    await ready(FINANCE_SET);
    await settle();

    expect(alerts(main()).join(' ')).toContain(
      "Your dashboard settings couldn't be loaded",
    );
    expect(sections()).toEqual(FINANCE_SET.map((code) => SECTION[code]));
    expect(buttonNamed(main(), 'Customize')?.disabled).toBe(true);

    await click(buttonNamed(main(), 'Try again'));
    await settleUntil(() => sections().length === 1);
    expect(sections()).toEqual(['balances']);
    expect(api.calls(`GET ${LAYOUT}`)).toHaveLength(2);
    expect(buttonNamed(main(), 'Customize')?.disabled).toBe(false);
  });

  it('with no visible widget, the empty state and Customize', async () => {
    const api = stub(
      COORDINATOR,
      answer(
        layoutOf(
          OPERATIONS.map((code) => [code, false] as [WidgetCode, boolean]),
          true,
        ),
      ),
    );
    await open();
    await settleUntil(() => main().querySelector('[data-dashboard-empty]'));

    expect(
      main().querySelector('[data-dashboard-empty]')?.textContent,
    ).toContain('No widgets are shown');
    expect(buttonNamed(main(), 'Customize')?.disabled).toBe(false);
    expect(
      reportRequests(api).filter((r) => !r.path.endsWith('/layout')),
    ).toEqual([]);
  });
});

// --- The dialog (E1, E2) ------------------------------------------------------------

const dialog = () => openDialog()!;
const rows = () =>
  [...dialog().querySelectorAll('[data-dashboard-row]')].map((row) =>
    row.getAttribute('data-dashboard-row'),
  );
const named = (name: string) =>
  [...dialog().querySelectorAll<HTMLElement>('button, [role="checkbox"]')].find(
    (node) =>
      node.getAttribute('aria-label') === name || node.textContent === name,
  )!;

async function openCustomize() {
  await click(buttonNamed(main(), 'Customize'));
  await settleUntil(() => openDialog()?.querySelector('[data-dashboard-row]'));
}

describe('the Customize dialog (E1, E2)', () => {
  it('lists every widget of the layout with its controls', async () => {
    stub(
      COORDINATOR,
      answer(
        layoutOf(
          [
            'student_retention',
            ['tutor_performance', false],
            'room_utilization',
          ],
          true,
        ),
      ),
    );
    await open();
    await ready(['student_retention', 'room_utilization']);
    await openCustomize();

    expect(dialog().querySelector('h2')?.textContent).toBe(
      'Customize the dashboard',
    );
    expect(rows()).toEqual([
      'student_retention',
      'tutor_performance',
      'room_utilization',
    ]);
    expect(named('Reorder “Student retention”')).toBeTruthy();
    expect(named('Show “Student retention”').getAttribute('aria-checked')).toBe(
      'true',
    );
    expect(
      named('Show “Tutor performance and center activity”').getAttribute(
        'aria-checked',
      ),
    ).toBe('false');
    // The ends are disabled.
    expect(
      (named('Move “Student retention” up') as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(
      (named('Move “Room utilization” down') as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(
      (named('Move “Room utilization” up') as HTMLButtonElement).disabled,
    ).toBe(false);
    // dnd-kit's instructions are localized and inside the dialog.
    expect(dialog().textContent).toContain(
      'To reorder, press Space or Enter to pick up the widget',
    );
  });

  it('moves with the buttons, keeps focus and announces; Save sends the exact list', async () => {
    let saved: unknown;
    const after = layoutOf(
      ['room_utilization', 'student_retention', ['tutor_performance', false]],
      true,
    );
    const api = stub(COORDINATOR, answer(layoutOf(OPERATIONS)), {
      [`PUT ${LAYOUT}`]: (_request, body) => {
        saved = body;
        return jsonResponse(200, after);
      },
    });
    await open();
    await ready(OPERATIONS);
    await openCustomize();

    const down = named('Move “Room utilization” down');
    await click(down);
    expect(rows()).toEqual([
      'student_retention',
      'room_utilization',
      'tutor_performance',
    ]);
    expect(document.activeElement).toBe(named('Move “Room utilization” down'));
    expect(dialog().querySelector('[data-dashboard-moved]')?.textContent).toBe(
      '“Room utilization”, position 2 of 3',
    );

    // Moving into the first place disables "up": focus goes to "down".
    await click(named('Move “Room utilization” up'));
    expect(rows()).toEqual([
      'room_utilization',
      'student_retention',
      'tutor_performance',
    ]);
    expect(document.activeElement).toBe(named('Move “Room utilization” down'));

    await click(named('Show “Tutor performance and center activity”'));
    expect(
      named('Show “Tutor performance and center activity”').getAttribute(
        'aria-checked',
      ),
    ).toBe('false');
    expect(api.calls(`PUT ${LAYOUT}`)).toHaveLength(0);

    await click(named('Save'));
    await settleUntil(() => openDialog() === null);

    expect(saved).toEqual({
      widgets: [
        { widget: 'room_utilization', visible: true },
        { widget: 'student_retention', visible: true },
        { widget: 'tutor_performance', visible: false },
      ],
    });
    expect(sections()).toEqual(['room-utilization', 'student-retention']);
    expect(api.calls(`GET ${LAYOUT}`)).toHaveLength(1);
  });

  it('Cancel and Esc discard the edits and send nothing', async () => {
    const api = stub(COORDINATOR, answer(layoutOf(OPERATIONS)));
    await open();
    await ready(OPERATIONS);
    await openCustomize();
    await click(named('Move “Room utilization” down'));
    await click(named('Cancel'));
    expect(openDialog()).toBeNull();

    await openCustomize();
    expect(rows()).toEqual(OPERATIONS);
    await click(named('Show “Room utilization”'));
    await pressEscape();
    expect(openDialog()).toBeNull();

    expect(sections()).toEqual(OPERATIONS.map((code) => SECTION[code]));
    expect(
      reportRequests(api)
        .filter((r) => r.method !== 'GET')
        .map((r) => r.path),
    ).toEqual([]);
  });

  it('Restore default deletes the layout, refetches and closes', async () => {
    const api = stub(
      COORDINATOR,
      [
        answer(
          layoutOf(
            [
              ['room_utilization', false],
              'tutor_performance',
              'student_retention',
            ],
            true,
          ),
        ),
        answer(layoutOf(OPERATIONS)),
      ],
      { [`DELETE ${LAYOUT}`]: () => new Response(null, { status: 204 }) },
    );
    await open();
    await ready(['tutor_performance', 'student_retention']);
    await openCustomize();
    await click(named('Restore default'));
    await settleUntil(() => openDialog() === null);
    await ready(OPERATIONS);

    expect(api.calls(`DELETE ${LAYOUT}`)).toHaveLength(1);
    expect(api.calls(`GET ${LAYOUT}`)).toHaveLength(2);
    expect(sections()).toEqual(OPERATIONS.map((code) => SECTION[code]));
  });

  it('422 dashboard_widget_unavailable refetches the layout and says why', async () => {
    const api = stub(
      COORDINATOR_FINANCE,
      [answer(layoutOf(ALL)), answer(layoutOf(OPERATIONS))],
      {
        [`PUT ${LAYOUT}`]: () =>
          problemWith(422, 'dashboard_widget_unavailable', {
            errors: [
              {
                path: ['body', 'widgets', 0, 'widget'],
                code: 'dashboard_widget_unavailable',
              },
            ],
          }),
      },
    );
    await open();
    await ready(ALL);
    await openCustomize();
    await click(named('Save'));
    await settleUntil(() => rows().length === 3);

    expect(openDialog()).not.toBeNull();
    expect(alerts(dialog())).toContain(
      'Your permissions changed; check the dashboard',
    );
    expect(rows()).toEqual(OPERATIONS);
    expect(api.calls(`GET ${LAYOUT}`)).toHaveLength(2);
  });

  it('another error stays in the dialog with the edits kept', async () => {
    stub(COORDINATOR, answer(layoutOf(OPERATIONS)), {
      [`PUT ${LAYOUT}`]: () => problem(500, 'internal_error'),
    });
    await open();
    await ready(OPERATIONS);
    await openCustomize();
    await click(named('Move “Room utilization” down'));
    await click(named('Save'));
    await settleUntil(() => alerts(dialog()).length > 0);

    expect(openDialog()).not.toBeNull();
    expect(rows()).toEqual([
      'student_retention',
      'room_utilization',
      'tutor_performance',
    ]);
  });

  it('is in Spanish with the Spanish catalog', async () => {
    applyLanguage('es');
    stub(COORDINATOR, answer(layoutOf(OPERATIONS)));
    await open();
    await ready(OPERATIONS);
    expect(h1()).toBe('Tablero');
    await click(buttonNamed(main(), 'Personalizar'));
    await settleUntil(() =>
      openDialog()?.querySelector('[data-dashboard-row]'),
    );
    expect(named('Subir «Retención de alumnos»')).toBeTruthy();
    expect(named('Bajar «Ocupación de salones»')).toBeTruthy();
    expect(named('Mostrar «Ocupación de salones»')).toBeTruthy();
    expect(named('Reordenar «Ocupación de salones»')).toBeTruthy();
    expect(named('Restablecer predeterminado')).toBeTruthy();
    expect(dialog().textContent).toContain('Para reordenar, pulsa Espacio');
  });
});
