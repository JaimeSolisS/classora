import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { apiCents } from '@/features/billing/accounts/cents';
import { NOTIFICATION_LINKS } from '@/features/notifications/messages';
import type { SessionUserBody } from '@/features/auth/session';
import { applyLanguage, i18n } from '@/i18n';
import {
  meAs,
  problem,
  stubApi,
  TEST_USER,
  type ApiStub,
} from '@/test/api-stub';
import { alerts, click } from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  settleUntil,
  type RenderedApp,
} from '@/test/render-app';
import { EARNINGS_KEY } from './api';
import {
  DETAIL_EMPTY,
  DETAIL_FULL,
  DETAIL_SALARIED,
  EARNINGS_EMPTY,
  EARNINGS_MIXED,
  EARNINGS_NONE,
  EARNINGS_PAID,
  EARNINGS_PARTIAL,
  EARNINGS_PERIOD_ID,
  EARNINGS_UNPAID,
  earningsPage,
  PAYMENT_KEPT,
  PAYMENT_UNDONE,
  PAYSLIP_ID,
} from './earningsFixtures';
import {
  groupEarningsLines,
  hoursAndMinutes,
  parseEarningsPage,
} from './earningsRules';
import { HOURLY, TUTOR_ID } from './fixtures';

// M7-10b E1: the tutor's earnings on /my-pay and a finalized period's page.
// The list (empty, rows, an empty period, no payslip, pages), the detail
// (every line and adjustment kind, a covered line, k / N, n / d, a reversed
// payment, part paid and paid), a 404 on each, a failed refetch, the
// payslip link, the notification's route and both languages. Amounts on
// screen are the fixtures' strings, formatted; nothing is added up.

const TUTOR: SessionUserBody = {
  ...TEST_USER,
  id: TUTOR_ID,
  roles: ['tutor'],
  permissions: ['tutor.view'],
};

const COMPENSATION = `GET /api/v1/payroll/tutors/${TUTOR_ID}/compensation`;
const LIST = `GET /api/v1/payroll/tutors/${TUTOR_ID}/earnings`;
const detailRoute = (id: string) =>
  `GET /api/v1/payroll/tutors/${TUTOR_ID}/earnings/${id}`;
const DETAIL = detailRoute(EARNINGS_PERIOD_ID);

let app: RenderedApp | undefined;
let listUrls: URL[] = [];

function stub(routes: Parameters<typeof stubApi>[0] = {}): ApiStub {
  return stubApi({
    'GET /api/v1/auth/me': meAs(TUTOR),
    [COMPENSATION]: () => jsonResponse(200, HOURLY),
    [LIST]: (request) => {
      listUrls.push(new URL(request.url));
      return jsonResponse(200, EARNINGS_MIXED);
    },
    [DETAIL]: () => jsonResponse(200, DETAIL_FULL),
    ...routes,
  });
}

const main = () => app!.container.querySelector('main')!;
const h1 = () => main().querySelector('h1')?.textContent;
const table = () => main().querySelector('[data-earnings-table]')!;
const row = (id: string) =>
  table().querySelector<HTMLElement>(`[data-earnings-id="${id}"]`)!;
const group = (name: string) =>
  main().querySelector<HTMLElement>(
    `[data-earnings-group="${name}"] [data-earnings-group-table]`,
  );
const rowsOf = (name: string) => [
  ...(group(name)?.querySelectorAll<HTMLElement>('tbody tr') ?? []),
];
const text = (element: Element | null | undefined) =>
  element?.textContent ?? '';
/** Whether the page says it reloaded: only "couldn't be reloaded" may appear. */
const claimsReloaded = () =>
  /(?<!couldn't be |could not be )(reloaded|updated)\b/i.test(text(main()));

async function open(path: string) {
  app = await renderApp(path);
  await settleUntil(() => main().querySelector('h1'));
}

async function openList() {
  await open('/my-pay');
  await settleUntil(
    () =>
      main().querySelector('[data-earnings-table]') ??
      main().querySelector('[data-earnings-empty]'),
  );
}

async function openDetail(id = EARNINGS_PERIOD_ID) {
  await open(`/my-pay/periods/${id}`);
  await settleUntil(
    () =>
      main().querySelector('[data-earnings-head]') ??
      main().querySelector('[data-earnings-not-found]') ??
      main().querySelector('[role="alert"]'),
  );
}

beforeEach(() => {
  listUrls = [];
  applyLanguage('en');
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.unstubAllGlobals();
});

describe('the earnings list on /my-pay (B)', () => {
  it('is empty until a period is finalized', async () => {
    stub({ [LIST]: () => jsonResponse(200, EARNINGS_NONE) });
    await openList();

    expect(h1()).toBe('My pay');
    expect(text(main().querySelector('[data-earnings-empty]'))).toBe(
      'No finalized periods yet. Your earnings appear here once a period is finalized.',
    );
    expect(main().querySelectorAll('button')).toHaveLength(0);
  });

  it('B1: each row, newest first as the API sends them, read from the first page', async () => {
    stub();
    await openList();

    expect(listUrls).toHaveLength(1);
    expect(listUrls[0]!.searchParams.get('limit')).toBe('25');
    expect(listUrls[0]!.searchParams.get('offset')).toBe('0');
    const ids = [...table().querySelectorAll('[data-earnings-id]')].map(
      (element) => element.getAttribute('data-earnings-id'),
    );
    expect(ids).toEqual(EARNINGS_MIXED.items.map((item) => item.id));

    const partial = row(EARNINGS_PARTIAL.id);
    expect(text(partial)).toContain('Oct 1, 2026 – Oct 15, 2026');
    expect(text(partial)).toContain('Biweekly');
    expect(text(partial)).toContain('$5,150.13');
    expect(text(partial.querySelector('[data-paid-state]'))).toBe(
      'Partly paid',
    );
    expect(text(partial.querySelector('[data-last-paid]'))).toBe(
      'Oct 20, 2026',
    );
    expect(text(partial.querySelector('[data-payslip]'))).toContain(
      'P-2026-000012',
    );
    const link = partial.querySelector<HTMLAnchorElement>('a[href^="/my-pay"]');
    expect(link?.getAttribute('href')).toBe(
      `/my-pay/periods/${EARNINGS_PARTIAL.id}`,
    );
    expect(link?.getAttribute('aria-label')).toBe(
      'Open the period Oct 1, 2026 – Oct 15, 2026',
    );
    expect(
      text(row(EARNINGS_UNPAID.id).querySelector('[data-paid-state]')),
    ).toBe('Unpaid');
    expect(text(row(EARNINGS_PAID.id).querySelector('[data-paid-state]'))).toBe(
      'Paid',
    );
    expect(
      text(row(EARNINGS_UNPAID.id).querySelector('[data-last-paid]')),
    ).toBe('—');
    expect(text(main().querySelector('[data-payslip-note]'))).toBe(
      'The payslip downloads in the language it was issued in.',
    );
  });

  it('B1: an empty period reads "No activity" and has no payslip', async () => {
    stub();
    await openList();

    const empty = row(EARNINGS_EMPTY.id);
    expect(text(empty.querySelector('[data-no-activity]'))).toBe('No activity');
    expect(text(empty.querySelector('[data-payslip]'))).toBe('—');
    expect(empty.querySelector('a[download]')).toBeNull();
    expect(
      row(EARNINGS_PARTIAL.id).querySelector('[data-no-activity]'),
    ).toBeNull();
  });

  it('B2: the payslip link is a plain same-origin download named for its number', async () => {
    stub();
    await openList();

    const link = row(EARNINGS_PARTIAL.id).querySelector<HTMLAnchorElement>(
      'a[download]',
    )!;
    expect(link.getAttribute('href')).toBe(
      `/api/v1/payroll/tutors/${TUTOR_ID}/payslips/${PAYSLIP_ID}/pdf`,
    );
    expect(link.getAttribute('download')).toBe('P-2026-000012.pdf');
    expect(link.getAttribute('aria-label')).toBe(
      'Download payslip P-2026-000012',
    );
    expect(link.textContent).toBe('Download payslip');
    expect(link.className).toContain('min-h-11');
  });

  it('pages through the API offsets, in the URL', async () => {
    const first = earningsPage([EARNINGS_PARTIAL], { total: 30 });
    const second = earningsPage([EARNINGS_PAID], { total: 30, offset: 25 });
    stub({
      [LIST]: (request) => {
        const url = new URL(request.url);
        listUrls.push(url);
        return jsonResponse(
          200,
          url.searchParams.get('offset') === '25' ? second : first,
        );
      },
    });
    await openList();
    expect(text(main())).toContain('1–1 of 30');

    await click(buttonNamed(main(), 'Next'));
    await settleUntil(() =>
      main().querySelector(`[data-earnings-id="${EARNINGS_PAID.id}"]`),
    );
    expect(app!.location()).toBe('/my-pay?page=2');
    expect(listUrls.at(-1)!.searchParams.get('offset')).toBe('25');

    await click(buttonNamed(main(), 'Previous'));
    await settleUntil(() =>
      main().querySelector(`[data-earnings-id="${EARNINGS_PARTIAL.id}"]`),
    );
    expect(app!.location()).toBe('/my-pay');
  });

  it('A2: a 404 on the list is the forbidden page', async () => {
    stub({ [LIST]: () => problem(404, 'not_found') });
    await open('/my-pay');
    await settleUntil(() => h1() === "You don't have access to this page");
  });

  it('D1: a failed first read shows the error with a retry', async () => {
    const api = stub({
      [LIST]: [
        () => problem(429, 'rate_limited'),
        () => jsonResponse(200, EARNINGS_MIXED),
      ],
    });
    await open('/my-pay');
    await settleUntil(() =>
      main().querySelector('[data-earnings-section] [role="alert"]'),
    );
    expect(main().querySelector('[data-earnings-table]')).toBeNull();

    await click(
      buttonNamed(
        main().querySelector('[data-earnings-section]')!,
        'Try again',
      ),
    );
    await settleUntil(() => main().querySelector('[data-earnings-table]'));
    expect(api.calls(LIST)).toHaveLength(2);
  });

  it('D1: a failed refetch keeps the rows, marks them, and never says they reloaded', async () => {
    const api = stub({
      [LIST]: [
        () => jsonResponse(200, EARNINGS_MIXED),
        () => problem(429, 'rate_limited'),
        () => jsonResponse(200, EARNINGS_MIXED),
      ],
    });
    await openList();

    await app!.queryClient.refetchQueries({ queryKey: EARNINGS_KEY });
    await settleUntil(() => main().querySelector('[data-earnings-stale]'));
    expect(row(EARNINGS_PARTIAL.id)).toBeDefined();
    const section = main().querySelector('[data-earnings-section]')!;
    expect(alerts(section)[0]).toContain(
      "Your earnings may be out of date: they couldn't be reloaded.",
    );
    expect(claimsReloaded()).toBe(false);

    await click(buttonNamed(section, 'Reload'));
    await settleUntil(() => !main().querySelector('[data-earnings-stale]'));
    expect(api.calls(LIST)).toHaveLength(3);
    expect(alerts(section)).toEqual([]);
    expect(claimsReloaded()).toBe(false);
  });
});

describe('a finalized period (C)', () => {
  it('C1: the head, part paid: what is left and the payslip', async () => {
    stub();
    await openDetail();

    expect(h1()).toBe('Pay for Oct 1, 2026 – Oct 15, 2026');
    const head = (key: string) =>
      text(main().querySelector(`[data-head="${key}"]`));
    expect(head('frequency')).toBe('Biweekly');
    expect(head('finalizedOn')).toBe('Oct 17, 2026');
    expect(head('total')).toBe('$5,150.13');
    expect(head('paid')).toBe('$3,000.00');
    expect(head('remaining')).toBe('$2,150.13');
    expect(head('state')).toBe('Partly paid');
    const link = main().querySelector<HTMLAnchorElement>(
      '[data-earnings-head] a[download]',
    )!;
    expect(link.getAttribute('href')).toBe(
      `/api/v1/payroll/tutors/${TUTOR_ID}/payslips/${PAYSLIP_ID}/pdf`,
    );
    expect(link.getAttribute('download')).toBe('P-2026-000012.pdf');
    expect(main().querySelector('a[href="/my-pay"]')?.textContent).toBe(
      'Back to My pay',
    );
    expect(main().querySelectorAll('button')).toHaveLength(0);
  });

  it('C1: paid in full shows nothing left to pay', async () => {
    stub({ [DETAIL]: () => jsonResponse(200, DETAIL_SALARIED) });
    await openDetail();

    expect(main().querySelector('[data-head="remaining"]')).toBeNull();
    expect(text(main().querySelector('[data-head="state"]'))).toBe('Paid');
  });

  it('C2: the summary, and its subtotals add up to the total shown', async () => {
    stub();
    await openDetail();

    const summary = (key: string) =>
      text(main().querySelector(`[data-summary="${key}"]`));
    expect(summary('classLines')).toBe('6');
    expect(summary('classTime')).toBe('6 h 5 min');
    expect(summary('classes')).toBe('$830.13');
    expect(summary('salary')).toBe('$4,000.00');
    expect(summary('bonuses')).toBe('$500.00');
    expect(summary('deductions')).toBe('-$300.00');
    expect(summary('adjustments')).toBe('$120.00');
    expect(summary('total')).toBe('$5,150.13');

    const { summary: parts, total } = DETAIL_FULL;
    const sum = [
      parts.classes,
      parts.salary,
      parts.bonuses,
      parts.deductions,
      parts.adjustments,
    ].reduce((acc, value) => acc + apiCents(value), 0);
    expect(sum).toBe(apiCents(total));
    expect(summary('total')).toBe(
      text(main().querySelector('[data-head="total"]')),
    );
  });

  it('C3: classes, with each event, rule, rate model, k / N and a covered line', async () => {
    stub();
    await openDetail();

    const classes = rowsOf('classes').map((element) =>
      [...element.querySelectorAll('td')].map((cell) => cell.textContent),
    );
    expect(classes).toEqual([
      [
        'Oct 2, 2026',
        'Individual',
        'Session',
        '60 min',
        'Per hour, $250.00',
        '$250.00',
      ],
      [
        'Oct 5, 2026',
        'Group',
        'Late cancellation (50% of the rate)',
        '60 min',
        'Per hour, $200.00',
        '$100.00',
      ],
      [
        'Oct 6, 2026',
        'Individual',
        'No-show (fixed amount of $80.00)',
        '60 min',
        'Per hour, $250.00',
        '$80.00',
      ],
      [
        'Oct 7, 2026',
        'Individual',
        'Makeup (100% of the rate)',
        '30 min',
        'Per session, $150.125',
        '$150.13',
      ],
      [
        'Oct 8, 2026',
        'Course',
        'Session',
        '95 min',
        'Per course, $3,000.00, session 3 / 12',
        '$250.00',
      ],
      [
        'Oct 9, 2026',
        'Individual',
        'Session',
        '60 min',
        'Covered by the salary',
        '$0.00',
      ],
    ]);
  });

  it('C3: salary n / d, bonuses, deductions, and the groups in the payslip order', async () => {
    stub();
    await openDetail();

    expect(
      [...main().querySelectorAll('[data-earnings-group]')].map((element) =>
        element.getAttribute('data-earnings-group'),
      ),
    ).toEqual(['classes', 'salary', 'bonuses', 'deductions', 'adjustments']);
    const cells = (name: string) =>
      rowsOf(name).map((element) =>
        [...element.querySelectorAll('td')].map((cell) => cell.textContent),
      );
    expect(cells('salary')).toEqual([
      [
        'Oct 1, 2026 – Oct 15, 2026',
        '11 / 22 of $8,000.00, counted in weekdays',
        '$4,000.00',
      ],
    ]);
    expect(cells('bonuses')).toEqual([
      ['Bono de puntualidad', 'Octubre', '$500.00'],
    ]);
    expect(cells('deductions')).toEqual([
      ['Préstamo', 'Cuota 2 de 6', '-$300.00'],
    ]);
  });

  it('C3: every adjustment kind, with its work, periods and a manual reason', async () => {
    stub();
    await openDetail();

    const adjustments = rowsOf('adjustments');
    expect(
      adjustments.map((element) => element.dataset.adjustmentKind),
    ).toEqual(['late_approval', 'hours_correction', 'manual', 'carry_forward']);
    const cells = adjustments.map((element) =>
      [...element.querySelectorAll('td')].map((cell) => cell.textContent),
    );
    expect(cells[0]).toEqual([
      'Late approval',
      'Work on Sep 28, 2026 (Individual)',
      '$250.00',
    ]);
    expect(cells[1]).toEqual([
      'Hours correction',
      'Work on Sep 29, 2026 (Group)From the period Sep 16, 2026 – Sep 30, 2026',
      '-$50.00',
    ]);
    expect(cells[2]).toEqual([
      'Manual adjustment',
      'Reason: Viáticos de la feria escolar',
      '$120.00',
    ]);
    expect(cells[3]).toEqual([
      'Carried balance',
      'From the period Sep 16, 2026 – Sep 30, 2026Related period: Sep 16, 2026 – Sep 30, 2026',
      '-$200.00',
    ]);
    expect(
      adjustments[0]!.querySelector('[data-adjustment-reason]'),
    ).toBeNull();
  });

  it('C4: payments, a reversed one struck through with its date and no reason', async () => {
    stub();
    await openDetail();

    const payments = main().querySelector(
      '[data-earnings-payments] [data-earnings-group-table]',
    )!;
    const kept = payments.querySelector<HTMLElement>(
      `[data-payment-id="${PAYMENT_KEPT.id}"]`,
    )!;
    const undone = payments.querySelector<HTMLElement>(
      `[data-payment-id="${PAYMENT_UNDONE.id}"]`,
    )!;
    expect(
      [...kept.querySelectorAll('td')].map((cell) => cell.textContent),
    ).toEqual(['Oct 20, 2026', '$3,000.00', 'Transfer', 'SPEI 4471', '—']);
    expect(undone.dataset.reversed).toBe('true');
    expect(undone.querySelector('[data-payment-amount]')!.className).toContain(
      'line-through',
    );
    expect(text(undone.querySelector('[data-payment-reversed]'))).toBe(
      'Reversed on Oct 19, 2026',
    );
    expect(text(undone)).not.toContain('Reason');
    expect(
      kept.querySelector('[data-payment-amount]')!.className,
    ).not.toContain('line-through');
  });

  it('a period with no lines: no groups, no payslip, no payments', async () => {
    stub({
      [detailRoute(EARNINGS_EMPTY.id)]: () => jsonResponse(200, DETAIL_EMPTY),
    });
    await openDetail(EARNINGS_EMPTY.id);

    expect(main().querySelector('[data-earnings-group]')).toBeNull();
    expect(text(main().querySelector('[data-earnings-no-lines]'))).toBe(
      'No activity in this period.',
    );
    expect(
      text(main().querySelector('[data-earnings-head] [data-payslip]')),
    ).toBe('—');
    expect(text(main().querySelector('[data-earnings-no-payments]'))).toBe(
      'No payments yet.',
    );
  });

  it.each([
    ['a 404', true],
    ['an id that is not a UUID', false],
  ] as const)(
    'A2: %s is the same "not found" with a link back',
    async (_name, valid) => {
      const api = stub({ [DETAIL]: () => problem(404, 'not_found') });
      await openDetail(valid ? EARNINGS_PERIOD_ID : 'not-a-period');

      expect(h1()).toBe('Period not found');
      expect(text(main().querySelector('[data-earnings-not-found]'))).toBe(
        "This period wasn't found among your finalized periods.",
      );
      expect(main().querySelector('a[href="/my-pay"]')).not.toBeNull();
      expect(alerts(main())).toEqual([]);
      expect(api.calls(DETAIL)).toHaveLength(valid ? 1 : 0);
    },
  );

  it('D1: a failed refetch keeps the period, marks it, and never says it reloaded', async () => {
    const api = stub({
      [DETAIL]: [
        () => jsonResponse(200, DETAIL_FULL),
        () => problem(429, 'rate_limited'),
        () => jsonResponse(200, DETAIL_FULL),
      ],
    });
    await openDetail();

    await app!.queryClient.refetchQueries({ queryKey: EARNINGS_KEY });
    await settleUntil(() =>
      main().querySelector('[data-earnings-period-stale]'),
    );
    expect(main().querySelector('[data-earnings-head]')).not.toBeNull();
    expect(alerts(main())[0]).toContain(
      "This period may be out of date: it couldn't be reloaded.",
    );

    await click(buttonNamed(main(), 'Reload'));
    await settleUntil(
      () => !main().querySelector('[data-earnings-period-stale]'),
    );
    expect(api.calls(DETAIL)).toHaveLength(3);
    expect(alerts(main())).toEqual([]);
    expect(claimsReloaded()).toBe(false);
  });

  it('D1: a failed first read shows the error with a retry', async () => {
    stub({ [DETAIL]: () => problem(429, 'rate_limited') });
    await openDetail();

    expect(h1()).toBe('Pay period');
    expect(alerts(main())).toHaveLength(1);
    expect(buttonNamed(main(), 'Try again')).toBeDefined();
  });

  it('is forbidden for a non-tutor with no request', async () => {
    const api = stubApi({
      'GET /api/v1/auth/me': meAs({
        ...TEST_USER,
        roles: ['finance_staff'],
        permissions: ['payroll.view'],
      }),
    });
    await open(`/my-pay/periods/${EARNINGS_PERIOD_ID}`);
    expect(h1()).toBe("You don't have access to this page");
    expect(api.requests.filter((r) => r.path.includes('/payroll'))).toEqual([]);
  });
});

describe('Spanish (C5)', () => {
  it('the list and the detail read in Spanish', async () => {
    applyLanguage('es');
    stub();
    await openList();

    const partial = row(EARNINGS_PARTIAL.id);
    expect(text(main().querySelector('[data-earnings-section] h2'))).toBe(
      'Ingresos',
    );
    expect(text(partial.querySelector('[data-paid-state]'))).toBe(
      'Pago parcial',
    );
    expect(
      text(row(EARNINGS_UNPAID.id).querySelector('[data-paid-state]')),
    ).toBe('Sin pagar');
    expect(
      text(row(EARNINGS_EMPTY.id).querySelector('[data-no-activity]')),
    ).toBe('Sin movimientos');
    expect(partial.querySelector('a[download]')?.textContent).toBe(
      'Descargar comprobante',
    );
    app!.unmount();

    app = await renderApp(`/my-pay/periods/${EARNINGS_PERIOD_ID}`);
    await settleUntil(() => main().querySelector('[data-earnings-head]'));
    expect(text(main().querySelector('[data-head="remaining"]'))).toBe(
      '$2,150.13',
    );
    expect(h1()).toBe('Pago del 1 oct 2026 al 15 oct 2026');
    expect(
      rowsOf('classes').map((element) =>
        text(element.querySelectorAll('td')[4]),
      ),
    ).toContain('Por curso, $3,000.00, sesión 3 / 12');
    expect(text(rowsOf('classes')[5]!.querySelectorAll('td')[4])).toBe(
      'Cubierta por salario',
    );
    expect(
      text(
        main().querySelector(
          `[data-earnings-payments] [data-earnings-group-table] [data-payment-id="${PAYMENT_UNDONE.id}"] [data-payment-reversed]`,
        ),
      ),
    ).toMatch(/^Anulado el /);
    expect(
      text(main().querySelector('[data-earnings-group="adjustments"] h3')),
    ).toBe('Ajustes');
  });

  it('every new string exists in both languages', () => {
    const keys = (value: unknown, prefix = ''): string[] =>
      typeof value === 'object' && value !== null
        ? Object.entries(value).flatMap(([key, child]) =>
            keys(child, prefix ? `${prefix}.${key}` : key),
          )
        : [prefix];
    for (const section of ['payroll.earnings', 'payroll.earningsPeriod']) {
      const en = keys(i18n.getResource('en', 'common', section));
      const es = keys(i18n.getResource('es', 'common', section));
      expect(es).toEqual(en);
      expect(en.length).toBeGreaterThan(10);
    }
  });
});

describe('rules and the notification (A3)', () => {
  it('payroll.period_finalized opens the period under My pay', () => {
    const link = NOTIFICATION_LINKS['payroll.period_finalized']!;
    expect(
      link(
        {
          payroll_period_id: EARNINGS_PERIOD_ID,
          starts_on: '2026-10-01',
          ends_on: '2026-10-15',
        },
        { userId: TUTOR_ID },
      ),
    ).toBe(`/my-pay/periods/${EARNINGS_PERIOD_ID}`);
    expect(link({}, { userId: TUTOR_ID })).toBe('/my-pay');
  });

  it('groups lines as the payslip does, and reads the page and the minutes', () => {
    const groups = groupEarningsLines(DETAIL_FULL.lines);
    expect(groups.classes).toHaveLength(6);
    expect(groups.salary).toHaveLength(1);
    expect(groups.bonuses).toHaveLength(1);
    expect(groups.deductions).toHaveLength(1);
    expect(groups.adjustments).toHaveLength(4);
    expect(hoursAndMinutes(365)).toEqual({ hours: 6, minutes: 5 });
    expect(hoursAndMinutes(0)).toEqual({ hours: 0, minutes: 0 });
    expect(parseEarningsPage(new URLSearchParams('page=3'))).toBe(3);
    expect(parseEarningsPage(new URLSearchParams('page=0'))).toBe(1);
    expect(parseEarningsPage(new URLSearchParams('page=x'))).toBe(1);
  });
});
