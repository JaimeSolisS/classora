import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SessionUserBody } from '@/features/auth/session';
import { problemWith } from '@/features/academics/testing';
import { NUNEZ, NUNEZ_ID } from '@/features/families/fixtures';
import { applyLanguage } from '@/i18n';
import {
  meAs,
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
import { SEARCH_DEBOUNCE_MS } from './AccountsPage';
import {
  ANA,
  CHARGE_OVERDUE,
  CHARGE_PAID,
  CHARGE_PARTIAL,
  CHARGES_PAGE,
  EMPTY_FAMILIES_PAGE,
  FAMILIES_PAGE,
  FAMILY_ID,
  LEDGER_PAGE,
  LEDGER_PAGE_2,
  LUIS,
  PAYMENT_DETAIL,
  PAYMENT_ID,
  PAYMENTS_PAGE,
  SUMMARY,
  SUMMARY_NO_ACCOUNT,
} from './fixtures';

// #379 G1 (A, B, C): the Accounts tab and routes by permission, the family
// search (debounced, in the URL), the account page (summary, per-student
// table, no account, not found), the charge filters in the URL and the
// request, the history with "Load more" and a stale cursor, the payments
// list and a payment's detail, and the family page's link.

const BASE = `/api/v1/families/${FAMILY_ID}/billing-account`;
const SUMMARY_ROUTE = `GET ${BASE}`;
const CHARGES = `GET ${BASE}/charges`;
const LEDGER = `GET ${BASE}/ledger`;
const PAYMENTS = `GET ${BASE}/payments`;
const PAYMENT = `GET ${BASE}/payments/${PAYMENT_ID}`;
const FAMILIES = 'GET /api/v1/families';
const ME = 'GET /api/v1/auth/me';

const user = (
  roles: SessionUserBody['roles'],
  permissions: SessionUserBody['permissions'],
): SessionUserBody => ({ ...TEST_USER, roles, permissions });

const FINANCE = user(
  ['finance_staff'],
  [
    'student.view',
    'guardian.view',
    'billing.view',
    'billing.manage',
    'payment.record',
  ],
);
const VIEWER = user(['finance_staff'], ['guardian.view', 'billing.view']);
const MANAGER_ONLY = user(['administrator'], ['billing.manage']);
const COORDINATOR = user(
  ['academic_coordinator'],
  ['student.view', 'guardian.view', 'schedule.view'],
);

let familyUrls: URL[] = [];
let chargeUrls: URL[] = [];
let ledgerUrls: URL[] = [];
let paymentUrls: URL[] = [];

function stub(
  routes: Record<string, Responder | Responder[]> = {},
  { as = FINANCE }: { as?: SessionUserBody } = {},
): ApiStub {
  return stubApi({
    [ME]: meAs(as),
    [FAMILIES]: (request) => {
      familyUrls.push(new URL(request.url));
      return jsonResponse(200, FAMILIES_PAGE);
    },
    [SUMMARY_ROUTE]: () => jsonResponse(200, SUMMARY),
    [CHARGES]: (request) => {
      chargeUrls.push(new URL(request.url));
      return jsonResponse(200, CHARGES_PAGE);
    },
    [LEDGER]: (request) => {
      const url = new URL(request.url);
      ledgerUrls.push(url);
      return jsonResponse(
        200,
        url.searchParams.get('cursor') ? LEDGER_PAGE_2 : LEDGER_PAGE,
      );
    },
    [PAYMENTS]: (request) => {
      paymentUrls.push(new URL(request.url));
      return jsonResponse(200, PAYMENTS_PAGE);
    },
    [PAYMENT]: () => jsonResponse(200, PAYMENT_DETAIL),
    ...routes,
  });
}

let app: RenderedApp | undefined;

const main = () => app!.container.querySelector('main')!;
const h1 = () => main().querySelector('h1')?.textContent;
const links = (label: string) =>
  [
    ...main().querySelectorAll<HTMLAnchorElement>(
      `nav[aria-label="${label}"] a`,
    ),
  ].map((a) => [a.textContent, a.getAttribute('aria-current')]);
const cells = (row: Element) =>
  [...row.querySelectorAll('td')].map((cell) => cell.textContent);
const row = (selector: string) =>
  main().querySelector<HTMLTableRowElement>(selector)!;
const choice = (root: ParentNode, text: string) => {
  const label = [...root.querySelectorAll('label')].find((candidate) =>
    candidate.textContent?.startsWith(text),
  );
  const found = label?.querySelector('input');
  if (!found) throw new Error(`No choice "${text}"`);
  return found;
};
const section = (heading: string) =>
  [...main().querySelectorAll('section')].find(
    (candidate) => candidate.querySelector('h2')?.textContent === heading,
  )!;

async function renderAt(path: string) {
  app = await renderApp(path);
  await settleUntil(() => main().querySelector('h1'));
}

async function renderAccount(path = `/billing/accounts/${FAMILY_ID}`) {
  await renderAt(path);
  await settleUntil(
    () =>
      main().querySelector('[data-charge-id]') &&
      main().querySelector('[data-ledger-entry]') &&
      main().querySelector('[data-payment-id]'),
  );
}

beforeEach(() => {
  applyLanguage('en');
  familyUrls = [];
  chargeUrls = [];
  ledgerUrls = [];
  paymentUrls = [];
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

describe('navigation (A)', () => {
  it('puts Accounts first and opens it from /billing', async () => {
    stub();
    await renderAt('/billing');
    await settleUntil(() => h1() === 'Family accounts');

    expect(app!.router.state.location.pathname).toBe('/billing/accounts');
    expect(links('Billing sections')).toEqual([
      ['Accounts', 'page'],
      ['Prices', null],
      ['Discounts', null],
      ['Custom prices', null],
      ['Tuition plans', null],
      ['Packages', null],
      ['Unbilled', null],
      ['Settings', null],
    ]);
    expect(document.title).toBe('Family accounts · Classora');
  });

  it('marks Accounts current on an account page', async () => {
    stub();
    await renderAccount();
    expect(links('Billing sections')[0]).toEqual(['Accounts', 'page']);
  });

  it('hides the tab and forbids both pages without billing.view', async () => {
    const api = stub({}, { as: MANAGER_ONLY });
    await renderAt('/billing/prices');
    expect(links('Billing sections').map(([name]) => name)).not.toContain(
      'Accounts',
    );
    app!.unmount();

    for (const path of [
      '/billing/accounts',
      `/billing/accounts/${FAMILY_ID}`,
    ]) {
      await renderAt(path);
      expect(h1()).toBe("You don't have access to this page");
      app!.unmount();
    }
    expect(api.calls(FAMILIES)).toHaveLength(0);
    expect(api.calls(SUMMARY_ROUTE)).toHaveLength(0);
    app = undefined;
  });

  it.each([
    ['billing.view', VIEWER, true],
    ['no billing.view', COORDINATOR, false],
  ] as const)(
    'the family page links to the billing account with %s',
    async (_who, as, shown) => {
      stub(
        {
          [`GET /api/v1/families/${NUNEZ_ID}`]: () => jsonResponse(200, NUNEZ),
        },
        { as },
      );
      await renderAt(`/families/${NUNEZ_ID}`);
      await settleUntil(() => h1() === NUNEZ.name);
      const link = [...main().querySelectorAll('a')].find(
        (a) => a.textContent === 'Billing account',
      );
      expect(link?.getAttribute('href') ?? null).toBe(
        shown ? `/billing/accounts/${NUNEZ_ID}` : null,
      );
    },
  );
});

describe('the accounts list (B1)', () => {
  it('lists families with status and students, linking to their accounts', async () => {
    stub();
    await renderAt('/billing/accounts');
    await settleUntil(() => main().querySelector('tbody'));

    const lopez = row(`[data-family-id="${FAMILY_ID}"]`);
    expect(cells(lopez)).toEqual(['Familia López', 'Active', '2']);
    expect(lopez.querySelector('a')?.getAttribute('href')).toBe(
      `/billing/accounts/${FAMILY_ID}`,
    );
    expect(main().textContent).toContain('1–2 of 2');
    expect(familyUrls.at(-1)?.searchParams.has('q')).toBe(false);
  });

  it('debounces the search, keeps it in the URL and goes back to page 1', async () => {
    stub();
    await renderAt('/billing/accounts?page=2&status=all');
    await settleUntil(() => familyUrls.length >= 1);
    expect(familyUrls.at(-1)?.searchParams.get('offset')).toBe('25');
    expect(familyUrls.at(-1)?.searchParams.get('status')).toBe('all');

    setFieldValue(
      labelled<HTMLInputElement>(main(), 'Search by family name'),
      'López',
    );
    expect(familyUrls).toHaveLength(1);
    await new Promise((resolve) =>
      setTimeout(resolve, SEARCH_DEBOUNCE_MS + 50),
    );
    await settleUntil(() => familyUrls.length >= 2);

    expect(app!.location()).toBe('/billing/accounts?status=all&q=L%C3%B3pez');
    expect(familyUrls.at(-1)?.searchParams.get('q')).toBe('López');
    expect(familyUrls.at(-1)?.searchParams.get('offset')).toBe('0');
  });

  it('shows the no-results state and an error with retry', async () => {
    stub({ [FAMILIES]: () => jsonResponse(200, EMPTY_FAMILIES_PAGE) });
    await renderAt('/billing/accounts?q=zzz');
    await settleUntil(() =>
      main().textContent?.includes('No families match this search.'),
    );
    app!.unmount();

    const api = stub({
      [FAMILIES]: [
        () => problemWith(400, 'bad_request'),
        () => jsonResponse(200, FAMILIES_PAGE),
      ],
    });
    await renderAt('/billing/accounts');
    await settleUntil(() => alerts(main()).length > 0);
    await click(buttonNamed(main(), 'Try again'));
    await settleUntil(() => main().querySelector('tbody'));
    expect(api.calls(FAMILIES)).toHaveLength(2);
  });
});

describe('the account page (C)', () => {
  it('shows the summary and one row per student', async () => {
    stub();
    await renderAccount();

    expect(h1()).toBe('Billing account: Familia López');
    expect(document.title).toBe('Billing account: Familia López · Classora');
    const summary = section('Balance');
    const terms = [...summary.querySelectorAll('dl > div')].map((item) => [
      item.querySelector('dt')?.textContent,
      item.querySelector('dd')?.textContent,
    ]);
    expect(terms).toEqual([
      ['Outstanding', '$2,750.00'],
      ['Account credit', '$300.00'],
      ['Net balance', '$2,450.00'],
      ['Pending', '$450.00'],
      ['Partially paid', '$800.00'],
      ['Overdue', '$1,500.00'],
    ]);
    expect(cells(row(`[data-student-balance="${ANA.id}"]`))).toEqual([
      'Ana López',
      '$1,950.00',
      '$1,500.00',
    ]);
    expect(cells(row(`[data-student-balance="${LUIS.id}"]`))).toEqual([
      'Luis López',
      '$800.00',
      '$0.00',
    ]);
    expect(summary.textContent).not.toContain('No charges or payments yet.');
  });

  it('shows zeros and "No charges or payments yet" without an account', async () => {
    stub({ [SUMMARY_ROUTE]: () => jsonResponse(200, SUMMARY_NO_ACCOUNT) });
    await renderAt(`/billing/accounts/${FAMILY_ID}`);
    await settleUntil(() => section('Balance'));

    expect(section('Balance').textContent).toContain(
      'No charges or payments yet.',
    );
    expect(section('Balance').textContent).toContain('$0.00');
    // No credit: only "Record payment".
    expect(
      [...main().querySelectorAll('button')].map((b) => b.textContent),
    ).not.toContain('Apply account credit');
  });

  it.each([404, 422])('shows the not-found state on %s', async (status) => {
    const api = stub({
      [SUMMARY_ROUTE]: () =>
        problemWith(status, status === 404 ? 'not_found' : 'validation_error'),
    });
    await renderAt(`/billing/accounts/${FAMILY_ID}`);
    await settleUntil(() => h1() === 'Billing account not found');

    expect(main().textContent).toContain(
      "This family doesn't exist, or you can't see it.",
    );
    expect(api.calls(CHARGES)).toHaveLength(0);
  });

  it('shows each charge with its kind, amounts and status', async () => {
    stub();
    await renderAccount();

    expect(cells(row(`[data-charge-id="${CHARGE_PARTIAL.id}"]`))).toEqual([
      'Oct 5, 2026',
      'Luis López',
      'Package purchase',
      '—',
      '$2,000.00',
      '$1,200.00',
      '$800.00',
      'Oct 5, 2026',
      'Partially paid',
    ]);
    expect(cells(row(`[data-charge-id="${CHARGE_OVERDUE.id}"]`))).toEqual([
      'Sep 1, 2026',
      'Ana López',
      'Tuition',
      'Colegiatura septiembre',
      '$1,500.00',
      '$0.00',
      '$1,500.00',
      'Sep 15, 2026',
      'Overdue',
    ]);
    expect(cells(row(`[data-charge-id="${CHARGE_PAID.id}"]`)).at(-1)).toBe(
      'Paid',
    );
  });

  it('keeps the student and status filters in the URL and sends them', async () => {
    stub();
    await renderAccount(`/billing/accounts/${FAMILY_ID}?page=2`);
    expect(chargeUrls.at(-1)?.searchParams.get('offset')).toBe('25');

    await click(choice(section('Charges'), 'Overdue'));
    await settleUntil(() =>
      chargeUrls.at(-1)?.searchParams.getAll('status').includes('overdue'),
    );
    await click(choice(section('Charges'), 'Pending'));
    await settleUntil(
      () => chargeUrls.at(-1)?.searchParams.getAll('status').length === 2,
    );
    expect(chargeUrls.at(-1)?.searchParams.getAll('status')).toEqual([
      'pending',
      'overdue',
    ]);
    expect(chargeUrls.at(-1)?.searchParams.get('offset')).toBe('0');

    setFieldValue(labelled<HTMLSelectElement>(main(), 'Student'), ANA.id);
    await settleUntil(
      () =>
        chargeUrls.at(-1)?.searchParams.get('student_id') === ANA.id &&
        ledgerUrls.at(-1)?.searchParams.get('student_id') === ANA.id,
    );
    expect(app!.location()).toBe(
      `/billing/accounts/${FAMILY_ID}?student_id=${ANA.id}&status=pending&status=overdue`,
    );
  });

  it('reads a deep-linked student and statuses', async () => {
    stub();
    await renderAccount(
      `/billing/accounts/${FAMILY_ID}?student_id=${LUIS.id}&status=paid&status=bogus`,
    );
    expect(chargeUrls.at(-1)?.searchParams.getAll('status')).toEqual(['paid']);
    expect(chargeUrls.at(-1)?.searchParams.get('student_id')).toBe(LUIS.id);
    expect(ledgerUrls.at(-1)?.searchParams.get('student_id')).toBe(LUIS.id);
    expect(labelled<HTMLSelectElement>(main(), 'Student').value).toBe(LUIS.id);
    expect(choice(section('Charges'), 'Paid').checked).toBe(true);
  });

  it('lists the history newest first and loads more on the cursor', async () => {
    stub();
    await renderAccount();
    const history = section('History');
    const rows = [...history.querySelectorAll('tbody tr')].map(cells);
    expect(rows).toEqual([
      ['Oct 12, 2026', 'Payment applied', 'Luis López', '$1,200.00', '$0.00'],
      ['Oct 12, 2026', 'PaymentTransfer', '—', '$1,500.00', '−$1,500.00'],
      [
        'Oct 5, 2026',
        'ChargePackage purchase',
        'Luis López',
        '$2,000.00',
        '+$2,000.00',
      ],
    ]);

    await click(buttonNamed(history, 'Load more'));
    await settleUntil(() => history.querySelectorAll('tbody tr').length === 4);
    expect(ledgerUrls.at(-1)?.searchParams.get('cursor')).toBe('cursor-2');
    expect(cells(history.querySelectorAll('tbody tr')[3]!)).toEqual([
      'Sep 2, 2026',
      'AdjustmentCancellation credit',
      'Ana López',
      '-$150.00',
      '−$150.00',
    ]);
    expect(
      [...history.querySelectorAll('button')].map((b) => b.textContent),
    ).not.toContain('Load more');
  });

  it('starts the history again on invalid_cursor', async () => {
    let calls = 0;
    stub({
      [LEDGER]: (request) => {
        calls += 1;
        const url = new URL(request.url);
        ledgerUrls.push(url);
        return url.searchParams.get('cursor')
          ? problemWith(400, 'invalid_cursor')
          : jsonResponse(200, LEDGER_PAGE);
      },
    });
    await renderAccount();
    await click(buttonNamed(section('History'), 'Load more'));
    await settleUntil(() => calls >= 3);
    expect(ledgerUrls.at(-1)?.searchParams.has('cursor')).toBe(false);
    await settleUntil(
      () => section('History').querySelectorAll('tbody tr').length === 3,
    );
  });

  it('lists the payments and opens one with its allocations', async () => {
    const api = stub();
    await renderAccount();

    expect(cells(row(`[data-payment-id="${PAYMENT_ID}"]`)).slice(0, 8)).toEqual(
      [
        'Oct 12, 2026',
        'Transfer',
        '$1,500.00',
        '$1,200.00',
        '$300.00',
        'SPEI 99812',
        'María López',
        'Pagó en ventanilla',
      ],
    );
    const view = row(`[data-payment-id="${PAYMENT_ID}"]`).querySelector(
      'button',
    )!;
    expect(view.getAttribute('aria-label')).toBe(
      'View the payment of $1,500.00 received Oct 12, 2026',
    );
    await click(view);
    await settleUntil(() =>
      openDialog()?.querySelector('[data-payment-allocation]'),
    );
    await settleUntil(() => openDialog()?.textContent?.includes('Inscripción'));

    const allocations = [
      ...openDialog()!.querySelectorAll('[data-payment-allocation]'),
    ].map(cells);
    expect(allocations).toEqual([
      [
        'Package purchaseOct 5, 2026',
        'Luis López',
        '$1,200.00',
        'Oct 12, 2026',
      ],
      [
        'Inscripción Inglés A1Aug 20, 2026',
        'Ana López',
        '$150.00Released',
        'Oct 12, 2026',
      ],
    ]);
    expect(api.calls(PAYMENT)).toHaveLength(1);
  });

  it('is read-only without payment.record', async () => {
    stub({}, { as: VIEWER });
    await renderAccount();
    const buttons = [...main().querySelectorAll('button')].map(
      (b) => b.textContent,
    );
    expect(buttons).not.toContain('Record payment');
    expect(buttons).not.toContain('Apply account credit');
  });

  it('pages the payments in the URL', async () => {
    stub({
      [PAYMENTS]: (request) => {
        paymentUrls.push(new URL(request.url));
        return jsonResponse(200, { ...PAYMENTS_PAGE, total: 30 });
      },
    });
    await renderAccount();
    await click(
      buttonNamed(
        main().querySelector('nav[aria-label="Payment pages"]')!,
        'Next',
      ),
    );
    await settleUntil(
      () => paymentUrls.at(-1)?.searchParams.get('offset') === '25',
    );
    expect(app!.location()).toBe(
      `/billing/accounts/${FAMILY_ID}?payments_page=2`,
    );
  });
});
