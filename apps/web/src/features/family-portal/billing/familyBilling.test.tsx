import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { NAV_ITEMS } from '@/app/navigation';
import { canAccess } from '@/features/auth/access';
import type { SessionUserBody } from '@/features/auth/session';
import { problemWith } from '@/features/academics/testing';
import {
  CHARGE_OVERDUE,
  LEDGER_PAGE,
  LEDGER_PAGE_2,
  PAYMENT,
  PAYMENT_ID,
  PAYMENT_OLD,
  PAYMENTS_PAGE,
  SUMMARY_NO_ACCOUNT,
} from '@/features/billing/accounts/fixtures';
import { applyLanguage } from '@/i18n';
import {
  meAs,
  problem,
  stubApi,
  TEST_USER,
  type ApiStub,
  type Responder,
} from '@/test/api-stub';
import { click, openDialog, setFieldValue } from '@/test/dom';
import {
  jsonResponse,
  renderApp,
  settle,
  settleUntil,
  type RenderedApp,
} from '@/test/render-app';
import {
  ALL_STATUSES_PAGE,
  ANA,
  CANARIES,
  CHARGE_MOVED,
  DISCOUNTED_DETAIL,
  EMPTY_PACKAGES_PAGE,
  FAMILY_ID,
  GUARDIAN,
  LUIS,
  OTHER_FAMILY_ID,
  OWN_FAMILY,
  OWN_INACTIVE,
  OWN_NONE,
  OWN_OTHER,
  PACKAGE_ACTIVE,
  PACKAGES_PAGE,
  PORTAL_CHARGES_PAGE,
  STAFF_GUARDIAN,
  STAFF_PAYMENT_DETAIL,
  SUMMARY,
  SUMMARY_INACTIVE,
  SUMMARY_ONE_CHILD,
  SUMMARY_OTHER,
  TODAY,
} from './fixtures';
import { expiresSoon, filterStatuses, portalView } from './rules';

// #388 J1: the family billing portal. Access per role (no request for
// anyone but a guardian), the own family (none, inactive), the A4 404
// refetch, the child filter (URL, requests, an unknown id), the summary's
// numbers (the fixture strings, never recomputed), the charge filter's
// statuses, the "changed" due-date marker, the charge detail (discounts,
// adjustment labels), payments with the receipt link, the E4 canaries
// (a staff+guardian user's unredacted responses), packages and their
// usage, the history's "Load more" and a stale cursor, and the card layout.

const BASE = `/api/v1/families/${FAMILY_ID}/billing-account`;
const OWN = 'GET /api/v1/families/own';
const SUMMARY_ROUTE = `GET ${BASE}`;
const CHARGES = `GET ${BASE}/charges`;
const CHARGE = `GET ${BASE}/charges/${CHARGE_OVERDUE.id}`;
const PAYMENTS = `GET ${BASE}/payments`;
const PAYMENT_ROUTE = `GET ${BASE}/payments/${PAYMENT_ID}`;
const PACKAGES = `GET ${BASE}/packages`;
const PACKAGE = `GET ${BASE}/packages/${PACKAGE_ACTIVE.id}`;
const LEDGER = `GET ${BASE}/ledger`;
const ME = 'GET /api/v1/auth/me';

let urls: Record<string, URL[]> = {};
const record = (route: string, request: Request) =>
  (urls[route] ??= []).push(new URL(request.url));

function stub(
  routes: Record<string, Responder | Responder[]> = {},
  { as = GUARDIAN }: { as?: SessionUserBody } = {},
): ApiStub {
  return stubApi({
    [ME]: meAs(as),
    [OWN]: () => jsonResponse(200, OWN_FAMILY),
    [SUMMARY_ROUTE]: () => jsonResponse(200, SUMMARY),
    [CHARGES]: (request) => {
      record(CHARGES, request);
      return jsonResponse(200, PORTAL_CHARGES_PAGE);
    },
    [CHARGE]: () => jsonResponse(200, DISCOUNTED_DETAIL),
    [PAYMENTS]: (request) => {
      record(PAYMENTS, request);
      return jsonResponse(200, PAYMENTS_PAGE);
    },
    [PAYMENT_ROUTE]: () => jsonResponse(200, STAFF_PAYMENT_DETAIL),
    [PACKAGES]: (request) => {
      record(PACKAGES, request);
      return jsonResponse(200, PACKAGES_PAGE);
    },
    [PACKAGE]: () => jsonResponse(200, PACKAGE_ACTIVE),
    [LEDGER]: (request) => {
      const url = new URL(request.url);
      record(LEDGER, request);
      return jsonResponse(
        200,
        url.searchParams.get('cursor') ? LEDGER_PAGE_2 : LEDGER_PAGE,
      );
    },
    ...routes,
  });
}

let app: RenderedApp | undefined;
const main = () => app!.container.querySelector('main')!;
const h1 = () => main().querySelector('h1')?.textContent;
const section = (heading: string) =>
  [...main().querySelectorAll('section')].find(
    (candidate) => candidate.querySelector('h2')?.textContent === heading,
  )!;
const balance = (key: string) =>
  [...main().querySelectorAll(`[data-balance="${key}"] dd`)]
    .map((dd) => dd.textContent)
    .join('');
const select = (label: string) => {
  const found = [...main().querySelectorAll('label')].find(
    (candidate) => candidate.textContent === label,
  );
  return document.getElementById(found!.htmlFor) as HTMLSelectElement;
};
const button = (root: ParentNode, name: string) => {
  const found = [...root.querySelectorAll('button')].find(
    (candidate) =>
      candidate.getAttribute('aria-label') === name ||
      candidate.textContent === name,
  );
  if (!found) throw new Error(`No button "${name}"`);
  return found;
};
const billingRequests = (api: ApiStub) =>
  api.requests.filter((request) => request.path.startsWith('/api/v1/families'));

/** The lazy route, then its chain of queries. */
async function open(path = '/my-billing'): Promise<RenderedApp> {
  const rendered = await renderApp(path);
  await settleUntil(() => {
    const page = rendered.container.querySelector('main');
    return page?.querySelector('h1') && !page.querySelector('[aria-busy]');
  });
  await settle();
  return rendered;
}

beforeEach(() => {
  applyLanguage('en');
  urls = {};
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('rules', () => {
  it('maps the charge filter to the API statuses', () => {
    expect(filterStatuses('open')).toEqual([
      'pending',
      'partially_paid',
      'overdue',
    ]);
    expect(filterStatuses('overdue')).toEqual(['overdue']);
    expect(filterStatuses('paid')).toEqual(['paid']);
    expect(filterStatuses('all')).toEqual([]);
  });

  it('reads the view from the URL, open by default', () => {
    expect(portalView(new URLSearchParams(''))).toEqual({
      student: null,
      status: 'open',
    });
    expect(
      portalView(new URLSearchParams(`status=paid&student=${ANA.id}`)),
    ).toEqual({ student: ANA.id, status: 'paid' });
    expect(portalView(new URLSearchParams('status=bogus&student=x'))).toEqual({
      student: null,
      status: 'open',
    });
  });

  it('marks an active package expiring within 14 days', () => {
    const pkg = (expires_on: string, status = 'active') => ({
      expires_on,
      status,
    });
    expect(expiresSoon(pkg('2026-11-03'), TODAY)).toBe(true);
    expect(expiresSoon(pkg(TODAY), TODAY)).toBe(true);
    expect(expiresSoon(pkg('2026-11-04'), TODAY)).toBe(false);
    expect(expiresSoon(pkg('2026-10-19'), TODAY)).toBe(false);
    expect(expiresSoon(pkg('2026-10-25', 'exhausted'), TODAY)).toBe(false);
  });
});

describe('access (A1, A2)', () => {
  const user = (
    roles: SessionUserBody['roles'],
    permissions: SessionUserBody['permissions'],
  ): SessionUserBody => ({ ...TEST_USER, roles, permissions });

  it.each([
    ['a guardian', GUARDIAN, true],
    ['a staff user who is also a guardian', STAFF_GUARDIAN, true],
    ['a student', user(['student'], ['student.view']), false],
    ['a tutor', user(['tutor'], ['student.view', 'tutor.view']), false],
    [
      'a Coordinator',
      user(['academic_coordinator'], ['student.view', 'schedule.view']),
      false,
    ],
    [
      'Finance',
      user(['finance_staff'], ['billing.view', 'billing.manage']),
      false,
    ],
  ] as const)('the nav item for %s: %s', (_who, as, visible) => {
    const items = NAV_ITEMS.filter((item) => canAccess(as, item));
    expect(items.some((item) => item.to === '/my-billing')).toBe(visible);
  });

  it('is the last family item, with its own label', () => {
    const index = NAV_ITEMS.findIndex((item) => item.to === '/my-billing');
    expect(NAV_ITEMS[index - 1]?.to).toBe('/change-requests');
    expect(NAV_ITEMS[index]?.labelKey).toBe('nav.familyBilling');
  });

  it.each([
    ['a student', user(['student'], ['student.view'])],
    ['a tutor', user(['tutor'], ['student.view', 'tutor.view'])],
    [
      'Finance without the guardian role',
      user(['finance_staff'], ['billing.view', 'billing.manage']),
    ],
  ] as const)('%s gets the forbidden page and no request', async (_who, as) => {
    const api = stub({}, { as });
    app = await open();
    expect(h1()).toBe("You don't have access to this page");
    const nav = [...app.container.querySelectorAll('header nav a')].map(
      (a) => a.textContent,
    );
    expect(nav).not.toContain('Family billing');
    expect(billingRequests(api)).toEqual([]);
  });

  it('shows both items to a staff user who is also a guardian', async () => {
    stub({}, { as: STAFF_GUARDIAN });
    app = await open();
    const nav = [...app.container.querySelectorAll('header nav a')].map(
      (a) => a.textContent,
    );
    expect(nav).toContain('Family billing');
    expect(nav).toContain('Billing');
    expect(h1()).toBe('Family billing — Familia López');
  });
});

describe('the own family (A3, A4)', () => {
  it('without a family: the message and no other request', async () => {
    const api = stub({ [OWN]: () => jsonResponse(200, OWN_NONE) });
    app = await open();
    expect(h1()).toBe('Family billing');
    expect(main().textContent).toContain(
      "Your family's billing account isn't set up yet. Contact the center.",
    );
    expect(billingRequests(api).map((request) => request.path)).toEqual([
      '/api/v1/families/own',
    ]);
  });

  it('an inactive family still works, with a note', async () => {
    stub({
      [OWN]: () => jsonResponse(200, OWN_INACTIVE),
      [SUMMARY_ROUTE]: () => jsonResponse(200, SUMMARY_INACTIVE),
    });
    app = await open();
    expect(h1()).toBe('Family billing — Familia López');
    expect(main().querySelector('[data-family-inactive]')?.textContent).toBe(
      'Inactive family: the account is still shown here.',
    );
    expect(main().querySelectorAll('[data-charge-id]')).toHaveLength(4);
  });

  it('a 404 asks for the own family once more, then shows that answer', async () => {
    const api = stub({
      [OWN]: [
        () => jsonResponse(200, OWN_FAMILY),
        () => jsonResponse(200, OWN_NONE),
      ],
      [CHARGES]: () => problem(404, 'not_found'),
    });
    app = await open();
    await settleUntil(() => main().querySelector('[data-no-family]'));
    expect(api.calls(OWN)).toHaveLength(2);
    expect(main().querySelector('[data-charge-id]')).toBeNull();
    expect(document.title).toContain('Family billing');
  });

  it('a 404 after a move shows the new family, never the old data', async () => {
    const other = `/api/v1/families/${OTHER_FAMILY_ID}/billing-account`;
    const api = stub({
      [OWN]: [
        () => jsonResponse(200, OWN_FAMILY),
        () => jsonResponse(200, OWN_OTHER),
      ],
      [SUMMARY_ROUTE]: () => problem(404, 'not_found'),
      [`GET ${other}`]: () => jsonResponse(200, SUMMARY_OTHER),
      [`GET ${other}/charges`]: () => jsonResponse(200, PORTAL_CHARGES_PAGE),
      [`GET ${other}/payments`]: () => jsonResponse(200, PAYMENTS_PAGE),
      [`GET ${other}/packages`]: () => jsonResponse(200, EMPTY_PACKAGES_PAGE),
      [`GET ${other}/ledger`]: () => jsonResponse(200, LEDGER_PAGE_2),
    });
    app = await open();
    await settleUntil(() => h1() === 'Family billing — Familia Ortiz');
    expect(api.calls(OWN)).toHaveLength(2);
    expect(api.calls(SUMMARY_ROUTE)).toHaveLength(1);
    expect(api.calls(CHARGES)).toHaveLength(0);
  });

  it('a second 404 for the same family shows the error, no loop', async () => {
    const api = stub({ [SUMMARY_ROUTE]: () => problem(404, 'not_found') });
    app = await open();
    await settleUntil(() => api.calls(SUMMARY_ROUTE).length === 2);
    await settle();
    await settle();
    expect(api.calls(OWN)).toHaveLength(2);
    expect(api.calls(SUMMARY_ROUTE)).toHaveLength(2);
    expect(main().querySelector('[role="alert"]')).not.toBeNull();
  });
});

describe('the child filter (B1-B3)', () => {
  it('sends no student by default and keeps the choice in the URL', async () => {
    stub();
    app = await open();
    expect(urls[CHARGES]!.at(-1)!.searchParams.has('student_id')).toBe(false);
    setFieldValue(select('Child'), LUIS.id);
    await settle();
    expect(app.location()).toBe(`/my-billing?student=${LUIS.id}`);
    for (const route of [CHARGES, PACKAGES, LEDGER]) {
      expect(urls[route]!.at(-1)!.searchParams.get('student_id')).toBe(LUIS.id);
    }
    // Payments are family-wide.
    expect(
      urls[PAYMENTS]!.every((url) => !url.searchParams.has('student_id')),
    ).toBe(true);
    expect(main().querySelector('[data-family-wide]')?.textContent).toBe(
      'Payments apply to the whole family.',
    );
    // The child's balance cards, the family's credit and net balance.
    expect(balance('outstanding')).toBe('$800.00For Luis López');
    expect(balance('overdue')).toBe('$0.00For Luis López');
    expect(balance('credit')).toBe('$300.00Whole family');
  });

  it('an unknown or foreign ?student= is "All children", never sent', async () => {
    stub();
    app = await open(`/my-billing?student=${OTHER_FAMILY_ID}`);
    expect(select('Child').value).toBe('');
    for (const route of [CHARGES, PACKAGES, LEDGER]) {
      expect(
        urls[route]!.every((url) => !url.searchParams.has('student_id')),
      ).toBe(true);
    }
  });

  it('one child: no selector, the name under the h1', async () => {
    stub({ [SUMMARY_ROUTE]: () => jsonResponse(200, SUMMARY_ONE_CHILD) });
    app = await open();
    expect(
      [...main().querySelectorAll('label')].map((label) => label.textContent),
    ).not.toContain('Child');
    expect(main().textContent).toContain('Charges and payments for Ana López');
  });
});

describe('the summary (C1-C3)', () => {
  it('shows the API strings, formatted, never recomputed', async () => {
    stub();
    app = await open();
    expect(document.title).toContain('Family billing — Familia López');
    expect(balance('outstanding')).toBe('$2,750.00');
    expect(balance('overdue')).toBe('$1,500.00 (has overdue charges)');
    expect(balance('credit')).toBe('$300.00');
    expect(balance('net')).toBe('$2,450.00');
    const ana = main().querySelector(`[data-student-balance="${ANA.id}"]`)!;
    expect(ana.textContent).toContain('$1,950.00');
    expect(ana.textContent).toContain('$1,500.00');
    expect(main().textContent).toContain('As of Oct 20, 2026.');
  });

  it('no account: zeros, the message, and every section still asks', async () => {
    const api = stub({
      [SUMMARY_ROUTE]: () => jsonResponse(200, SUMMARY_NO_ACCOUNT),
      [CHARGES]: () =>
        jsonResponse(200, { items: [], total: 0, limit: 25, offset: 0 }),
      [PAYMENTS]: () =>
        jsonResponse(200, { items: [], total: 0, limit: 25, offset: 0 }),
      [PACKAGES]: () => jsonResponse(200, EMPTY_PACKAGES_PAGE),
      [LEDGER]: () => jsonResponse(200, { items: [], next_cursor: null }),
    });
    app = await open();
    expect(main().textContent).toContain('No charges or payments yet.');
    expect(balance('outstanding')).toBe('$0.00');
    expect(section('Payments').textContent).toContain('No payments yet.');
    expect(section('Prepaid packages').textContent).toContain('No packages.');
    expect(section('History').textContent).toContain('No movements yet.');
    for (const route of [CHARGES, PAYMENTS, PACKAGES, LEDGER]) {
      expect(api.calls(route)).toHaveLength(1);
    }
    expect(main().querySelector('[role="alert"]')).toBeNull();
  });
});

describe('charges (D1-D4)', () => {
  it('maps the status filter to the API, open by default', async () => {
    stub({
      [CHARGES]: (request) => {
        record(CHARGES, request);
        return jsonResponse(200, ALL_STATUSES_PAGE);
      },
    });
    app = await open();
    expect(urls[CHARGES]!.at(-1)!.searchParams.getAll('status')).toEqual([
      'pending',
      'partially_paid',
      'overdue',
    ]);
    setFieldValue(select('Show'), 'paid');
    await settle();
    expect(app.location()).toBe('/my-billing?status=paid');
    expect(urls[CHARGES]!.at(-1)!.searchParams.getAll('status')).toEqual([
      'paid',
    ]);
    setFieldValue(select('Show'), 'all');
    await settle();
    expect(urls[CHARGES]!.at(-1)!.searchParams.has('status')).toBe(false);
    // Every status has its text badge.
    const badges = [
      ...main().querySelectorAll('[data-slot="charge-status-badge"]'),
    ].map((badge) => badge.textContent);
    expect(badges).toEqual([
      'Pending',
      'Partially paid',
      'Overdue',
      'Paid',
      'Void',
    ]);
  });

  it('opens on ?status=overdue (the reminder link)', async () => {
    stub();
    app = await open('/my-billing?status=overdue');
    expect(select('Show').value).toBe('overdue');
    expect(select('Child').value).toBe('');
    expect(urls[CHARGES]!.at(-1)!.searchParams.getAll('status')).toEqual([
      'overdue',
    ]);
  });

  it('marks a moved due date with the original date, never a reason', async () => {
    stub();
    app = await open();
    const moved = main().querySelector(
      `[data-charge-id="${CHARGE_MOVED.id}"]`,
    )!;
    const marker = moved.querySelector('[data-due-date-changed]')!;
    expect(marker.textContent).toBe('Changed. Original due date: Oct 25, 2026');
    expect(marker.className).toContain('size-11');
  });

  it('a charge detail: discounts, adjustment labels, payments applied', async () => {
    stub();
    app = await open();
    await click(
      button(
        main(),
        'Details of Colegiatura septiembre for Ana López, Sep 1, 2026',
      ),
    );
    await settleUntil(() =>
      openDialog()?.querySelector('[data-charge-detail]'),
    );
    const dialog = openDialog()!;
    expect(dialog.textContent).toContain('List price$2,000.00');
    const discounts = [
      ...dialog.querySelectorAll('[data-charge-discount]'),
    ].map((line) => line.textContent);
    expect(discounts).toEqual([
      'Beca deportiva ($200.00)$200.00',
      'Hermanos (10 %)$200.00',
    ]);
    expect(dialog.textContent).toContain('Amount after discounts$1,600.00');
    const adjustments = [
      ...dialog.querySelectorAll('[data-charge-adjustment]'),
    ].map((line) => line.textContent);
    expect(adjustments).toEqual([
      'Cancellation creditSep 10, 2026−$100.00',
      'AdjustmentSep 12, 2026−$10.00',
    ]);
    expect(
      dialog.querySelector('[data-charge-allocation]')?.textContent,
    ).toContain('Released');
    expect(dialog.textContent).toContain('Net$1,500.00');
  });
});

describe('payments (E1-E4)', () => {
  it('shows the receipt link, or "—" before receipts', async () => {
    stub();
    app = await open();
    const row = main().querySelector(`[data-payment-id="${PAYMENT.id}"]`)!;
    const link = row.querySelector('a')!;
    expect(link.getAttribute('href')).toBe(
      `${BASE}/receipts/${PAYMENT.receipt!.id}/pdf`,
    );
    expect(link.getAttribute('download')).toBe('R-2026-000123.pdf');
    expect(link.textContent).toBe('Download receipt');
    expect(row.textContent).toContain('R-2026-000123');
    expect(row.textContent).toContain('Transfer');
    expect(row.textContent).toContain('Not applied (credit)$300.00');
    const old = main().querySelector(`[data-payment-id="${PAYMENT_OLD.id}"]`)!;
    expect(old.querySelector('a')).toBeNull();
    expect(old.textContent).toContain('Receipt—');
  });

  it('never renders staff-only fields, even unredacted (E4)', async () => {
    stub(
      {
        [PAYMENT_ROUTE]: () => jsonResponse(200, STAFF_PAYMENT_DETAIL),
      },
      { as: STAFF_GUARDIAN },
    );
    app = await open();
    await click(
      button(
        main(),
        'Details of the payment of $1,500.00 received Oct 12, 2026',
      ),
    );
    await settleUntil(() =>
      openDialog()?.querySelector('[data-payment-detail]'),
    );
    const dialog = openDialog()!;
    expect(dialog.querySelector('[data-payment-reversal]')?.textContent).toBe(
      'ReversalReversed onOct 15, 2026',
    );
    expect(
      dialog.querySelector('[data-payment-refund]')?.textContent,
    ).toContain('$100.00');
    expect(dialog.querySelector('[data-refund-withdrawal]')).toBeNull();
    expect(dialog.querySelectorAll('[data-payment-allocation]')).toHaveLength(
      2,
    );
    // The charge detail carries a staff reason too.
    await click(button(dialog, 'Close'));
    await click(
      button(
        main(),
        'Details of Colegiatura septiembre for Ana López, Sep 1, 2026',
      ),
    );
    await settleUntil(() =>
      openDialog()?.querySelector('[data-charge-detail]'),
    );
    const text = `${main().textContent} ${dialog.textContent} ${openDialog()!.textContent}`;
    for (const canary of CANARIES) {
      expect(text).not.toContain(canary);
    }
    expect(text).not.toContain('Note');
    expect(text).not.toContain('Reason');
  });
});

describe('packages (F1, F2)', () => {
  it('shows each package and opens its usage', async () => {
    stub();
    app = await open();
    const card = main().querySelector(
      `[data-package-id="${PACKAGE_ACTIVE.id}"]`,
    )!;
    expect(card.textContent).toContain('Álgebra 10');
    expect(card.textContent).toContain('Luis López');
    expect(card.textContent).toContain('7 / 10 sessions');
    expect(card.textContent).toContain('Active');
    expect(card.querySelector('[data-package-expires-soon]')?.textContent).toBe(
      'Expires soon',
    );
    expect(section('Prepaid packages').textContent).toContain('Any subject');
    expect(section('Prepaid packages').textContent).toContain('Used up');
    await click(button(main(), 'Usage of Álgebra 10 for Luis López'));
    await settleUntil(() =>
      openDialog()?.querySelector('[data-package-detail]'),
    );
    const moves = [
      ...openDialog()!.querySelectorAll('[data-package-transaction]'),
    ].map((move) => move.textContent);
    expect(moves).toEqual([
      'GrantedOct 5, 2026+10 sessions',
      'Used for session on Oct 8, 2026Oct 8, 2026−1 session',
    ]);
  });
});

describe('history (G1)', () => {
  it('loads more, and starts again on a stale cursor', async () => {
    stub();
    app = await open();
    const history = section('History');
    expect(history.querySelectorAll('[data-ledger-entry]')).toHaveLength(3);
    await click(button(history, 'Load more'));
    await settleUntil(
      () =>
        section('History').querySelectorAll('[data-ledger-entry]').length === 4,
    );
    expect(
      section('History').querySelector('[data-ledger-entry="adjustment"]')
        ?.textContent,
    ).toContain('Cancellation credit');

    app.unmount();
    urls = {};
    stub({
      [LEDGER]: [
        (request) => {
          record(LEDGER, request);
          return jsonResponse(200, LEDGER_PAGE);
        },
        () => problemWith(400, 'invalid_cursor'),
        (request) => {
          record(LEDGER, request);
          return jsonResponse(200, LEDGER_PAGE);
        },
      ],
    });
    app = await open();
    await click(button(section('History'), 'Load more'));
    await settleUntil(() => (urls[LEDGER]?.length ?? 0) >= 2);
    expect(urls[LEDGER]!.at(-1)!.searchParams.has('cursor')).toBe(false);
  });
});

describe('the phone layout (I1)', () => {
  it('renders every list as cards, with 44 px controls', async () => {
    stub();
    app = await open();
    expect(main().querySelector('table')).toBeNull();
    expect(main().querySelectorAll('li[data-charge-id]')).toHaveLength(4);
    expect(main().querySelectorAll('li[data-payment-id]')).toHaveLength(2);
    expect(main().querySelectorAll('li[data-ledger-entry]')).toHaveLength(3);
    for (const control of main().querySelectorAll('select, main li button')) {
      expect(control.className).toMatch(/min-h-11|h-11|size-11/);
    }
    expect(main().querySelectorAll('h2')).toHaveLength(5);
  });

  it('the charge dialog is a full-screen sheet below 768 px', async () => {
    stub();
    app = await open();
    await click(
      button(
        main(),
        'Details of Colegiatura septiembre for Ana López, Sep 1, 2026',
      ),
    );
    await settleUntil(() =>
      openDialog()?.querySelector('[data-charge-detail]'),
    );
    expect(openDialog()!.className).toContain('max-md:h-dvh');
  });
});
