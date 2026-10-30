import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SessionUserBody } from '@/features/auth/session';
import { problemWith } from '@/features/academics/testing';
import { applyLanguage } from '@/i18n';
import { meAs, stubApi, TEST_USER, type Responder } from '@/test/api-stub';
import { alerts, click, labelled, setFieldValue } from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  settleUntil,
  type RenderedApp,
} from '@/test/render-app';
import {
  EMPTY_OVERDUE_PAGE,
  LOPEZ_ID,
  ORTIZ_ID,
  OVERDUE_PAGE,
} from './fixtures';

// #382 F1 (A, B): the Overdue tab and route by permission, then the
// overdue page: the totals, the rows and their links (to the account,
// filtered to its overdue charges), the filters and the page in the URL
// and the request, the empty and error states, and the cards below `lg`.

const OVERDUE = 'GET /api/v1/billing/overdue-accounts';
const ME = 'GET /api/v1/auth/me';

const user = (
  roles: SessionUserBody['roles'],
  permissions: SessionUserBody['permissions'],
): SessionUserBody => ({ ...TEST_USER, roles, permissions });

const FINANCE = user(
  ['finance_staff'],
  ['guardian.view', 'billing.view', 'billing.manage', 'payment.record'],
);
const VIEWER = user(['finance_staff'], ['guardian.view', 'billing.view']);
const MANAGER_ONLY = user(['administrator'], ['billing.manage']);

let overdueUrls: URL[] = [];

function stub(
  routes: Record<string, Responder | Responder[]> = {},
  { as = FINANCE }: { as?: SessionUserBody } = {},
) {
  return stubApi({
    [ME]: meAs(as),
    [OVERDUE]: (request) => {
      overdueUrls.push(new URL(request.url));
      return jsonResponse(200, OVERDUE_PAGE);
    },
    ...routes,
  });
}

let app: RenderedApp | undefined;

const main = () => app!.container.querySelector('main')!;
const h1 = () => main().querySelector('h1')?.textContent;
const tabs = () =>
  [
    ...main().querySelectorAll<HTMLAnchorElement>(
      'nav[aria-label="Billing sections"] a',
    ),
  ].map((a) => [a.textContent, a.getAttribute('aria-current')]);
const cells = (row: Element) =>
  [...row.querySelectorAll('td')].map((cell) => cell.textContent);
const terms = (root: ParentNode) =>
  [...root.querySelectorAll('dl > div')].map((item) => [
    item.querySelector('dt')?.textContent,
    item.querySelector('dd')?.textContent,
  ]);

async function renderAt(path: string) {
  app = await renderApp(path);
  await settleUntil(() => main().querySelector('h1'));
}

async function renderOverdue(path = '/billing/overdue') {
  await renderAt(path);
  await settleUntil(() => main().querySelector('[data-overdue-account]'));
}

beforeEach(() => {
  applyLanguage('en');
  overdueUrls = [];
  vi.useFakeTimers({ toFake: ['Date'] });
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

describe('navigation (A)', () => {
  it('puts Overdue right after Accounts for billing.view holders', async () => {
    stub({}, { as: VIEWER });
    await renderOverdue();

    expect(tabs()).toEqual([
      ['Accounts', null],
      ['Overdue', 'page'],
      ['Tuition plans', null],
      ['Packages', null],
      ['Unbilled', null],
      ['Settings', null],
    ]);
    expect(h1()).toBe('Overdue accounts');
    expect(document.title).toBe('Overdue accounts · Classora');
  });

  it('hides the tab and forbids the page without billing.view', async () => {
    const api = stub({}, { as: MANAGER_ONLY });
    await renderAt('/billing/prices');
    expect(tabs().map(([name]) => name)).not.toContain('Overdue');
    app!.unmount();

    await renderAt('/billing/overdue');
    expect(h1()).toBe("You don't have access to this page");
    expect(api.calls(OVERDUE)).toHaveLength(0);
  });
});

describe('the overdue page (B)', () => {
  it('shows the totals as tiles', async () => {
    stub();
    await renderOverdue();

    const totals = [...main().querySelectorAll('section')].find(
      (section) => section.querySelector('h2')?.textContent === 'Totals',
    )!;
    expect(terms(totals)).toEqual([
      ['Accounts', '27'],
      ['Overdue total', '$48,250.50'],
      ['1–30 days', '$10,200.00'],
      ['31–60 days', '$15,050.50'],
      ['61–90 days', '$8,000.00'],
      ['Over 90 days', '$15,000.00'],
    ]);
    expect(totals.textContent).toContain('As of Oct 20, 2026.');
  });

  it('shows one row per account, linking to its overdue charges', async () => {
    stub();
    await renderOverdue();

    const headers = [...main().querySelectorAll('thead th')].map(
      (th) => th.textContent,
    );
    expect(headers).toEqual([
      'Family',
      'Overdue',
      'Overdue charges',
      'Oldest due date',
      'Days overdue',
      '1–30 days',
      '31–60 days',
      '61–90 days',
      'Over 90 days',
      'Outstanding',
      'Account credit',
      'Last reminder',
    ]);
    const ortiz = main().querySelector(
      `tr[data-overdue-account="${ORTIZ_ID}"]`,
    )!;
    // The inactive family carries its badge; the reminder is a center-zone date.
    expect(cells(ortiz)).toEqual([
      'Familia OrtizInactive',
      '$3,200.00',
      '3',
      'Jun 30, 2026',
      '112',
      '$200.00',
      '$1,000.00',
      '$0.00',
      '$2,000.00',
      '$3,400.00',
      '$0.00',
      'Oct 19, 2026',
    ]);
    const lopez = main().querySelector(
      `tr[data-overdue-account="${LOPEZ_ID}"]`,
    )!;
    expect(cells(lopez)[0]).toBe('Familia López');
    expect(cells(lopez)[10]).toBe('$300.00');
    expect(cells(lopez).at(-1)).toBe('—');
    expect(lopez.querySelector('a')?.getAttribute('href')).toBe(
      `/billing/accounts/${LOPEZ_ID}?status=overdue`,
    );
    // The API's order is kept.
    expect(
      [...main().querySelectorAll('tr[data-overdue-account]')].map((row) =>
        row.getAttribute('data-overdue-account'),
      ),
    ).toEqual([ORTIZ_ID, LOPEZ_ID]);
  });

  it('opens the account filtered to its overdue charges', async () => {
    stub({
      [`GET /api/v1/families/${LOPEZ_ID}/billing-account`]: () =>
        new Promise<Response>(() => {}),
    });
    await renderOverdue();

    await click(
      main().querySelector<HTMLAnchorElement>(
        `tr[data-overdue-account="${LOPEZ_ID}"] a`,
      )!,
    );
    await settleUntil(
      () => app!.location() === `/billing/accounts/${LOPEZ_ID}?status=overdue`,
    );
  });

  it('shows each account as a card with the buckets collapsed', async () => {
    stub();
    await renderOverdue();

    const card = main().querySelector(`li[data-overdue-card="${ORTIZ_ID}"]`)!;
    expect(card.querySelector('a')?.getAttribute('href')).toBe(
      `/billing/accounts/${ORTIZ_ID}?status=overdue`,
    );
    const [shown, collapsed] = [...card.querySelectorAll('dl')];
    expect(terms(shown!)).toEqual([
      ['Overdue', '$3,200.00'],
      ['Days overdue', '112 days'],
      ['Oldest due date', 'Jun 30, 2026'],
    ]);
    const details = card.querySelector('details')!;
    expect(details.open).toBe(false);
    expect(details.querySelector('summary')?.textContent).toBe(
      'Aging and balance',
    );
    expect(details.contains(collapsed!)).toBe(true);
    expect(terms(collapsed!).slice(0, 4)).toEqual([
      ['1–30 days', '$200.00'],
      ['31–60 days', '$1,000.00'],
      ['61–90 days', '$0.00'],
      ['Over 90 days', '$2,000.00'],
    ]);
    // The table is for `lg` and up, the cards below it.
    expect(
      main().querySelector('table')?.parentElement?.parentElement?.className,
    ).toContain('hidden lg:block');
    expect(card.parentElement?.className).toContain('lg:hidden');
  });

  it('keeps the filters and the page in the URL and sends them', async () => {
    stub();
    await renderOverdue('/billing/overdue?min_days_overdue=31&page=2');
    expect(overdueUrls.at(-1)?.searchParams.get('min_days_overdue')).toBe('31');
    expect(overdueUrls.at(-1)?.searchParams.get('family_status')).toBe('all');
    expect(overdueUrls.at(-1)?.searchParams.get('offset')).toBe('25');
    expect(
      labelled<HTMLSelectElement>(main(), 'Minimum days overdue').value,
    ).toBe('31');

    setFieldValue(
      labelled<HTMLSelectElement>(main(), 'Minimum days overdue'),
      '91',
    );
    await settleUntil(
      () => overdueUrls.at(-1)?.searchParams.get('min_days_overdue') === '91',
    );
    expect(app!.location()).toBe('/billing/overdue?min_days_overdue=91');
    expect(overdueUrls.at(-1)?.searchParams.get('offset')).toBe('0');

    setFieldValue(
      labelled<HTMLSelectElement>(main(), 'Family status'),
      'inactive',
    );
    await settleUntil(
      () =>
        overdueUrls.at(-1)?.searchParams.get('family_status') === 'inactive',
    );
    expect(app!.location()).toBe(
      '/billing/overdue?min_days_overdue=91&family_status=inactive',
    );

    // "Any" is the default and leaves the URL.
    setFieldValue(
      labelled<HTMLSelectElement>(main(), 'Minimum days overdue'),
      '1',
    );
    await settleUntil(
      () => overdueUrls.at(-1)?.searchParams.get('min_days_overdue') === '1',
    );
    expect(app!.location()).toBe('/billing/overdue?family_status=inactive');
  });

  it('pages through the accounts', async () => {
    stub();
    await renderOverdue();
    expect(main().textContent).toContain('1–2 of 27');

    await click(buttonNamed(main(), 'Next'));
    await settleUntil(
      () => overdueUrls.at(-1)?.searchParams.get('offset') === '25',
    );
    expect(app!.location()).toBe('/billing/overdue?page=2');
  });

  it('ignores filters it does not know', async () => {
    stub();
    await renderOverdue('/billing/overdue?min_days_overdue=7&family_status=x');
    expect(overdueUrls.at(-1)?.searchParams.get('min_days_overdue')).toBe('1');
    expect(overdueUrls.at(-1)?.searchParams.get('family_status')).toBe('all');
  });

  it('shows the empty state and an error with retry', async () => {
    stub({ [OVERDUE]: () => jsonResponse(200, EMPTY_OVERDUE_PAGE) });
    await renderAt('/billing/overdue');
    await settleUntil(() =>
      main().textContent?.includes('No overdue accounts.'),
    );
    expect(main().querySelector('table')).toBeNull();
    app!.unmount();

    const api = stub({
      [OVERDUE]: [
        () => problemWith(400, 'bad_request'),
        () => jsonResponse(200, OVERDUE_PAGE),
      ],
    });
    await renderAt('/billing/overdue');
    await settleUntil(() => alerts(main()).length > 0);
    await click(buttonNamed(main(), 'Try again'));
    await settleUntil(() => main().querySelector('[data-overdue-account]'));
    expect(api.calls(OVERDUE)).toHaveLength(2);
  });
});
