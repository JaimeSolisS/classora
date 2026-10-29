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
import {
  GROUP_OPTIONS,
  PRICE_GROUP_PER_PERIOD,
  PRICE_PER_PERIOD,
} from '../pricing/fixtures';
import type { TuitionPlanPage } from './api';
import {
  CATALOG_PRICES,
  EMPTY_PLANS_PAGE,
  ENROLLMENT_FEE_CHARGED,
  ENROLLMENT_OPEN,
  ENROLLMENT_WITH_PLAN,
  ENROLLMENTS,
  PLAN_ACTIVE,
  PLAN_ACTIVE_DETAIL,
  PLAN_PAUSED,
  PLAN_SCHEDULED,
  PLANS_PAGE,
  STUDENT_ANA,
} from './fixtures';

// #374 H1 (A, C, D): the Tuition plans tab behind billing.view (hidden
// without it), the list (rows, status badges, the status and student
// filters in the URL and the request, empty, error with retry), read-only
// without billing.manage, and the create dialog per type (the enrollment
// choices with their disabled reasons, the prices filtered to the type and
// the enrollment, "After N periods" → ends_on, each error code, 201 → the
// plan's page).

const LIST = 'GET /api/v1/billing/tuition-plans';
const CREATE = 'POST /api/v1/billing/tuition-plans';
const CATALOG = 'GET /api/v1/billing/service-prices';
const ELIGIBLE = 'GET /api/v1/billing/tuition-plans/eligible-enrollments';
const ME = 'GET /api/v1/auth/me';

const user = (
  roles: SessionUserBody['roles'],
  permissions: SessionUserBody['permissions'],
): SessionUserBody => ({ ...TEST_USER, roles, permissions });

const FINANCE = user(
  ['finance_staff'],
  ['student.view', 'guardian.view', 'billing.view', 'billing.manage'],
);
const VIEWER = user(['finance_staff'], ['billing.view']);
const MANAGER_ONLY = user(['finance_staff'], ['billing.manage']);

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
let catalogUrls: URL[] = [];
let studentUrls: URL[] = [];

function stub(
  routes: Record<string, Responder | Responder[]> = {},
  {
    page = PLANS_PAGE,
    as = FINANCE,
  }: { page?: TuitionPlanPage; as?: SessionUserBody } = {},
): ApiStub {
  return stubApi({
    [ME]: meAs(as),
    [LIST]: (request) => {
      listUrls.push(new URL(request.url));
      return jsonResponse(200, page);
    },
    [CATALOG]: (request) => {
      catalogUrls.push(new URL(request.url));
      return jsonResponse(200, {
        items: CATALOG_PRICES,
        total: CATALOG_PRICES.length,
        limit: 100,
        offset: 0,
      });
    },
    [ELIGIBLE]: () => jsonResponse(200, ENROLLMENTS),
    'GET /api/v1/students': (request) => {
      studentUrls.push(new URL(request.url));
      return jsonResponse(200, studentsPage);
    },
    [`GET /api/v1/students/${STUDENT_ANA.id}`]: () =>
      jsonResponse(200, { ...studentsPage.items[0] }),
    [`GET /api/v1/billing/tuition-plans/${PLAN_ACTIVE.id}`]: () =>
      jsonResponse(200, PLAN_ACTIVE_DETAIL),
    ...routes,
  });
}

let app: RenderedApp | undefined;

const main = () => app!.container.querySelector('main')!;
const h1 = () => main().querySelector('h1')?.textContent;
const dialog = () => openDialog()!;
const rowOf = (id: string) =>
  main().querySelector<HTMLTableRowElement>(`[data-tuition-plan-id="${id}"]`)!;
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
const tabs = () =>
  [
    ...main().querySelectorAll<HTMLAnchorElement>(
      'nav[aria-label="Billing sections"] a',
    ),
  ].map((a) => [a.textContent, a.getAttribute('aria-current')]);
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
  await click(buttonNamed(main(), 'New plan'));
  await settleUntil(() => openDialog()?.querySelector('form'));
}

async function pickStudent() {
  await search(dialog(), 'Student', 'Ana');
  await click(choice(dialog(), 'Ana López'));
}

beforeEach(() => {
  applyLanguage('en');
  listUrls = [];
  catalogUrls = [];
  studentUrls = [];
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

describe('access (A1)', () => {
  it('shows the tab after the pricing tabs and before Unbilled', async () => {
    stub();
    await renderAt('/billing/tuition-plans');

    expect(tabs()).toEqual([
      ['Prices', null],
      ['Discounts', null],
      ['Custom prices', null],
      ['Tuition plans', 'page'],
      ['Unbilled', null],
      ['Settings', null],
    ]);
    expect(h1()).toBe('Tuition plans');
    expect(document.title).toBe('Tuition plans · Classora');
  });

  it('hides the tab and forbids the pages without billing.view', async () => {
    const api = stub({}, { as: MANAGER_ONLY });
    await renderAt('/billing/tuition-plans');

    expect(h1()).toBe("You don't have access to this page");
    expect(api.calls(LIST)).toHaveLength(0);
    app!.unmount();

    await renderAt(`/billing/tuition-plans/${PLAN_ACTIVE.id}`);
    expect(h1()).toBe("You don't have access to this page");
    app!.unmount();

    await renderAt('/billing/prices');
    expect(tabs().map(([name]) => name)).not.toContain('Tuition plans');
  });

  it('is read-only for a billing.view-only holder', async () => {
    stub({}, { as: VIEWER });
    await renderAt('/billing/tuition-plans');
    await settleUntil(() => main().querySelector('tbody'));

    expect(
      [...main().querySelectorAll('button')].map((b) => b.textContent),
    ).not.toContain('New plan');
    expect(tabs().map(([name]) => name)).toEqual([
      'Tuition plans',
      'Unbilled',
      'Settings',
    ]);
  });
});

describe('the list (C1, C2)', () => {
  it('shows each plan with its status badge', async () => {
    stub();
    await renderAt('/billing/tuition-plans');
    await settleUntil(() => main().querySelector('tbody'));

    expect(cells(rowOf(PLAN_ACTIVE.id)).slice(0, 7)).toEqual([
      'Ana López',
      'Familia López',
      'Álgebra · any levelIndividual · Monthly',
      '$1,500.00',
      'Oct 1, 2026',
      'Open-ended',
      'Active',
    ]);
    expect(cells(rowOf(PLAN_PAUSED.id)).slice(0, 7)).toEqual([
      'Bruno Díaz',
      'Familia Díaz',
      'Álgebra · any levelÁlgebra sábados · Biweekly',
      '$700.00',
      'Sep 7, 2026',
      'Dec 27, 2026',
      'Paused',
    ]);
    expect(cells(rowOf(PLAN_SCHEDULED.id)).slice(3, 7)).toEqual([
      'Not offered',
      'Nov 1, 2026',
      'Apr 30, 2027',
      'Scheduled',
    ]);
    const view = rowOf(PLAN_ACTIVE.id).querySelector('a')!;
    expect(view.getAttribute('href')).toBe(
      `/billing/tuition-plans/${PLAN_ACTIVE.id}`,
    );
    expect(view.getAttribute('aria-label')).toBe(
      'View plan: Ana López, Álgebra · any level · Individual · Monthly',
    );
    expect(main().textContent).toContain('1–3 of 3');
  });

  it('keeps the statuses in the URL and sends them', async () => {
    stub();
    await renderAt('/billing/tuition-plans');
    expect(listUrls.at(-1)?.searchParams.has('status')).toBe(false);

    await click(choice(main(), 'Paused'));
    await settleUntil(() => listUrls.length >= 2);
    await click(choice(main(), 'Active'));
    await settleUntil(() => listUrls.length >= 3);

    expect(app!.location()).toBe(
      '/billing/tuition-plans?status=active&status=paused',
    );
    expect(listUrls.at(-1)?.searchParams.getAll('status')).toEqual([
      'active',
      'paused',
    ]);
    expect(listUrls.at(-1)?.searchParams.get('offset')).toBe('0');
  });

  it('filters by a student picked in the search, and reads a deep link', async () => {
    stub();
    await renderAt('/billing/tuition-plans?page=2');
    expect(listUrls.at(-1)?.searchParams.get('offset')).toBe('25');

    await search(main(), 'Search student', 'Ana');
    await click(choice(main(), 'Ana López'));
    await settleUntil(() => listUrls.length >= 2);

    expect(app!.location()).toBe(
      `/billing/tuition-plans?student_id=${STUDENT_ANA.id}`,
    );
    expect(listUrls.at(-1)?.searchParams.get('student_id')).toBe(
      STUDENT_ANA.id,
    );
    expect(studentUrls.at(-1)?.searchParams.get('status')).toBe('active');

    await click(buttonNamed(main(), 'Show all students'));
    await settleUntil(() => listUrls.length >= 3);
    expect(app!.location()).toBe('/billing/tuition-plans');
    app!.unmount();

    await renderAt(
      `/billing/tuition-plans?student_id=${STUDENT_ANA.id}&status=ended&status=bogus`,
    );
    await settleUntil(() => main().textContent?.includes('Ana López'));
    expect(listUrls.at(-1)?.searchParams.getAll('status')).toEqual(['ended']);
    expect(choice(main(), 'Ended').checked).toBe(true);
  });

  it('shows the empty state with the add button', async () => {
    stub({}, { page: EMPTY_PLANS_PAGE });
    await renderAt('/billing/tuition-plans');
    await settleUntil(() => main().textContent?.includes('No tuition plans'));

    expect(main().textContent).toContain('No tuition plans yet.');
    expect(
      [...main().querySelectorAll('button')].filter(
        (b) => b.textContent === 'New plan',
      ),
    ).toHaveLength(2);
  });

  it('shows a failed load with a retry', async () => {
    const api = stub({
      [LIST]: [
        () => problemWith(400, 'bad_request'),
        () => jsonResponse(200, PLANS_PAGE),
      ],
    });
    await renderAt('/billing/tuition-plans');
    await settleUntil(() => alerts(main()).length > 0);

    await click(buttonNamed(main(), 'Try again'));
    await settleUntil(() => main().querySelector('tbody'));
    expect(api.calls(LIST)).toHaveLength(2);
  });
});

describe('the create dialog (D)', () => {
  it('creates an open-ended individual plan and opens it', async () => {
    const api = stub({
      [CREATE]: () => jsonResponse(201, PLAN_ACTIVE_DETAIL),
    });
    await renderAt('/billing/tuition-plans?status=active');
    await openAdd();
    expect(catalogUrls.at(-1)?.searchParams.get('pricing_model')).toBe(
      'per_period',
    );

    // D2: the per-session note for an individual plan.
    expect(dialog().textContent).toContain(
      'Per-session charges continue for this student',
    );
    await pickStudent();
    // Only individual per-period prices.
    expect(options(select(dialog(), 'Per-period price'))).toEqual([
      ['Choose an option', false],
      ['Álgebra · any level · Individual · Monthly — $1,500.00', false],
    ]);
    expect(input(dialog(), 'Starts on').value).toBe('2026-10-20');
    expect(dialog().textContent).toContain('Up to 31 days back.');
    setFieldValue(select(dialog(), 'Per-period price'), PRICE_PER_PERIOD.id);
    setFieldValue(input(dialog(), 'Starts on'), '2026-11-01');
    await click(buttonNamed(dialog(), 'Create plan'));
    await settleUntil(() => main().querySelector('dl'));

    expect(api.calls(CREATE).map((call) => call.body)).toEqual([
      {
        student_id: STUDENT_ANA.id,
        service_price_id: PRICE_PER_PERIOD.id,
        starts_on: '2026-11-01',
        due_offset_days: 0,
      },
    ]);
    expect(app!.router.state.location.pathname).toBe(
      `/billing/tuition-plans/${PLAN_ACTIVE.id}`,
    );
    const back = [...main().querySelectorAll('a')].find(
      (a) => a.textContent === 'Back to tuition plans',
    );
    expect(back?.getAttribute('href')).toBe(
      '/billing/tuition-plans?status=active',
    );
  });

  it('turns "After N periods" into the last day of the Nth period', async () => {
    const api = stub({
      [CREATE]: () => jsonResponse(201, PLAN_ACTIVE_DETAIL),
    });
    await renderAt('/billing/tuition-plans');
    await openAdd();
    await pickStudent();
    setFieldValue(select(dialog(), 'Per-period price'), PRICE_PER_PERIOD.id);
    setFieldValue(input(dialog(), 'Starts on'), '2027-01-31');
    await click(choice(dialog(), 'After a number of periods'));
    setFieldValue(input(dialog(), 'Number of periods'), '2');
    expect(dialog().textContent).toContain(
      "The plan's last day: Mar 30, 2027.",
    );
    setFieldValue(input(dialog(), 'Due days after each period starts'), '5');
    await click(buttonNamed(dialog(), 'Create plan'));

    expect(api.calls(CREATE)[0]?.body).toEqual({
      student_id: STUDENT_ANA.id,
      service_price_id: PRICE_PER_PERIOD.id,
      starts_on: '2027-01-31',
      ends_on: '2027-03-30',
      due_offset_days: 5,
    });
  });

  it('checks the fields before sending', async () => {
    const api = stub();
    await renderAt('/billing/tuition-plans');
    await openAdd();

    await click(choice(dialog(), 'After a number of periods'));
    setFieldValue(input(dialog(), 'Number of periods'), '121');
    setFieldValue(input(dialog(), 'Due days after each period starts'), '61');
    await click(buttonNamed(dialog(), 'Create plan'));

    expect(dialog().textContent).toContain('Choose the student.');
    expect(dialog().textContent).toContain('Choose the price.');
    expect(errorOf(input(dialog(), 'Number of periods'))).toContain(
      'Enter whole periods from 1 to 120.',
    );
    expect(
      errorOf(input(dialog(), 'Due days after each period starts')),
    ).toContain('Enter whole days from 0 to 60.');
    expect(document.activeElement).toBe(input(dialog(), 'Student'));
    expect(api.calls(CREATE)).toHaveLength(0);
  });

  it('offers the student’s enrollments with the disabled reasons and the group’s prices', async () => {
    const api = stub({
      [CREATE]: () => jsonResponse(201, PLAN_ACTIVE_DETAIL),
    });
    await renderAt('/billing/tuition-plans');
    await openAdd();

    await click(choice(dialog(), 'Group enrollment'));
    expect(select(dialog(), 'Enrollment').disabled).toBe(true);
    expect(dialog().textContent).toContain('Choose the student first.');
    await pickStudent();
    await settleUntil(() => !select(dialog(), 'Enrollment').disabled);

    expect(options(select(dialog(), 'Enrollment'))).toEqual([
      ['Choose an option', false],
      ['Álgebra sábados (Álgebra), since Sep 1, 2026', false],
      [
        'Álgebra lunes (Álgebra), since Aug 15, 2026 — flat fee already charged',
        true,
      ],
      [
        'Álgebra martes (Álgebra), since Aug 15, 2026 — already has a plan',
        true,
      ],
    ]);
    expect(api.calls(ELIGIBLE)).toHaveLength(1);
    expect(select(dialog(), 'Per-period price').disabled).toBe(true);
    setFieldValue(
      select(dialog(), 'Enrollment'),
      ENROLLMENT_OPEN.group_enrollment_id,
    );
    expect(options(select(dialog(), 'Per-period price'))).toEqual([
      ['Choose an option', false],
      ['Álgebra · any level · Álgebra sábados · Biweekly — $700.00', false],
    ]);
    setFieldValue(
      select(dialog(), 'Per-period price'),
      PRICE_GROUP_PER_PERIOD.id,
    );
    await click(choice(dialog(), 'After a number of periods'));
    setFieldValue(input(dialog(), 'Starts on'), '2026-10-05');
    setFieldValue(input(dialog(), 'Number of periods'), '3');
    await click(buttonNamed(dialog(), 'Create plan'));

    expect(api.calls(CREATE)[0]?.body).toEqual({
      student_id: STUDENT_ANA.id,
      service_price_id: PRICE_GROUP_PER_PERIOD.id,
      group_enrollment_id: ENROLLMENT_OPEN.group_enrollment_id,
      starts_on: '2026-10-05',
      ends_on: '2026-11-15',
      due_offset_days: 0,
    });
    // The disabled ones are not offered as a group's other choices.
    expect(ENROLLMENT_FEE_CHARGED.group.id).not.toBe(GROUP_OPTIONS[0]!.id);
    expect(ENROLLMENT_WITH_PLAN.has_plan).toBe(true);
  });

  it.each([
    ['service_price_id', 'tuition_plan_price_invalid', 'Per-period price'],
    ['starts_on', 'tuition_plan_starts_on_invalid', 'Starts on'],
    ['ends_on', 'tuition_plan_ends_on_invalid', 'Number of periods'],
    ['due_offset_days', 'less_than_equal', 'Due days after each period starts'],
  ] as const)(
    'puts a 422 on %s on its field (%s)',
    async (key, code, label) => {
      stub({
        [CREATE]: () =>
          problemWith(
            422,
            code === 'less_than_equal' ? 'validation_error' : code,
            {
              errors: [{ path: ['body', key], code }],
            },
          ),
      });
      await renderAt('/billing/tuition-plans');
      await openAdd();
      await pickStudent();
      setFieldValue(select(dialog(), 'Per-period price'), PRICE_PER_PERIOD.id);
      await click(choice(dialog(), 'After a number of periods'));
      await click(buttonNamed(dialog(), 'Create plan'));

      const field = labelled<HTMLInputElement | HTMLSelectElement>(
        dialog(),
        label,
      );
      expect(field.getAttribute('aria-invalid')).toBe('true');
      expect(errorOf(field)).not.toBe('');
      expect(document.activeElement).toBe(field);
    },
  );

  it('puts tuition_plan_enrollment_invalid on the enrollment', async () => {
    stub({
      [CREATE]: () => problemWith(422, 'tuition_plan_enrollment_invalid'),
    });
    await renderAt('/billing/tuition-plans');
    await openAdd();
    await click(choice(dialog(), 'Group enrollment'));
    await pickStudent();
    await settleUntil(() => !select(dialog(), 'Enrollment').disabled);
    setFieldValue(
      select(dialog(), 'Enrollment'),
      ENROLLMENT_OPEN.group_enrollment_id,
    );
    setFieldValue(
      select(dialog(), 'Per-period price'),
      PRICE_GROUP_PER_PERIOD.id,
    );
    await click(buttonNamed(dialog(), 'Create plan'));

    expect(errorOf(select(dialog(), 'Enrollment'))).toContain(
      "Choose one of the student's active group enrollments.",
    );
  });

  it('shows student_has_no_family with a link to the families', async () => {
    stub({ [CREATE]: () => problemWith(409, 'student_has_no_family') });
    await renderAt('/billing/tuition-plans');
    await openAdd();
    await pickStudent();
    setFieldValue(select(dialog(), 'Per-period price'), PRICE_PER_PERIOD.id);
    await click(buttonNamed(dialog(), 'Create plan'));

    expect(alerts(dialog()).join()).toContain("This student isn't in a family");
    const link = [...dialog().querySelectorAll('a')].find(
      (a) => a.textContent === 'Open families',
    );
    expect(link?.getAttribute('href')).toBe('/families');
    expect(select(dialog(), 'Per-period price').value).toBe(
      PRICE_PER_PERIOD.id,
    );
  });

  it('leaves the families link out without guardian.view', async () => {
    stub(
      { [CREATE]: () => problemWith(409, 'student_has_no_family') },
      {
        as: user(
          ['finance_staff'],
          ['student.view', 'billing.view', 'billing.manage'],
        ),
      },
    );
    await renderAt('/billing/tuition-plans');
    await openAdd();
    await pickStudent();
    setFieldValue(select(dialog(), 'Per-period price'), PRICE_PER_PERIOD.id);
    await click(buttonNamed(dialog(), 'Create plan'));

    expect(alerts(dialog()).join()).toContain("This student isn't in a family");
    expect(dialog().querySelector('a')).toBeNull();
  });

  it.each([
    [
      'tuition_plan_overlap',
      'Another plan already covers these dates for this student and price or this enrollment.',
    ],
    [
      'tuition_plan_enrollment_fee_charged',
      "This enrollment's flat fee is already charged, so it can't take a tuition plan.",
    ],
    [
      'tuition_plan_sessions_charged',
      "Sessions of this group inside the plan's dates are already charged per session.",
    ],
  ])('shows a 409 %s as a form alert', async (code, text) => {
    stub({ [CREATE]: () => problemWith(409, code) });
    await renderAt('/billing/tuition-plans');
    await openAdd();
    await pickStudent();
    setFieldValue(select(dialog(), 'Per-period price'), PRICE_PER_PERIOD.id);
    await click(buttonNamed(dialog(), 'Create plan'));

    expect(alerts(dialog()).join()).toContain(text);
    expect(openDialog()).not.toBeNull();
  });
});
