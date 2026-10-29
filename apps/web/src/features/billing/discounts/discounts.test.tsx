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
import type { DiscountPage } from './api';
import {
  DISCOUNTS_PAGE,
  EMPTY_PAGE,
  FAMILY_LOPEZ,
  PROMOTION,
  RETIRED,
  SCHOLARSHIP,
  SCHOLARSHIP_DETAIL,
  SIBLING,
  SIBLING_DETAIL,
  STUDENT_ANA,
  STUDENT_LUIS,
  SUBJECT_REF,
} from './fixtures';
import { SEARCH_DEBOUNCE_MS } from './DiscountsView';

// #367 G1 (and A-D, F): the discount list behind billing.manage (the tabs,
// each status and value type, the criteria summary, the filters in the
// URL and the request), the dialog's kind rules (scholarship → assigned,
// sibling → everyone with a required minimum, promotion → both dates), the
// percent and money checks, the string bodies, 201 → the detail, the edit
// sending only the changed fields with the version, the 409s and field
// 422s, deactivating, and the assignments (student, family, duplicate,
// remove).

const LIST = 'GET /api/v1/billing/discounts';
const CREATE = 'POST /api/v1/billing/discounts';
const ME = 'GET /api/v1/auth/me';
const detailRoute = (id: string) => `GET /api/v1/billing/discounts/${id}`;
const patchRoute = (id: string) => `PATCH /api/v1/billing/discounts/${id}`;
const assignRoute = (id: string) =>
  `POST /api/v1/billing/discounts/${id}/assignments`;
const unassignRoute = (id: string, assignmentId: string) =>
  `DELETE /api/v1/billing/discounts/${id}/assignments/${assignmentId}`;

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

const subjectsPage = {
  items: [
    {
      id: SUBJECT_REF.id,
      name: SUBJECT_REF.name,
      education_level_id: '20000000-0000-4000-8000-000000000002',
      education_level_name: 'Secundaria',
      is_active: true,
      version: 1,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    },
  ],
  total: 1,
  limit: 100,
  offset: 0,
};
const levelsPage = {
  items: [
    {
      id: '20000000-0000-4000-8000-000000000002',
      name: 'Secundaria',
      sort_order: 1,
      is_active: true,
      active_subject_count: 1,
      version: 1,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    },
  ],
  total: 1,
  limit: 100,
  offset: 0,
};
const studentsPage = {
  items: [STUDENT_ANA, STUDENT_LUIS].map((student) => {
    const [given, family] = student.name.split(' ');
    return {
      id: student.id,
      given_names: given,
      family_names: family,
      is_active: true,
      is_minor: true,
      school_grade: null,
      education_level: null,
    };
  }),
  total: 2,
  limit: 10,
  offset: 0,
};
const familiesPage = {
  items: [
    {
      id: FAMILY_LOPEZ.id,
      name: FAMILY_LOPEZ.name,
      is_active: true,
      student_count: 2,
    },
  ],
  total: 1,
  limit: 10,
  offset: 0,
};

/** The URLs the list was read with. */
let listUrls: URL[] = [];

function stub(
  routes: Record<string, Responder | Responder[]> = {},
  {
    page = DISCOUNTS_PAGE,
    as = FINANCE,
  }: { page?: DiscountPage; as?: SessionUserBody } = {},
): ApiStub {
  return stubApi({
    [ME]: meAs(as),
    [LIST]: (request) => {
      listUrls.push(new URL(request.url));
      return jsonResponse(200, page);
    },
    'GET /api/v1/subjects': () => jsonResponse(200, subjectsPage),
    'GET /api/v1/education-levels': () => jsonResponse(200, levelsPage),
    'GET /api/v1/students': () => jsonResponse(200, studentsPage),
    'GET /api/v1/families': () => jsonResponse(200, familiesPage),
    [detailRoute(SCHOLARSHIP.id)]: () => jsonResponse(200, SCHOLARSHIP_DETAIL),
    [detailRoute(SIBLING.id)]: () => jsonResponse(200, SIBLING_DETAIL),
    ...routes,
  });
}

let app: RenderedApp | undefined;

const main = () => app!.container.querySelector('main')!;
const h1 = () => main().querySelector('h1')?.textContent;
const dialog = () => openDialog()!;
const rowOf = (id: string) =>
  main().querySelector<HTMLTableRowElement>(`[data-discount-id="${id}"]`)!;
const cells = (row: Element) =>
  [...row.querySelectorAll('td')].map((cell) => cell.textContent);
const input = (root: ParentNode, label: string) =>
  labelled<HTMLInputElement>(root, label);
const select = (root: ParentNode, label: string) =>
  labelled<HTMLSelectElement>(root, label);
/** The radio or checkbox whose label starts with `text`. */
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
const tabs = () =>
  [
    ...main().querySelectorAll<HTMLAnchorElement>(
      'nav[aria-label="Billing sections"] a',
    ),
  ].map((a) => [a.textContent, a.getAttribute('aria-current')]);

async function renderAt(path: string) {
  app = await renderApp(path);
  await settleUntil(() => main().querySelector('h1'));
}

async function openAdd() {
  await click(buttonNamed(main(), 'Add discount'));
  await settleUntil(() => openDialog()?.querySelector('form'));
}

async function search(root: ParentNode, label: string, q: string) {
  setFieldValue(input(root, label), q);
  await new Promise((resolve) => setTimeout(resolve, PICKER_DEBOUNCE_MS + 30));
  await settleUntil(() =>
    root.querySelector('fieldset input[type="radio"][value]'),
  );
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

// --- A: route, tabs and access -----------------------------------------------

describe('access and tabs (A)', () => {
  it('shows Finance the Discounts tab and the page', async () => {
    stub();

    await renderAt('/billing/discounts');

    expect(tabs()).toEqual([
      ['Accounts', null],
      ['Prices', null],
      ['Discounts', 'page'],
      ['Custom prices', null],
      ['Tuition plans', null],
      ['Packages', null],
      ['Unbilled', null],
      ['Settings', null],
    ]);
    expect(main().querySelectorAll('h1')).toHaveLength(1);
    expect(h1()).toBe('Discounts');
    expect(document.title).toBe('Discounts · Classora');
    expect(main().textContent).toContain(
      'in the order scholarship → sibling discount → discount → promotion',
    );
  });

  it('forbids a Coordinator and reads nothing', async () => {
    const api = stub({}, { as: COORDINATOR });

    await renderAt('/billing/discounts');

    expect(h1()).toBe("You don't have access to this page");
    expect(api.calls(LIST)).toHaveLength(0);
  });

  it('hides the tab from a billing.view-only holder', async () => {
    stub({}, { as: { ...FINANCE, permissions: ['billing.view'] } });

    await renderAt('/billing/discounts');

    expect(h1()).toBe("You don't have access to this page");
    await renderAt('/billing/unbilled');
    expect(tabs().map(([name]) => name)).toEqual([
      'Accounts',
      'Tuition plans',
      'Packages',
      'Unbilled',
      'Settings',
    ]);
  });
});

// --- B: the list -------------------------------------------------------------------

describe('the discount list (B)', () => {
  it('shows each kind, value, criteria, validity and status', async () => {
    stub();

    await renderAt('/billing/discounts');

    expect(cells(rowOf(SCHOLARSHIP.id)).slice(0, 7)).toEqual([
      'Beca excelencia',
      'Scholarship',
      '50 %',
      '2 assigned',
      'Sessions, Course enrollment; Álgebra',
      'Aug 1, 2026 – No end',
      'Current',
    ]);
    expect(cells(rowOf(SIBLING.id)).slice(0, 7)).toEqual([
      'Hermanos',
      'Sibling discount',
      '12.5 %',
      'Everyone who qualifies',
      'families with ≥ 2 active students',
      '—',
      'Current',
    ]);
    expect(cells(rowOf(PROMOTION.id)).slice(0, 7)).toEqual([
      'Buen Fin',
      'Promotion',
      '$100.00',
      'Everyone who qualifies',
      'Individual class; Secundaria',
      'Nov 13, 2026 – Nov 16, 2026',
      'Upcoming',
    ]);
    expect(cells(rowOf(RETIRED.id)).slice(0, 7)).toEqual([
      'Verano',
      'Discount',
      '10 %',
      'Everyone who qualifies',
      'Any',
      'Until Aug 31, 2026',
      'Inactive',
    ]);
    const view = rowOf(SCHOLARSHIP.id).querySelector('a')!;
    expect(view.textContent).toBe('View');
    expect(view.getAttribute('aria-label')).toBe('View Beca excelencia');
    expect(view.getAttribute('href')).toBe(
      `/billing/discounts/${SCHOLARSHIP.id}`,
    );
  });

  it('asks for current and upcoming by default and keeps the filters in the URL', async () => {
    stub();
    await renderAt('/billing/discounts');
    expect(listUrls.at(-1)?.searchParams.getAll('status')).toEqual([
      'current',
      'upcoming',
    ]);

    setFieldValue(select(main(), 'Kind'), 'scholarship');
    await settleUntil(() => listUrls.length >= 2);
    setFieldValue(select(main(), 'Status'), 'inactive');
    await settleUntil(() => listUrls.length >= 3);
    setFieldValue(input(main(), 'Name'), ' Beca ');
    await new Promise((resolve) =>
      setTimeout(resolve, SEARCH_DEBOUNCE_MS + 30),
    );
    await settleUntil(() => listUrls.length >= 4);

    expect(app!.router.state.location.search).toBe(
      '?kind=scholarship&status=inactive&q=Beca',
    );
    const last = listUrls.at(-1)!;
    expect(last.searchParams.getAll('kind')).toEqual(['scholarship']);
    expect(last.searchParams.getAll('status')).toEqual(['inactive']);
    expect(last.searchParams.get('q')).toBe('Beca');
    expect(last.searchParams.get('offset')).toBe('0');
  });

  it('reads the filters from the URL; "All" sends no status', async () => {
    stub();

    await renderAt('/billing/discounts?status=all&page=2');

    expect(select(main(), 'Status').value).toBe('all');
    const last = listUrls.at(-1)!;
    expect(last.searchParams.getAll('status')).toEqual([]);
    expect(last.searchParams.get('offset')).toBe('25');
  });

  it('shows the empty state with the add button', async () => {
    stub({}, { page: EMPTY_PAGE });

    await renderAt('/billing/discounts');

    expect(main().textContent).toContain('No discounts yet.');
    expect(
      [...main().querySelectorAll('button')].filter(
        (b) => b.textContent === 'Add discount',
      ),
    ).toHaveLength(2);
  });

  it('shows a failed load with a retry', async () => {
    const api = stub({
      [LIST]: [
        () => problemWith(400, 'bad_request'),
        () => jsonResponse(200, DISCOUNTS_PAGE),
      ],
    });

    await renderAt('/billing/discounts');
    expect(alerts(main()).join()).toContain('Try again');

    await click(buttonNamed(main(), 'Try again'));
    await settleUntil(() => main().querySelector('tbody'));
    expect(api.calls(LIST)).toHaveLength(2);
  });
});

// --- C: the dialog ---------------------------------------------------------------

describe('the add dialog (C)', () => {
  it('applies the kind rules to who gets it and the minimum', async () => {
    stub();
    await renderAt('/billing/discounts');
    await openAdd();

    const everyone = () => choice(dialog(), 'Everyone who qualifies');
    const assigned = () => choice(dialog(), 'Only assigned');
    expect(everyone().checked).toBe(true);
    expect(assigned().disabled).toBe(false);

    await click(choice(dialog(), 'Scholarship'));
    expect(assigned().checked).toBe(true);
    expect(everyone().disabled).toBe(true);
    expect(dialog().textContent).toContain(
      'A scholarship is only for the students or families you assign.',
    );
    expect(() =>
      input(dialog(), 'Minimum active students in the family'),
    ).toThrow();

    await click(choice(dialog(), 'Sibling discount'));
    expect(everyone().checked).toBe(true);
    expect(assigned().disabled).toBe(true);
    setFieldValue(input(dialog(), 'Name'), 'Hermanos');
    setFieldValue(input(dialog(), 'Percentage (%)'), '10');
    await click(buttonNamed(dialog(), 'Save'));
    expect(
      errorOf(input(dialog(), 'Minimum active students in the family')),
    ).toContain('Enter the minimum number of active students.');
    setFieldValue(
      input(dialog(), 'Minimum active students in the family'),
      '21',
    );
    await click(buttonNamed(dialog(), 'Save'));
    expect(
      errorOf(input(dialog(), 'Minimum active students in the family')),
    ).toContain('Enter a whole number from 2 to 20.');
  });

  it('requires both dates for a promotion and keeps them in order', async () => {
    const api = stub();
    await renderAt('/billing/discounts');
    await openAdd();

    await click(choice(dialog(), 'Promotion'));
    setFieldValue(input(dialog(), 'Name'), 'Buen Fin');
    setFieldValue(input(dialog(), 'Percentage (%)'), '15');
    await click(buttonNamed(dialog(), 'Save'));
    expect(errorOf(input(dialog(), 'Valid from'))).toContain(
      'A promotion needs both dates.',
    );
    expect(errorOf(input(dialog(), 'Valid until'))).toContain(
      'A promotion needs both dates.',
    );

    setFieldValue(input(dialog(), 'Valid from'), '2026-11-16');
    setFieldValue(input(dialog(), 'Valid until'), '2026-11-13');
    await click(buttonNamed(dialog(), 'Save'));
    expect(errorOf(input(dialog(), 'Valid until'))).toContain(
      "The end can't be before the start.",
    );
    expect(api.calls(CREATE)).toHaveLength(0);
  });

  it.each([
    ['', 'Enter the value.'],
    ['0', 'Enter more than 0 and up to 100.'],
    ['100.5', 'Enter more than 0 and up to 100.'],
    ['12.34567', 'A percentage has at most 4 decimals.'],
    ['10%', 'Enter a percentage such as 10 or 12.5.'],
  ])('checks the percentage %j', async (value, message) => {
    stub();
    await renderAt('/billing/discounts');
    await openAdd();

    setFieldValue(input(dialog(), 'Name'), 'Descuento');
    setFieldValue(input(dialog(), 'Percentage (%)'), value);
    await click(buttonNamed(dialog(), 'Save'));

    expect(errorOf(input(dialog(), 'Percentage (%)'))).toContain(message);
    expect(document.activeElement).toBe(input(dialog(), 'Percentage (%)'));
  });

  it('checks a fixed amount', async () => {
    stub();
    await renderAt('/billing/discounts');
    await openAdd();

    setFieldValue(input(dialog(), 'Name'), 'Descuento');
    await click(choice(dialog(), 'Fixed amount'));
    setFieldValue(input(dialog(), 'Amount (MXN)'), '0');
    await click(buttonNamed(dialog(), 'Save'));
    expect(errorOf(input(dialog(), 'Amount (MXN)'))).toContain(
      'Enter an amount greater than 0.',
    );
    setFieldValue(input(dialog(), 'Amount (MXN)'), '12.345');
    await click(buttonNamed(dialog(), 'Save'));
    expect(errorOf(input(dialog(), 'Amount (MXN)'))).toContain(
      'Enter an amount with up to 2 decimals',
    );
  });

  it('sends strings and the chosen criteria, then opens the new discount', async () => {
    const api = stub({
      [CREATE]: () => jsonResponse(201, SCHOLARSHIP_DETAIL),
    });
    await renderAt('/billing/discounts?kind=scholarship');
    await openAdd();

    await click(choice(dialog(), 'Scholarship'));
    setFieldValue(input(dialog(), 'Name'), ' Beca excelencia ');
    setFieldValue(input(dialog(), 'Percentage (%)'), '50');
    await click(choice(dialog(), 'Course enrollment'));
    await click(choice(dialog(), 'Sessions'));
    await click(choice(dialog(), 'Álgebra'));
    setFieldValue(input(dialog(), 'Valid from'), '2026-11-01');
    await click(buttonNamed(dialog(), 'Save'));
    await settleUntil(() => main().querySelector('dl'));

    expect(api.calls(CREATE).map((call) => call.body)).toEqual([
      {
        kind: 'scholarship',
        audience: 'assigned',
        name: 'Beca excelencia',
        percent: '50',
        starts_on: '2026-11-01',
        eligibility: {
          charge_kinds: ['session', 'cohort_enrollment'],
          subject_ids: [SUBJECT_REF.id],
        },
        is_active: true,
      },
    ]);
    expect(openDialog()).toBe(null);
    expect(app!.router.state.location.pathname).toBe(
      `/billing/discounts/${SCHOLARSHIP.id}`,
    );
    const back = [...main().querySelectorAll('a')].find(
      (a) => a.textContent === 'Back to discounts',
    );
    expect(back?.getAttribute('href')).toBe(
      '/billing/discounts?kind=scholarship',
    );
  });

  it('sends a sibling discount with an amount and its minimum', async () => {
    const api = stub({ [CREATE]: () => jsonResponse(201, SIBLING_DETAIL) });
    await renderAt('/billing/discounts');
    await openAdd();

    await click(choice(dialog(), 'Sibling discount'));
    setFieldValue(input(dialog(), 'Name'), 'Hermanos');
    await click(choice(dialog(), 'Fixed amount'));
    setFieldValue(input(dialog(), 'Amount (MXN)'), ' 150 ');
    setFieldValue(
      input(dialog(), 'Minimum active students in the family'),
      '2',
    );
    await click(buttonNamed(dialog(), 'Save'));

    expect(api.calls(CREATE)[0]?.body).toEqual({
      kind: 'sibling',
      audience: 'all',
      name: 'Hermanos',
      amount: '150',
      eligibility: { min_family_students: 2 },
      is_active: true,
    });
  });

  it('puts 409 discount_name_exists and the field 422s on their fields', async () => {
    stub({
      [CREATE]: [
        () => problemWith(409, 'discount_name_exists'),
        () =>
          problemWith(422, 'validation_error', {
            errors: [
              {
                path: ['body', 'eligibility', 'subject_ids', 0],
                code: 'subject_inactive',
              },
              { path: ['body', 'percent'], code: 'percent_precision' },
            ],
          }),
      ],
    });
    await renderAt('/billing/discounts');
    await openAdd();

    setFieldValue(input(dialog(), 'Name'), 'Verano');
    setFieldValue(input(dialog(), 'Percentage (%)'), '10');
    await click(choice(dialog(), 'Álgebra'));
    await click(buttonNamed(dialog(), 'Save'));
    expect(errorOf(input(dialog(), 'Name'))).toBe(
      'A discount with this name already exists.',
    );
    expect(document.activeElement).toBe(input(dialog(), 'Name'));

    await click(buttonNamed(dialog(), 'Save'));
    const subjects = [...dialog().querySelectorAll('fieldset')].find(
      (fieldset) =>
        fieldset.querySelector('legend')?.textContent === 'Subjects',
    )!;
    expect(errorOf(subjects)).toContain(
      'This subject or its education level is inactive.',
    );
    expect(errorOf(input(dialog(), 'Percentage (%)'))).toContain(
      'A percentage has at most 4 decimals.',
    );
    // Nothing typed is lost.
    expect(input(dialog(), 'Name').value).toBe('Verano');
    expect(choice(dialog(), 'Álgebra').checked).toBe(true);
  });
});

// --- D: the detail page --------------------------------------------------------

describe('the detail page (D)', () => {
  const PAGE = `/billing/discounts/${SCHOLARSHIP.id}`;
  const assignmentRow = (id: string) =>
    main().querySelector<HTMLTableRowElement>(`[data-assignment-id="${id}"]`)!;

  it('shows the discount and its assignments', async () => {
    stub();

    await renderAt(PAGE);

    expect(h1()).toBe('Beca excelencia');
    expect(document.title).toBe('Beca excelencia · Classora');
    const terms = [...main().querySelectorAll('dt')].map((dt) => [
      dt.textContent,
      dt.nextElementSibling?.textContent,
    ]);
    expect(terms).toEqual([
      ['Kind', 'Scholarship'],
      ['Value', '50 %'],
      ['Who gets it', '2 assigned'],
      ['Criteria', 'Sessions, Course enrollment; Álgebra'],
      ['Valid', 'Aug 1, 2026 – No end'],
      ['Status', 'Current'],
    ]);
    const [ana, family] = SCHOLARSHIP_DETAIL.assignments;
    expect(cells(assignmentRow(ana!.id))).toEqual([
      'Ana López',
      'Student',
      'Aug 2, 2026',
      'Remove',
    ]);
    expect(cells(assignmentRow(family!.id))).toEqual([
      'Familia López',
      'Family',
      'Aug 3, 2026',
      'Remove',
    ]);
    const back = [...main().querySelectorAll('a')].find(
      (a) => a.textContent === 'Back to discounts',
    );
    expect(back?.getAttribute('href')).toBe('/billing/discounts');
  });

  it('says who qualifies instead of assignments for everyone', async () => {
    stub();

    await renderAt(`/billing/discounts/${SIBLING.id}`);

    expect(main().textContent).toContain(
      'This discount applies to everyone who meets its criteria',
    );
    expect(() => buttonNamed(main(), 'Assign')).toThrow();
  });

  it('edits only the changed fields with the version', async () => {
    const api = stub({
      [patchRoute(SCHOLARSHIP.id)]: () => jsonResponse(200, SCHOLARSHIP_DETAIL),
    });
    await renderAt(PAGE);

    await click(buttonNamed(main(), 'Edit'));
    await settleUntil(() => openDialog()?.querySelector('form'));
    expect(dialog().textContent).toContain(
      'Changes apply to charges posted from now on. Posted charges keep the discount as it was.',
    );
    // The kind and who gets it are read-only.
    expect(() => choice(dialog(), 'Promotion')).toThrow();
    expect(input(dialog(), 'Percentage (%)').value).toBe('50');

    await click(choice(dialog(), 'Fixed amount'));
    setFieldValue(input(dialog(), 'Amount (MXN)'), '500');
    await click(choice(dialog(), 'Course enrollment'));
    await click(buttonNamed(dialog(), 'Save'));
    await settleUntil(() => openDialog() === null);

    expect(api.calls(patchRoute(SCHOLARSHIP.id)).map((c) => c.body)).toEqual([
      {
        version: SCHOLARSHIP.version,
        amount: '500',
        percent: null,
        eligibility: {
          charge_kinds: ['session'],
          subject_ids: [SUBJECT_REF.id],
        },
      },
    ]);
    expect(statusText()).toContain('Discount saved.');
  });

  it('shows the reload message on a version conflict', async () => {
    const api = stub({
      [patchRoute(SCHOLARSHIP.id)]: () => problemWith(409, 'version_conflict'),
    });
    await renderAt(PAGE);
    await click(buttonNamed(main(), 'Edit'));
    await settleUntil(() => openDialog()?.querySelector('form'));

    setFieldValue(input(dialog(), 'Name'), 'Beca 2027');
    await click(buttonNamed(dialog(), 'Save'));

    expect(alerts(dialog()).join()).toContain(
      'Someone else changed this record.',
    );
    const before = api.calls(detailRoute(SCHOLARSHIP.id)).length;
    await click(buttonNamed(dialog(), 'Reload'));
    await settleUntil(
      () => api.calls(detailRoute(SCHOLARSHIP.id)).length > before,
    );
    expect(input(dialog(), 'Name').value).toBe('Beca 2027');
  });

  it('deactivates behind a confirmation', async () => {
    const api = stub({
      [patchRoute(SCHOLARSHIP.id)]: () =>
        jsonResponse(200, { ...SCHOLARSHIP_DETAIL, is_active: false }),
    });
    await renderAt(PAGE);

    await click(buttonNamed(main(), 'Deactivate'));
    expect(dialog().textContent).toContain('Deactivate this discount?');
    await click(buttonNamed(dialog(), 'Deactivate'));
    await settleUntil(() => openDialog() === null);

    expect(api.calls(patchRoute(SCHOLARSHIP.id))[0]?.body).toEqual({
      version: SCHOLARSHIP.version,
      is_active: false,
    });
    expect(statusText()).toContain('Discount deactivated.');
  });

  it('assigns a student, disabling the ones already assigned', async () => {
    const api = stub({
      [assignRoute(SCHOLARSHIP.id)]: () =>
        jsonResponse(201, SCHOLARSHIP_DETAIL),
    });
    await renderAt(PAGE);

    await click(buttonNamed(main(), 'Assign'));
    await search(dialog(), 'Search student', 'L');
    expect(choice(dialog(), 'Ana López (already assigned)').disabled).toBe(
      true,
    );
    await click(choice(dialog(), 'Luis Pérez'));
    await click(buttonNamed(dialog(), 'Assign'));
    await settleUntil(() => openDialog() === null);

    expect(api.calls(assignRoute(SCHOLARSHIP.id))[0]?.body).toEqual({
      student_id: STUDENT_LUIS.id,
    });
    expect(statusText()).toContain('Luis Pérez assigned.');
  });

  it('assigns a family and shows a duplicate inline', async () => {
    const api = stub({
      [assignRoute(SCHOLARSHIP.id)]: () =>
        problemWith(409, 'discount_assignment_exists'),
      [detailRoute(SCHOLARSHIP.id)]: () =>
        jsonResponse(200, { ...SCHOLARSHIP_DETAIL, assignments: [] }),
    });
    await renderAt(PAGE);

    await click(buttonNamed(main(), 'Assign'));
    await click(choice(dialog(), 'Family'));
    await search(dialog(), 'Search family', 'López');
    await click(choice(dialog(), 'Familia López'));
    await click(buttonNamed(dialog(), 'Assign'));

    expect(api.calls(assignRoute(SCHOLARSHIP.id))[0]?.body).toEqual({
      family_id: FAMILY_LOPEZ.id,
    });
    expect(alerts(dialog()).join()).toContain(
      'This student or family is already assigned to this discount.',
    );
    expect(openDialog()).not.toBe(null);
  });

  it('removes an assignment behind a confirmation', async () => {
    const [ana] = SCHOLARSHIP_DETAIL.assignments;
    const api = stub({
      [unassignRoute(SCHOLARSHIP.id, ana!.id)]: noContent,
    });
    await renderAt(PAGE);

    await click(buttonNamed(main(), 'Remove Ana López'));
    expect(dialog().textContent).toContain('Remove this assignment?');
    await click(buttonNamed(dialog(), 'Remove'));
    await settleUntil(() => openDialog() === null);

    expect(api.calls(unassignRoute(SCHOLARSHIP.id, ana!.id))).toHaveLength(1);
    expect(statusText()).toContain('Assignment removed.');
  });

  it('shows the not-found message for an unknown id', async () => {
    const unknown = '70000000-0000-4000-8000-000000000099';
    stub({
      [detailRoute(unknown)]: () => problemWith(404, 'not_found'),
    });

    await renderAt(`/billing/discounts/${unknown}`);
    await settleUntil(() => h1() === 'Discount not found');

    expect(main().textContent).toContain(
      "This discount doesn't exist or is no longer available.",
    );
  });
});
