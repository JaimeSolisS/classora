import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { NAV_ITEMS } from '@/app/navigation';
import { problemWith } from '@/features/academics/testing';
import { canAccess } from '@/features/auth/access';
import type { SessionUserBody } from '@/features/auth/session';
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
import {
  PAYROLL_SETTINGS_KEY,
  type PayrollSettings,
  type TutorCompensation,
} from './api';
import {
  EMPTY_PAGE,
  HOURLY,
  NOT_SET_UP,
  RATE_CURRENT,
  RATE_GROUP_ZERO,
  RATE_UPCOMING,
  SALARIED,
  SALARY_PLUS,
  SETTINGS_LOCKED,
  SETTINGS_OFF,
  SETTINGS_ON,
  STOPPED,
  TERMS_BIWEEKLY,
  TUTOR_ID,
  TUTORS_PAGE,
} from './fixtures';
import { paySummary } from './rules';

// #425 H1: access per role with no request, the tutor list, the versions and
// their badges, "covered by salary", both add dialogs, delete, no controls
// without payroll.manage, a non-Owner's own pay, the settings page and My
// pay. Amounts on screen are the fixtures' strings, formatted.

const ME = 'GET /api/v1/auth/me';
const LIST = 'GET /api/v1/payroll/tutors';
const SETTINGS = 'GET /api/v1/payroll/settings';
const PATCH = 'PATCH /api/v1/payroll/settings';
const compensationRoute = (id: string) =>
  `GET /api/v1/payroll/tutors/${id}/compensation`;
const TERMS_POST = `POST /api/v1/payroll/tutors/${TUTOR_ID}/pay-terms`;
const RATE_POST = `POST /api/v1/payroll/tutors/${TUTOR_ID}/rates`;
const rateDelete = (id: string) =>
  `DELETE /api/v1/payroll/tutors/${TUTOR_ID}/rates/${id}`;
const COMPENSATION = compensationRoute(TUTOR_ID);

const user = (
  roles: SessionUserBody['roles'],
  permissions: SessionUserBody['permissions'],
): SessionUserBody => ({ ...TEST_USER, roles, permissions });

const FINANCE = user(['finance_staff'], ['payroll.view', 'payroll.manage']);
const VIEWER = user(['finance_staff'], ['payroll.view']);
const OWNER = user(['owner'], ['payroll.view', 'payroll.manage']);
const ADMIN = user(['administrator'], ['payroll.finalize']);
const COORDINATOR = user(['academic_coordinator'], ['schedule.view']);
const GUARDIAN = user(['guardian'], ['guardian.view']);
const STUDENT = user(['student'], ['student.view']);
const TUTOR: SessionUserBody = {
  ...user(['tutor'], ['tutor.view']),
  id: TUTOR_ID,
};
const FINANCE_TUTOR: SessionUserBody = {
  ...user(['finance_staff', 'tutor'], ['payroll.view', 'payroll.manage']),
  id: TUTOR_ID,
};
const OWNER_TUTOR: SessionUserBody = {
  ...user(['owner', 'tutor'], ['payroll.view', 'payroll.manage']),
  id: TUTOR_ID,
};

let listUrls: URL[] = [];

function stub(
  routes: Record<string, Responder | Responder[]> = {},
  {
    as = FINANCE,
    compensation = HOURLY,
    settings = SETTINGS_OFF,
  }: {
    as?: SessionUserBody;
    compensation?: TutorCompensation;
    settings?: PayrollSettings;
  } = {},
): ApiStub {
  return stubApi({
    [ME]: meAs(as),
    [LIST]: (request) => {
      listUrls.push(new URL(request.url));
      return jsonResponse(200, TUTORS_PAGE);
    },
    [COMPENSATION]: () => jsonResponse(200, compensation),
    [SETTINGS]: () => jsonResponse(200, settings),
    ...routes,
  });
}

let app: RenderedApp | undefined;

const main = () => app!.container.querySelector('main')!;
const h1 = () => main().querySelector('h1')?.textContent;
const dialog = () => openDialog()!;
const announced = () =>
  [...main().querySelectorAll('[role="status"]')]
    .map((s) => s.textContent)
    .join('|');
const version = (id: string) =>
  main().querySelector<HTMLElement>(`[data-version-id="${id}"]`);
const kind = (name: string) =>
  main().querySelector<HTMLElement>(`[data-class-kind="${name}"]`)!;
const hasButton = (name: string) =>
  [...main().querySelectorAll('button')].some(
    (button) =>
      button.textContent === name ||
      button.getAttribute('aria-label')?.startsWith(name),
  );

async function open(path: string) {
  app = await renderApp(path);
  await settleUntil(() => main().querySelector('h1'));
}

beforeEach(() => {
  listUrls = [];
  applyLanguage('en');
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.unstubAllGlobals();
});

describe('access (A)', () => {
  it.each([
    ['Finance', FINANCE, true, false],
    ['a view-only holder', VIEWER, true, false],
    ['the Owner', OWNER, true, false],
    ['an Administrator with payroll.finalize', ADMIN, false, false],
    ['a Coordinator', COORDINATOR, false, false],
    ['a guardian', GUARDIAN, false, false],
    ['a student', STUDENT, false, false],
    ['a tutor', TUTOR, false, true],
    ['Finance who also tutors', FINANCE_TUTOR, true, true],
  ] as const)('the nav items for %s', (_who, as, payroll, myPay) => {
    const items = NAV_ITEMS.filter((item) => canAccess(as, item));
    expect(items.some((item) => item.to === '/payroll')).toBe(payroll);
    expect(items.some((item) => item.to === '/my-pay')).toBe(myPay);
  });

  it('puts Payroll next to Billing, labelled in both languages', () => {
    const index = NAV_ITEMS.findIndex((item) => item.to === '/payroll');
    expect(NAV_ITEMS[index - 1]?.to).toBe('/billing');
    expect(NAV_ITEMS[index]?.labelKey).toBe('nav.payroll');
  });

  it.each([
    ['an Administrator with payroll.finalize', ADMIN],
    ['a Coordinator', COORDINATOR],
    ['a guardian', GUARDIAN],
    ['a student', STUDENT],
    ['a tutor', TUTOR],
  ] as const)(
    '%s is forbidden on /payroll/* with no request',
    async (_w, as) => {
      for (const path of [
        '/payroll',
        '/payroll/tutors',
        `/payroll/tutors/${TUTOR_ID}`,
        '/payroll/settings',
      ]) {
        const api = stub({}, { as });
        await open(path);
        expect(h1()).toBe("You don't have access to this page");
        expect(
          api.requests.filter((request) => request.path.includes('/payroll')),
        ).toEqual([]);
        app!.unmount();
        app = undefined;
      }
    },
  );

  it.each([
    ['Finance', FINANCE],
    ['a guardian', GUARDIAN],
  ] as const)('%s is forbidden on /my-pay with no request', async (_w, as) => {
    const api = stub({}, { as });
    await open('/my-pay');
    expect(h1()).toBe("You don't have access to this page");
    expect(
      api.requests.filter((request) => request.path.includes('/payroll')),
    ).toEqual([]);
  });

  it('/payroll opens the tutor list, with the tabs', async () => {
    stub();
    await open('/payroll');
    await settleUntil(() => h1() === 'Tutors');

    expect(app!.router.state.location.pathname).toBe('/payroll/tutors');
    const tabs = [
      ...main().querySelectorAll<HTMLAnchorElement>(
        'nav[aria-label="Payroll sections"] a',
      ),
    ].map((a) => [a.textContent, a.getAttribute('aria-current')]);
    expect(tabs).toEqual([
      ['Tutors', 'page'],
      ['Settings', null],
    ]);
  });
});

describe('tutor list (B)', () => {
  it('shows active tutors by default and never calls GET /tutors', async () => {
    const api = stub();
    await open('/payroll/tutors');
    await settleUntil(() => main().querySelector('tbody tr'));

    expect(listUrls[0]?.searchParams.get('state')).toBe('active');
    expect(api.calls('GET /api/v1/tutors')).toHaveLength(0);
    const rows = [...main().querySelectorAll('tbody tr')].map((row) =>
      [...row.querySelectorAll('td')].map((cell) => cell.textContent),
    );
    expect(rows).toEqual([
      [
        'Ana Tutora',
        'ana.tutora',
        'Active',
        'Biweekly',
        'No salary',
        'Individual, Group',
      ],
      [
        'Beto Salario',
        'beto.salario',
        'Active',
        'Monthly',
        '$18,000.00',
        'No rate',
      ],
      [
        'Carla Nueva',
        'carla.nueva',
        'Pending',
        'Not set up or not on payroll',
        'No salary',
        'No rate',
      ],
    ]);
    expect(main().querySelector('tbody a')?.getAttribute('href')).toBe(
      `/payroll/tutors/${TUTOR_ID}`,
    );
  });

  it('takes the filters from the URL and sends every state as none', async () => {
    stub();
    await open('/payroll/tutors?q=ana&state=all&page=2');
    await settleUntil(() => listUrls.length > 0);

    expect(listUrls[0]?.searchParams.get('q')).toBe('ana');
    expect(listUrls[0]?.searchParams.has('state')).toBe(false);
    expect(listUrls[0]?.searchParams.get('offset')).toBe('25');
    expect(labelled<HTMLSelectElement>(main(), 'State').value).toBe('all');
  });

  it('renders cards for narrow screens, hidden from 768 px', async () => {
    stub();
    await open('/payroll/tutors');
    await settleUntil(() => main().querySelector('[data-payroll-cards]'));

    const cards = main().querySelector('[data-payroll-cards]')!;
    expect(cards.className).toContain('md:hidden');
    expect(cards.querySelectorAll('[data-tutor-card]')).toHaveLength(3);
    expect(cards.textContent).toContain('$18,000.00');
    expect(main().querySelector('[data-payroll-table]')?.className).toContain(
      'hidden md:block',
    );
  });

  it('has empty and error states', async () => {
    stub({ [LIST]: () => jsonResponse(200, EMPTY_PAGE) });
    await open('/payroll/tutors');
    await settleUntil(() =>
      main().textContent.includes('No tutors match these filters.'),
    );
    app!.unmount();

    stub({ [LIST]: () => problem(500, 'internal_error') });
    await open('/payroll/tutors');
    await settleUntil(() => alerts(main()).length > 0);
    expect(buttonNamed(main(), 'Try again')).toBeTruthy();
  });
});

describe('tutor pay page (C)', () => {
  it('shows the versions in order, with status words and exact amounts', async () => {
    stub();
    await open(`/payroll/tutors/${TUTOR_ID}`);
    await settleUntil(() => version(RATE_CURRENT.id));

    expect(h1()).toBe('Ana Tutora');
    expect(main().textContent).toContain('Today:');
    const individual = [...kind('individual').querySelectorAll('li')];
    expect(individual.map((li) => li.dataset.versionStatus)).toEqual([
      'current',
      'upcoming',
      'past',
    ]);
    expect(individual[0]?.textContent).toContain('Per hour · $250.125');
    expect(individual[0]?.textContent).toContain('Current');
    expect(individual[1]?.textContent).toContain('$275.00');
    expect(individual[1]?.textContent).toContain('Upcoming');
    expect(individual[2]?.textContent).toContain('$200.00');
    expect(individual[2]?.textContent).toContain('Past');
    expect(version(TERMS_BIWEEKLY.id)?.textContent).toContain(
      'Biweekly · No salary',
    );
    expect(main().querySelector('[data-pay-summary]')?.textContent).toBe(
      'Per hour',
    );
  });

  it('labels a salary per period and marks kinds covered by it', async () => {
    stub({}, { compensation: SALARIED });
    await open(`/payroll/tutors/${TUTOR_ID}`);
    await settleUntil(() => main().querySelector('[data-class-kind]'));

    expect(main().textContent).toContain(
      'Monthly · $18,000.00 per monthly period',
    );
    expect(kind('group').querySelector('[data-no-rate]')?.textContent).toBe(
      'Covered by salary',
    );
    expect(main().querySelector('[data-pay-summary]')?.textContent).toBe(
      'Fixed salary',
    );
  });

  it('reads "No rate" without terms, not "covered"', async () => {
    stub({}, { compensation: NOT_SET_UP });
    await open(`/payroll/tutors/${TUTOR_ID}`);
    await settleUntil(() => main().querySelector('[data-class-kind]'));

    expect(kind('course').querySelector('[data-no-rate]')?.textContent).toBe(
      'No rate',
    );
    expect(main().textContent).not.toContain('Covered by salary');
    expect(main().querySelector('[data-pay-summary]')).toBeNull();
  });

  it('reads null versions as a stop, from their date', async () => {
    stub({}, { compensation: STOPPED });
    await open(`/payroll/tutors/${TUTOR_ID}`);
    await settleUntil(() => main().querySelector('[data-class-kind]'));

    expect(main().textContent).toContain('Not on payroll since Oct 1, 2026');
    expect(main().textContent).toContain('No rate since Sep 1, 2026');
    expect(main().querySelector('[data-pay-summary]')).toBeNull();
  });

  it('summarizes the model from the current data only', () => {
    expect(paySummary(HOURLY)).toEqual(['per_hour']);
    expect(paySummary(SALARIED)).toEqual(['salary']);
    expect(paySummary(SALARY_PLUS)).toEqual(['salaryPlus']);
    expect(paySummary(STOPPED)).toEqual([]);
    expect(paySummary(NOT_SET_UP)).toEqual([]);
    // A 0.0000 rate is a rate, not "no rate".
    expect(
      paySummary({ terms: [], rates: [RATE_GROUP_ZERO, RATE_CURRENT] }),
    ).toEqual(['per_session', 'per_hour']);
  });

  it('shows a 0.0000 rate as $0.00', async () => {
    stub(
      {},
      {
        compensation: {
          ...NOT_SET_UP,
          rates: [RATE_GROUP_ZERO],
        },
      },
    );
    await open(`/payroll/tutors/${TUTOR_ID}`);
    await settleUntil(() => version(RATE_GROUP_ZERO.id));

    expect(version(RATE_GROUP_ZERO.id)?.textContent).toContain(
      'Per session · $0.00',
    );
  });

  it('adds terms with a string salary and refreshes', async () => {
    const api = stub({
      [TERMS_POST]: () => jsonResponse(201, TERMS_BIWEEKLY),
    });
    await open(`/payroll/tutors/${TUTOR_ID}`);
    await settleUntil(() => hasButton('Add terms'));
    const reads = api.calls(COMPENSATION).length;

    await click(buttonNamed(main(), 'Add terms'));
    setFieldValue(
      labelled<HTMLInputElement>(dialog(), 'Start date'),
      '2026-11-01',
    );
    setFieldValue(
      labelled<HTMLSelectElement>(dialog(), 'Pay frequency'),
      'monthly',
    );
    setFieldValue(
      labelled<HTMLInputElement>(dialog(), 'Salary per period (optional)'),
      ' 8000.50 ',
    );
    await click(buttonNamed(dialog(), 'Save terms'));
    await settleUntil(() => !openDialog());

    expect(api.calls(TERMS_POST)[0]?.body).toEqual({
      effective_from: '2026-11-01',
      pay_frequency: 'monthly',
      salary_amount: '8000.50',
    });
    expect(api.calls(COMPENSATION).length).toBeGreaterThan(reads);
    expect(announced()).toContain('Change saved.');
  });

  it('stop clears and disables the other fields and sends nulls', async () => {
    const api = stub({
      [TERMS_POST]: () => jsonResponse(201, TERMS_BIWEEKLY),
    });
    await open(`/payroll/tutors/${TUTOR_ID}`);
    await settleUntil(() => hasButton('Add terms'));

    await click(buttonNamed(main(), 'Add terms'));
    const salary = labelled<HTMLInputElement>(
      dialog(),
      'Salary per period (optional)',
    );
    setFieldValue(salary, '100');
    await click(
      dialog().querySelector<HTMLElement>('button[role="checkbox"]')!,
    );
    expect(salary.value).toBe('');
    expect(salary.disabled).toBe(true);
    expect(
      labelled<HTMLSelectElement>(dialog(), 'Pay frequency').disabled,
    ).toBe(true);
    await click(buttonNamed(dialog(), 'Save terms'));
    await settleUntil(() => !openDialog());

    expect(api.calls(TERMS_POST)[0]?.body).toMatchObject({
      pay_frequency: null,
      salary_amount: null,
    });
  });

  it('validates the terms form before any request', async () => {
    const api = stub();
    await open(`/payroll/tutors/${TUTOR_ID}`);
    await settleUntil(() => hasButton('Add terms'));

    await click(buttonNamed(main(), 'Add terms'));
    setFieldValue(
      labelled<HTMLInputElement>(dialog(), 'Salary per period (optional)'),
      '0',
    );
    await click(buttonNamed(dialog(), 'Save terms'));

    expect(api.calls(TERMS_POST)).toHaveLength(0);
    expect(dialog().textContent).toContain('Choose the pay frequency.');
    expect(dialog().textContent).toContain('A salary must be above zero.');
  });

  it('keeps the dialog open on 409 pay_terms_exists', async () => {
    stub({ [TERMS_POST]: () => problem(409, 'pay_terms_exists') });
    await open(`/payroll/tutors/${TUTOR_ID}`);
    await settleUntil(() => hasButton('Add terms'));

    await click(buttonNamed(main(), 'Add terms'));
    setFieldValue(
      labelled<HTMLSelectElement>(dialog(), 'Pay frequency'),
      'weekly',
    );
    await click(buttonNamed(dialog(), 'Save terms'));
    await settleUntil(() => alerts(dialog()).length > 0);

    expect(alerts(dialog()).join()).toContain(
      'This tutor already has pay terms from this date.',
    );
    expect(labelled<HTMLSelectElement>(dialog(), 'Pay frequency').value).toBe(
      'weekly',
    );
  });

  it.each([
    [
      'effective_from_inside_generated_period',
      'A frequency change must be dated after the last generated period.',
    ],
    [
      'effective_from_before_first_period',
      "can't start before the tutor's first payroll period",
    ],
  ])('puts the top-level 422 %s on the date', async (code, text) => {
    stub({ [TERMS_POST]: () => problem(422, code) });
    await open(`/payroll/tutors/${TUTOR_ID}`);
    await settleUntil(() => hasButton('Add terms'));

    await click(buttonNamed(main(), 'Add terms'));
    setFieldValue(
      labelled<HTMLSelectElement>(dialog(), 'Pay frequency'),
      'weekly',
    );
    await click(buttonNamed(dialog(), 'Save terms'));
    await settleUntil(() => dialog().textContent.includes(text));

    const date = labelled<HTMLInputElement>(dialog(), 'Start date');
    expect(date.getAttribute('aria-invalid')).toBe('true');
    expect(document.activeElement).toBe(date);
  });

  it('adds a rate: per_course only for Course, the rate a string', async () => {
    const api = stub({
      [RATE_POST]: () => jsonResponse(201, RATE_CURRENT),
    });
    await open(`/payroll/tutors/${TUTOR_ID}`);
    await settleUntil(() => hasButton('Add rate'));

    await click(buttonNamed(main(), 'Add rate'));
    const modelOptions = () =>
      [...labelled<HTMLSelectElement>(dialog(), 'Model').options].map(
        (option) => option.value,
      );
    setFieldValue(
      labelled<HTMLSelectElement>(dialog(), 'Class type'),
      'course',
    );
    expect(modelOptions()).toContain('per_course');
    setFieldValue(labelled<HTMLSelectElement>(dialog(), 'Model'), 'per_course');

    setFieldValue(labelled<HTMLSelectElement>(dialog(), 'Class type'), 'group');
    expect(modelOptions()).toEqual(['', 'per_hour', 'per_session']);
    expect(labelled<HTMLSelectElement>(dialog(), 'Model').value).toBe('');

    setFieldValue(labelled<HTMLSelectElement>(dialog(), 'Model'), 'per_hour');
    setFieldValue(labelled<HTMLInputElement>(dialog(), 'Rate'), '250.12345');
    await click(buttonNamed(dialog(), 'Save rate'));
    expect(api.calls(RATE_POST)).toHaveLength(0);
    expect(dialog().textContent).toContain('up to four decimals');

    setFieldValue(labelled<HTMLInputElement>(dialog(), 'Rate'), '250.1250');
    await click(buttonNamed(dialog(), 'Save rate'));
    await settleUntil(() => !openDialog());

    expect(api.calls(RATE_POST)[0]?.body).toMatchObject({
      class_kind: 'group',
      rate_model: 'per_hour',
      rate: '250.1250',
    });
  });

  it('"no rate" sends nulls; a field 422 lands on its field', async () => {
    const api = stub({
      [RATE_POST]: [
        () =>
          problemWith(422, 'validation_error', {
            errors: [{ path: ['body', 'rate'], code: 'rate_out_of_range' }],
          }),
        () => jsonResponse(201, RATE_CURRENT),
      ],
    });
    await open(`/payroll/tutors/${TUTOR_ID}`);
    await settleUntil(() => hasButton('Add rate'));

    await click(buttonNamed(main(), 'Add rate'));
    setFieldValue(
      labelled<HTMLSelectElement>(dialog(), 'Class type'),
      'individual',
    );
    setFieldValue(labelled<HTMLSelectElement>(dialog(), 'Model'), 'per_hour');
    setFieldValue(labelled<HTMLInputElement>(dialog(), 'Rate'), '9999999999');
    await click(buttonNamed(dialog(), 'Save rate'));
    await settleUntil(() =>
      dialog().textContent.includes('The rate is too large.'),
    );

    await click(
      dialog().querySelector<HTMLElement>('button[role="checkbox"]')!,
    );
    await click(buttonNamed(dialog(), 'Save rate'));
    await settleUntil(() => !openDialog());

    expect(api.calls(RATE_POST)[1]?.body).toMatchObject({
      class_kind: 'individual',
      rate_model: null,
      rate: null,
    });
  });

  it('deletes a version after a confirmation naming it', async () => {
    const api = stub({ [rateDelete(RATE_UPCOMING.id)]: noContent });
    await open(`/payroll/tutors/${TUTOR_ID}`);
    await settleUntil(() => version(RATE_UPCOMING.id));

    await click(version(RATE_UPCOMING.id)!.querySelector('button')!);
    expect(dialog().textContent).toContain('Dec 1, 2026');
    expect(dialog().textContent).toContain('$275.00');
    await click(buttonNamed(dialog(), 'Delete'));
    await settleUntil(() => !openDialog());

    expect(api.calls(rateDelete(RATE_UPCOMING.id))).toHaveLength(1);
    expect(announced()).toContain('Version deleted.');
  });

  it('keeps a paid version: 409 compensation_rate_in_use', async () => {
    stub({
      [rateDelete(RATE_CURRENT.id)]: () =>
        problem(409, 'compensation_rate_in_use'),
    });
    await open(`/payroll/tutors/${TUTOR_ID}`);
    await settleUntil(() => version(RATE_CURRENT.id));

    await click(version(RATE_CURRENT.id)!.querySelector('button')!);
    await click(buttonNamed(dialog(), 'Delete'));
    await settleUntil(() => alerts(dialog()).length > 0);

    expect(alerts(dialog()).join()).toContain(
      'This rate has been paid in a payroll period and must stay.',
    );
    expect(version(RATE_CURRENT.id)).not.toBeNull();
  });

  it('refreshes when the version is already gone: 404', async () => {
    const api = stub({
      [rateDelete(RATE_CURRENT.id)]: () => problem(404, 'not_found'),
    });
    await open(`/payroll/tutors/${TUTOR_ID}`);
    await settleUntil(() => version(RATE_CURRENT.id));
    const reads = api.calls(COMPENSATION).length;

    await click(version(RATE_CURRENT.id)!.querySelector('button')!);
    await click(buttonNamed(dialog(), 'Delete'));
    await settleUntil(() => !openDialog());

    expect(api.calls(COMPENSATION).length).toBeGreaterThan(reads);
    expect(announced()).toContain('That version no longer exists.');
  });

  it('has no add or delete controls without payroll.manage', async () => {
    stub({}, { as: VIEWER });
    await open(`/payroll/tutors/${TUTOR_ID}`);
    await settleUntil(() => version(RATE_CURRENT.id));

    expect(hasButton('Add terms')).toBe(false);
    expect(hasButton('Add rate')).toBe(false);
    expect(hasButton('Delete')).toBe(false);
    expect(main().querySelector('[data-pay-note]')).toBeNull();
  });

  it('shows a non-Owner their own pay read-only, with the note', async () => {
    stub({}, { as: FINANCE_TUTOR });
    await open(`/payroll/tutors/${TUTOR_ID}`);
    await settleUntil(() => version(RATE_CURRENT.id));

    expect(main().querySelector('[data-pay-note]')?.textContent).toBe(
      'Someone else must change your pay.',
    );
    expect(hasButton('Add rate')).toBe(false);
    expect(hasButton('Delete')).toBe(false);
  });

  it('lets an Owner change their own pay', async () => {
    stub({}, { as: OWNER_TUTOR });
    await open(`/payroll/tutors/${TUTOR_ID}`);
    await settleUntil(() => version(RATE_CURRENT.id));

    expect(hasButton('Add rate')).toBe(true);
    expect(main().querySelector('[data-pay-note]')).toBeNull();
  });

  it('shows the same message for 403 payroll_own_compensation', async () => {
    stub({ [TERMS_POST]: () => problem(403, 'payroll_own_compensation') });
    await open(`/payroll/tutors/${TUTOR_ID}`);
    await settleUntil(() => hasButton('Add terms'));

    await click(buttonNamed(main(), 'Add terms'));
    setFieldValue(
      labelled<HTMLSelectElement>(dialog(), 'Pay frequency'),
      'weekly',
    );
    await click(buttonNamed(dialog(), 'Save terms'));
    await settleUntil(() => alerts(dialog()).length > 0);

    expect(alerts(dialog()).join()).toContain(
      'Someone else must change your pay.',
    );
  });

  it('is the not-found view on 404', async () => {
    stub({ [COMPENSATION]: () => problem(404, 'not_found') });
    await open(`/payroll/tutors/${TUTOR_ID}`);
    await settleUntil(() => h1() === 'Page not found');
  });
});

describe('settings (D)', () => {
  const change = () => buttonNamed(main(), 'Change');
  const anchor = () =>
    labelled<HTMLInputElement>(dialog(), 'Biweekly anchor date');
  const start = () =>
    labelled<HTMLInputElement>(dialog(), 'Payroll start date');

  it('shows payroll off, the rule, and a warning before a first date', async () => {
    const api = stub({
      [PATCH]: () => jsonResponse(200, SETTINGS_ON),
    });
    await open('/payroll/settings');
    await settleUntil(() => main().querySelector('[data-payroll-status]'));

    expect(main().querySelector('[data-payroll-status]')?.textContent).toBe(
      'Payroll is off',
    );
    expect(main().textContent).toContain(
      'Work before this date is never paid.',
    );

    await click(change());
    expect(dialog().querySelector('[data-payroll-warning]')).toBeNull();
    setFieldValue(start(), '2026-11-02');
    const warning = dialog().querySelector('[data-payroll-warning]');
    expect(warning?.textContent).toContain(
      "can't be changed after the next nightly run",
    );
    expect(warning?.textContent).toContain('not immediately');

    await click(buttonNamed(dialog(), 'Save settings'));
    await settleUntil(() => !openDialog());

    expect(api.calls(PATCH)[0]?.body).toEqual({
      version: 1,
      payroll_starts_on: '2026-11-02',
      biweekly_anchor_on: '2026-01-05',
      salary_proration_basis: 'calendar_days',
    });
    expect(announced()).toContain('Settings saved.');
    expect(
      main().querySelector('[data-payroll-status]')?.textContent,
    ).toContain('Payroll starts on');
  });

  it('refuses an anchor that is not a Monday before any request', async () => {
    const api = stub();
    await open('/payroll/settings');
    await settleUntil(() => main().querySelector('[data-payroll-status]'));

    await click(change());
    setFieldValue(anchor(), '2026-01-06');
    await click(buttonNamed(dialog(), 'Save settings'));

    expect(api.calls(PATCH)).toHaveLength(0);
    expect(dialog().textContent).toContain(
      'The biweekly anchor date must be a Monday.',
    );
  });

  it('maps the API anchor refusal to the field', async () => {
    stub({
      [PATCH]: () =>
        problemWith(422, 'validation_error', {
          errors: [
            { path: ['body', 'biweekly_anchor_on'], code: 'anchor_not_monday' },
          ],
        }),
    });
    await open('/payroll/settings');
    await settleUntil(() => main().querySelector('[data-payroll-status]'));

    await click(change());
    await click(buttonNamed(dialog(), 'Save settings'));
    await settleUntil(() => anchor().getAttribute('aria-invalid') === 'true');
  });

  it.each([
    [
      'en',
      'Salary proration basis',
      'Save settings',
      'Someone else changed these settings.',
    ],
    [
      'es',
      'Base de prorrateo del salario',
      'Guardar configuración',
      'Alguien más cambió esta configuración.',
    ],
  ] as const)(
    '%s: a version_conflict shows the current values and needs a new save',
    async (language, basisLabel, save, notice) => {
      applyLanguage(language);
      const theirs = {
        ...SETTINGS_OFF,
        salary_proration_basis: 'weekdays',
        version: 9,
      };
      const api = stub({
        [SETTINGS]: [
          () => jsonResponse(200, SETTINGS_OFF),
          () => jsonResponse(200, theirs),
        ],
        [PATCH]: [
          () => problem(409, 'version_conflict'),
          () => jsonResponse(200, { ...theirs, version: 10 }),
        ],
      });
      app = await renderApp('/payroll/settings');
      await settleUntil(() => main().querySelector('[data-payroll-status]'));

      await click(
        [...main().querySelectorAll('button')].find(
          (button) =>
            button.textContent === 'Change' || button.textContent === 'Cambiar',
        )!,
      );
      const inputs = () =>
        dialog().querySelectorAll<HTMLInputElement>('input[type="date"]');
      setFieldValue(inputs()[0]!, '2026-09-01');
      await click(buttonNamed(dialog(), save));
      await settleUntil(() =>
        dialog().querySelector('[data-payroll-reloaded]'),
      );

      // The server's values, the notice, and nothing sent on the user's behalf.
      expect(
        dialog().querySelector('[data-payroll-reloaded]')?.textContent,
      ).toContain(notice);
      expect(
        dialog().querySelector('[data-payroll-reloaded]')?.getAttribute('role'),
      ).toBe('alert');
      expect(labelled<HTMLSelectElement>(dialog(), basisLabel).value).toBe(
        'weekdays',
      );
      expect(inputs()[0]!.value).toBe('');
      expect(api.calls(PATCH)).toHaveLength(1);

      await click(buttonNamed(dialog(), save));
      await settleUntil(() => !openDialog());

      // The colleague's change survives.
      expect(api.calls(PATCH)[1]?.body).toEqual({
        version: 9,
        payroll_starts_on: null,
        biweekly_anchor_on: '2026-01-05',
        salary_proration_basis: 'weekdays',
      });
    },
  );

  // QA #425 D3: the refetch lands while the PATCH is pending, then the PATCH
  // fails. Ordered by hand: nothing here depends on timing.
  describe.each([
    [
      'en',
      'Salary proration basis',
      'Save settings',
      'Someone else changed these',
    ],
    [
      'es',
      'Base de prorrateo del salario',
      'Guardar configuración',
      'Alguien más cambió',
    ],
  ] as const)(
    '%s: a refetch during the save',
    (language, basisLabel, save, notice) => {
      it.each([
        ['409 version_conflict', 409, 'version_conflict'],
        ['500', 500, 'internal_error'],
      ] as const)(
        'then %s shows the server values',
        async (_name, status, code) => {
          applyLanguage(language);
          const theirs = {
            ...SETTINGS_OFF,
            salary_proration_basis: 'weekdays',
            version: 9,
          };
          let answer: (response: Response) => void = () => {};
          const api = stub({
            [SETTINGS]: [
              () => jsonResponse(200, SETTINGS_OFF),
              () => jsonResponse(200, theirs),
            ],
            [PATCH]: [
              () =>
                new Promise<Response>((resolve) => {
                  answer = resolve;
                }),
              () => jsonResponse(200, { ...theirs, version: 10 }),
            ],
          });
          app = await renderApp('/payroll/settings');
          await settleUntil(() =>
            main().querySelector('[data-payroll-status]'),
          );
          await click(
            [...main().querySelectorAll('button')].find(
              (button) =>
                button.textContent === 'Change' ||
                button.textContent === 'Cambiar',
            )!,
          );
          const start = () =>
            dialog().querySelector<HTMLInputElement>('input[type="date"]')!;
          setFieldValue(start(), '2026-10-05');
          await click(buttonNamed(dialog(), save));
          await settleUntil(() => api.calls(PATCH).length === 1);

          await act(() =>
            app!.queryClient.refetchQueries({ queryKey: PAYROLL_SETTINGS_KEY }),
          );
          await settleUntil(() => api.calls(SETTINGS).length === 2);
          // Mid-save nothing moves: the snapshot still matches the request.
          expect(labelled<HTMLSelectElement>(dialog(), basisLabel).value).toBe(
            'calendar_days',
          );

          answer(problem(status, code));
          await settleUntil(() =>
            dialog().querySelector('[data-payroll-reloaded]'),
          );

          expect(
            dialog().querySelector('[data-payroll-reloaded]')?.textContent,
          ).toContain(notice);
          expect(labelled<HTMLSelectElement>(dialog(), basisLabel).value).toBe(
            'weekdays',
          );
          expect(start().value).toBe('');
          expect(api.calls(PATCH)).toHaveLength(1);
          expect(api.calls(PATCH)[0]?.body).toMatchObject({ version: 1 });

          await click(buttonNamed(dialog(), save));
          await settleUntil(() => !openDialog());
          expect(api.calls(PATCH)[1]?.body).toEqual({
            version: 9,
            payroll_starts_on: null,
            biweekly_anchor_on: '2026-01-05',
            salary_proration_basis: 'weekdays',
          });
        },
      );
    },
  );

  it('keeps the conflict with Reload when the reload fails', async () => {
    const api = stub({
      [SETTINGS]: [
        () => jsonResponse(200, SETTINGS_OFF),
        () => problem(500, 'internal_error'),
        () => jsonResponse(200, { ...SETTINGS_OFF, version: 9 }),
      ],
      [PATCH]: () => problem(409, 'version_conflict'),
    });
    await open('/payroll/settings');
    await settleUntil(() => main().querySelector('[data-payroll-status]'));

    await click(change());
    await click(buttonNamed(dialog(), 'Save settings'));
    await settleUntil(() => alerts(dialog()).length > 0);
    expect(dialog().querySelector('[data-payroll-reloaded]')).toBeNull();
    expect(alerts(dialog()).join()).toContain('Someone else changed this');

    await click(buttonNamed(dialog(), 'Reload'));
    await settleUntil(() => dialog().querySelector('[data-payroll-reloaded]'));
    expect(api.calls(PATCH)).toHaveLength(1);
  });

  it('shows locked fields read-only and still sends their values', async () => {
    const api = stub(
      { [PATCH]: () => jsonResponse(200, SETTINGS_LOCKED) },
      { settings: SETTINGS_LOCKED },
    );
    await open('/payroll/settings');
    await settleUntil(() => main().querySelector('[data-payroll-status]'));

    expect(
      main().querySelector('[data-payroll-locked="start"]'),
    ).not.toBeNull();
    expect(
      main().querySelector('[data-payroll-locked="anchor"]'),
    ).not.toBeNull();

    await click(change());
    expect(start().readOnly).toBe(true);
    expect(anchor().readOnly).toBe(true);
    expect(dialog().querySelector('[data-payroll-warning]')).toBeNull();
    expect(dialog().textContent).toContain(
      "Payroll periods already exist, so this date can't change.",
    );
    setFieldValue(
      labelled<HTMLSelectElement>(dialog(), 'Salary proration basis'),
      'calendar_days',
    );
    await click(buttonNamed(dialog(), 'Save settings'));
    await settleUntil(() => !openDialog());

    expect(api.calls(PATCH)[0]?.body).toEqual({
      version: 3,
      payroll_starts_on: '2026-09-01',
      biweekly_anchor_on: '2026-01-05',
      salary_proration_basis: 'calendar_days',
    });
  });

  it.each(['payroll_start_locked', 'biweekly_anchor_locked'])(
    'reloads on 409 %s',
    async (code) => {
      const api = stub({ [PATCH]: () => problem(409, code) });
      await open('/payroll/settings');
      await settleUntil(() => main().querySelector('[data-payroll-status]'));

      await click(change());
      await click(buttonNamed(dialog(), 'Save settings'));
      await settleUntil(() => alerts(dialog()).length > 0);

      expect(alerts(dialog()).join()).toContain('The settings were reloaded.');
      expect(api.calls(SETTINGS).length).toBeGreaterThanOrEqual(2);
    },
  );

  it('is read-only without payroll.manage', async () => {
    stub({}, { as: VIEWER, settings: SETTINGS_ON });
    await open('/payroll/settings');
    await settleUntil(() => main().querySelector('[data-payroll-status]'));

    expect(hasButton('Change')).toBe(false);
  });
});

describe('my pay (F)', () => {
  const OWN = compensationRoute(TUTOR_ID);

  it('reads the signed-in tutor with no controls, exact strings', async () => {
    const api = stub({}, { as: TUTOR, compensation: SALARY_PLUS });
    await open('/my-pay');
    await settleUntil(() => version(RATE_CURRENT.id));

    expect(h1()).toBe('My pay');
    expect(api.calls(OWN)).toHaveLength(1);
    expect(main().textContent).toContain('$18,000.00 per monthly period');
    expect(main().textContent).toContain('Per hour · $250.125');
    expect(main().querySelector('[data-pay-summary]')?.textContent).toBe(
      'Salary plus extras',
    );
    expect(main().querySelectorAll('button')).toHaveLength(0);
    expect(main().querySelector('a[href^="/payroll"]')).toBeNull();
    expect(api.calls(LIST)).toHaveLength(0);
    expect(api.calls(SETTINGS)).toHaveLength(0);
  });

  it('shows upcoming versions', async () => {
    stub({}, { as: TUTOR });
    await open('/my-pay');
    await settleUntil(() => version(RATE_UPCOMING.id));

    expect(version(RATE_UPCOMING.id)?.dataset.versionStatus).toBe('upcoming');
  });

  it('says when pay is not set up yet', async () => {
    stub({}, { as: TUTOR, compensation: NOT_SET_UP });
    await open('/my-pay');
    await settleUntil(() => main().querySelector('[data-pay-empty]'));

    expect(main().querySelector('[data-pay-empty]')?.textContent).toBe(
      "Your pay isn't set up yet. Check with administration.",
    );
    expect(main().querySelector('[data-class-kind]')).toBeNull();
  });

  it('is the forbidden page on 404 (the role was lost)', async () => {
    stub({ [OWN]: () => problem(404, 'not_found') }, { as: TUTOR });
    await open('/my-pay');
    await settleUntil(() => h1() === "You don't have access to this page");
  });
});
