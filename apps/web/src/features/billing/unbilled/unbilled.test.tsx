import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SessionUserBody } from '@/features/auth/session';
import { applyLanguage } from '@/i18n';
import {
  meAs,
  problem,
  stubApi,
  TEST_USER,
  type ApiStub,
  type Responder,
} from '@/test/api-stub';
import { alerts, click, labelled, setFieldValue } from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  settleUntil,
  type RenderedApp,
} from '@/test/render-app';
import type { BillingSettings } from '../settings/api';
import { SETTINGS_OFF, SETTINGS_ON } from '../settings/fixtures';
import type {
  UnbilledEnrollmentPage,
  UnbilledPage,
  UnbilledTuitionPage,
} from './api';
import {
  EMPTY_UNBILLED_ENROLLMENTS_PAGE,
  EMPTY_UNBILLED_PAGE,
  EMPTY_UNBILLED_TUITION_PAGE,
  ENROLLMENT_COHORT_NO_FAMILY,
  ENROLLMENT_COHORT_NO_PRICE,
  ENROLLMENT_GROUP_NO_FAMILY,
  ITEM_GROUP,
  ITEM_NO_FAMILY,
  ITEM_NO_PRICE,
  TUITION_FAMILY_CHANGED,
  TUITION_NO_PRICE,
  UNBILLED_ENROLLMENTS_PAGE,
  UNBILLED_PAGE,
  UNBILLED_TUITION_PAGE,
} from './fixtures';

// #366 E1 (A, C, D): the Unbilled tab behind billing.view: the rows (date
// and time in the center's zone, length, family or "No family", both
// reasons), the reason filter in the URL and the request, the pagination,
// the fix links per permission, both empty states (billing off with the
// Settings link; on with nothing unbilled), the 15-minute note and a
// failed load with retry. #372 D1: the Class column (individual and
// group), the Enrollments section (rows of both kinds and both reasons,
// the shared reason filter on both requests, its own pagination, fix links
// per permission, empty and off states, and an error with retry on one
// section only). #374 H1: the Tuition section (rows, both reasons, the
// shared no_price filter, links per permission, empty, off, its own page
// and an error with retry on that section only).

const LIST = 'GET /api/v1/billing/unbilled-items';
const ENROLLMENTS = 'GET /api/v1/billing/unbilled-enrollments';
const TUITION = 'GET /api/v1/billing/unbilled-tuition-periods';
const SETTINGS = 'GET /api/v1/billing/settings';
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

/** The URLs the list was read with. */
let listUrls: URL[] = [];
/** The URLs the enrollments list was read with. */
let enrollmentUrls: URL[] = [];
/** The URLs the tuition list was read with. */
let tuitionUrls: URL[] = [];

function stub(
  routes: Record<string, Responder | Responder[]> = {},
  {
    settings = SETTINGS_ON,
    page = UNBILLED_PAGE,
    enrollments = UNBILLED_ENROLLMENTS_PAGE,
    tuition = UNBILLED_TUITION_PAGE,
    as = FINANCE,
  }: {
    settings?: BillingSettings;
    page?: UnbilledPage;
    enrollments?: UnbilledEnrollmentPage;
    tuition?: UnbilledTuitionPage;
    as?: SessionUserBody;
  } = {},
): ApiStub {
  return stubApi({
    [ME]: meAs(as),
    [SETTINGS]: () => jsonResponse(200, settings),
    [LIST]: (request) => {
      listUrls.push(new URL(request.url));
      return jsonResponse(200, page);
    },
    [ENROLLMENTS]: (request) => {
      enrollmentUrls.push(new URL(request.url));
      return jsonResponse(200, enrollments);
    },
    [TUITION]: (request) => {
      tuitionUrls.push(new URL(request.url));
      return jsonResponse(200, tuition);
    },
    ...routes,
  });
}

let app: RenderedApp | undefined;

const main = () => app!.container.querySelector('main')!;
const h1 = () => main().querySelector('h1')?.textContent;
const rowOf = (id: string) =>
  main().querySelector<HTMLTableRowElement>(
    `[data-unbilled-session-id="${id}"]`,
  )!;
const cells = (row: Element) =>
  [...row.querySelectorAll('td')].map((cell) => cell.textContent);
const enrollmentRowOf = (id: string) =>
  main().querySelector<HTMLTableRowElement>(
    `[data-unbilled-enrollment-id="${id}"]`,
  )!;
const sessionsSection = () =>
  main().querySelector('[data-unbilled-section="sessions"]')!;
const enrollmentsSection = () =>
  main().querySelector('[data-unbilled-section="enrollments"]');
const tuitionSection = () =>
  main().querySelector('[data-unbilled-section="tuition"]');
const tuitionRowOf = (planId: string) =>
  main().querySelector<HTMLTableRowElement>(
    `[data-unbilled-tuition-plan-id="${planId}"]`,
  )!;
const lastTuitionQuery = () => tuitionUrls.at(-1)!.searchParams;
const linksIn = (row: Element) =>
  [...row.querySelectorAll('a')].map((a) => [
    a.textContent,
    a.getAttribute('href'),
    a.getAttribute('aria-label'),
  ]);
const reasonFilter = () => labelled<HTMLSelectElement>(main(), 'Reason');
const lastQuery = () => listUrls.at(-1)!.searchParams;
const lastEnrollmentQuery = () => enrollmentUrls.at(-1)!.searchParams;

async function renderAt(path = '/billing/unbilled') {
  app = await renderApp(path);
  await settleUntil(() =>
    main().querySelector('table, [data-unbilled-empty], [role="alert"]'),
  );
}

beforeEach(() => {
  applyLanguage('en');
  listUrls = [];
  enrollmentUrls = [];
  tuitionUrls = [];
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

describe('the unbilled list (C1, C5)', () => {
  it('shows each row with its reason, under the tabs', async () => {
    stub();
    await renderAt();

    expect(h1()).toBe('Unbilled sessions and enrollments');
    expect(document.title).toBe('Unbilled sessions and enrollments · Classora');
    const tabs = [
      ...main().querySelectorAll<HTMLAnchorElement>(
        'nav[aria-label="Billing sections"] a',
      ),
    ].map((a) => [a.textContent, a.getAttribute('aria-current')]);
    expect(tabs).toEqual([
      ['Accounts', null],
      ['Prices', null],
      ['Discounts', null],
      ['Custom prices', null],
      ['Tuition plans', null],
      ['Packages', null],
      ['Unbilled', 'page'],
      ['Settings', null],
    ]);
    expect(main().textContent).toContain(
      'New confirmations and enrollments are billed within about 15 minutes.',
    );
    const sessions = sessionsSection();
    expect(sessions.querySelector('h2')?.textContent).toBe('Sessions');
    expect(
      sessions.querySelector('table')?.getAttribute('aria-labelledby'),
    ).toBe(sessions.querySelector('h2')?.id);
    // 16:00Z is 10:00 in Mexico City; 23:30Z is 17:30 the same day there.
    expect(cells(rowOf(ITEM_NO_FAMILY.class_session_id)).slice(0, 7)).toEqual([
      'Oct 5, 2026, 10:00 AM',
      'Ana López',
      'Álgebra',
      'Individual',
      '60 min',
      'No family',
      'Not in a family',
    ]);
    expect(cells(rowOf(ITEM_NO_PRICE.class_session_id)).slice(0, 7)).toEqual([
      'Oct 6, 2026, 5:30 PM',
      'Bruno Díaz',
      'Física',
      'Individual',
      '45 min',
      'Familia Díaz',
      'No price for this class',
    ]);
    // A group session (A1): the Class column names the group.
    expect(cells(rowOf(ITEM_GROUP.class_session_id)).slice(0, 7)).toEqual([
      'Oct 7, 2026, 4:00 PM',
      'Carla Ruiz',
      'Álgebra',
      'Álgebra tarde',
      '90 min',
      'No family',
      'Not in a family',
    ]);
    expect(lastQuery().get('limit')).toBe('25');
    expect(lastQuery().get('offset')).toBe('0');
    expect(lastQuery().has('reason')).toBe(false);
  });

  it('gives fix links to Finance (C3)', async () => {
    stub();
    await renderAt();

    expect(linksIn(rowOf(ITEM_NO_FAMILY.class_session_id))).toEqual([
      ['Open families', '/families', 'Open families to add Ana López to one'],
    ]);
    expect(linksIn(rowOf(ITEM_NO_PRICE.class_session_id))).toEqual([
      ['Open prices', '/billing/prices', 'Open prices to add one for Física'],
    ]);
  });

  it('leaves out the links the user can’t use (C3)', async () => {
    stub({}, { as: VIEWER });
    await renderAt();

    expect(linksIn(rowOf(ITEM_NO_FAMILY.class_session_id))).toEqual([]);
    expect(linksIn(rowOf(ITEM_NO_PRICE.class_session_id))).toEqual([]);
  });

  it('pages through the list (C1)', async () => {
    stub({}, { page: { ...UNBILLED_PAGE, total: 30, limit: 25, offset: 0 } });
    await renderAt();

    await click(buttonNamed(main(), 'Next'));
    await settleUntil(() => lastQuery().get('offset') === '25');

    expect(app!.location()).toBe('/billing/unbilled?page=2');
    expect(lastQuery().get('limit')).toBe('25');
  });
});

describe('the reason filter (C2)', () => {
  it.each(['no_billing_account', 'no_price'] as const)(
    'sends reason=%s',
    async (reason) => {
      stub();
      await renderAt('/billing/unbilled?page=2&enrollment_page=2');
      expect(lastEnrollmentQuery().get('offset')).toBe('25');

      expect(
        [...reasonFilter().options].map((option) => option.textContent),
      ).toEqual(['All', 'Not in a family', 'No price for this class']);
      setFieldValue(reasonFilter(), reason);
      await settleUntil(() => lastQuery().get('reason') === reason);

      expect(lastQuery().getAll('reason')).toEqual([reason]);
      expect(lastQuery().get('offset')).toBe('0');
      // B1: the enrollments list shares the filter and goes back to page 1.
      await settleUntil(() => lastEnrollmentQuery().get('reason') === reason);
      expect(lastEnrollmentQuery().getAll('reason')).toEqual([reason]);
      expect(lastEnrollmentQuery().get('offset')).toBe('0');
      expect(app!.location()).toBe(`/billing/unbilled?reason=${reason}`);
    },
  );

  it('reads the reason from the URL and ignores an unknown one', async () => {
    stub();
    await renderAt('/billing/unbilled?reason=no_price');
    expect(reasonFilter().value).toBe('no_price');
    expect(lastQuery().getAll('reason')).toEqual(['no_price']);
    app!.unmount();

    await renderAt('/billing/unbilled?reason=bogus');
    expect(reasonFilter().value).toBe('');
    expect(lastQuery().has('reason')).toBe(false);
  });

  it('says when no session has the chosen reason', async () => {
    stub({}, { page: EMPTY_UNBILLED_PAGE });
    await renderAt('/billing/unbilled?reason=no_price');

    expect(
      main().querySelector('[data-unbilled-empty="filtered"]')?.textContent,
    ).toBe('No unbilled sessions with this reason.');
    expect(sessionsSection().querySelector('table')).toBeNull();
  });
});

describe('the empty states (C4)', () => {
  it('billing off: says nothing is charged and links to Settings', async () => {
    stub({}, { settings: SETTINGS_OFF, page: EMPTY_UNBILLED_PAGE });
    await renderAt();

    const empty = main().querySelector('[data-unbilled-empty="off"]')!;
    expect(empty.textContent).toContain(
      'Billing is off; nothing is charged yet.',
    );
    const link = empty.querySelector('a');
    expect(link?.textContent).toBe('Open billing settings');
    expect(link?.getAttribute('href')).toBe('/billing/settings');
    expect(main().querySelector('table')).toBeNull();
  });

  it('billing on and nothing unbilled: everything since the date is billed', async () => {
    stub({}, { page: EMPTY_UNBILLED_PAGE });
    await renderAt();

    expect(
      main().querySelector('[data-unbilled-empty="on"]')?.textContent,
    ).toBe('Every confirmed session since Sep 1, 2026 is billed.');
  });
});

describe('errors (C4)', () => {
  it('shows a failed list with Try again, which reloads', async () => {
    const api = stub({
      [LIST]: [
        // A 4xx is never retried by the query client.
        () => problem(400, 'bad_request'),
        (request) => {
          listUrls.push(new URL(request.url));
          return jsonResponse(200, UNBILLED_PAGE);
        },
      ],
    });
    await renderAt();

    expect(alerts(main())).toHaveLength(1);
    await click(buttonNamed(main(), 'Try again'));
    await settleUntil(() => main().querySelector('table'));

    expect(api.calls(LIST)).toHaveLength(2);
    expect(api.calls(SETTINGS)).toHaveLength(1);
  });

  it('shows a failed settings read with Try again', async () => {
    const api = stub({
      [SETTINGS]: [
        // A 4xx is never retried by the query client.
        () => problem(400, 'bad_request'),
        () => jsonResponse(200, SETTINGS_ON),
      ],
    });
    await renderAt();

    expect(alerts(main())).toHaveLength(1);
    // The enrollments section waits for the settings (B3).
    expect(enrollmentsSection()).toBeNull();
    await click(buttonNamed(main(), 'Try again'));
    await settleUntil(() => main().querySelector('table'));

    expect(api.calls(SETTINGS)).toHaveLength(2);
    expect(enrollmentsSection()).not.toBeNull();
  });
});

describe('the enrollments section (B1–B4)', () => {
  it('lists the enrollments of both kinds and both reasons', async () => {
    stub();
    await renderAt();

    const section = enrollmentsSection()!;
    const title = section.querySelector('h2')!;
    expect(title.textContent).toBe('Enrollments');
    expect(section.getAttribute('aria-labelledby')).toBe(title.id);
    expect(
      section.querySelector('table')?.getAttribute('aria-labelledby'),
    ).toBe(title.id);
    expect(
      [...section.querySelectorAll('th')].map((th) => th.textContent),
    ).toEqual([
      'Enrollment date',
      'Student',
      'Group or course',
      'Subject',
      'Family',
      'Reason',
      'Actions',
    ]);
    // The enrollment's local date: 05:00Z on Oct 2 is Oct 1 in Mexico City.
    expect(
      cells(enrollmentRowOf(ENROLLMENT_GROUP_NO_FAMILY.enrollment_id)).slice(
        0,
        6,
      ),
    ).toEqual([
      'Oct 1, 2026',
      'Carla Ruiz',
      'Álgebra intensivoGroup',
      'Álgebra',
      'No family',
      'Not in a family',
    ]);
    expect(
      cells(enrollmentRowOf(ENROLLMENT_COHORT_NO_PRICE.enrollment_id)).slice(
        0,
        6,
      ),
    ).toEqual([
      'Oct 3, 2026',
      'Bruno Díaz',
      'Física I otoñoCourse',
      'Física',
      'Familia Díaz',
      'No course or group fee',
    ]);
    expect(
      cells(enrollmentRowOf(ENROLLMENT_COHORT_NO_FAMILY.enrollment_id)).slice(
        0,
        6,
      ),
    ).toEqual([
      'Oct 3, 2026',
      'Diego Mora',
      'Física I otoñoCourse',
      'Física',
      'No family',
      'Not in a family',
    ]);
    expect(section.textContent).toContain(
      'Group enrollments charged per session appear in the sessions table, not here.',
    );
    expect(lastEnrollmentQuery().get('limit')).toBe('25');
    expect(lastEnrollmentQuery().get('offset')).toBe('0');
    expect(lastEnrollmentQuery().has('reason')).toBe(false);
  });

  it('gives fix links to Finance (B2)', async () => {
    stub();
    await renderAt();

    expect(
      linksIn(enrollmentRowOf(ENROLLMENT_GROUP_NO_FAMILY.enrollment_id)),
    ).toEqual([
      ['Open families', '/families', 'Open families to add Carla Ruiz to one'],
    ]);
    expect(
      linksIn(enrollmentRowOf(ENROLLMENT_COHORT_NO_FAMILY.enrollment_id)),
    ).toEqual([
      ['Open families', '/families', 'Open families to add Diego Mora to one'],
    ]);
    expect(
      linksIn(enrollmentRowOf(ENROLLMENT_COHORT_NO_PRICE.enrollment_id)),
    ).toEqual([
      [
        'Open prices',
        '/billing/prices?class_type=course',
        'Open prices to add a fee for Física I otoño',
      ],
    ]);
  });

  it('leaves out the links the user can’t use (B2)', async () => {
    stub({}, { as: VIEWER });
    await renderAt();

    for (const item of UNBILLED_ENROLLMENTS_PAGE.items) {
      expect(linksIn(enrollmentRowOf(item.enrollment_id))).toEqual([]);
    }
  });

  it('gives each link only to its permission (B2)', async () => {
    stub(
      {},
      {
        as: user(['finance_staff'], ['billing.view', 'guardian.view']),
      },
    );
    await renderAt();

    expect(
      linksIn(enrollmentRowOf(ENROLLMENT_GROUP_NO_FAMILY.enrollment_id)),
    ).toHaveLength(1);
    expect(
      linksIn(enrollmentRowOf(ENROLLMENT_COHORT_NO_PRICE.enrollment_id)),
    ).toEqual([]);
  });

  it('pages on its own, keeping the sessions page (B1)', async () => {
    stub(
      {},
      {
        page: { ...UNBILLED_PAGE, total: 30, limit: 25, offset: 0 },
        enrollments: {
          ...UNBILLED_ENROLLMENTS_PAGE,
          total: 30,
          limit: 25,
          offset: 0,
        },
      },
    );
    await renderAt('/billing/unbilled?page=2');
    const sessionRequests = listUrls.length;

    const section = enrollmentsSection()!;
    expect(section.querySelector('nav')?.getAttribute('aria-label')).toBe(
      'Enrollment pages',
    );
    expect(
      sessionsSection().querySelector('nav')?.getAttribute('aria-label'),
    ).toBe('Session pages');
    await click(buttonNamed(section, 'Next'));
    await settleUntil(() => lastEnrollmentQuery().get('offset') === '25');

    expect(app!.location()).toBe('/billing/unbilled?page=2&enrollment_page=2');
    expect(lastEnrollmentQuery().get('limit')).toBe('25');
    // The sessions page didn't change, so it isn't read again.
    expect(listUrls).toHaveLength(sessionRequests);
    expect(lastQuery().get('offset')).toBe('25');
  });

  it('says every enrollment since the date is charged (B3)', async () => {
    stub({}, { enrollments: EMPTY_UNBILLED_ENROLLMENTS_PAGE });
    await renderAt();

    const section = enrollmentsSection()!;
    expect(section.querySelector('table')).toBeNull();
    expect(
      section.querySelector('[data-unbilled-enrollments-empty="on"]')
        ?.textContent,
    ).toBe('Every enrollment since Sep 1, 2026 is charged.');
  });

  it('says when no enrollment has the chosen reason', async () => {
    stub({}, { enrollments: EMPTY_UNBILLED_ENROLLMENTS_PAGE });
    await renderAt('/billing/unbilled?reason=no_price');

    expect(
      enrollmentsSection()!.querySelector(
        '[data-unbilled-enrollments-empty="filtered"]',
      )?.textContent,
    ).toBe('No unbilled enrollments with this reason.');
  });

  it('is hidden while billing is off (B3)', async () => {
    stub(
      {},
      {
        settings: SETTINGS_OFF,
        page: EMPTY_UNBILLED_PAGE,
        enrollments: EMPTY_UNBILLED_ENROLLMENTS_PAGE,
      },
    );
    await renderAt();

    expect(main().querySelector('[data-unbilled-empty="off"]')).not.toBeNull();
    expect(enrollmentsSection()).toBeNull();
    expect(main().querySelector('h2')).toBeNull();
  });

  it('shows its own failed load with Try again, apart from the sessions (B3)', async () => {
    const api = stub({
      [ENROLLMENTS]: [
        // A 4xx is never retried by the query client.
        () => problem(400, 'bad_request'),
        (request) => {
          enrollmentUrls.push(new URL(request.url));
          return jsonResponse(200, UNBILLED_ENROLLMENTS_PAGE);
        },
      ],
    });
    await renderAt();
    await settleUntil(() =>
      enrollmentsSection()?.querySelector('[role="alert"]'),
    );

    expect(alerts(main())).toHaveLength(1);
    expect(sessionsSection().querySelector('table')).not.toBeNull();
    await click(buttonNamed(enrollmentsSection()!, 'Try again'));
    await settleUntil(() => enrollmentsSection()!.querySelector('table'));

    expect(alerts(main())).toHaveLength(0);
    expect(api.calls(ENROLLMENTS)).toHaveLength(2);
    expect(api.calls(LIST)).toHaveLength(1);
    expect(api.calls(SETTINGS)).toHaveLength(1);
  });

  it('keeps its rows when the sessions fail (B3)', async () => {
    stub({ [LIST]: () => problem(400, 'bad_request') });
    await renderAt();
    await settleUntil(() => enrollmentsSection()?.querySelector('table'));

    expect(alerts(sessionsSection())).toHaveLength(1);
    expect(alerts(enrollmentsSection()!)).toHaveLength(0);
  });

  it('names the section in Spanish', async () => {
    applyLanguage('es');
    stub();
    await renderAt();

    const section = enrollmentsSection()!;
    expect(section.querySelector('h2')?.textContent).toBe('Inscripciones');
    expect(
      cells(enrollmentRowOf(ENROLLMENT_COHORT_NO_PRICE.enrollment_id)).slice(
        0,
        6,
      ),
    ).toEqual([
      '3 oct 2026',
      'Bruno Díaz',
      'Física I otoñoCurso',
      'Física',
      'Familia Díaz',
      'Sin cuota de curso o grupo',
    ]);
  });
});

describe('the tuition section (#374 F1)', () => {
  it('lists the periods with both reasons below the enrollments', async () => {
    stub();
    await renderAt();
    await settleUntil(() => tuitionSection()?.querySelector('table'));

    const sections = [
      ...main().querySelectorAll<HTMLElement>('[data-unbilled-section]'),
    ].map((section) => section.dataset.unbilledSection);
    expect(sections).toEqual(['sessions', 'enrollments', 'tuition']);
    expect(tuitionSection()!.querySelector('h2')?.textContent).toBe('Tuition');
    expect(
      cells(tuitionRowOf(TUITION_NO_PRICE.tuition_plan_id)).slice(0, 5),
    ).toEqual([
      'Oct 1, 2026 – Oct 31, 2026',
      'Ana López',
      'ÁlgebraIndividual · Monthly',
      'Familia López',
      'No price for the period',
    ]);
    expect(
      cells(tuitionRowOf(TUITION_FAMILY_CHANGED.tuition_plan_id)).slice(0, 5),
    ).toEqual([
      'Oct 5, 2026 – Oct 18, 2026',
      'Bruno Díaz',
      'ÁlgebraÁlgebra sábados · Biweekly',
      'No family',
      "Student is no longer in the plan's family",
    ]);
    expect(lastTuitionQuery().has('reason')).toBe(false);
    expect(lastTuitionQuery().get('limit')).toBe('25');
  });

  it('gives the fix links per permission', async () => {
    stub();
    await renderAt();
    await settleUntil(() => tuitionSection()?.querySelector('table'));

    expect(linksIn(tuitionRowOf(TUITION_NO_PRICE.tuition_plan_id))).toEqual([
      [
        'Open prices',
        '/billing/prices?class_type=individual&pricing_model=per_period',
        'Open prices to add a per-period price for Álgebra',
      ],
    ]);
    expect(
      linksIn(tuitionRowOf(TUITION_FAMILY_CHANGED.tuition_plan_id)),
    ).toEqual([
      [
        'Open plan',
        `/billing/tuition-plans/${TUITION_FAMILY_CHANGED.tuition_plan_id}`,
        'Open plan of Bruno Díaz',
      ],
    ]);
    app!.unmount();

    stub({}, { as: VIEWER });
    await renderAt();
    await settleUntil(() => tuitionSection()?.querySelector('table'));
    expect(linksIn(tuitionRowOf(TUITION_NO_PRICE.tuition_plan_id))).toEqual([]);
    expect(
      linksIn(tuitionRowOf(TUITION_FAMILY_CHANGED.tuition_plan_id)),
    ).toHaveLength(1);
  });

  it('shares the no_price reason and lists nothing for a reason it lacks', async () => {
    stub({}, { tuition: EMPTY_UNBILLED_TUITION_PAGE });
    await renderAt('/billing/unbilled?reason=no_price');
    await settleUntil(() => tuitionUrls.length > 0);
    expect(lastTuitionQuery().getAll('reason')).toEqual(['no_price']);
    await settleUntil(() =>
      tuitionSection()?.querySelector('[data-unbilled-tuition-empty]'),
    );
    expect(
      tuitionSection()!.querySelector(
        '[data-unbilled-tuition-empty="filtered"]',
      )?.textContent,
    ).toBe('No unbilled tuition periods with this reason.');
    const requests = tuitionUrls.length;

    setFieldValue(reasonFilter(), 'no_billing_account');
    await settleUntil(() => lastQuery().get('reason') === 'no_billing_account');
    // No tuition reason stands for "Not in a family": nothing is read.
    expect(tuitionUrls).toHaveLength(requests);
    expect(
      tuitionSection()!.querySelector(
        '[data-unbilled-tuition-empty="filtered"]',
      ),
    ).not.toBeNull();
  });

  it('says every period since the date is charged', async () => {
    stub({}, { tuition: EMPTY_UNBILLED_TUITION_PAGE });
    await renderAt();
    await settleUntil(() =>
      tuitionSection()?.querySelector('[data-unbilled-tuition-empty]'),
    );

    expect(
      tuitionSection()!.querySelector('[data-unbilled-tuition-empty="on"]')
        ?.textContent,
    ).toBe('Every tuition period since Sep 1, 2026 is charged.');
  });

  it('is hidden while billing is off', async () => {
    stub(
      {},
      {
        settings: SETTINGS_OFF,
        page: EMPTY_UNBILLED_PAGE,
        enrollments: EMPTY_UNBILLED_ENROLLMENTS_PAGE,
        tuition: EMPTY_UNBILLED_TUITION_PAGE,
      },
    );
    await renderAt();

    expect(main().querySelector('[data-unbilled-empty="off"]')).not.toBeNull();
    expect(tuitionSection()).toBeNull();
  });

  it('pages on its own', async () => {
    stub(
      {},
      {
        tuition: { ...UNBILLED_TUITION_PAGE, total: 30, limit: 25, offset: 0 },
      },
    );
    await renderAt();
    await settleUntil(() => tuitionSection()?.querySelector('table'));
    const enrollmentRequests = enrollmentUrls.length;

    const section = tuitionSection()!;
    expect(section.querySelector('nav')?.getAttribute('aria-label')).toBe(
      'Tuition pages',
    );
    await click(buttonNamed(section, 'Next'));
    await settleUntil(() => lastTuitionQuery().get('offset') === '25');

    expect(app!.location()).toBe('/billing/unbilled?tuition_page=2');
    expect(enrollmentUrls).toHaveLength(enrollmentRequests);
  });

  it('shows its own failed load with Try again', async () => {
    const api = stub({
      [TUITION]: [
        () => problem(400, 'bad_request'),
        (request) => {
          tuitionUrls.push(new URL(request.url));
          return jsonResponse(200, UNBILLED_TUITION_PAGE);
        },
      ],
    });
    await renderAt();
    await settleUntil(() => tuitionSection()?.querySelector('[role="alert"]'));

    expect(alerts(main())).toHaveLength(1);
    expect(enrollmentsSection()!.querySelector('table')).not.toBeNull();
    await click(buttonNamed(tuitionSection()!, 'Try again'));
    await settleUntil(() => tuitionSection()!.querySelector('table'));

    expect(alerts(main())).toHaveLength(0);
    expect(api.calls(TUITION)).toHaveLength(2);
    expect(api.calls(ENROLLMENTS)).toHaveLength(1);
  });
});

describe('Spanish (D1)', () => {
  it('names the columns and the reasons in Spanish', async () => {
    applyLanguage('es');
    stub();
    await renderAt();

    expect(h1()).toBe('Sesiones e inscripciones sin cobrar');
    expect(cells(rowOf(ITEM_NO_FAMILY.class_session_id)).slice(0, 7)).toEqual([
      '5 oct 2026, 10:00',
      'Ana López',
      'Álgebra',
      'Individual',
      '60 min',
      'Sin familia',
      'No pertenece a una familia',
    ]);
  });
});
