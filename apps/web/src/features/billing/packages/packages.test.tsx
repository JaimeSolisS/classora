import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SessionUserBody } from '@/features/auth/session';
import { problemWith } from '@/features/academics/testing';
import { PICKER_DEBOUNCE_MS } from '@/features/guardians/SearchPicker';
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
import type { PackagePage } from './api';
import {
  ACTIVE_PRODUCTS,
  EMPTY_PACKAGES_PAGE,
  FAMILY_LOPEZ,
  PACKAGE_ACTIVE,
  PACKAGE_ACTIVE_DETAIL,
  PACKAGE_EXHAUSTED,
  PACKAGE_EXPIRED,
  PACKAGE_EXPIRED_DETAIL,
  PACKAGE_SOLD_DETAIL,
  PACKAGES_PAGE,
  PRODUCT_HOURS,
  PRODUCT_NOT_OFFERED,
  PRODUCT_SESSIONS,
  PURCHASE_CHARGE,
  SESSION_ID,
  STUDENT_ANA,
} from './fixtures';

// #377 G1 (C, D, E): the sold list (rows, status badges, the status,
// student and family filters in the URL and the request, empty, error
// with retry, read-only without billing.manage); the sell dialog (the
// products with a disabled one not offered today, the summary, success
// with an Idempotency-Key header, the same key on a retry after a network
// error, a new key after a success, each 409, idempotency_key_reused, a
// 422 on its field); and the package page (summary with the purchase
// charge, the history labels with the session link, the rules note).

const LIST = 'GET /api/v1/billing/packages';
const SELL = 'POST /api/v1/billing/packages';
const PRODUCTS = 'GET /api/v1/billing/package-products';
const ME = 'GET /api/v1/auth/me';
const CHARGES = `GET /api/v1/families/${FAMILY_LOPEZ.id}/billing-account/charges`;

const user = (
  roles: SessionUserBody['roles'],
  permissions: SessionUserBody['permissions'],
): SessionUserBody => ({ ...TEST_USER, roles, permissions });

const FINANCE = user(
  ['finance_staff'],
  [
    'student.view',
    'guardian.view',
    'schedule.view',
    'billing.view',
    'billing.manage',
  ],
);
const VIEWER = user(['finance_staff'], ['billing.view']);

const studentsPage = {
  items: [
    {
      id: STUDENT_ANA.id,
      given_names: 'Ana',
      family_names: 'López',
      is_active: true,
      is_minor: true,
      school_grade: null,
      education_level: null,
    },
  ],
  total: 1,
  limit: 10,
  offset: 0,
};
const familiesPage = {
  items: [
    {
      id: FAMILY_LOPEZ.id,
      name: FAMILY_LOPEZ.name,
      is_active: true,
      student_count: 1,
      guardian_count: 1,
    },
  ],
  total: 1,
  limit: 10,
  offset: 0,
};

let listUrls: URL[] = [];
let productUrls: URL[] = [];
let chargeUrls: URL[] = [];

function stub(
  routes: Record<string, Responder | Responder[]> = {},
  {
    page = PACKAGES_PAGE,
    as = FINANCE,
  }: { page?: PackagePage; as?: SessionUserBody } = {},
): ApiStub {
  return stubApi({
    [ME]: meAs(as),
    [LIST]: (request) => {
      listUrls.push(new URL(request.url));
      return jsonResponse(200, page);
    },
    [PRODUCTS]: (request) => {
      productUrls.push(new URL(request.url));
      return jsonResponse(200, {
        items: ACTIVE_PRODUCTS,
        total: ACTIVE_PRODUCTS.length,
        limit: 100,
        offset: 0,
      });
    },
    'GET /api/v1/students': () => jsonResponse(200, studentsPage),
    [`GET /api/v1/students/${STUDENT_ANA.id}`]: () =>
      jsonResponse(200, { ...studentsPage.items[0] }),
    'GET /api/v1/families': () => jsonResponse(200, familiesPage),
    [`GET /api/v1/families/${FAMILY_LOPEZ.id}`]: () =>
      jsonResponse(200, { ...familiesPage.items[0] }),
    [`GET /api/v1/billing/packages/${PACKAGE_ACTIVE.id}`]: () =>
      jsonResponse(200, PACKAGE_ACTIVE_DETAIL),
    [`GET /api/v1/billing/packages/${PACKAGE_SOLD_DETAIL.id}`]: () =>
      jsonResponse(200, PACKAGE_SOLD_DETAIL),
    [CHARGES]: (request) => {
      chargeUrls.push(new URL(request.url));
      return jsonResponse(200, {
        items: [PURCHASE_CHARGE],
        total: 1,
        limit: 100,
        offset: 0,
      });
    },
    ...routes,
  });
}

let app: RenderedApp | undefined;

const main = () => app!.container.querySelector('main')!;
const h1 = () => main().querySelector('h1')?.textContent;
const dialog = () => openDialog()!;
const rowOf = (id: string) =>
  main().querySelector<HTMLTableRowElement>(`[data-package-id="${id}"]`)!;
const cells = (row: Element) =>
  [...row.querySelectorAll('td')].map((cell) => cell.textContent);
const input = (root: ParentNode, label: string) =>
  labelled<HTMLInputElement>(root, label);
const select = (root: ParentNode, label: string) =>
  labelled<HTMLSelectElement>(root, label);
const choice = (root: ParentNode, text: string) => {
  const label = [...root.querySelectorAll('label')].find((candidate) =>
    candidate.textContent?.startsWith(text),
  );
  const found = label?.querySelector('input');
  if (!found) throw new Error(`No choice "${text}"`);
  return found;
};
const options = (field: HTMLSelectElement) =>
  [...field.options].map((option) => [option.textContent, option.disabled]);
const keyOf = (call: { headers: Headers }) =>
  call.headers.get('Idempotency-Key');

async function renderAt(path: string) {
  app = await renderApp(path);
  await settleUntil(() => main().querySelector('h1'));
}

async function search(root: ParentNode, label: string, q: string) {
  setFieldValue(input(root, label), q);
  await new Promise((resolve) => setTimeout(resolve, PICKER_DEBOUNCE_MS + 30));
  await settleUntil(() =>
    [...root.querySelectorAll('fieldset input[type="radio"]')].some(
      (radio) => (radio as HTMLInputElement).value.length > 20,
    ),
  );
}

async function openSell() {
  await click(buttonNamed(main(), 'Sell package'));
  await settleUntil(() => openDialog()?.querySelector('form'));
}

async function fillSale(productId = PRODUCT_SESSIONS.id) {
  await search(dialog(), 'Student', 'Ana');
  await click(choice(dialog(), 'Ana López'));
  setFieldValue(select(dialog(), 'Package'), productId);
}

beforeEach(() => {
  applyLanguage('en');
  listUrls = [];
  productUrls = [];
  chargeUrls = [];
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

describe('the sold list (C1, C2)', () => {
  it('shows each package with its balance and status badge', async () => {
    stub();
    await renderAt('/billing/packages');
    await settleUntil(() => main().querySelector('tbody'));

    expect(h1()).toBe('Sold packages');
    expect(document.title).toBe('Sold packages · Classora');
    expect(cells(rowOf(PACKAGE_ACTIVE.id)).slice(0, 7)).toEqual([
      'Ana López',
      'Familia López',
      'Álgebra 10 sesiones',
      '8 / 10 sessions',
      'Oct 1, 2026',
      'Dec 30, 2026',
      'Active',
    ]);
    expect(cells(rowOf(PACKAGE_EXHAUSTED.id)).slice(3, 7)).toEqual([
      '0 min / 20 h',
      'Jun 1, 2026',
      'Nov 28, 2026',
      'Used up',
    ]);
    expect(cells(rowOf(PACKAGE_EXPIRED.id)).slice(3, 7)).toEqual([
      '1 h 15 min / 20 h',
      'Mar 1, 2026',
      'Aug 28, 2026',
      'Expired',
    ]);
    const view = rowOf(PACKAGE_ACTIVE.id).querySelector('a')!;
    expect(view.getAttribute('href')).toBe(
      `/billing/packages/${PACKAGE_ACTIVE.id}`,
    );
    expect(view.getAttribute('aria-label')).toBe(
      'View: Ana López, Álgebra 10 sesiones',
    );
    expect(main().textContent).toContain('1–3 of 3');
  });

  it('keeps the statuses in the URL and sends them', async () => {
    stub();
    await renderAt('/billing/packages');
    expect(listUrls.at(-1)?.searchParams.has('status')).toBe(false);

    await click(choice(main(), 'Expired'));
    await settleUntil(() => listUrls.length >= 2);
    await click(choice(main(), 'Active'));
    await settleUntil(() => listUrls.length >= 3);

    expect(app!.location()).toBe(
      '/billing/packages?status=active&status=expired',
    );
    expect(listUrls.at(-1)?.searchParams.getAll('status')).toEqual([
      'active',
      'expired',
    ]);
  });

  it('filters by a student and a family picked in the search', async () => {
    stub();
    await renderAt('/billing/packages?page=2');
    expect(listUrls.at(-1)?.searchParams.get('offset')).toBe('25');

    await search(main(), 'Search student', 'Ana');
    await click(choice(main(), 'Ana López'));
    await settleUntil(() => listUrls.length >= 2);
    await search(main(), 'Search family', 'López');
    await click(choice(main(), 'Familia López'));
    await settleUntil(() => listUrls.length >= 3);

    expect(app!.location()).toBe(
      `/billing/packages?student_id=${STUDENT_ANA.id}&family_id=${FAMILY_LOPEZ.id}`,
    );
    const last = listUrls.at(-1)!.searchParams;
    expect(last.get('student_id')).toBe(STUDENT_ANA.id);
    expect(last.get('family_id')).toBe(FAMILY_LOPEZ.id);
    expect(last.get('offset')).toBe('0');

    await click(buttonNamed(main(), 'Show all families'));
    await settleUntil(() => listUrls.length >= 4);
    expect(app!.location()).toBe(
      `/billing/packages?student_id=${STUDENT_ANA.id}`,
    );
  });

  it('reads deep-linked filters', async () => {
    stub();
    await renderAt(
      `/billing/packages?student_id=${STUDENT_ANA.id}&family_id=${FAMILY_LOPEZ.id}&status=exhausted&status=bogus`,
    );
    await settleUntil(
      () =>
        main().textContent?.includes('Ana López') &&
        main().textContent?.includes('Familia López'),
    );
    expect(listUrls.at(-1)?.searchParams.getAll('status')).toEqual([
      'exhausted',
    ]);
    expect(choice(main(), 'Used up').checked).toBe(true);
  });

  it('shows the empty state with the sell button, and an error with retry', async () => {
    stub({}, { page: EMPTY_PACKAGES_PAGE });
    await renderAt('/billing/packages');
    await settleUntil(() => main().textContent?.includes('No packages sold'));
    expect(
      [...main().querySelectorAll('button')].filter(
        (b) => b.textContent === 'Sell package',
      ),
    ).toHaveLength(2);
    app!.unmount();

    const api = stub({
      [LIST]: [
        () => problemWith(400, 'bad_request'),
        () => jsonResponse(200, PACKAGES_PAGE),
      ],
    });
    await renderAt('/billing/packages');
    await settleUntil(() => alerts(main()).length > 0);
    await click(buttonNamed(main(), 'Try again'));
    await settleUntil(() => main().querySelector('tbody'));
    expect(api.calls(LIST)).toHaveLength(2);
  });

  it('is read-only for a billing.view-only holder', async () => {
    stub({}, { as: VIEWER });
    await renderAt('/billing/packages');
    await settleUntil(() => main().querySelector('tbody'));

    expect(
      [...main().querySelectorAll('button')].map((b) => b.textContent),
    ).not.toContain('Sell package');
    // No guardian.view: no family filter.
    expect(main().textContent).not.toContain('Search family');
  });
});

describe('the sell dialog (D)', () => {
  it('sells with an Idempotency-Key and opens the new package', async () => {
    const api = stub({
      [SELL]: () => jsonResponse(201, PACKAGE_SOLD_DETAIL),
    });
    await renderAt('/billing/packages?status=active');
    await openSell();
    expect(productUrls.at(-1)?.searchParams.get('is_active')).toBe('true');

    expect(options(select(dialog(), 'Package'))).toEqual([
      ['Choose an option', false],
      ['Álgebra 10 sesiones · 10 sessions · 90 days — $2,000.00', false],
      ['Bolsa 20 horas · 20 h · 180 days — $4,500.00', false],
      ['Física 5 sesiones · 5 sessions · 30 days — Not offered', true],
    ]);
    expect(dialog().textContent).toContain(
      'Sessions already charged before the sale (for example, an already confirmed series) are not converted.',
    );
    await fillSale();
    expect(dialog().textContent).toContain(
      'Charge $2,000.00 today, before discounts · expires Jan 18, 2027',
    );
    await click(buttonNamed(dialog(), 'Sell package'));
    await settleUntil(() => main().querySelector('dl'));

    const calls = api.calls(SELL);
    expect(calls.map((call) => call.body)).toEqual([
      { student_id: STUDENT_ANA.id, package_product_id: PRODUCT_SESSIONS.id },
    ]);
    expect(keyOf(calls[0]!)).toMatch(/^[A-Za-z0-9_-]{16,128}$/);
    expect(app!.router.state.location.pathname).toBe(
      `/billing/packages/${PACKAGE_SOLD_DETAIL.id}`,
    );
    expect(main().textContent).toContain(
      'Package sold and charged to the family.',
    );
    const back = [...main().querySelectorAll('a')].find(
      (a) => a.textContent === 'Back to sold packages',
    );
    expect(back?.getAttribute('href')).toBe('/billing/packages?status=active');
  });

  it('resends the same key after a network error, and a new one after a success', async () => {
    const api = stub({
      [SELL]: [
        () => Promise.reject(new TypeError('Failed to fetch')),
        () => jsonResponse(201, PACKAGE_SOLD_DETAIL),
      ],
    });
    await renderAt('/billing/packages');
    await openSell();
    await fillSale();
    await click(buttonNamed(dialog(), 'Sell package'));
    await settleUntil(() => alerts(dialog()).length > 0);
    expect(alerts(dialog()).join()).toContain("Can't reach the server");

    await click(buttonNamed(dialog(), 'Sell package'));
    await settleUntil(() => main().querySelector('dl'));
    const [first, second] = api.calls(SELL);
    expect(keyOf(second!)).toBe(keyOf(first!));

    // A second sale of the same body is a new submission.
    await click(
      [...main().querySelectorAll('a')].find(
        (a) => a.textContent === 'Back to sold packages',
      )!,
    );
    await settleUntil(() => main().querySelector('tbody'));
    await openSell();
    await fillSale();
    await click(buttonNamed(dialog(), 'Sell package'));
    await settleUntil(() => api.calls(SELL).length === 3);
    expect(keyOf(api.calls(SELL)[2]!)).not.toBe(keyOf(first!));
  });

  it('uses a new key when the sale changes after an error', async () => {
    const api = stub({
      [SELL]: () => problemWith(409, 'package_not_offered'),
    });
    await renderAt('/billing/packages');
    await openSell();
    await fillSale();
    await click(buttonNamed(dialog(), 'Sell package'));
    await settleUntil(() => alerts(dialog()).length > 0);
    setFieldValue(select(dialog(), 'Package'), PRODUCT_HOURS.id);
    await click(buttonNamed(dialog(), 'Sell package'));
    await settleUntil(() => api.calls(SELL).length === 2);

    const [first, second] = api.calls(SELL);
    expect(keyOf(second!)).not.toBe(keyOf(first!));
  });

  it('shows student_has_no_family with a link to the student', async () => {
    stub({ [SELL]: () => problemWith(409, 'student_has_no_family') });
    await renderAt('/billing/packages');
    await openSell();
    await fillSale();
    await click(buttonNamed(dialog(), 'Sell package'));
    await settleUntil(() => alerts(dialog()).length > 0);

    expect(alerts(dialog()).join()).toContain("This student isn't in a family");
    const link = [...dialog().querySelectorAll('a')].find(
      (a) => a.textContent === "Open Ana López's page",
    );
    expect(link?.getAttribute('href')).toBe(`/students/${STUDENT_ANA.id}`);
  });

  it.each([
    ['package_product_inactive', 'This package is no longer sold.'],
    ['package_not_offered', 'This package has no price in effect today'],
  ])('shows %s and reloads the products', async (code, text) => {
    stub({ [SELL]: () => problemWith(409, code) });
    await renderAt('/billing/packages');
    await openSell();
    const before = productUrls.length;
    await fillSale();
    await click(buttonNamed(dialog(), 'Sell package'));
    await settleUntil(() => alerts(dialog()).length > 0);

    expect(alerts(dialog()).join()).toContain(text);
    await settleUntil(() => productUrls.length > before);
  });

  it('maps idempotency_key_reused', async () => {
    stub({ [SELL]: () => problemWith(422, 'idempotency_key_reused') });
    await renderAt('/billing/packages');
    await openSell();
    await fillSale();
    await click(buttonNamed(dialog(), 'Sell package'));
    await settleUntil(() => alerts(dialog()).length > 0);
    expect(alerts(dialog()).join()).toContain(
      'This submission was already used for different data.',
    );
  });

  it('puts a 422 on its field and checks the choices first', async () => {
    const api = stub({
      [SELL]: () =>
        problemWith(422, 'validation_error', {
          errors: [
            {
              path: ['body', 'package_product_id'],
              code: 'package_product_unknown',
            },
          ],
        }),
    });
    await renderAt('/billing/packages');
    await openSell();
    await click(buttonNamed(dialog(), 'Sell package'));
    expect(api.calls(SELL)).toHaveLength(0);
    expect(dialog().textContent).toContain('Choose the student.');
    expect(dialog().textContent).toContain(
      'Choose a package that is offered today.',
    );

    await fillSale();
    await click(buttonNamed(dialog(), 'Sell package'));
    await settleUntil(() =>
      dialog().textContent?.includes("This package doesn't exist anymore."),
    );
  });

  it('does not offer a product that is not offered today', async () => {
    const api = stub();
    await renderAt('/billing/packages');
    await openSell();
    await fillSale(PRODUCT_NOT_OFFERED.id);
    await click(buttonNamed(dialog(), 'Sell package'));
    expect(api.calls(SELL)).toHaveLength(0);
    expect(dialog().textContent).toContain(
      'Choose a package that is offered today.',
    );
  });
});

describe('the package page (E)', () => {
  it('shows the summary with the purchase charge, the history and the rules', async () => {
    const api = stub();
    await renderAt(`/billing/packages/${PACKAGE_ACTIVE.id}`);
    await settleUntil(() => main().textContent?.includes('$1,800.00'));

    expect(h1()).toBe('Álgebra 10 sesiones — Ana López');
    expect(main().querySelector('dl')!.textContent).toBe(
      'StudentAna LópezFamilyFamilia LópezPackageÁlgebra 10 sesiones' +
        'Remaining8 sessionsGranted10 sessionsSubjectÁlgebra' +
        'PurchasedOct 1, 2026ExpiresDec 30, 2026StatusActive' +
        'Purchase charge$1,800.00Pending',
    );
    expect(api.calls(CHARGES)).toHaveLength(1);
    expect(chargeUrls.at(-1)?.searchParams.get('student_id')).toBe(
      STUDENT_ANA.id,
    );

    const rows = [
      ...main().querySelectorAll<HTMLTableRowElement>(
        '[data-package-transaction]',
      ),
    ].map(cells);
    expect(rows).toEqual([
      ['Granted', '+10 sessions', 'Oct 1, 2026'],
      ['Used for session on Oct 5, 2026', '−1 session', 'Oct 5, 2026'],
      ['Used for session on Oct 12, 2026', '−1 session', 'Oct 12, 2026'],
    ]);
    const link = main().querySelector<HTMLAnchorElement>(
      '[data-package-transaction="consume"] a',
    );
    expect(link?.getAttribute('href')).toBe(`/schedule/sessions/${SESSION_ID}`);
    expect(main().textContent).toContain(
      'Unused units expire the day after the expiry date.',
    );
  });

  it('labels expiry and reversal rows, without session links for a billing.view-only holder', async () => {
    stub(
      {
        [`GET /api/v1/billing/packages/${PACKAGE_EXPIRED.id}`]: () =>
          jsonResponse(200, PACKAGE_EXPIRED_DETAIL),
        [CHARGES]: () =>
          jsonResponse(200, { items: [], total: 0, limit: 100, offset: 0 }),
      },
      { as: VIEWER },
    );
    await renderAt(`/billing/packages/${PACKAGE_EXPIRED.id}`);
    await settleUntil(() =>
      main().textContent?.includes("We couldn't load the charge."),
    );

    const rows = [
      ...main().querySelectorAll<HTMLTableRowElement>(
        '[data-package-transaction]',
      ),
    ].map(cells);
    expect(rows.map((row) => [row[0], row[1]])).toEqual([
      ['Granted', '+20 h'],
      ['Used for session on Apr 10, 2026', '−1 h 30 min'],
      ['Reversal', '−17 h 15 min'],
      ['Expired', '−1 h 15 min'],
    ]);
    expect(main().querySelector('[data-package-transaction] a')).toBeNull();
    expect(main().textContent).toContain('Expired');
    // No "Sell" anywhere and no Products sub-link.
    expect(main().textContent).not.toContain('Products');
  });

  it('shows the not-found message for an unknown id', async () => {
    stub({
      [`GET /api/v1/billing/packages/${PACKAGE_EXHAUSTED.id}`]: () =>
        problemWith(404, 'not_found'),
    });
    await renderAt(`/billing/packages/${PACKAGE_EXHAUSTED.id}`);
    await settleUntil(() => h1() === 'Package not found');
  });
});
