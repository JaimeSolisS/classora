import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { problemWith } from '@/features/academics/testing';
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
import {
  alerts,
  click,
  fieldByLabel,
  labelled,
  setFieldValue,
  typeInto,
} from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  settleUntil,
  type RenderedApp,
} from '@/test/render-app';
import { PAYROLL_PERIODS_KEY, UNRESOLVED_KEY } from './api';
import { SETTINGS_OFF, SETTINGS_ON, TUTOR_ID } from './fixtures';
import {
  ADMIN_ID,
  ALL_LINES,
  EARLIER_PERIOD_ID,
  EMPTY_PERIODS,
  FINANCE_ID,
  FUTURE_PERIOD_ID,
  LINE_DEDUCTION,
  LINE_HOURLY,
  OWNER_ID,
  PERIOD_FINALIZED,
  PERIOD_ID,
  PERIOD_NOT_ENDED,
  PERIOD_OPEN,
  PERIOD_PREPARED,
  PERIOD_RETURNED,
  PERIODS_PAGE,
  review,
  SESSION_ID,
} from './periodFixtures';
import {
  finalizeBlocks,
  isNegativeAmount,
  isUnknownResult,
  lineKindKey,
  parsePeriodFilters,
  periodActions,
  periodSearchParams,
  returnReasonError,
  unresolvedWorkDetails,
} from './periodRules';
import { ApiError } from '@/app/api';
import type { ApprovalRequestItem } from '@/features/approvals/api';
import {
  approvalSummary,
  itemReviewLink,
  reviewLinkOf,
  typeLabel,
} from '@/features/approvals/summary';
import {
  NOTIFICATION_LINKS,
  notificationMessage,
} from '@/features/notifications/messages';
import { i18n } from '@/i18n';

// #110b H1: per-role access and tabs, the list and its filters, each kind of
// line, every refusal, D3's order, D7-D11, and the pure rules apart from
// React. Amounts on screen are the fixtures' strings, formatted.

const ME = 'GET /api/v1/auth/me';
const LIST = 'GET /api/v1/payroll/periods';
const SETTINGS = 'GET /api/v1/payroll/settings';
const LINES = `GET /api/v1/payroll/periods/${PERIOD_ID}/lines`;
const RECALCULATE = `POST /api/v1/payroll/periods/${PERIOD_ID}/recalculate`;
const PREPARE = `POST /api/v1/payroll/periods/${PERIOD_ID}/prepare`;
const RETURN = `POST /api/v1/payroll/periods/${PERIOD_ID}/return`;
const FINALIZE = `POST /api/v1/payroll/periods/${PERIOD_ID}/finalize`;
const REAUTH = 'POST /api/v1/auth/reauthenticate';

const user = (
  roles: SessionUserBody['roles'],
  permissions: SessionUserBody['permissions'],
  id = TEST_USER.id,
): SessionUserBody => ({ ...TEST_USER, id, roles, permissions });

const FINANCE = user(
  ['finance_staff'],
  ['payroll.view', 'payroll.manage', 'payroll.prepare', 'schedule.view'],
  FINANCE_ID,
);
const OWNER = user(
  ['owner'],
  [
    'payroll.view',
    'payroll.manage',
    'payroll.prepare',
    'payroll.finalize',
    'schedule.view',
  ],
  OWNER_ID,
);
const ADMIN = user(
  ['administrator'],
  ['payroll.finalize', 'schedule.view'],
  ADMIN_ID,
);
const COORDINATOR = user(['academic_coordinator'], ['schedule.view']);

const transition = (status: string) =>
  jsonResponse(200, {
    id: PERIOD_ID,
    status,
    prepared_at: null,
    finalized_at: null,
  });

function stub(
  routes: Record<string, Responder | Responder[]> = {},
  as: SessionUserBody = FINANCE,
): ApiStub {
  return stubApi({
    [ME]: meAs(as),
    [LIST]: () => jsonResponse(200, PERIODS_PAGE),
    [SETTINGS]: () => jsonResponse(200, SETTINGS_ON),
    [LINES]: () => jsonResponse(200, review(PERIOD_OPEN)),
    [REAUTH]: () =>
      jsonResponse(200, { reauthenticated_until: '2026-10-19T18:05:00Z' }),
    ...routes,
  });
}

let app: RenderedApp | undefined;

const main = () => app!.container.querySelector('main')!;
const h1 = () => main().querySelector('h1')?.textContent;
const text = () => main().textContent ?? '';
const status = () =>
  main().querySelector('[data-period-announcement]')?.textContent ?? '';
const dialogTitled = (title: string) =>
  [...document.querySelectorAll<HTMLElement>('[role="dialog"]')].find(
    (dialog) => dialog.querySelector('h2')?.textContent === title,
  );
const finalizeDialog = () => dialogTitled('Finalize this period?');
const returnDialog = () => dialogTitled('Send this period back?');
const button = (name: string) =>
  [...main().querySelectorAll('button')].find(
    (candidate) => candidate.textContent === name,
  );
const tabs = () =>
  [
    ...main().querySelectorAll<HTMLAnchorElement>(
      'nav[aria-label="Payroll sections"] a',
    ),
  ].map((a) => a.textContent);
const order = (api: ApiStub) =>
  api.requests
    .filter((r) => r.path.includes('/payroll/'))
    .map((r) => `${r.method} ${r.path.replace(/^\/api\/v1\/payroll/, '')}`);

async function open(path: string) {
  app = await renderApp(path);
  await settleUntil(() => main().querySelector('h1'));
}

async function openReview() {
  await open(`/payroll/periods/${PERIOD_ID}`);
  await settleUntil(() => main().querySelector('[data-period-head]'));
}

beforeEach(() => {
  applyLanguage('en');
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('access and routing (A)', () => {
  it('/payroll sends a delegated Administrator to the periods, Periods alone', async () => {
    stub({ [LIST]: () => jsonResponse(200, EMPTY_PERIODS) }, ADMIN);
    await open('/payroll');
    await settleUntil(() => h1() === 'Payroll periods');

    expect(app!.router.state.location.pathname).toBe('/payroll/periods');
    expect(tabs()).toEqual(['Periods']);
  });

  it('/payroll keeps Finance on the tutors, with Periods first', async () => {
    stub({
      'GET /api/v1/payroll/tutors': () => jsonResponse(200, EMPTY_PERIODS),
    });
    await open('/payroll');
    await settleUntil(() => h1() === 'Tutors');

    expect(app!.router.state.location.pathname).toBe('/payroll/tutors');
    expect(tabs()).toEqual([
      'Periods',
      'Tutors',
      'Pay rules',
      'Bonus and deduction types',
      'Unresolved work',
      'Settings',
    ]);
  });

  it.each([`/payroll/periods`, `/payroll/periods/${PERIOD_ID}`])(
    'a Coordinator is forbidden on %s with no request',
    async (path) => {
      const api = stub({}, COORDINATOR);
      await open(path);
      expect(h1()).toBe("You don't have access to this page");
      expect(api.requests.filter((r) => r.path.includes('/payroll'))).toEqual(
        [],
      );
    },
  );

  it('an open period by URL is not found for a delegated Administrator', async () => {
    stub({ [LINES]: () => problem(404, 'not_found') }, ADMIN);
    await open(`/payroll/periods/${PERIOD_ID}`);
    await settleUntil(() => h1() === 'Page not found');
  });

  it('a malformed id is not found, with no request', async () => {
    const api = stub();
    await open('/payroll/periods/not-a-uuid');
    await settleUntil(() => h1() === 'Page not found');
    expect(api.requests.filter((r) => r.path.includes('/lines'))).toEqual([]);
  });
});

describe('period list (B)', () => {
  const rows = () =>
    [...main().querySelectorAll('[data-periods-table] tbody tr')].map((row) =>
      [...row.querySelectorAll('td')].map((cell) => cell.textContent),
    );

  it('shows each row, a left tutor, a negative and a too-large total', async () => {
    stub();
    await open('/payroll/periods');
    await settleUntil(() => rows().length === 3);

    expect(rows()).toEqual([
      [
        'Ana Tutora',
        'Oct 1, 2026 – Oct 15, 2026',
        'Biweekly',
        'Open',
        '4',
        '$1,250.00',
      ],
      [
        'Beto SalarioArchived',
        'Sep 1, 2026 – Sep 30, 2026',
        'Monthly',
        'Prepared',
        '4',
        '-$150.00',
      ],
      [
        'Carla Grande',
        'Oct 1, 2026 – Oct 15, 2026',
        'Weekly',
        // M7-09b D2: what's paid, on a finalized row only.
        'FinalizedUnpaid',
        '12',
        'Too large to show',
      ],
    ]);
    const links = [
      ...main().querySelectorAll<HTMLAnchorElement>('[data-periods-table] a'),
    ];
    expect(links[2]!.getAttribute('href')).toBe(
      `/payroll/periods/${PERIODS_PAGE.items[2]!.id}`,
    );
  });

  it('reads the filters and the page from the URL, ignoring invalid ones', async () => {
    const urls: URL[] = [];
    stub({
      [LIST]: (request) => {
        urls.push(new URL(request.url));
        return jsonResponse(200, PERIODS_PAGE);
      },
    });
    await open(`/payroll/periods?status=prepared&tutor=${TUTOR_ID}&page=2`);
    await settleUntil(() => urls.length > 0);
    expect(urls[0]!.searchParams.get('status')).toBe('prepared');
    expect(urls[0]!.searchParams.get('tutor_user_id')).toBe(TUTOR_ID);
    expect(urls[0]!.searchParams.get('offset')).toBe('25');
    app!.unmount();

    urls.length = 0;
    await open('/payroll/periods?status=paid&tutor=nobody&page=-1');
    await settleUntil(() => urls.length > 0);
    expect([...urls[0]!.searchParams.keys()].sort()).toEqual([
      'limit',
      'offset',
    ]);
    expect(urls[0]!.searchParams.get('offset')).toBe('0');
  });

  it('a status filter writes the URL and goes back to page 1', async () => {
    stub();
    await open('/payroll/periods?page=3');
    await settleUntil(() => rows().length === 3);

    setFieldValue(labelled(main(), 'Status'), 'finalized');
    await settleUntil(
      () => app!.location() === '/payroll/periods?status=finalized',
    );
  });

  it('says payroll is off, with the Settings link, when it is', async () => {
    stub({
      [LIST]: () => jsonResponse(200, EMPTY_PERIODS),
      [SETTINGS]: () => jsonResponse(200, SETTINGS_OFF),
    });
    await open('/payroll/periods');
    await settleUntil(() => main().querySelector('[data-payroll-off]'));

    expect(main().querySelector('[data-periods-empty]')).toBeNull();
    expect(
      main().querySelector('[data-payroll-off] a')?.getAttribute('href'),
    ).toBe('/payroll/settings');
  });

  it.each([
    ['no periods', '/payroll/periods', 'There are no periods yet.'],
    [
      'no match',
      '/payroll/periods?status=open',
      'Nothing matches these filters.',
    ],
  ])('the empty state: %s', async (_case, path, said) => {
    stub({ [LIST]: () => jsonResponse(200, EMPTY_PERIODS) });
    await open(path);
    await settleUntil(() => text().includes(said));
    expect(main().querySelector('[data-payroll-off]')).toBeNull();
  });

  it('a delegated Administrator: nothing waiting, no settings, no picker', async () => {
    const api = stub({ [LIST]: () => jsonResponse(200, EMPTY_PERIODS) }, ADMIN);
    await open('/payroll/periods');
    await settleUntil(() =>
      text().includes('Nothing is waiting for your review.'),
    );

    expect(api.calls(SETTINGS)).toHaveLength(0);
    expect(main().querySelector('[role="combobox"]')).toBeNull();
    const options = [
      ...labelled<HTMLSelectElement>(main(), 'Status').options,
    ].map((option) => option.value);
    expect(options).toEqual(['', 'prepared', 'finalized']);
  });

  it('a failed refresh keeps the rows, says they may be out of date, until a reload works', async () => {
    stub({
      // A 4xx: the query client retries a 5xx by itself.
      [LIST]: [
        () => jsonResponse(200, PERIODS_PAGE),
        () => problem(400, 'bad_request'),
        () => jsonResponse(200, PERIODS_PAGE),
      ],
    });
    await open('/payroll/periods');
    await settleUntil(() => rows().length === 3);

    await app!.queryClient.invalidateQueries({ queryKey: PAYROLL_PERIODS_KEY });
    await settleUntil(() => text().includes('may be out of date'));
    expect(rows()).toHaveLength(3);

    await click(buttonNamed(main(), 'Reload'));
    await settleUntil(() => !text().includes('may be out of date'));
  });
});

describe('review page (C)', () => {
  const line = (id: string) =>
    main().querySelector<HTMLElement>(
      `[data-period-lines-table] [data-line-id="${id}"]`,
    )!;
  const cells = (id: string) =>
    [...line(id).querySelectorAll('td')].map((cell) => cell.textContent);
  const head = () =>
    Object.fromEntries(
      [...main().querySelectorAll('[data-period-head] > div')].map((row) => [
        row.querySelector('dt')!.textContent,
        row.querySelector('dd')!.textContent,
      ]),
    );

  it('shows the head of a prepared period', async () => {
    stub({ [LINES]: () => jsonResponse(200, review(PERIOD_PREPARED)) });
    await openReview();

    expect(h1()).toBe('Ana Tutora: Oct 1, 2026 – Oct 15, 2026');
    expect(head()).toMatchObject({
      Tutor: 'Ana TutoraActive',
      Dates: 'Oct 1, 2026 – Oct 15, 2026',
      Frequency: 'Biweekly',
      Status: 'Prepared',
      Total: '$4,166.67',
    });
    expect(head()['Prepared by']).toMatch(/^Demo Finance, Oct 16, 2026/);
  });

  it('shows who sent an open period back, when and why', async () => {
    stub({ [LINES]: () => jsonResponse(200, review(PERIOD_RETURNED)) });
    await openReview();

    expect(head()['Sent back by']).toMatch(/^Demo Admin, Oct 17, 2026/);
    expect(head()['Reason it was sent back']).toBe(
      'Falta el bono de transporte.',
    );
    expect(main().querySelector('[data-period-returned]')).not.toBeNull();
  });

  it('warns about pending closeouts, never at 0', async () => {
    stub({
      [LINES]: () =>
        jsonResponse(200, review({ ...PERIOD_OPEN, pending_closeouts: 2 })),
    });
    await openReview();
    expect(
      main().querySelector('[data-pending-closeouts]')?.textContent,
    ).toContain('2 sessions in this period are waiting');
    expect(button('Prepare')?.disabled).toBe(false);
    app!.unmount();

    stub();
    await openReview();
    expect(main().querySelector('[data-pending-closeouts]')).toBeNull();
  });

  it('shows every line kind, in the API order, with the API amounts', async () => {
    stub();
    await openReview();

    expect(
      [...main().querySelectorAll('[data-period-lines-table] tbody tr')].map(
        (row) => row.getAttribute('data-line-id'),
      ),
    ).toEqual(ALL_LINES.map((l) => l.id));
    expect(cells(ALL_LINES[0]!.id)).toEqual([
      'Oct 5, 2026',
      'Session',
      'Individual90 minPer hour, $250.00Open session',
      '$375.00',
    ]);
    expect(cells(ALL_LINES[1]!.id)[2]).toBe(
      'Course60 minPer course, $1,000.00Session 2 of 6Open session',
    );
    expect(cells(ALL_LINES[2]!.id)[1]).toBe('No-show');
    expect(cells(ALL_LINES[3]!.id)[1]).toBe('Makeup');
    expect(cells(ALL_LINES[4]!.id)[1]).toBe('Late cancellation');
    expect(cells(ALL_LINES[5]!.id)[2]).toBe(
      'Group60 minCovered by the salaryOpen session',
    );
    expect(cells(ALL_LINES[6]!.id)).toEqual([
      'Oct 1, 2026',
      'Salary',
      'Oct 1, 2026 – Oct 9, 20263/5 of $5,000.00Counted in weekdays',
      '$3,000.00',
    ]);
    expect(cells(ALL_LINES[7]!.id)[2]).toBe(
      'Oct 10, 2026 – Oct 11, 20260/5 of $5,000.00Paid for no daysCounted in weekdays',
    );
    expect(cells(ALL_LINES[8]!.id)).toEqual([
      '—',
      'Transporte (Bonus)',
      'Apoyo de transporte',
      '$300.00',
    ]);
    expect(cells(LINE_DEDUCTION.id)).toEqual([
      '—',
      'Préstamo (Deduction)',
      'Abono al préstamo',
      '-$150.00',
    ]);
    // M7-08b A1: #111's adjustment, labelled.
    expect(cells(ALL_LINES[10]!.id)).toEqual([
      'Sep 28, 2026',
      'Late approval',
      'Work on Sep 28, 2026IndividualOpen the origin period',
      '$50.00',
    ]);
    expect(line(LINE_HOURLY.id).querySelector('a')?.getAttribute('href')).toBe(
      `/schedule/sessions/${SESSION_ID}`,
    );
  });

  it('has no session link without schedule.view', async () => {
    stub({}, user(['finance_staff'], ['payroll.view', 'payroll.prepare']));
    await openReview();
    expect(
      main().querySelector('[data-period-lines-table] a[href^="/schedule"]'),
    ).toBeNull();
  });

  it('an empty period says "No lines" and $0.00, and still prepares', async () => {
    stub({ [LINES]: () => jsonResponse(200, review(PERIOD_OPEN, [], '0.00')) });
    await openReview();
    expect(main().querySelector('[data-period-no-lines]')?.textContent).toBe(
      'No lines',
    );
    expect(head().Total).toBe('$0.00');
    expect(button('Prepare')?.disabled).toBe(false);
  });

  it('a negative total reads negative, with why prepare will refuse (C7)', async () => {
    stub({
      [LINES]: () =>
        jsonResponse(200, review(PERIOD_OPEN, [LINE_DEDUCTION], '-150.00')),
    });
    await openReview();
    expect(head().Total).toBe('-$150.00');
    // M7-08b E2: an ended one offers carry-forward to payroll.prepare.
    expect(main().querySelector('[data-carry-banner]')?.textContent).toContain(
      "can't be prepared",
    );
  });

  describe('payroll_amount_out_of_range (C8)', () => {
    const outOfRange = () =>
      problemWith(409, 'payroll_amount_out_of_range', {
        details: { class_session_id: SESSION_ID },
      });

    it('the lines failing: an error state with the way out, never an empty table', async () => {
      stub({ [LINES]: outOfRange });
      await open(`/payroll/periods/${PERIOD_ID}`);
      await settleUntil(() => main().querySelector('[data-period-load-error]'));

      expect(main().querySelector('[data-period-lines-table]')).toBeNull();
      expect(alerts(main()).join()).toContain(
        "This period's lines or total are beyond the maximum amount.",
      );
      const help = main().querySelector('[data-out-of-range-help]')!;
      expect(help.textContent).toContain('one item at a time');
      expect(
        [...help.querySelectorAll('a')].map((a) => a.getAttribute('href')),
      ).toEqual(['/payroll/tutors', `/schedule/sessions/${SESSION_ID}`]);
      expect(button('Recalculate')).toBeDefined();
    });

    it('links to the tutor’s items when the list named the period', async () => {
      stub({ [LINES]: outOfRange });
      await open('/payroll/periods');
      await settleUntil(() => main().querySelector('[data-periods-table] a'));
      await click(
        main().querySelector<HTMLAnchorElement>('[data-periods-table] a')!,
      );
      await settleUntil(() => main().querySelector('[data-out-of-range-help]'));
      expect(
        main()
          .querySelector('[data-out-of-range-help] a')
          ?.getAttribute('href'),
      ).toBe(`/payroll/tutors/${TUTOR_ID}#pay-items`);
    });

    it('recalculate answering it shows the same way out', async () => {
      stub({ [RECALCULATE]: outOfRange });
      await openReview();
      await click(button('Recalculate')!);
      await settleUntil(() => main().querySelector('[data-out-of-range-help]'));
      expect(
        main()
          .querySelector('[data-out-of-range-help] a')
          ?.getAttribute('href'),
      ).toBe(`/payroll/tutors/${TUTOR_ID}#pay-items`);
    });
  });
});

describe('actions (D)', () => {
  it.each([
    ['Finance on open', FINANCE, PERIOD_OPEN, ['Recalculate', 'Prepare']],
    ['Finance on prepared', FINANCE, PERIOD_PREPARED, ['Send back']],
    ['the Owner on open', OWNER, PERIOD_OPEN, ['Recalculate', 'Prepare']],
    [
      'the Owner on prepared',
      OWNER,
      PERIOD_PREPARED,
      ['Send back', 'Finalize'],
    ],
    [
      'an Administrator on prepared',
      ADMIN,
      PERIOD_PREPARED,
      ['Send back', 'Finalize'],
    ],
    ['anyone on finalized', OWNER, PERIOD_FINALIZED, []],
  ] as const)('%s', async (_who, as, period, names) => {
    stub({ [LINES]: () => jsonResponse(200, review(period)) }, as);
    await openReview();
    expect(
      [...main().querySelectorAll('[data-period-actions] button')].map(
        (b) => b.textContent,
      ),
    ).toEqual(names);
  });

  it('D14: a finalized period says it is locked', async () => {
    stub({ [LINES]: () => jsonResponse(200, review(PERIOD_FINALIZED)) }, OWNER);
    await openReview();
    expect(main().querySelector('[data-period-locked]')?.textContent).toContain(
      'finalized and locked',
    );
    expect(main().querySelector('[data-period-actions]')).toBeNull();
  });

  it('D1: recalculate replaces the lines and refreshes', async () => {
    const api = stub({
      [RECALCULATE]: () =>
        jsonResponse(200, { line_count: 11, total: '4166.67' }),
    });
    await openReview();
    await click(button('Recalculate')!);
    await settleUntil(() => status() === 'Lines recalculated.');
    expect(order(api).slice(-2)).toEqual([
      `POST /periods/${PERIOD_ID}/recalculate`,
      `GET /periods/${PERIOD_ID}/lines`,
    ]);
  });

  it('D2: a period that has not ended can be previewed, not prepared', async () => {
    const id = FUTURE_PERIOD_ID;
    stub({
      [`GET /api/v1/payroll/periods/${id}/lines`]: () =>
        jsonResponse(200, review(PERIOD_NOT_ENDED)),
    });
    await open(`/payroll/periods/${id}`);
    await settleUntil(() => main().querySelector('[data-period-head]'));
    expect(button('Recalculate (preview)')?.disabled).toBe(false);
    const prepare = button('Prepare')!;
    expect(prepare.disabled).toBe(true);
    const reason = document.getElementById(
      prepare.getAttribute('aria-describedby')!,
    );
    expect(reason?.textContent).toBe(
      'It can be prepared after Jan 15, 2099, when the period has ended.',
    );
  });

  it('D2: 409 payroll_period_not_ended is shown anyway', async () => {
    stub({ [PREPARE]: () => problem(409, 'payroll_period_not_ended') });
    await openReview();
    await click(button('Prepare')!);
    await settleUntil(() => alerts(main()).length > 0);
    expect(alerts(main()).join()).toContain("hasn't ended yet");
  });

  it('prepares, and says it waits for review', async () => {
    stub({
      [PREPARE]: () => transition('prepared'),
      [LINES]: [
        () => jsonResponse(200, review(PERIOD_OPEN)),
        () => jsonResponse(200, review(PERIOD_PREPARED)),
      ],
    });
    await openReview();
    await click(button('Prepare')!);
    await settleUntil(() => status().startsWith('Period prepared.'));
    expect(main().querySelector('[data-period-status]')?.textContent).toBe(
      'Prepared',
    );
  });

  describe('D3: a refused prepare recalculates, refreshes, then says why', () => {
    const unresolved = () =>
      new Response(
        JSON.stringify({
          type: 'about:blank',
          title: 'Problem',
          status: 409,
          code: 'payroll_unresolved_work',
          request_id: 'req-u',
          details: {
            count: 3,
            items: [
              {
                class_session_id: SESSION_ID,
                service_date: '2026-10-05',
                reason: 'no_rate',
              },
            ],
          },
        }),
        {
          status: 409,
          headers: { 'Content-Type': 'application/problem+json' },
        },
      );

    it.each([
      ['payroll_unresolved_work', unresolved],
      ['payroll_negative_total', () => problem(409, 'payroll_negative_total')],
    ] as const)('%s', async (code, answer) => {
      const api = stub({
        [PREPARE]: answer,
        [RECALCULATE]: () =>
          jsonResponse(200, { line_count: 11, total: '4166.67' }),
      });
      await openReview();
      const invalidate = vi.spyOn(app!.queryClient, 'invalidateQueries');
      await click(button('Prepare')!);
      await settleUntil(() => alerts(main()).length > 0);

      expect(order(api).slice(-3)).toEqual([
        `POST /periods/${PERIOD_ID}/prepare`,
        `POST /periods/${PERIOD_ID}/recalculate`,
        `GET /periods/${PERIOD_ID}/lines`,
      ]);
      expect(
        invalidate.mock.calls.map(([filters]) => filters?.queryKey),
      ).toEqual([PAYROLL_PERIODS_KEY, UNRESOLVED_KEY]);
      if (code === 'payroll_unresolved_work') {
        const help = main().querySelector('[data-unresolved-help]')!;
        expect(help.textContent).toContain(
          '3 items of approved work have no pay basis',
        );
        expect(help.textContent).toContain(
          'Oct 5, 2026: Has no rate for this class type',
        );
        expect(
          [...help.querySelectorAll('a')].map((a) => a.getAttribute('href')),
        ).toEqual([
          `/schedule/sessions/${SESSION_ID}`,
          `/payroll/unresolved?tutor=${TUTOR_ID}`,
        ]);
      } else {
        expect(alerts(main()).join()).toContain('below zero');
      }
    });
  });

  it('D4: 409 payroll_period_not_open refreshes and says the status changed', async () => {
    const api = stub({
      [RECALCULATE]: () => problem(409, 'payroll_period_not_open'),
    });
    await openReview();
    await click(button('Recalculate')!);
    await settleUntil(() => alerts(main()).length > 0);
    expect(alerts(main()).join()).toContain("This period's status changed");
    expect(api.calls(LINES)).toHaveLength(2);
  });

  it('a lost answer to prepare refreshes before anyone acts again', async () => {
    let release: () => void = () => {};
    stub({
      [PREPARE]: () => {
        throw new TypeError('Failed to fetch');
      },
      [LINES]: [
        () => jsonResponse(200, review(PERIOD_OPEN)),
        () =>
          new Promise<Response>((resolve) => {
            release = () => resolve(jsonResponse(200, review(PERIOD_PREPARED)));
          }),
      ],
    });
    await openReview();
    await click(button('Prepare')!);
    expect(button('Recalculate')?.disabled).toBe(true);
    release();
    await settleUntil(() => alerts(main()).length > 0);
    expect(alerts(main()).join()).toContain('this may have happened');
    expect(main().querySelector('[data-period-status]')?.textContent).toBe(
      'Prepared',
    );
  });

  describe('D5: send back', () => {
    it('needs a reason of 1 to 1000 characters, then shows the period open', async () => {
      const api = stub(
        {
          [LINES]: [
            () => jsonResponse(200, review(PERIOD_PREPARED)),
            () => jsonResponse(200, review(PERIOD_RETURNED)),
          ],
          [RETURN]: () => transition('open'),
        },
        ADMIN,
      );
      await openReview();
      await click(button('Send back')!);
      const dialog = returnDialog()!;
      expect(dialog.textContent).toContain('It goes back to Finance');

      await click(buttonNamed(dialog, 'Send back'));
      expect(dialog.textContent).toContain('Write a reason.');
      const reason = dialog.querySelector('textarea')!;
      setFieldValue(reason, '😀'.repeat(1001));
      await click(buttonNamed(dialog, 'Send back'));
      expect(dialog.textContent).toContain('Use 1,000 characters or fewer.');
      expect(api.calls(RETURN)).toHaveLength(0);

      setFieldValue(reason, `  ${'😀'.repeat(1000)}  `);
      await click(buttonNamed(dialog, 'Send back'));
      await settleUntil(() => status() === 'Period sent back to Finance.');
      expect(api.calls(RETURN)[0]!.body).toEqual({ reason: '😀'.repeat(1000) });
      expect(returnDialog()).toBeUndefined();
      expect(main().querySelector('[data-period-status]')?.textContent).toBe(
        'Open',
      );
    });

    it('a second reviewer gets not_prepared: the dialog closes and the page refreshes', async () => {
      const api = stub(
        {
          [LINES]: () => jsonResponse(200, review(PERIOD_PREPARED)),
          [RETURN]: () => problem(409, 'payroll_period_not_prepared'),
        },
        ADMIN,
      );
      await openReview();
      await click(button('Send back')!);
      setFieldValue(returnDialog()!.querySelector('textarea')!, 'Revisar');
      await click(buttonNamed(returnDialog()!, 'Send back'));
      await settleUntil(() => alerts(main()).length > 0);
      expect(returnDialog()).toBeUndefined();
      expect(alerts(main()).join()).toContain("This period's status changed");
      expect(api.calls(LINES)).toHaveLength(2);
    });
  });

  describe("QA D1: a period that leaves a reviewer's sight", () => {
    it('their own send-back confirms it on the periods list', async () => {
      stub(
        {
          [LINES]: [
            () => jsonResponse(200, review(PERIOD_PREPARED)),
            () => problem(404, 'not_found'),
          ],
          [RETURN]: () => transition('open'),
          [LIST]: () => jsonResponse(200, EMPTY_PERIODS),
        },
        ADMIN,
      );
      await openReview();
      await click(button('Send back')!);
      setFieldValue(returnDialog()!.querySelector('textarea')!, 'Revisar');
      await click(buttonNamed(returnDialog()!, 'Send back'));
      await settleUntil(
        () => app!.router.state.location.pathname === '/payroll/periods',
      );
      await settleUntil(() => h1() === 'Payroll periods');
      expect(main().querySelector('[data-periods-notice]')?.textContent).toBe(
        "Period sent back to Finance. It's no longer in your review.",
      );
      expect(text()).not.toContain('Page not found');
    });

    it.each([
      ['an action refused as not prepared', true],
      ['a background refresh', false],
    ])(
      "someone else's change, found by %s, says the status changed",
      async (_how, act) => {
        stub(
          {
            [LINES]: [
              () => jsonResponse(200, review(PERIOD_PREPARED)),
              () => problem(404, 'not_found'),
            ],
            [FINALIZE]: () => problem(409, 'payroll_period_not_prepared'),
          },
          ADMIN,
        );
        await openReview();
        if (act) {
          await click(button('Finalize')!);
          await settleUntil(() => finalizeDialog());
          await click(buttonNamed(finalizeDialog()!, 'Finalize'));
        } else {
          await app!.queryClient.invalidateQueries({
            queryKey: PAYROLL_PERIODS_KEY,
          });
        }
        await settleUntil(() => main().querySelector('[data-period-gone]'));
        const gone = main().querySelector('[data-period-gone]')!;
        expect(gone.textContent).toContain(
          "This period's status changed, and it's no longer in your review.",
        );
        expect(gone.querySelector('a')?.getAttribute('href')).toBe(
          '/payroll/periods',
        );
        expect(h1()).not.toBe('Page not found');
        expect(main().querySelector('[data-period-actions]')).toBeNull();
        expect(finalizeDialog()).toBeUndefined();
      },
    );
  });

  describe('finalize (D6-D13)', () => {
    async function openFinalize() {
      await openReview();
      await click(button('Finalize')!);
      await settleUntil(() => finalizeDialog());
    }
    const confirm = () => click(buttonNamed(finalizeDialog()!, 'Finalize'));

    it('D6, D7: repeats the page data and sends its prepared_at', async () => {
      const api = stub(
        {
          [LINES]: [
            () => jsonResponse(200, review(PERIOD_PREPARED)),
            () => jsonResponse(200, review(PERIOD_FINALIZED)),
          ],
          [FINALIZE]: () => transition('finalized'),
        },
        ADMIN,
      );
      await openFinalize();
      const summary = finalizeDialog()!.textContent;
      expect(summary).toContain('Ana Tutora');
      expect(summary).toContain('Oct 1, 2026 – Oct 15, 2026');
      expect(summary).toContain('$4,166.67');
      expect(summary).toContain("can't change");

      await confirm();
      await settleUntil(() => status() === 'Period finalized.');
      const call = api.calls(FINALIZE)[0]!;
      expect(call.body).toEqual({ prepared_at: PERIOD_PREPARED.prepared_at });
      expect(call.headers.get('Idempotency-Key')).toMatch(/^[0-9a-f-]{36}$/);
      expect(finalizeDialog()).toBeUndefined();
      expect(main().querySelector('[data-period-locked]')).not.toBeNull();
    });

    it('D7: review_outdated closes, refreshes and says so; nothing retries', async () => {
      const api = stub(
        {
          [LINES]: () => jsonResponse(200, review(PERIOD_PREPARED)),
          [FINALIZE]: () => problem(409, 'payroll_review_outdated'),
        },
        ADMIN,
      );
      await openFinalize();
      await confirm();
      await settleUntil(() => alerts(main()).length > 0);
      expect(finalizeDialog()).toBeUndefined();
      expect(alerts(main()).join()).toContain('changed since you opened it');
      expect(api.calls(FINALIZE)).toHaveLength(1);
      expect(api.calls(LINES)).toHaveLength(2);
    });

    it('D8: the password dialog, then the same submit with the same key', async () => {
      const api = stub(
        {
          [LINES]: [
            () => jsonResponse(200, review(PERIOD_PREPARED)),
            () => jsonResponse(200, review(PERIOD_FINALIZED)),
          ],
          [FINALIZE]: [
            () => problem(403, 'reauth_required'),
            () => transition('finalized'),
          ],
        },
        ADMIN,
      );
      await openFinalize();
      await confirm();
      const password = dialogTitled('Confirm your password')!;
      typeInto(fieldByLabel(password, 'Password'), 'my-own-password');
      await click(buttonNamed(password, 'Confirm'));
      await settleUntil(() => status() === 'Period finalized.');

      const [first, second] = api.calls(FINALIZE);
      expect(second!.headers.get('Idempotency-Key')).toBe(
        first!.headers.get('Idempotency-Key'),
      );
      expect(second!.body).toEqual(first!.body);
    });

    it('D8: cancelling the password leaves the period and the dialog as they were', async () => {
      const api = stub(
        {
          [LINES]: () => jsonResponse(200, review(PERIOD_PREPARED)),
          [FINALIZE]: () => problem(403, 'reauth_required'),
        },
        ADMIN,
      );
      await openFinalize();
      await confirm();
      await click(
        buttonNamed(dialogTitled('Confirm your password')!, 'Cancel'),
      );
      await settleUntil(() => !dialogTitled('Confirm your password'));

      expect(finalizeDialog()).toBeDefined();
      expect(alerts(finalizeDialog())).toEqual([]);
      expect(alerts(main())).toEqual([]);
      expect(api.calls(FINALIZE)).toHaveLength(1);
      expect(api.calls(LINES)).toHaveLength(1);
    });

    it('D9: a lost answer refreshes, keeps the key for a retry; a new opening gets a new one', async () => {
      const api = stub(
        {
          [LINES]: () => jsonResponse(200, review(PERIOD_PREPARED)),
          [FINALIZE]: [
            () => {
              throw new TypeError('Failed to fetch');
            },
            () => problem(503, 'database_unavailable'),
            () => {
              throw new TypeError('Failed to fetch');
            },
          ],
        },
        ADMIN,
      );
      await openFinalize();
      await confirm();
      await settleUntil(() =>
        finalizeDialog()?.querySelector('[data-finalize-unknown]'),
      );
      expect(finalizeDialog()!.textContent).toContain("won't finalize twice");
      await settleUntil(() => api.calls(LINES).length === 2);

      await confirm();
      await settleUntil(() => api.calls(FINALIZE).length === 2);
      const keys = () =>
        api.calls(FINALIZE).map((call) => call.headers.get('Idempotency-Key'));
      expect(keys()[1]).toBe(keys()[0]);

      await click(buttonNamed(finalizeDialog()!, 'Cancel'));
      await click(button('Finalize')!);
      await settleUntil(() => finalizeDialog());
      await confirm();
      await settleUntil(() => api.calls(FINALIZE).length === 3);
      expect(keys()[2]).not.toBe(keys()[0]);
    });

    it('D9: when the refresh after a lost answer shows it finalized, the dialog closes', async () => {
      stub(
        {
          [LINES]: [
            () => jsonResponse(200, review(PERIOD_PREPARED)),
            () => jsonResponse(200, review(PERIOD_FINALIZED)),
          ],
          [FINALIZE]: () => {
            throw new TypeError('Failed to fetch');
          },
        },
        ADMIN,
      );
      await openFinalize();
      await confirm();
      await settleUntil(() => status() === 'Period finalized.');
      expect(finalizeDialog()).toBeUndefined();
    });

    it.each([
      [
        'the latest preparer',
        user(
          ['administrator', 'finance_staff'],
          ['payroll.view', 'payroll.prepare', 'payroll.finalize'],
          FINANCE_ID,
        ),
        PERIOD_PREPARED,
        'You prepared this period, so someone else must finalize it.',
      ],
      [
        'the period’s own tutor',
        user(['administrator', 'tutor'], ['payroll.finalize'], TUTOR_ID),
        PERIOD_PREPARED,
        'This is your own pay, so someone else must finalize it.',
      ],
      [
        'anyone, with an earlier period open',
        ADMIN,
        {
          ...PERIOD_PREPARED,
          earlier_unfinalized_period_id: EARLIER_PERIOD_ID,
        },
        'An earlier period of this tutor must be finalized first.',
      ],
    ] as const)(
      'D10, D11: Finalize is disabled for %s',
      async (_who, as, period, reason) => {
        stub({ [LINES]: () => jsonResponse(200, review(period)) }, as);
        await openReview();
        const finalize = button('Finalize')!;
        expect(finalize.disabled).toBe(true);
        const why = document.getElementById(
          finalize.getAttribute('aria-describedby')!,
        )!;
        expect(why.textContent).toContain(reason);
        if (period.earlier_unfinalized_period_id) {
          expect(why.querySelector('a')?.getAttribute('href')).toBe(
            `/payroll/periods/${EARLIER_PERIOD_ID}`,
          );
        }
      },
    );

    it('D10: the Owner who prepared it may finalize', async () => {
      stub(
        {
          [LINES]: () =>
            jsonResponse(
              200,
              review({
                ...PERIOD_PREPARED,
                prepared_by: { id: OWNER_ID, display_name: 'Demo Owner' },
              }),
            ),
        },
        OWNER,
      );
      await openReview();
      expect(button('Finalize')?.disabled).toBe(false);
    });

    it.each([
      ['preparer', 'You prepared this period'],
      ['own_pay', 'This is your own pay'],
    ])(
      'D10: 403 payroll_self_finalize (%s) is handled anyway',
      async (reason, said) => {
        stub(
          {
            [LINES]: () => jsonResponse(200, review(PERIOD_PREPARED)),
            [FINALIZE]: () =>
              problemWith(403, 'payroll_self_finalize', {
                details: { reason },
              }),
          },
          ADMIN,
        );
        await openFinalize();
        await confirm();
        await settleUntil(() => alerts(main()).length > 0);
        expect(alerts(main()).join()).toContain(said);
      },
    );

    it('D11: 409 out_of_order links to the oldest period', async () => {
      stub(
        {
          [LINES]: () => jsonResponse(200, review(PERIOD_PREPARED)),
          [FINALIZE]: () =>
            problemWith(409, 'payroll_period_out_of_order', {
              details: {
                earliest_period_id: EARLIER_PERIOD_ID,
                status: 'open',
              },
            }),
        },
        ADMIN,
      );
      await openFinalize();
      await confirm();
      await settleUntil(() => alerts(main()).length > 0);
      const alert = main().querySelector('[role="alert"]')!;
      expect(alert.textContent).toContain('An earlier period');
      expect(alert.querySelector('a')?.getAttribute('href')).toBe(
        `/payroll/periods/${EARLIER_PERIOD_ID}`,
      );
    });

    it('D12: stale closes the dialog and offers Send back', async () => {
      stub(
        {
          [LINES]: () => jsonResponse(200, review(PERIOD_PREPARED)),
          [FINALIZE]: () =>
            problemWith(409, 'payroll_period_stale', {
              details: { added: '1' },
            }),
        },
        ADMIN,
      );
      await openFinalize();
      await confirm();
      await settleUntil(() => alerts(main()).length > 0);
      expect(finalizeDialog()).toBeUndefined();
      const alert = main().querySelector('[role="alert"]')!;
      expect(alert.textContent).toContain('Send it back to Finance');
      await click(buttonNamed(alert, 'Send back'));
      expect(returnDialog()).toBeDefined();
    });

    it('D13: unresolved work at finalize shows the items, with no recalculate', async () => {
      const api = stub(
        {
          [LINES]: () => jsonResponse(200, review(PERIOD_PREPARED)),
          [FINALIZE]: () =>
            problemWith(409, 'payroll_unresolved_work', {
              details: { count: '1' },
            }),
        },
        OWNER,
      );
      await openFinalize();
      await confirm();
      await settleUntil(() => main().querySelector('[data-unresolved-help]'));
      expect(api.calls(RECALCULATE)).toHaveLength(0);
      expect(
        main().querySelector('[data-unresolved-help] a')?.getAttribute('href'),
      ).toBe(`/payroll/unresolved?tutor=${TUTOR_ID}`);
    });

    it('QA D2: after a lost answer and Cancel, nothing looks current until the refresh lands', async () => {
      let release: () => void = () => {};
      const api = stub(
        {
          [LINES]: [
            () => jsonResponse(200, review(PERIOD_PREPARED)),
            () =>
              new Promise<Response>((resolve) => {
                release = () =>
                  resolve(jsonResponse(200, review(PERIOD_FINALIZED)));
              }),
          ],
          [FINALIZE]: () => {
            throw new TypeError('Failed to fetch');
          },
        },
        ADMIN,
      );
      await openFinalize();
      await confirm();
      await settleUntil(() =>
        finalizeDialog()?.querySelector('[data-finalize-unknown]'),
      );
      await click(buttonNamed(finalizeDialog()!, 'Cancel'));
      await settleUntil(() => !finalizeDialog());

      const actions = [
        ...main().querySelectorAll<HTMLButtonElement>(
          '[data-period-actions] button',
        ),
      ];
      expect(actions.length).toBeGreaterThan(0);
      expect(actions.every((b) => b.disabled)).toBe(true);
      expect(alerts(main()).join()).toContain('this may have happened');

      release();
      await settleUntil(() => status() === 'Period finalized.');
      expect(alerts(main())).toEqual([]);
      expect(main().querySelector('[data-period-actions]')).toBeNull();
      expect(api.calls(FINALIZE)).toHaveLength(1);
    });

    it('a failed refresh disables every action until a reload works', async () => {
      stub(
        {
          // A 4xx (the client retries a 5xx): the lines went out of range.
          [LINES]: [
            () => jsonResponse(200, review(PERIOD_PREPARED)),
            () =>
              problemWith(409, 'payroll_amount_out_of_range', {
                details: { class_session_id: SESSION_ID },
              }),
            () => jsonResponse(200, review(PERIOD_PREPARED)),
          ],
        },
        ADMIN,
      );
      await openReview();
      await app!.queryClient.invalidateQueries({
        queryKey: PAYROLL_PERIODS_KEY,
      });
      await settleUntil(() => text().includes('may be out of date'));
      expect(main().querySelector('[data-out-of-range-help]')).not.toBeNull();
      expect(button('Finalize')?.disabled).toBe(true);
      expect(button('Send back')?.disabled).toBe(true);

      await click(buttonNamed(main(), 'Reload'));
      await settleUntil(() => !text().includes('may be out of date'));
      expect(button('Finalize')?.disabled).toBe(false);
    });
  });
});

describe('inbox and notifications (F)', () => {
  const item = (status: string): ApprovalRequestItem =>
    ({
      id: 'a1',
      request_type: 'payroll.period',
      status,
      subject: {
        period_id: PERIOD_ID,
        tutor: { id: TUTOR_ID, display_name: 'Ana Tutora' },
        starts_on: '2026-10-01',
        ends_on: '2026-10-15',
        prepared_by: { display_name: 'Demo Finance' },
        prepared_at: '2026-10-16T15:30:00Z',
      },
      allowed_actions: [],
    }) as unknown as ApprovalRequestItem;

  it('F1: labels the type and summarizes it with no amount', () => {
    const t = i18n.t.bind(i18n);
    expect(typeLabel('payroll.period', t)).toBe('Payroll period');
    expect(
      approvalSummary(item('pending'), t, undefined, (date) => `<${date}>`),
    ).toBe(
      'Ana Tutora · <2026-10-01> – <2026-10-15> · prepared by Demo Finance',
    );
  });

  it('F2: a pending item links to the period page; a decided one does not', () => {
    expect(itemReviewLink(item('pending'))).toBe(
      `/payroll/periods/${PERIOD_ID}`,
    );
    expect(itemReviewLink(item('approved'))).toBeNull();
    const error = new ApiError({
      code: 'payroll_review_route_required',
      status: 409,
      problem: {
        type: 'about:blank',
        title: 'x',
        status: 409,
        code: 'payroll_review_route_required',
        request_id: 'r',
        details: { review_path: `/payroll/periods/${PERIOD_ID}` },
      },
    });
    expect(reviewLinkOf(error)).toBe(`/payroll/periods/${PERIOD_ID}`);
  });

  it('F3: the tutor is told, with the dates, and it opens the period (M7-10b A3)', () => {
    const notification = {
      notification_type: 'payroll.period_finalized',
      params: {
        payroll_period_id: PERIOD_ID,
        starts_on: '2026-10-01',
        ends_on: '2026-10-15',
      },
    };
    expect(notificationMessage(i18n.t.bind(i18n), i18n, notification)).toBe(
      'Your pay for Oct 1, 2026 – Oct 15, 2026 is finalized.',
    );
    expect(
      NOTIFICATION_LINKS['payroll.period_finalized']!(notification.params, {
        userId: TUTOR_ID,
      }),
    ).toBe(`/my-pay/periods/${PERIOD_ID}`);
  });
});

describe('pure rules', () => {
  it('reads the URL filters and writes them back', () => {
    const params = parsePeriodFilters(
      new URLSearchParams(`status=open&tutor=${TUTOR_ID.toUpperCase()}&page=2`),
    );
    expect(params).toEqual({
      status: 'open',
      tutor: TUTOR_ID,
      paid: null,
      page: 2,
    });
    expect(periodSearchParams(params).toString()).toBe(
      `status=open&tutor=${TUTOR_ID}&page=2`,
    );
    expect(
      periodSearchParams({
        status: null,
        tutor: null,
        paid: null,
        page: 1,
      }).toString(),
    ).toBe('');
  });

  it('reads a sign from the string, never "-0.00"', () => {
    expect(isNegativeAmount('-0.01')).toBe(true);
    expect(isNegativeAmount('-0.00')).toBe(false);
    expect(isNegativeAmount('0.00')).toBe(false);
    expect(isNegativeAmount('12.50')).toBe(false);
  });

  it('knows the line kinds, and calls any other unknown', () => {
    expect(lineKindKey('pay_item')).toBe('pay_item');
    expect(lineKindKey('adjustment')).toBe('adjustment');
    expect(lineKindKey('bonus')).toBe('unknown');
  });

  it('shows the actions per status and permission', () => {
    expect(periodActions('open', { prepare: false, finalize: true })).toEqual({
      recalculate: false,
      prepare: false,
      return: false,
      finalize: false,
    });
    expect(
      periodActions('prepared', { prepare: true, finalize: false }),
    ).toEqual({
      recalculate: false,
      prepare: false,
      return: true,
      finalize: false,
    });
  });

  it('blocks finalize in the API order, with the Owner excepted', () => {
    const period = {
      prepared_by: { id: 'u1', display_name: 'U' },
      tutor_user_id: 'u1',
      earlier_unfinalized_period_id: 'p0',
    };
    expect(
      finalizeBlocks(period, { id: 'u1', roles: ['administrator'] }).map(
        (b) => b.kind,
      ),
    ).toEqual(['preparer', 'ownPay', 'earlier']);
    expect(
      finalizeBlocks(period, { id: 'u1', roles: ['owner'] }).map((b) => b.kind),
    ).toEqual(['earlier']);
  });

  it('counts a return reason by code point, trimmed', () => {
    expect(returnReasonError('   ')).toBe('payroll.returnDialog.required');
    expect(returnReasonError('😀'.repeat(1000))).toBeUndefined();
    expect(returnReasonError('😀'.repeat(1001))).toBe(
      'payroll.returnDialog.tooLong',
    );
  });

  it('tells an unknown result from a settled refusal', () => {
    expect(isUnknownResult(new ApiError({ code: 'network_error' }))).toBe(true);
    expect(
      isUnknownResult(new ApiError({ code: 'internal_error', status: 500 })),
    ).toBe(true);
    expect(
      isUnknownResult(
        new ApiError({ code: 'payroll_period_stale', status: 409 }),
      ),
    ).toBe(false);
  });

  it('reads unresolved work details defensively', () => {
    const error = new ApiError({
      code: 'payroll_unresolved_work',
      status: 409,
      problem: {
        type: 'about:blank',
        title: 'x',
        status: 409,
        code: 'payroll_unresolved_work',
        request_id: 'r',
        details: {
          count: 21,
          items: [
            {
              class_session_id: 's',
              service_date: '2026-10-01',
              reason: 'no_rate',
            },
            { nonsense: true },
          ],
        },
      },
    });
    expect(unresolvedWorkDetails(error)).toEqual({
      count: 21,
      items: [
        {
          class_session_id: 's',
          service_date: '2026-10-01',
          reason: 'no_rate',
        },
      ],
    });
  });
});
