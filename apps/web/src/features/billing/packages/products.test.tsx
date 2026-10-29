import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SessionUserBody } from '@/features/auth/session';
import { problemWith } from '@/features/academics/testing';
import { applyLanguage } from '@/i18n';
import {
  meAs,
  noContent,
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
import {
  SUBJECT_ALGEBRA,
  SUBJECT_PHYSICS,
  V_CURRENT,
  V_UPCOMING,
} from '../pricing/fixtures';
import type { PackageProductPage } from './api';
import {
  EMPTY_PRODUCTS_PAGE,
  PACKAGES_PAGE,
  PRODUCT_DETAIL,
  PRODUCT_HOURS,
  PRODUCT_INACTIVE,
  PRODUCT_INACTIVE_DETAIL,
  PRODUCT_NOT_OFFERED,
  PRODUCT_SESSIONS,
  PRODUCTS_PAGE,
} from './fixtures';

// #377 G1 (A, B): the Packages tab behind billing.view and its Products
// sub-link behind billing.manage; the product list (rows, filters in the
// URL and the request, empty, error with retry); the create dialog (hours
// sent as minutes, each field code inline, 201 → the product page); and
// the product page (edit the name, deactivate behind a confirmation,
// activate, the price versions, and the 409s).

const LIST = 'GET /api/v1/billing/package-products';
const CREATE = 'POST /api/v1/billing/package-products';
const DETAIL = `GET /api/v1/billing/package-products/${PRODUCT_SESSIONS.id}`;
const PATCH = `PATCH /api/v1/billing/package-products/${PRODUCT_SESSIONS.id}`;
const VERSIONS = `POST /api/v1/billing/package-products/${PRODUCT_SESSIONS.id}/versions`;
const DELETE_UPCOMING = `DELETE /api/v1/billing/package-products/${PRODUCT_SESSIONS.id}/versions/${V_UPCOMING.id}`;
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
    'schedule.view',
    'billing.view',
    'billing.manage',
  ],
);
const VIEWER = user(['finance_staff'], ['billing.view']);
const MANAGER_ONLY = user(['finance_staff'], ['billing.manage']);

const subjectsPage = {
  items: [SUBJECT_ALGEBRA, SUBJECT_PHYSICS].map((subject) => ({
    id: subject.id,
    name: subject.name,
    education_level_id: subject.education_level_id,
    education_level_name: subject.education_level_name,
    is_active: true,
    version: 1,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  })),
  total: 2,
  limit: 100,
  offset: 0,
};

let listUrls: URL[] = [];

function stub(
  routes: Record<string, Responder | Responder[]> = {},
  {
    page = PRODUCTS_PAGE,
    as = FINANCE,
  }: { page?: PackageProductPage; as?: SessionUserBody } = {},
): ApiStub {
  return stubApi({
    [ME]: meAs(as),
    [LIST]: (request) => {
      listUrls.push(new URL(request.url));
      return jsonResponse(200, page);
    },
    'GET /api/v1/subjects': () => jsonResponse(200, subjectsPage),
    [DETAIL]: () => jsonResponse(200, PRODUCT_DETAIL),
    'GET /api/v1/billing/packages': () => jsonResponse(200, PACKAGES_PAGE),
    ...routes,
  });
}

let app: RenderedApp | undefined;

const main = () => app!.container.querySelector('main')!;
const h1 = () => main().querySelector('h1')?.textContent;
const dialog = () => openDialog()!;
const rowOf = (id: string) =>
  main().querySelector<HTMLTableRowElement>(
    `[data-package-product-id="${id}"]`,
  )!;
const cells = (row: Element) =>
  [...row.querySelectorAll('td')].map((cell) => cell.textContent);
const input = (root: ParentNode, label: string) =>
  labelled<HTMLInputElement>(root, label);
const select = (root: ParentNode, label: string) =>
  labelled<HTMLSelectElement>(root, label);
const choice = (root: ParentNode, text: string) => {
  const label = [...root.querySelectorAll('label')].find(
    (candidate) => candidate.textContent === text,
  );
  const found = label?.querySelector('input');
  if (!found) throw new Error(`No choice "${text}"`);
  return found;
};
const links = (label: string) =>
  [
    ...main().querySelectorAll<HTMLAnchorElement>(
      `nav[aria-label="${label}"] a`,
    ),
  ].map((a) => [a.textContent, a.getAttribute('aria-current')]);
const tabs = () => links('Billing sections');
const subNav = () => links('Package pages');
const errorOf = (element: Element) =>
  (element.getAttribute('aria-describedby') ?? '')
    .split(' ')
    .map((id) => document.getElementById(id)?.textContent)
    .filter(Boolean)
    .join('|');

async function renderAt(path: string) {
  app = await renderApp(path);
  await settleUntil(() => main().querySelector('h1'));
}

async function openAdd() {
  await click(buttonNamed(main(), 'New package'));
  await settleUntil(() => openDialog()?.querySelector('form'));
}

function fillProduct({
  name = 'Álgebra 10 sesiones',
  quantity = '10',
  validity = '90',
  amount = '2000',
}: Partial<Record<'name' | 'quantity' | 'validity' | 'amount', string>> = {}) {
  setFieldValue(input(dialog(), 'Name'), name);
  setFieldValue(
    input(
      dialog(),
      choice(dialog(), 'Hours').checked
        ? 'Number of hours'
        : 'Number of sessions',
    ),
    quantity,
  );
  setFieldValue(input(dialog(), 'Validity in days'), validity);
  setFieldValue(input(dialog(), 'Price'), amount);
}

beforeEach(() => {
  applyLanguage('en');
  listUrls = [];
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

describe('access (A1, A2)', () => {
  it('shows the Packages tab after Tuition plans, current on product pages', async () => {
    stub();
    await renderAt('/billing/package-products');

    expect(tabs()).toEqual([
      ['Accounts', null],
      ['Prices', null],
      ['Discounts', null],
      ['Custom prices', null],
      ['Tuition plans', null],
      ['Packages', 'page'],
      ['Unbilled', null],
      ['Settings', null],
    ]);
    expect(subNav()).toEqual([
      ['Sold', null],
      ['Products', 'page'],
    ]);
    expect(h1()).toBe('Package products');
    expect(document.title).toBe('Package products · Classora');
  });

  it('hides the tab and the sold pages without billing.view', async () => {
    const api = stub({}, { as: MANAGER_ONLY });
    await renderAt('/billing/packages');
    expect(h1()).toBe("You don't have access to this page");
    expect(api.calls('GET /api/v1/billing/packages')).toHaveLength(0);
    app!.unmount();

    await renderAt('/billing/prices');
    expect(tabs().map(([name]) => name)).not.toContain('Packages');
  });

  it('forbids the product pages and hides their link without billing.manage', async () => {
    const api = stub({}, { as: VIEWER });
    await renderAt('/billing/package-products');
    expect(h1()).toBe("You don't have access to this page");
    app!.unmount();
    await renderAt(`/billing/package-products/${PRODUCT_SESSIONS.id}`);
    expect(h1()).toBe("You don't have access to this page");
    expect(api.calls(LIST)).toHaveLength(0);
    expect(api.calls(DETAIL)).toHaveLength(0);
    app!.unmount();

    await renderAt('/billing/packages');
    expect(subNav()).toEqual([['Sold', 'page']]);
    expect(tabs().map(([name]) => name)).toEqual([
      'Accounts',
      'Tuition plans',
      'Packages',
      'Unbilled',
      'Settings',
    ]);
  });
});

describe('the product list (B1)', () => {
  it('shows each product with its units, validity, subject, price and state', async () => {
    stub();
    await renderAt('/billing/package-products');
    await settleUntil(() => main().querySelector('tbody'));

    expect(cells(rowOf(PRODUCT_SESSIONS.id)).slice(0, 6)).toEqual([
      'Álgebra 10 sesiones',
      '10 sessions',
      '90 days',
      'Álgebra',
      '$2,000.00',
      'Active',
    ]);
    expect(cells(rowOf(PRODUCT_HOURS.id)).slice(1, 4)).toEqual([
      '20 h',
      '180 days',
      'Any subject',
    ]);
    expect(cells(rowOf(PRODUCT_NOT_OFFERED.id))[4]).toBe('Not offered');
    expect(cells(rowOf(PRODUCT_INACTIVE.id)).slice(0, 6)).toEqual([
      'Repaso 75 minutos',
      '1 h 15 min',
      '1 day',
      'Any subject',
      '$300.00',
      'Inactive',
    ]);
    const view = rowOf(PRODUCT_SESSIONS.id).querySelector('a')!;
    expect(view.getAttribute('href')).toBe(
      `/billing/package-products/${PRODUCT_SESSIONS.id}`,
    );
    expect(view.getAttribute('aria-label')).toBe(
      'View package: Álgebra 10 sesiones',
    );
  });

  it('keeps the status and subject in the URL and sends them', async () => {
    stub();
    await renderAt('/billing/package-products?page=2');
    expect(listUrls.at(-1)?.searchParams.get('offset')).toBe('25');
    expect(listUrls.at(-1)?.searchParams.has('is_active')).toBe(false);
    await settleUntil(() => select(main(), 'Subject').options.length > 1);

    setFieldValue(select(main(), 'Status'), 'false');
    await settleUntil(() => listUrls.length >= 2);
    setFieldValue(select(main(), 'Subject'), SUBJECT_ALGEBRA.id);
    await settleUntil(() => listUrls.length >= 3);

    expect(app!.location()).toBe(
      `/billing/package-products?is_active=false&subject_id=${SUBJECT_ALGEBRA.id}`,
    );
    const last = listUrls.at(-1)!.searchParams;
    expect(last.get('is_active')).toBe('false');
    expect(last.get('subject_id')).toBe(SUBJECT_ALGEBRA.id);
    expect(last.get('offset')).toBe('0');
  });

  it('shows the empty state and a failed load with a retry', async () => {
    stub({}, { page: EMPTY_PRODUCTS_PAGE });
    await renderAt('/billing/package-products');
    await settleUntil(() => main().textContent?.includes('No package'));
    expect(main().textContent).toContain('No package products yet.');
    app!.unmount();

    const api = stub({
      [LIST]: [
        () => problemWith(400, 'bad_request'),
        () => jsonResponse(200, PRODUCTS_PAGE),
      ],
    });
    await renderAt('/billing/package-products');
    await settleUntil(() => alerts(main()).length > 0);
    await click(buttonNamed(main(), 'Try again'));
    await settleUntil(() => main().querySelector('tbody'));
    expect(api.calls(LIST)).toHaveLength(2);
  });
});

describe('the new-product dialog (B2)', () => {
  it('creates a sessions product and opens its page', async () => {
    const api = stub({
      [CREATE]: () => jsonResponse(201, PRODUCT_DETAIL),
    });
    await renderAt('/billing/package-products?is_active=true');
    await openAdd();

    expect(input(dialog(), 'Price effective from').value).toBe('2026-10-20');
    fillProduct();
    setFieldValue(select(dialog(), 'Subject'), SUBJECT_ALGEBRA.id);
    await click(buttonNamed(dialog(), 'Create package'));
    await settleUntil(() => main().querySelector('dl'));

    expect(api.calls(CREATE).map((call) => call.body)).toEqual([
      {
        name: 'Álgebra 10 sesiones',
        unit: 'sessions',
        quantity: 10,
        validity_days: 90,
        subject_id: SUBJECT_ALGEBRA.id,
        amount: '2000',
        effective_from: '2026-10-20',
      },
    ]);
    expect(app!.router.state.location.pathname).toBe(
      `/billing/package-products/${PRODUCT_SESSIONS.id}`,
    );
    const back = [...main().querySelectorAll('a')].find(
      (a) => a.textContent === 'Back to package products',
    );
    expect(back?.getAttribute('href')).toBe(
      '/billing/package-products?is_active=true',
    );
  });

  it('sends an hours quantity as minutes, for any subject', async () => {
    const api = stub({
      [CREATE]: () =>
        jsonResponse(201, { ...PRODUCT_DETAIL, ...PRODUCT_HOURS }),
    });
    await renderAt('/billing/package-products');
    await openAdd();

    await click(choice(dialog(), 'Hours'));
    fillProduct({ name: 'Bolsa', quantity: '1.25', amount: '450.50' });
    await click(buttonNamed(dialog(), 'Create package'));
    await settleUntil(() => api.calls(CREATE).length > 0);

    expect(api.calls(CREATE)[0]!.body).toMatchObject({
      unit: 'minutes',
      quantity: 75,
      subject_id: null,
      amount: '450.50',
    });
  });

  it('checks the fields before sending', async () => {
    const api = stub();
    await renderAt('/billing/package-products');
    await openAdd();

    await click(choice(dialog(), 'Hours'));
    fillProduct({ name: ' ', quantity: '1.1', validity: '731', amount: 'x' });
    await click(buttonNamed(dialog(), 'Create package'));

    expect(api.calls(CREATE)).toHaveLength(0);
    expect(errorOf(input(dialog(), 'Name'))).toBe('Enter a name.');
    expect(errorOf(input(dialog(), 'Number of hours'))).toContain(
      'Enter 0.25 to 500 hours, in quarter hours.',
    );
    expect(errorOf(input(dialog(), 'Validity in days'))).toContain(
      'Enter whole days from 1 to 730.',
    );
    expect(errorOf(input(dialog(), 'Price'))).toContain(
      'Enter an amount like 1250 or 1250.50.',
    );
    expect(document.activeElement).toBe(input(dialog(), 'Name'));
  });

  it('puts each server field code on its field', async () => {
    stub({
      [CREATE]: () =>
        problemWith(422, 'validation_error', {
          errors: [
            { path: ['body', 'quantity'], code: 'quantity_step' },
            {
              path: ['body', 'validity_days'],
              code: 'validity_days_out_of_range',
            },
            { path: ['body', 'amount'], code: 'amount_negative' },
            { path: ['body', 'subject_id'], code: 'subject_inactive' },
          ],
        }),
    });
    await renderAt('/billing/package-products');
    await openAdd();
    fillProduct();
    await click(buttonNamed(dialog(), 'Create package'));
    await settleUntil(() =>
      errorOf(input(dialog(), 'Price')).includes('negative'),
    );

    expect(errorOf(input(dialog(), 'Number of sessions'))).toContain(
      'quarter hour',
    );
    expect(errorOf(input(dialog(), 'Validity in days'))).toContain(
      'Enter a validity from 1 day to two years',
    );
    expect(errorOf(input(dialog(), 'Price'))).toContain(
      "The amount can't be negative.",
    );
    expect(errorOf(select(dialog(), 'Subject'))).toContain(
      'This subject or its education level is inactive.',
    );
    // Nothing typed is lost.
    expect(input(dialog(), 'Name').value).toBe('Álgebra 10 sesiones');
  });

  it('maps the quantity range code', async () => {
    stub({
      [CREATE]: () =>
        problemWith(422, 'validation_error', {
          errors: [
            { path: ['body', 'quantity'], code: 'quantity_out_of_range' },
          ],
        }),
    });
    await renderAt('/billing/package-products');
    await openAdd();
    fillProduct();
    await click(buttonNamed(dialog(), 'Create package'));
    await settleUntil(() =>
      errorOf(input(dialog(), 'Number of sessions')).includes('hundred hours'),
    );
    expect(errorOf(input(dialog(), 'Number of sessions'))).toContain(
      'Enter from 1 to five hundred sessions',
    );
  });
});

describe('the product page (B3)', () => {
  it('shows the product, the fixed-fields hint and its versions', async () => {
    stub();
    await renderAt(`/billing/package-products/${PRODUCT_SESSIONS.id}`);
    await settleUntil(() => main().querySelector('dl'));

    expect(h1()).toBe('Álgebra 10 sesiones');
    expect(document.title).toBe('Álgebra 10 sesiones · Classora');
    expect(main().querySelector('dl')!.textContent).toBe(
      'Units10 sessionsValidity90 daysSubjectÁlgebraCurrent price$2,000.00StatusActive',
    );
    expect(main().textContent).toContain(
      "The unit, quantity, validity and subject can't be changed later",
    );
    expect(main().querySelectorAll('[data-version-id]')).toHaveLength(3);
  });

  it('renames the product with its version', async () => {
    const api = stub({
      [PATCH]: () =>
        jsonResponse(200, {
          ...PRODUCT_DETAIL,
          name: 'Álgebra 10',
          version: 2,
        }),
    });
    await renderAt(`/billing/package-products/${PRODUCT_SESSIONS.id}`);
    await settleUntil(() => main().querySelector('dl'));

    await click(buttonNamed(main(), 'Edit'));
    setFieldValue(input(dialog(), 'Name'), 'Álgebra 10');
    await click(buttonNamed(dialog(), 'Save'));
    await settleUntil(() => !openDialog());

    expect(api.calls(PATCH).map((call) => call.body)).toEqual([
      { version: 1, name: 'Álgebra 10' },
    ]);
    expect(main().textContent).toContain('Package saved.');
  });

  it('shows a stale edit and reloads the product', async () => {
    const api = stub({
      [PATCH]: () => problemWith(409, 'version_conflict'),
    });
    await renderAt(`/billing/package-products/${PRODUCT_SESSIONS.id}`);
    await settleUntil(() => main().querySelector('dl'));
    await click(buttonNamed(main(), 'Edit'));
    setFieldValue(input(dialog(), 'Name'), 'Otro');
    await click(buttonNamed(dialog(), 'Save'));
    await settleUntil(() => alerts(dialog()).length > 0);

    expect(alerts(dialog()).join()).toContain('changed');
    await settleUntil(() => api.calls(DETAIL).length >= 2);
    expect(openDialog()).not.toBeNull();
  });

  it('deactivates behind a confirmation, and a stale one reloads', async () => {
    const api = stub({
      [PATCH]: [
        () => problemWith(409, 'version_conflict'),
        () => jsonResponse(200, PRODUCT_INACTIVE_DETAIL),
      ],
    });
    await renderAt(`/billing/package-products/${PRODUCT_SESSIONS.id}`);
    await settleUntil(() => main().querySelector('dl'));

    await click(buttonNamed(main(), 'Deactivate'));
    expect(dialog().textContent).toContain('Deactivate this package?');
    await click(buttonNamed(dialog(), 'Deactivate'));
    await settleUntil(() => alerts(dialog()).length > 0);
    await settleUntil(() => api.calls(DETAIL).length >= 2);

    await click(buttonNamed(dialog(), 'Deactivate'));
    await settleUntil(() => !openDialog());
    expect(api.calls(PATCH).map((call) => call.body)).toEqual([
      { version: 1, is_active: false },
      { version: 1, is_active: false },
    ]);
    expect(main().textContent).toContain('Package deactivated');
  });

  it('activates an inactive product without a confirmation', async () => {
    const api = stub({
      [`GET /api/v1/billing/package-products/${PRODUCT_INACTIVE.id}`]: () =>
        jsonResponse(200, PRODUCT_INACTIVE_DETAIL),
      [`PATCH /api/v1/billing/package-products/${PRODUCT_INACTIVE.id}`]: () =>
        jsonResponse(200, { ...PRODUCT_INACTIVE_DETAIL, is_active: true }),
    });
    await renderAt(`/billing/package-products/${PRODUCT_INACTIVE.id}`);
    await settleUntil(() => main().querySelector('dl'));

    await click(buttonNamed(main(), 'Activate'));
    await settleUntil(() => main().textContent?.includes('Package activated'));
    expect(openDialog()).toBeNull();
    expect(
      api
        .calls(`PATCH /api/v1/billing/package-products/${PRODUCT_INACTIVE.id}`)
        .map((call) => call.body),
    ).toEqual([{ version: 2, is_active: true }]);
  });

  it('schedules a price version and deletes an upcoming one', async () => {
    const api = stub({
      [VERSIONS]: () => jsonResponse(201, PRODUCT_DETAIL),
      [DELETE_UPCOMING]: noContent,
    });
    await renderAt(`/billing/package-products/${PRODUCT_SESSIONS.id}`);
    await settleUntil(() => main().querySelector('dl'));

    await click(buttonNamed(main(), 'Schedule a price change'));
    setFieldValue(input(dialog(), 'Effective from'), '2026-12-01');
    setFieldValue(input(dialog(), 'Amount (MXN)'), '2100');
    await click(buttonNamed(dialog(), 'Schedule'));
    await settleUntil(() => api.calls(VERSIONS).length > 0 && !openDialog());
    expect(api.calls(VERSIONS)[0]!.body).toEqual({
      effective_from: '2026-12-01',
      amount: '2100',
    });

    const row = main().querySelector(`[data-version-id="${V_UPCOMING.id}"]`)!;
    expect(
      main().querySelector(`[data-version-id="${V_CURRENT.id}"] button`),
    ).toBeNull();
    await click(row.querySelector('button')!);
    await click(buttonNamed(dialog(), 'Delete'));
    await settleUntil(() => api.calls(DELETE_UPCOMING).length > 0);
    await settleUntil(() => !openDialog());
  });

  it('shows price_version_in_use with a reload', async () => {
    const api = stub({
      [DELETE_UPCOMING]: () => problemWith(409, 'price_version_in_use'),
    });
    await renderAt(`/billing/package-products/${PRODUCT_SESSIONS.id}`);
    await settleUntil(() => main().querySelector('dl'));

    const row = main().querySelector(`[data-version-id="${V_UPCOMING.id}"]`)!;
    await click(row.querySelector('button')!);
    await click(buttonNamed(dialog(), 'Delete'));
    await settleUntil(() => alerts(dialog()).length > 0);
    await click(buttonNamed(dialog(), 'Reload'));
    await settleUntil(() => api.calls(DETAIL).length >= 2);
    expect(openDialog()).toBeNull();
  });

  it('shows the not-found message for an unknown id', async () => {
    stub({
      [`GET /api/v1/billing/package-products/${PRODUCT_HOURS.id}`]: () =>
        problemWith(404, 'not_found'),
    });
    await renderAt(`/billing/package-products/${PRODUCT_HOURS.id}`);
    await settleUntil(() => h1() === 'Package product not found');
    expect(main().textContent).toContain("This package product doesn't exist.");
  });
});
