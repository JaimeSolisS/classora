import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SessionUserBody } from '@/features/auth/session';
import { problemWith } from '@/features/academics/testing';
import { PICKER_DEBOUNCE_MS } from '@/features/guardians/SearchPicker';
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
import { FAMILY_LOPEZ, STUDENT_ANA } from '../discounts/fixtures';
import { PRICE_WITH_NEXT, PRICES_PAGE } from '../pricing/fixtures';
import type { CustomPricePage } from './api';
import {
  CP_CURRENT,
  CP_PAST,
  CP_UPCOMING,
  CUSTOM_PRICES_PAGE,
  EMPTY_CUSTOM_PAGE,
} from './fixtures';

// #367 G1 (and A, E): the custom price list behind billing.manage (the tab,
// each status with its End and Delete actions, the filters in the URL and
// the request, the student or family filter and its deep link), "Add
// custom price" (the target and catalog price, string body, 409 overlap,
// 422 on the start date, the deep link preselecting the family), "End"
// (default and minimum date, PATCH with the version, 422 expiry) and
// "Delete" (204, 409 in use with "Reload").

const LIST = 'GET /api/v1/billing/custom-prices';
const CREATE = 'POST /api/v1/billing/custom-prices';
const CATALOG = 'GET /api/v1/billing/service-prices';
const ME = 'GET /api/v1/auth/me';
const itemRoute = (method: string, id: string) =>
  `${method} /api/v1/billing/custom-prices/${id}`;

const FINANCE: SessionUserBody = {
  ...TEST_USER,
  roles: ['finance_staff'],
  permissions: [
    'student.view',
    'guardian.view',
    'billing.manage',
    'billing.view',
  ],
};
const COORDINATOR: SessionUserBody = {
  ...TEST_USER,
  roles: ['academic_coordinator'],
  permissions: ['student.view', 'catalog.manage', 'schedule.view'],
};

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

let listUrls: URL[] = [];

function stub(
  routes: Record<string, Responder | Responder[]> = {},
  {
    page = CUSTOM_PRICES_PAGE,
    as = FINANCE,
  }: { page?: CustomPricePage; as?: SessionUserBody } = {},
): ApiStub {
  return stubApi({
    [ME]: meAs(as),
    [LIST]: (request) => {
      listUrls.push(new URL(request.url));
      return jsonResponse(200, page);
    },
    [CATALOG]: () => jsonResponse(200, PRICES_PAGE),
    'GET /api/v1/students': () => jsonResponse(200, studentsPage),
    [`GET /api/v1/families/${FAMILY_LOPEZ.id}`]: () =>
      jsonResponse(200, { id: FAMILY_LOPEZ.id, name: FAMILY_LOPEZ.name }),
    ...routes,
  });
}

let app: RenderedApp | undefined;

const main = () => app!.container.querySelector('main')!;
const h1 = () => main().querySelector('h1')?.textContent;
const dialog = () => openDialog()!;
const rowOf = (id: string) =>
  main().querySelector<HTMLTableRowElement>(`[data-custom-price-id="${id}"]`)!;
const cells = (row: Element) =>
  [...row.querySelectorAll('td')].map((cell) => cell.textContent);
const buttons = (row: Element) =>
  [...row.querySelectorAll('button')].map((b) => b.textContent);
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
const errorOf = (element: Element) =>
  (element.getAttribute('aria-describedby') ?? '')
    .split(' ')
    .map((id) => document.getElementById(id)?.textContent)
    .filter(Boolean)
    .join('|');
const statusText = () =>
  [...main().querySelectorAll('[role="status"]')]
    .map((s) => s.textContent)
    .join('|');

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

async function openAdd() {
  await click(buttonNamed(main(), 'Add custom price'));
  await settleUntil(() => openDialog()?.querySelector('form'));
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

describe('access (A)', () => {
  it('shows Finance the Custom prices tab and the page', async () => {
    stub();

    await renderAt('/billing/custom-prices');

    const tabs = [
      ...main().querySelectorAll<HTMLAnchorElement>(
        'nav[aria-label="Billing sections"] a',
      ),
    ].map((a) => [a.textContent, a.getAttribute('aria-current')]);
    expect(tabs).toEqual([
      ['Prices', null],
      ['Discounts', null],
      ['Custom prices', 'page'],
      ['Unbilled', null],
      ['Settings', null],
    ]);
    expect(h1()).toBe('Custom prices');
    expect(document.title).toBe('Custom prices · Classora');
    expect(main().textContent).toContain(
      "a student's beats the family's). Discounts still apply on top.",
    );
  });

  it('forbids a Coordinator and reads nothing', async () => {
    const api = stub({}, { as: COORDINATOR });

    await renderAt('/billing/custom-prices');

    expect(h1()).toBe("You don't have access to this page");
    expect(api.calls(LIST)).toHaveLength(0);
  });
});

describe('the list (E2, E3, E6)', () => {
  it('shows each status with its actions', async () => {
    stub();

    await renderAt('/billing/custom-prices');

    expect(cells(rowOf(CP_CURRENT.id)).slice(0, 6)).toEqual([
      'Ana LópezStudent',
      'Individual class · per session, Secundaria · Álgebra, 60 min',
      '$350.00',
      '$300.00',
      'Sep 1, 2026 – No end',
      'Current',
    ]);
    expect(buttons(rowOf(CP_CURRENT.id))).toEqual(['End']);
    expect(cells(rowOf(CP_UPCOMING.id)).slice(0, 6)).toEqual([
      'Familia LópezFamily',
      'Group · flat fee per enrollment, Secundaria · Álgebra, Álgebra sábados',
      'Not offered',
      '$1,000.00',
      'Nov 1, 2026 – Dec 31, 2026',
      'Upcoming',
    ]);
    expect(buttons(rowOf(CP_UPCOMING.id))).toEqual(['End', 'Delete']);
    expect(cells(rowOf(CP_PAST.id)).slice(2, 4)).toEqual([
      'No price yet',
      '$0.00',
    ]);
    expect(buttons(rowOf(CP_PAST.id))).toEqual([]);
    expect(
      rowOf(CP_CURRENT.id).querySelector('button')?.getAttribute('aria-label'),
    ).toBe(
      'End the custom price of Ana López for Individual class · per session, Secundaria · Álgebra, 60 min',
    );
  });

  it('asks for current and upcoming by default and keeps the status in the URL', async () => {
    stub();
    await renderAt('/billing/custom-prices');
    expect(listUrls.at(-1)?.searchParams.getAll('status')).toEqual([
      'current',
      'upcoming',
    ]);

    setFieldValue(select(main(), 'Status'), 'past');
    await settleUntil(() => listUrls.length >= 2);

    expect(app!.router.state.location.search).toBe('?status=past');
    expect(listUrls.at(-1)?.searchParams.getAll('status')).toEqual(['past']);
  });

  it('filters by a student picked in the search', async () => {
    stub();
    await renderAt('/billing/custom-prices');

    await search(main(), 'Search student', 'Ana');
    await click(choice(main(), 'Ana López'));
    await settleUntil(() => listUrls.length >= 2);

    expect(app!.router.state.location.search).toBe(
      `?student_id=${STUDENT_ANA.id}`,
    );
    expect(listUrls.at(-1)?.searchParams.get('student_id')).toBe(
      STUDENT_ANA.id,
    );
    expect(main().textContent).toContain('Ana López');

    await click(buttonNamed(main(), 'Show everyone'));
    await settleUntil(() => listUrls.length >= 3);
    expect(app!.router.state.location.search).toBe('');
  });

  it('reads a family deep link and preselects it in the add dialog', async () => {
    stub();

    await renderAt(`/billing/custom-prices?family_id=${FAMILY_LOPEZ.id}`);
    await settleUntil(() => main().textContent?.includes('Familia López'));

    expect(listUrls.at(-1)?.searchParams.get('family_id')).toBe(
      FAMILY_LOPEZ.id,
    );
    await openAdd();
    expect(choice(dialog(), 'Family').checked).toBe(true);
    expect(dialog().textContent).toContain('Chosen: Familia López');
  });

  it('shows the empty state with the add button', async () => {
    stub({}, { page: EMPTY_CUSTOM_PAGE });

    await renderAt('/billing/custom-prices');

    expect(main().textContent).toContain('No custom prices yet.');
  });

  it('shows a failed load with a retry', async () => {
    const api = stub({
      [LIST]: [
        () => problemWith(400, 'bad_request'),
        () => jsonResponse(200, CUSTOM_PRICES_PAGE),
      ],
    });

    await renderAt('/billing/custom-prices');
    await click(buttonNamed(main(), 'Try again'));
    await settleUntil(() => main().querySelector('tbody'));
    expect(api.calls(LIST)).toHaveLength(2);
  });
});

describe('the add dialog (E4)', () => {
  async function fill() {
    await search(dialog(), 'Search student', 'Ana');
    await click(choice(dialog(), 'Ana López'));
    setFieldValue(select(dialog(), 'Catalog price'), PRICE_WITH_NEXT.id);
    setFieldValue(input(dialog(), 'Custom price (MXN)'), ' 300 ');
    setFieldValue(input(dialog(), 'From'), '2026-10-21');
  }

  it('lists the catalog prices with their price today and checks the fields', async () => {
    const api = stub();
    await renderAt('/billing/custom-prices');
    await openAdd();

    const options = [...select(dialog(), 'Catalog price').options].map(
      (option) => option.textContent,
    );
    expect(options).toEqual([
      'Choose an option',
      'Individual class · per session, Secundaria · Álgebra, 60 min — $350.00',
      'Group · flat fee per enrollment, Secundaria · Álgebra, Álgebra sábados — Not offered',
      'Course · course fee, Preparatoria · Física, Preparatoria — No price yet',
    ]);
    expect(input(dialog(), 'From').value).toBe('2026-10-20');
    expect(input(dialog(), 'From').min).toBe('2026-10-20');

    setFieldValue(input(dialog(), 'From'), '2026-10-19');
    setFieldValue(input(dialog(), 'Custom price (MXN)'), '-1');
    await click(buttonNamed(dialog(), 'Save'));
    expect(dialog().textContent).toContain('Choose a student or a family.');
    expect(dialog().textContent).toContain('Choose the catalog price.');
    expect(errorOf(input(dialog(), 'Custom price (MXN)'))).toContain(
      'Enter an amount of 0 or more',
    );
    expect(errorOf(input(dialog(), 'From'))).toContain(
      'Choose today or a later date.',
    );
    expect(api.calls(CREATE)).toHaveLength(0);
  });

  it('sends the amount as a string and announces the new price', async () => {
    const api = stub({ [CREATE]: () => jsonResponse(201, CP_CURRENT) });
    await renderAt('/billing/custom-prices');
    await openAdd();

    await fill();
    await click(buttonNamed(dialog(), 'Save'));
    await settleUntil(() => openDialog() === null);

    expect(api.calls(CREATE).map((call) => call.body)).toEqual([
      {
        service_price_id: PRICE_WITH_NEXT.id,
        student_id: STUDENT_ANA.id,
        effective_from: '2026-10-21',
        amount: '300',
      },
    ]);
    expect(statusText()).toContain('Custom price added.');
    expect(api.calls(LIST).length).toBeGreaterThanOrEqual(2);
  });

  it('shows an overlap inside the dialog and a refused date on the field', async () => {
    stub({
      [CREATE]: [
        () => problemWith(409, 'custom_price_overlap'),
        () =>
          problemWith(422, 'custom_price_effective_from_invalid', {
            errors: [
              {
                path: ['body', 'effective_from'],
                code: 'custom_price_effective_from_invalid',
              },
            ],
          }),
      ],
    });
    await renderAt('/billing/custom-prices');
    await openAdd();
    await fill();

    await click(buttonNamed(dialog(), 'Save'));
    expect(alerts(dialog()).join()).toContain(
      'This student already has a custom price for this service in those dates.',
    );

    await click(buttonNamed(dialog(), 'Save'));
    expect(errorOf(input(dialog(), 'From'))).toContain(
      "a custom price can't start in the past",
    );
    expect(input(dialog(), 'Custom price (MXN)').value).toBe(' 300 ');
  });
});

describe('end and delete (E5)', () => {
  it('ends a price from today with the version', async () => {
    const api = stub({
      [itemRoute('PATCH', CP_CURRENT.id)]: () =>
        jsonResponse(200, { ...CP_CURRENT, expires_on: '2026-10-31' }),
    });
    await renderAt('/billing/custom-prices');

    await click(buttonNamed(rowOf(CP_CURRENT.id), 'End'));
    expect(input(dialog(), 'Until').value).toBe('2026-10-20');
    expect(input(dialog(), 'Until').min).toBe('2026-10-20');
    setFieldValue(input(dialog(), 'Until'), '2026-10-31');
    await click(buttonNamed(dialog(), 'End'));
    await settleUntil(() => openDialog() === null);

    expect(api.calls(itemRoute('PATCH', CP_CURRENT.id))[0]?.body).toEqual({
      version: CP_CURRENT.version,
      expires_on: '2026-10-31',
    });
    expect(statusText()).toContain('Custom price ended.');
  });

  it('puts custom_price_expiry_invalid on the date', async () => {
    stub({
      [itemRoute('PATCH', CP_CURRENT.id)]: () =>
        problemWith(422, 'custom_price_expiry_invalid', {
          errors: [
            {
              path: ['body', 'expires_on'],
              code: 'custom_price_expiry_invalid',
            },
          ],
        }),
    });
    await renderAt('/billing/custom-prices');

    await click(buttonNamed(rowOf(CP_CURRENT.id), 'End'));
    await click(buttonNamed(dialog(), 'End'));

    expect(errorOf(input(dialog(), 'Until'))).toContain(
      'not before a session already charged at this price',
    );
  });

  it('an upcoming price starts its end at its start date', async () => {
    stub();
    await renderAt('/billing/custom-prices');

    await click(buttonNamed(rowOf(CP_UPCOMING.id), 'End'));

    expect(input(dialog(), 'Until').value).toBe('2026-11-01');
    expect(input(dialog(), 'Until').min).toBe('2026-11-01');
  });

  it('deletes an upcoming price behind a confirmation', async () => {
    const api = stub({
      [itemRoute('DELETE', CP_UPCOMING.id)]: noContent,
    });
    await renderAt('/billing/custom-prices');

    await click(buttonNamed(rowOf(CP_UPCOMING.id), 'Delete'));
    expect(dialog().textContent).toContain('Delete this custom price?');
    await click(buttonNamed(dialog(), 'Delete'));
    await settleUntil(() => openDialog() === null);

    expect(api.calls(itemRoute('DELETE', CP_UPCOMING.id))).toHaveLength(1);
    expect(statusText()).toContain('Custom price deleted.');
  });

  it('keeps a refused delete open with "Reload"', async () => {
    const api = stub({
      [itemRoute('DELETE', CP_UPCOMING.id)]: () =>
        problemWith(409, 'custom_price_in_use'),
    });
    await renderAt('/billing/custom-prices');

    await click(buttonNamed(rowOf(CP_UPCOMING.id), 'Delete'));
    await click(buttonNamed(dialog(), 'Delete'));
    expect(alerts(dialog()).join()).toContain(
      'A charge already uses this custom price: end it instead.',
    );

    const before = api.calls(LIST).length;
    await click(buttonNamed(dialog(), 'Reload'));
    await settleUntil(() => openDialog() === null);
    await settleUntil(() => api.calls(LIST).length > before);
  });
});
