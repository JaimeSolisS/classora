import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { canAccess } from '@/features/auth/access';
import type { SessionUserBody } from '@/features/auth/session';
import { problemWith } from '@/features/academics/testing';
import { NAV_ITEMS } from '@/app/navigation';
import { applyLanguage } from '@/i18n';
import {
  meAs,
  noContent,
  problem,
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
import type { ServicePriceDetail, ServicePricePage } from './api';
import {
  COHORT_OPTIONS,
  EMPTY_PAGE,
  GROUP_OPTIONS,
  LEVEL_OPTIONS,
  PRICE_DETAIL,
  PRICE_DETAIL_WITHOUT_UPCOMING,
  PRICE_NOT_OFFERED,
  PRICE_NO_PRICE_YET,
  PRICE_PER_PERIOD,
  PRICE_WITH_NEXT,
  PRICES_PAGE,
  SUBJECT_ALGEBRA,
  SUBJECT_PHYSICS,
  V_CURRENT,
  V_PAST,
  V_UPCOMING,
} from './fixtures';

// #363 F1 (and A-E): the price list behind billing.manage (the nav item,
// the tabs, each price state, the filters in the URL and the request), the
// "Add price" dialog (the combinations, the scope clearing level and
// duration, the money rules and the string body, 201 → the item, 409 with
// the existing item's link, field 422s), the detail page (versions and
// badges, the schedule-change dialog's date default and minimum, "Not
// offered" sends null, the delete outcomes) and 404.

const LIST = 'GET /api/v1/billing/service-prices';
const CREATE = 'POST /api/v1/billing/service-prices';
const ME = 'GET /api/v1/auth/me';
const detailRoute = (id: string) => `GET /api/v1/billing/service-prices/${id}`;
const versionsRoute = (id: string) =>
  `POST /api/v1/billing/service-prices/${id}/versions`;
const deleteRoute = (id: string, versionId: string) =>
  `DELETE /api/v1/billing/service-prices/${id}/versions/${versionId}`;

const FINANCE: SessionUserBody = {
  ...TEST_USER,
  roles: ['finance_staff'],
  permissions: [
    'student.view',
    'schedule.view',
    'course.view',
    'billing.manage',
    'billing.view',
  ],
};
const COORDINATOR: SessionUserBody = {
  ...TEST_USER,
  roles: ['academic_coordinator'],
  permissions: ['student.view', 'catalog.manage', 'schedule.view'],
};
const user = (
  roles: SessionUserBody['roles'],
  permissions: SessionUserBody['permissions'],
): SessionUserBody => ({ ...TEST_USER, roles, permissions });

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
const levelsPage = {
  items: LEVEL_OPTIONS.map((level, index) => ({
    id: level.id,
    name: level.name,
    sort_order: index + 1,
    is_active: true,
    active_subject_count: 1,
    version: 1,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  })),
  total: LEVEL_OPTIONS.length,
  limit: 100,
  offset: 0,
};

/** The URLs the list was read with. */
let listUrls: URL[] = [];

function stub(
  routes: Record<string, Responder | Responder[]> = {},
  {
    page = PRICES_PAGE,
    as = FINANCE,
  }: { page?: ServicePricePage; as?: SessionUserBody } = {},
): ApiStub {
  return stubApi({
    [ME]: meAs(as),
    [LIST]: (request) => {
      listUrls.push(new URL(request.url));
      return jsonResponse(200, page);
    },
    'GET /api/v1/subjects': () => jsonResponse(200, subjectsPage),
    'GET /api/v1/education-levels': () => jsonResponse(200, levelsPage),
    'GET /api/v1/groups': () =>
      jsonResponse(200, {
        items: GROUP_OPTIONS.map((group) => ({ ...group })),
        next_cursor: null,
      }),
    'GET /api/v1/cohorts': () =>
      jsonResponse(200, {
        items: COHORT_OPTIONS.map((cohort) => ({ ...cohort })),
        next_cursor: null,
      }),
    [detailRoute(PRICE_DETAIL.id)]: () => jsonResponse(200, PRICE_DETAIL),
    ...routes,
  });
}

let app: RenderedApp | undefined;

const main = () => app!.container.querySelector('main')!;
const h1 = () => main().querySelector('h1')?.textContent;
const dialog = () => openDialog()!;
const rowOf = (id: string) =>
  main().querySelector<HTMLTableRowElement>(`[data-price-id="${id}"]`)!;
const cells = (row: Element) =>
  [...row.querySelectorAll('td')].map((cell) => cell.textContent);
const select = (root: ParentNode, label: string) =>
  labelled<HTMLSelectElement>(root, label);
const input = (root: ParentNode, label: string) =>
  labelled<HTMLInputElement>(root, label);
const optionTexts = (field: HTMLSelectElement) =>
  [...field.options].map((option) => option.textContent);
const statusText = () =>
  [...main().querySelectorAll('[role="status"]')]
    .map((s) => s.textContent)
    .join('|');

async function renderAt(path: string) {
  app = await renderApp(path);
  await settleUntil(() => main().querySelector('h1'));
}

async function openAdd() {
  await click(buttonNamed(main(), 'Add price'));
  await settleUntil(() => openDialog()?.querySelector('form'));
}

/** Fills the dialog with an individual, per-session 60-minute price. */
function fillIndividual(amount = '350.00') {
  setFieldValue(select(dialog(), 'Class type'), 'individual');
  setFieldValue(select(dialog(), 'Pricing model'), 'per_session');
  setFieldValue(select(dialog(), 'Subject'), SUBJECT_ALGEBRA.id);
  setFieldValue(input(dialog(), 'Duration (minutes)'), '60');
  setFieldValue(input(dialog(), 'Amount (MXN)'), amount);
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

// --- A: route, navigation and access ----------------------------------------

describe('access and navigation (A)', () => {
  it('shows Finance the Billing item, the tabs and the page', async () => {
    stub();

    await renderAt('/billing/prices');

    const nav = [
      ...app!.container.querySelectorAll<HTMLAnchorElement>('header nav a'),
    ].find((a) => a.textContent === 'Billing');
    // #366 A2: /billing opens the first Billing tab the user may open.
    expect(nav?.getAttribute('href')).toBe('/billing');
    const tabs = [
      ...main().querySelectorAll<HTMLAnchorElement>(
        'nav[aria-label="Billing sections"] a',
      ),
    ].map((a) => [a.textContent, a.getAttribute('aria-current')]);
    expect(tabs).toEqual([
      ['Accounts', null],
      ['Prices', 'page'],
      ['Discounts', null],
      ['Custom prices', null],
      ['Tuition plans', null],
      ['Packages', null],
      ['Unbilled', null],
      ['Settings', null],
    ]);
    expect(main().querySelectorAll('h1')).toHaveLength(1);
    expect(h1()).toBe('Prices');
    expect(document.title).toBe('Prices · Classora');
  });

  it('forbids a Coordinator and reads nothing', async () => {
    const api = stub({}, { as: COORDINATOR });

    await renderAt('/billing/prices');

    expect(h1()).toBe("You don't have access to this page");
    expect(api.calls(LIST)).toHaveLength(0);
    const labels = [...app!.container.querySelectorAll('header nav a')].map(
      (a) => a.textContent,
    );
    expect(labels).not.toContain('Billing');
  });

  it.each([
    ['an Owner', user(['owner'], ['billing.manage', 'billing.view']), true],
    ['a billing.view holder', user(['finance_staff'], ['billing.view']), true],
    ['Finance', FINANCE, true],
    ['a Coordinator', COORDINATOR, false],
    ['a tutor', user(['tutor'], ['student.view', 'tutor.view']), false],
    ['a student', user(['student'], ['student.view']), false],
    [
      'a guardian',
      user(['guardian'], ['student.view', 'guardian.view']),
      false,
    ],
  ] as const)('the nav item for %s: %s', (_who, as, visible) => {
    const items = NAV_ITEMS.filter((item) => canAccess(as, item));
    expect(items.some((item) => item.to === '/billing')).toBe(visible);
  });

  it('places the item after Settings with the billing label', () => {
    const index = NAV_ITEMS.findIndex((item) => item.to === '/billing');
    expect(NAV_ITEMS[index - 1]?.to).toBe('/settings');
    expect(NAV_ITEMS[index]?.labelKey).toBe('nav.billing');
  });
});

// --- B: the list --------------------------------------------------------------

describe('the price list (B)', () => {
  it('shows each price state', async () => {
    stub();

    await renderAt('/billing/prices');

    expect(cells(rowOf(PRICE_WITH_NEXT.id)).slice(0, 7)).toEqual([
      'Individual class · per session',
      'Secundaria · Álgebra',
      'Any',
      '60 min',
      '—',
      '$350.00',
      `${'Nov 3, 2026'}: $380.00`,
    ]);
    expect(cells(rowOf(PRICE_NOT_OFFERED.id)).slice(0, 7)).toEqual([
      'Group · flat fee per enrollment',
      'Secundaria · Álgebra',
      'Any',
      'Any',
      'Álgebra sábados',
      'Not offered',
      '—',
    ]);
    expect(cells(rowOf(PRICE_NO_PRICE_YET.id)).slice(0, 7)).toEqual([
      'Course · course fee',
      'Preparatoria · Física',
      'Preparatoria',
      'Any',
      '—',
      'No price yet',
      '—',
    ]);
    const view = rowOf(PRICE_WITH_NEXT.id).querySelector('a')!;
    expect(view.textContent).toBe('View prices');
    expect(view.getAttribute('aria-label')).toBe(
      'View prices for Individual class · per session, Secundaria · Álgebra',
    );
    expect(view.getAttribute('href')).toBe(
      `/billing/prices/${PRICE_WITH_NEXT.id}`,
    );
    expect(main().textContent).toContain('1–3 of 3');
  });

  it('keeps the filters in the URL and sends them', async () => {
    stub();
    await renderAt('/billing/prices');
    expect(listUrls.at(-1)?.searchParams.get('class_type')).toBe(null);

    setFieldValue(select(main(), 'Class type'), 'group');
    await settleUntil(() => listUrls.length >= 2);
    setFieldValue(select(main(), 'Pricing model'), 'full_course');
    await settleUntil(() => listUrls.length >= 3);

    expect(app!.router.state.location.search).toBe(
      '?class_type=group&pricing_model=full_course',
    );
    const last = listUrls.at(-1)!;
    expect(last.searchParams.get('class_type')).toBe('group');
    expect(last.searchParams.get('pricing_model')).toBe('full_course');
    expect(last.searchParams.get('offset')).toBe('0');
  });

  it('reads the filters and the page from the URL', async () => {
    stub();

    await renderAt(
      '/billing/prices?class_type=course&pricing_model=full_course&page=2',
    );

    expect(select(main(), 'Class type').value).toBe('course');
    expect(select(main(), 'Pricing model').value).toBe('full_course');
    const last = listUrls.at(-1)!;
    expect(last.searchParams.get('class_type')).toBe('course');
    expect(last.searchParams.get('offset')).toBe('25');
    expect(last.searchParams.get('limit')).toBe('25');
  });

  it('shows the empty state with the add button', async () => {
    stub({}, { page: EMPTY_PAGE });

    await renderAt('/billing/prices');

    expect(main().textContent).toContain('No prices yet.');
    expect(
      [...main().querySelectorAll('button')].filter(
        (b) => b.textContent === 'Add price',
      ),
    ).toHaveLength(2);
  });

  it('shows a failed load with a retry', async () => {
    const api = stub({
      [LIST]: [
        // A 4xx is never retried by the query client.
        () => problem(400, 'bad_request'),
        () => jsonResponse(200, PRICES_PAGE),
      ],
    });

    await renderAt('/billing/prices');
    expect(alerts(main()).join()).toContain('Try again');

    await click(buttonNamed(main(), 'Try again'));
    await settleUntil(() => main().querySelector('tbody'));
    expect(api.calls(LIST)).toHaveLength(2);
  });
});

// --- C: the add dialog -----------------------------------------------------------

describe('the add dialog (C)', () => {
  it('offers only the pricing models of the class type', async () => {
    stub();
    await renderAt('/billing/prices');
    await openAdd();
    const model = () => select(dialog(), 'Pricing model');
    expect(model().disabled).toBe(true);

    // #374 B1: individual and group prices can also be per period.
    setFieldValue(select(dialog(), 'Class type'), 'individual');
    expect(optionTexts(model())).toEqual([
      'Choose an option',
      'Per session',
      'Per period',
    ]);
    expect(model().value).toBe('');
    setFieldValue(model(), 'per_session');

    setFieldValue(select(dialog(), 'Class type'), 'group');
    expect(optionTexts(model())).toEqual([
      'Choose an option',
      'Per session',
      'Flat fee per enrollment',
      'Per period',
    ]);
    // per_session is still allowed for a group: kept.
    expect(model().value).toBe('per_session');

    setFieldValue(select(dialog(), 'Class type'), 'course');
    expect(optionTexts(model())).toEqual(['Course fee']);
    expect(model().value).toBe('full_course');
    // Only per-session prices have a duration.
    expect(input(dialog(), 'Duration (minutes)').disabled).toBe(true);
  });

  it('groups the subjects by level and defaults the date to today', async () => {
    stub();
    await renderAt('/billing/prices');
    await openAdd();

    const groups = [
      ...select(dialog(), 'Subject').querySelectorAll('optgroup'),
    ].map((group) => [
      group.label,
      [...group.querySelectorAll('option')].map((o) => o.textContent),
    ]);
    expect(groups).toEqual([
      ['Secundaria', ['Álgebra']],
      ['Preparatoria', ['Física']],
    ]);
    expect(input(dialog(), 'Effective from').value).toBe('2026-10-20');
  });

  it('a group scope clears and disables the level and the duration', async () => {
    const urls: URL[] = [];
    stub({
      'GET /api/v1/groups': (request) => {
        urls.push(new URL(request.url));
        return jsonResponse(200, { items: GROUP_OPTIONS, next_cursor: null });
      },
    });
    await renderAt('/billing/prices');
    await openAdd();

    setFieldValue(select(dialog(), 'Class type'), 'group');
    setFieldValue(select(dialog(), 'Pricing model'), 'per_session');
    setFieldValue(select(dialog(), 'Subject'), SUBJECT_ALGEBRA.id);
    setFieldValue(select(dialog(), 'Student level'), LEVEL_OPTIONS[1]!.id);
    setFieldValue(input(dialog(), 'Duration (minutes)'), '90');
    await settleUntil(() => !select(dialog(), 'Applies to').disabled);
    expect(urls[0]?.searchParams.get('subject_id')).toBe(SUBJECT_ALGEBRA.id);
    expect(optionTexts(select(dialog(), 'Applies to'))).toEqual([
      'None (all)',
      'Álgebra sábados',
    ]);

    setFieldValue(select(dialog(), 'Applies to'), GROUP_OPTIONS[0]!.id);

    expect(select(dialog(), 'Student level').value).toBe('');
    expect(select(dialog(), 'Student level').disabled).toBe(true);
    expect(input(dialog(), 'Duration (minutes)').value).toBe('');
    expect(input(dialog(), 'Duration (minutes)').disabled).toBe(true);
  });

  it('lists the cohorts of the subject for a course', async () => {
    const urls: URL[] = [];
    stub({
      'GET /api/v1/cohorts': (request) => {
        urls.push(new URL(request.url));
        return jsonResponse(200, {
          items: COHORT_OPTIONS,
          next_cursor: null,
        });
      },
    });
    await renderAt('/billing/prices');
    await openAdd();

    setFieldValue(select(dialog(), 'Class type'), 'course');
    setFieldValue(select(dialog(), 'Subject'), SUBJECT_PHYSICS.id);
    await settleUntil(() => !select(dialog(), 'Applies to').disabled);

    expect(urls[0]?.searchParams.get('subject_id')).toBe(SUBJECT_PHYSICS.id);
    expect(optionTexts(select(dialog(), 'Applies to'))).toEqual([
      'None (all)',
      'Física 2027-A',
    ]);
  });

  it('checks the required fields, the duration and the amount before sending', async () => {
    const api = stub();
    await renderAt('/billing/prices');
    await openAdd();

    await click(buttonNamed(dialog(), 'Save'));
    expect(dialog().textContent).toContain('Choose the class type.');
    expect(dialog().textContent).toContain('Choose the subject.');
    expect(dialog().textContent).toContain('Enter the amount.');
    expect(document.activeElement).toBe(select(dialog(), 'Class type'));

    fillIndividual('12.345');
    setFieldValue(input(dialog(), 'Duration (minutes)'), '10');
    await click(buttonNamed(dialog(), 'Save'));
    expect(dialog().textContent).toContain(
      'Enter whole minutes from 15 to 480, or leave it empty.',
    );
    expect(dialog().textContent).toContain(
      'Enter an amount of 0 or more with up to 2 decimals, for example 350.00.',
    );
    expect(input(dialog(), 'Amount (MXN)').getAttribute('aria-invalid')).toBe(
      'true',
    );

    setFieldValue(input(dialog(), 'Amount (MXN)'), '-5');
    await click(buttonNamed(dialog(), 'Save'));
    expect(dialog().textContent).toContain('Enter an amount of 0 or more');
    expect(api.calls(CREATE)).toHaveLength(0);
  });

  it('sends the amount as a string and opens the new price on 201', async () => {
    const api = stub({
      [CREATE]: () => jsonResponse(201, PRICE_DETAIL),
    });
    await renderAt('/billing/prices?class_type=individual');
    await openAdd();

    fillIndividual(' 250 ');
    await click(buttonNamed(dialog(), 'Save'));
    await settleUntil(() => main().querySelector('dl'));

    expect(api.calls(CREATE).map((call) => call.body)).toEqual([
      {
        class_type: 'individual',
        pricing_model: 'per_session',
        subject_id: SUBJECT_ALGEBRA.id,
        duration_minutes: 60,
        effective_from: '2026-10-20',
        amount: '250',
      },
    ]);
    expect(openDialog()).toBe(null);
    expect(app!.router.state.location.pathname).toBe(
      `/billing/prices/${PRICE_DETAIL.id}`,
    );
    // Back keeps the list's filters.
    const back = [...main().querySelectorAll('a')].find(
      (a) => a.textContent === 'Back to prices',
    );
    expect(back?.getAttribute('href')).toBe(
      '/billing/prices?class_type=individual',
    );
  });

  it('sends a group scope as group_id without level or duration', async () => {
    const api = stub({ [CREATE]: () => jsonResponse(201, PRICE_DETAIL) });
    await renderAt('/billing/prices');
    await openAdd();

    setFieldValue(select(dialog(), 'Class type'), 'group');
    setFieldValue(select(dialog(), 'Pricing model'), 'full_course');
    setFieldValue(select(dialog(), 'Subject'), SUBJECT_ALGEBRA.id);
    await settleUntil(() => !select(dialog(), 'Applies to').disabled);
    setFieldValue(select(dialog(), 'Applies to'), GROUP_OPTIONS[0]!.id);
    setFieldValue(input(dialog(), 'Amount (MXN)'), '1200.50');
    await click(buttonNamed(dialog(), 'Save'));

    expect(api.calls(CREATE)[0]?.body).toEqual({
      class_type: 'group',
      pricing_model: 'full_course',
      subject_id: SUBJECT_ALGEBRA.id,
      group_id: GROUP_OPTIONS[0]!.id,
      effective_from: '2026-10-20',
      amount: '1200.50',
    });
  });

  it('links to the existing price on 409 service_price_exists', async () => {
    stub({
      [CREATE]: () =>
        problemWith(409, 'service_price_exists', {
          details: { service_price_id: PRICE_WITH_NEXT.id },
        }),
    });
    await renderAt('/billing/prices');
    await openAdd();

    fillIndividual();
    await click(buttonNamed(dialog(), 'Save'));

    expect(alerts(dialog()).join()).toContain(
      'A price for this service already exists.',
    );
    const link = [...dialog().querySelectorAll('a')].find(
      (a) => a.textContent === 'View the existing price',
    );
    expect(link?.getAttribute('href')).toBe(
      `/billing/prices/${PRICE_WITH_NEXT.id}`,
    );
    // Nothing typed is lost.
    expect(input(dialog(), 'Amount (MXN)').value).toBe('350.00');
  });

  it('puts the 422 field errors on their fields and keeps the values', async () => {
    stub({
      [CREATE]: () =>
        problemWith(422, 'validation_error', {
          errors: [
            { path: ['body', 'subject_id'], code: 'subject_inactive' },
            {
              path: ['body', 'education_level_id'],
              code: 'education_level_inactive',
            },
            { path: ['body', 'effective_from'], code: 'date_out_of_range' },
            { path: ['body', 'amount'], code: 'money_precision' },
          ],
        }),
    });
    await renderAt('/billing/prices');
    await openAdd();

    fillIndividual();
    setFieldValue(select(dialog(), 'Student level'), LEVEL_OPTIONS[0]!.id);
    await click(buttonNamed(dialog(), 'Save'));

    const errorOf = (field: HTMLElement) =>
      document.getElementById(
        field.getAttribute('aria-describedby')!.split(' ').at(-1)!,
      )?.textContent;
    expect(errorOf(select(dialog(), 'Subject'))).toBe(
      'This subject or its education level is inactive. Choose another one.',
    );
    expect(errorOf(select(dialog(), 'Student level'))).toBe(
      'The education level is inactive. Reactivate it first.',
    );
    expect(errorOf(input(dialog(), 'Effective from'))).toBe(
      'Choose a date in this century.',
    );
    expect(errorOf(input(dialog(), 'Amount (MXN)'))).toBe(
      'An amount has at most 2 decimal places.',
    );
    expect(document.activeElement).toBe(select(dialog(), 'Subject'));
    expect(select(dialog(), 'Subject').value).toBe(SUBJECT_ALGEBRA.id);
    expect(input(dialog(), 'Amount (MXN)').value).toBe('350.00');
  });

  it('shows a null-amount money_invalid as "amount required"', async () => {
    stub({
      [CREATE]: () =>
        problemWith(422, 'validation_error', {
          errors: [{ path: ['body', 'amount'], code: 'money_invalid' }],
        }),
    });
    await renderAt('/billing/prices');
    await openAdd();

    fillIndividual();
    await click(buttonNamed(dialog(), 'Save'));

    expect(dialog().textContent).toContain(
      'Enter the amount in pesos, with up to 2 decimals.',
    );
  });
});

// --- #374 B: per-period prices ---------------------------------------------------------

describe('per-period prices (#374 B)', () => {
  it('labels a per-period item with its frequency and filters by the model', async () => {
    stub({}, { page: { ...PRICES_PAGE, items: [PRICE_PER_PERIOD], total: 1 } });
    await renderAt('/billing/prices');

    expect(cells(rowOf(PRICE_PER_PERIOD.id)).slice(0, 6)).toEqual([
      'Individual class · per period · monthly',
      'Secundaria · Álgebra',
      'Any',
      '—',
      '—',
      '$1,500.00',
    ]);
    expect(optionTexts(select(main(), 'Pricing model'))).toEqual([
      'All',
      'Per session',
      'Flat fee or course fee',
      'Per period',
    ]);

    setFieldValue(select(main(), 'Pricing model'), 'per_period');
    await settleUntil(() => listUrls.length >= 2);
    expect(app!.router.state.location.search).toBe('?pricing_model=per_period');
    expect(listUrls.at(-1)?.searchParams.get('pricing_model')).toBe(
      'per_period',
    );
  });

  it('asks for the frequency instead of the duration and sends it', async () => {
    const api = stub({ [CREATE]: () => jsonResponse(201, PRICE_DETAIL) });
    await renderAt('/billing/prices');
    await openAdd();

    setFieldValue(select(dialog(), 'Class type'), 'individual');
    setFieldValue(select(dialog(), 'Pricing model'), 'per_period');
    expect(() => input(dialog(), 'Duration (minutes)')).toThrow();
    expect(optionTexts(select(dialog(), 'Frequency'))).toEqual([
      'Choose an option',
      'Weekly',
      'Biweekly',
      'Monthly',
    ]);
    setFieldValue(select(dialog(), 'Subject'), SUBJECT_ALGEBRA.id);
    setFieldValue(input(dialog(), 'Amount (MXN)'), '1500.00');

    await click(buttonNamed(dialog(), 'Save'));
    expect(dialog().textContent).toContain('Choose the frequency.');
    expect(document.activeElement).toBe(select(dialog(), 'Frequency'));
    expect(api.calls(CREATE)).toHaveLength(0);

    setFieldValue(select(dialog(), 'Frequency'), 'monthly');
    await click(buttonNamed(dialog(), 'Save'));

    expect(api.calls(CREATE)[0]?.body).toEqual({
      class_type: 'individual',
      pricing_model: 'per_period',
      subject_id: SUBJECT_ALGEBRA.id,
      period_frequency: 'monthly',
      effective_from: '2026-10-20',
      amount: '1500.00',
    });
  });

  it('puts period_frequency_required on the frequency', async () => {
    stub({
      [CREATE]: () =>
        problemWith(422, 'validation_error', {
          errors: [
            {
              path: ['body', 'period_frequency'],
              code: 'period_frequency_not_allowed',
            },
          ],
        }),
    });
    await renderAt('/billing/prices');
    await openAdd();

    setFieldValue(select(dialog(), 'Class type'), 'group');
    setFieldValue(select(dialog(), 'Pricing model'), 'per_period');
    setFieldValue(select(dialog(), 'Frequency'), 'weekly');
    setFieldValue(select(dialog(), 'Subject'), SUBJECT_ALGEBRA.id);
    setFieldValue(input(dialog(), 'Amount (MXN)'), '300');
    await click(buttonNamed(dialog(), 'Save'));

    const frequency = select(dialog(), 'Frequency');
    expect(frequency.getAttribute('aria-invalid')).toBe('true');
    expect(dialog().textContent).toContain(
      'Only per-period prices have a frequency.',
    );
    expect(frequency.value).toBe('weekly');
  });

  it('clears the frequency when the model changes', async () => {
    stub();
    await renderAt('/billing/prices');
    await openAdd();

    setFieldValue(select(dialog(), 'Class type'), 'group');
    setFieldValue(select(dialog(), 'Pricing model'), 'per_period');
    setFieldValue(select(dialog(), 'Frequency'), 'weekly');
    setFieldValue(select(dialog(), 'Pricing model'), 'per_session');
    expect(input(dialog(), 'Duration (minutes)').disabled).toBe(false);
    setFieldValue(select(dialog(), 'Pricing model'), 'per_period');
    expect(select(dialog(), 'Frequency').value).toBe('');
  });
});

// --- D: the detail page --------------------------------------------------------------

describe('the detail page (D)', () => {
  const PAGE = `/billing/prices/${PRICE_DETAIL.id}`;
  const versionRow = (id: string) =>
    main().querySelector<HTMLTableRowElement>(`[data-version-id="${id}"]`)!;

  it('shows the key and the versions with their badges', async () => {
    stub();

    await renderAt(PAGE);

    expect(h1()).toBe('Individual class · per session: Secundaria · Álgebra');
    const terms = [...main().querySelectorAll('dt')].map((dt) => [
      dt.textContent,
      dt.nextElementSibling?.textContent,
    ]);
    expect(terms).toEqual([
      ['Service', 'Individual class · per session'],
      ['Subject', 'Secundaria · Álgebra'],
      ['Student level', 'Any'],
      ['Duration', '60 min'],
      ['Applies to', '—'],
      ['Current price', '$350.00'],
      ['Next change', 'Nov 3, 2026: $380.00'],
    ]);
    expect(cells(versionRow(V_UPCOMING.id))).toEqual([
      'Nov 3, 2026',
      '$380.00',
      'Upcoming',
      'Delete',
    ]);
    expect(cells(versionRow(V_CURRENT.id))).toEqual([
      'Aug 1, 2026',
      '$350.00',
      'Current',
      '',
    ]);
    expect(cells(versionRow(V_PAST.id))).toEqual([
      'Jan 5, 2026',
      '$300.00',
      'Past',
      '',
    ]);
    // The back link without list state goes to the plain list.
    const back = [...main().querySelectorAll('a')].find(
      (a) => a.textContent === 'Back to prices',
    );
    expect(back?.getAttribute('href')).toBe('/billing/prices');
  });

  it('defaults a change to the day after the latest upcoming one, not before today', async () => {
    stub();
    await renderAt(PAGE);

    await click(buttonNamed(main(), 'Schedule a price change'));
    const date = input(dialog(), 'Effective from');

    expect(date.value).toBe('2026-11-04');
    expect(date.min).toBe('2026-10-20');
  });

  it('defaults to today when the latest version already started', async () => {
    stub({
      [detailRoute(PRICE_DETAIL.id)]: () =>
        jsonResponse(200, PRICE_DETAIL_WITHOUT_UPCOMING),
    });
    await renderAt(PAGE);

    await click(buttonNamed(main(), 'Schedule a price change'));

    expect(input(dialog(), 'Effective from').value).toBe('2026-10-20');
  });

  it('has no minimum date for an item without versions', async () => {
    const empty: ServicePriceDetail = {
      ...PRICE_DETAIL,
      current: null,
      next: null,
      versions: [],
    };
    stub({ [detailRoute(PRICE_DETAIL.id)]: () => jsonResponse(200, empty) });
    await renderAt(PAGE);

    expect(main().textContent).toContain(
      'This service has no prices yet. Schedule one.',
    );
    await click(buttonNamed(main(), 'Schedule a price change'));
    expect(input(dialog(), 'Effective from').min).toBe('');
  });

  it('sends a typed amount, refreshes and announces', async () => {
    const api = stub({
      [versionsRoute(PRICE_DETAIL.id)]: () => jsonResponse(201, PRICE_DETAIL),
    });
    await renderAt(PAGE);
    const reads = api.calls(detailRoute(PRICE_DETAIL.id)).length;

    await click(buttonNamed(main(), 'Schedule a price change'));
    setFieldValue(input(dialog(), 'Amount (MXN)'), '400');
    await click(buttonNamed(dialog(), 'Schedule'));

    expect(api.calls(versionsRoute(PRICE_DETAIL.id))[0]?.body).toEqual({
      effective_from: '2026-11-04',
      amount: '400',
    });
    expect(openDialog()).toBe(null);
    expect(statusText()).toContain('Price change scheduled.');
    expect(api.calls(detailRoute(PRICE_DETAIL.id)).length).toBeGreaterThan(
      reads,
    );
  });

  it('"Not offered from this date" clears the amount and sends null', async () => {
    const api = stub({
      [versionsRoute(PRICE_DETAIL.id)]: () => jsonResponse(201, PRICE_DETAIL),
    });
    await renderAt(PAGE);
    await click(buttonNamed(main(), 'Schedule a price change'));
    setFieldValue(input(dialog(), 'Amount (MXN)'), '400');

    const checkbox = document.getElementById(
      [...dialog().querySelectorAll('label')].find(
        (label) => label.textContent === 'Not offered from this date',
      )!.htmlFor,
    )!;
    await click(checkbox);
    expect(input(dialog(), 'Amount (MXN)').value).toBe('');
    expect(input(dialog(), 'Amount (MXN)').disabled).toBe(true);
    await click(buttonNamed(dialog(), 'Schedule'));

    expect(api.calls(versionsRoute(PRICE_DETAIL.id))[0]?.body).toEqual({
      effective_from: '2026-11-04',
      amount: null,
    });
  });

  it('refuses a date before today and shows price_effective_from_invalid on the date', async () => {
    const api = stub({
      [versionsRoute(PRICE_DETAIL.id)]: () =>
        problemWith(422, 'validation_error', {
          errors: [
            {
              path: ['body', 'effective_from'],
              code: 'price_effective_from_invalid',
            },
          ],
        }),
    });
    await renderAt(PAGE);
    await click(buttonNamed(main(), 'Schedule a price change'));
    setFieldValue(input(dialog(), 'Amount (MXN)'), '400');

    setFieldValue(input(dialog(), 'Effective from'), '2026-10-19');
    await click(buttonNamed(dialog(), 'Schedule'));
    expect(dialog().textContent).toContain('Choose today or a later date.');
    expect(api.calls(versionsRoute(PRICE_DETAIL.id))).toHaveLength(0);

    setFieldValue(input(dialog(), 'Effective from'), '2026-10-25');
    await click(buttonNamed(dialog(), 'Schedule'));
    expect(dialog().textContent).toContain(
      'A price change must start today or later, and after the latest scheduled change. Choose a later date.',
    );
    expect(document.activeElement).toBe(input(dialog(), 'Effective from'));
    expect(input(dialog(), 'Amount (MXN)').value).toBe('400');
  });

  it('deletes an upcoming version behind the confirmation (204)', async () => {
    const api = stub({
      [detailRoute(PRICE_DETAIL.id)]: [
        () => jsonResponse(200, PRICE_DETAIL),
        () => jsonResponse(200, PRICE_DETAIL_WITHOUT_UPCOMING),
      ],
      [deleteRoute(PRICE_DETAIL.id, V_UPCOMING.id)]: noContent,
    });
    await renderAt(PAGE);

    await click(buttonNamed(main(), 'Delete the change of Nov 3, 2026'));
    expect(dialog().textContent).toContain(
      'The change of Nov 3, 2026 ($380.00) will be deleted.',
    );
    await click(buttonNamed(dialog(), 'Delete'));
    await settleUntil(
      () => !main().querySelector(`[data-version-id="${V_UPCOMING.id}"]`),
    );

    expect(api.calls(deleteRoute(PRICE_DETAIL.id, V_UPCOMING.id))).toHaveLength(
      1,
    );
    expect(openDialog()).toBe(null);
    expect(statusText()).toContain('Price change deleted.');
  });

  it.each([
    [
      'price_version_in_effect',
      "This price is already in effect or past, so it can't be deleted. Reload the page.",
    ],
    [
      'price_version_in_use',
      "This price has already been used by charges, so it can't be deleted. Reload the page.",
    ],
  ])('shows 409 %s in the dialog with Reload', async (code, text) => {
    const api = stub({
      [deleteRoute(PRICE_DETAIL.id, V_UPCOMING.id)]: () => problem(409, code),
    });
    await renderAt(PAGE);
    const reads = api.calls(detailRoute(PRICE_DETAIL.id)).length;

    await click(buttonNamed(main(), 'Delete the change of Nov 3, 2026'));
    await click(buttonNamed(dialog(), 'Delete'));

    expect(alerts(dialog()).join()).toContain(text);
    await click(buttonNamed(dialog(), 'Reload'));
    expect(openDialog()).toBe(null);
    expect(api.calls(detailRoute(PRICE_DETAIL.id)).length).toBeGreaterThan(
      reads,
    );
  });

  it('shows not found for an unknown id, with a link back', async () => {
    const unknown = '79999999-0000-4000-8000-000000000000';
    stub({ [detailRoute(unknown)]: () => problem(404, 'not_found') });

    await renderAt(`/billing/prices/${unknown}`);

    expect(h1()).toBe('Price not found');
    expect(document.title).toBe('Price not found · Classora');
    const back = [...main().querySelectorAll('a')].find(
      (a) => a.textContent === 'Back to prices',
    );
    expect(back?.getAttribute('href')).toBe('/billing/prices');
  });
});
