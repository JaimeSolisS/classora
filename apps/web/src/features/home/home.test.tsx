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
import { alerts, click } from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  settleUntil,
  type RenderedApp,
} from '@/test/render-app';
import {
  ADMIN,
  assignmentsPage,
  at,
  BUSY_TODAY,
  calendarPage,
  COORDINATOR,
  COORDINATOR_TUTOR,
  FAMILY_COURSE,
  FAMILY_ID,
  FAMILY_TODAY,
  FINANCE,
  GUARDIAN,
  homeworkSummary,
  KID_A,
  KID_B,
  NO_ROLE,
  NOW,
  offsetPage,
  overdueAccountsPage,
  OWN_FAMILY,
  OWNER,
  scheduleOf,
  SELF_STUDENT,
  staffItem,
  STUDENT,
  SUMMARY,
  TUTOR,
  TUTOR_TODAY,
} from './fixtures';

// HF-06 F1: Home per role. Which sections render, exactly which requests
// each role makes (and that nothing else is asked: C2), the counts and
// their links, the empty states (B4, C3), the fallback (D3), an error in
// one section (B5), a silent 403 (C2), "n+" on truncation, and the center
// time zone "today" boundary.

const SHELL = new Set([
  '/api/v1/auth/me',
  '/api/v1/auth/me/preferences',
  '/api/v1/notifications/unread-count',
]);
const LAYOUT = 'GET /api/v1/reports/dashboard/layout';

const P = {
  sessions: '/api/v1/class-sessions',
  approvals: '/api/v1/approval-requests',
  closeouts: '/api/v1/closeouts/overdue',
  accounts: '/api/v1/billing/overdue-accounts',
  tutorSchedule: `/api/v1/tutors/${TEST_USER.id}/schedule`,
  offers: '/api/v1/assignment-offers',
  assignments: '/api/v1/assignments',
  familyStudents: '/api/v1/schedule/family-students',
  schedule: (id: string) => `/api/v1/students/${id}/schedule`,
  homework: (id: string) => `/api/v1/students/${id}/assignments`,
  ownFamily: '/api/v1/families/own',
  billing: `/api/v1/families/${FAMILY_ID}/billing-account`,
};

const ok =
  (body: unknown): Responder =>
  () =>
    jsonResponse(200, body);

/** Every source answered with data; tests override what they check. */
function defaults(): Record<string, Responder> {
  return {
    [`GET ${P.sessions}`]: ok(calendarPage(BUSY_TODAY)),
    [`GET ${P.approvals}`]: ok(offsetPage(3)),
    [`GET ${P.closeouts}`]: ok(offsetPage(2)),
    [`GET ${P.accounts}`]: ok(overdueAccountsPage(1)),
    [`GET ${P.tutorSchedule}`]: ok(scheduleOf(TUTOR_TODAY)),
    [`GET ${P.offers}`]: ok(offsetPage(2)),
    [`GET ${P.assignments}`]: ok(assignmentsPage([2, 1])),
    [`GET ${P.familyStudents}`]: ok({ items: [KID_A, KID_B] }),
    [`GET ${P.schedule(KID_A.student_id)}`]: ok(scheduleOf(FAMILY_TODAY)),
    [`GET ${P.schedule(SELF_STUDENT.student_id)}`]: ok(scheduleOf([])),
    [`GET ${P.homework(KID_A.student_id)}`]: ok(homeworkSummary(2, 1)),
    [`GET ${P.homework(SELF_STUDENT.student_id)}`]: ok(homeworkSummary(0, 0)),
    [`GET ${P.ownFamily}`]: ok(OWN_FAMILY),
    [`GET ${P.billing}`]: ok(SUMMARY),
    [LAYOUT]: ok({ widgets: [], customized: false }),
  };
}

let app: RenderedApp | undefined;
const main = () => app!.container.querySelector('main')!;
const section = (code: string) =>
  main().querySelector(`[data-home-section="${code}"]`);
const sectionCodes = () =>
  [...main().querySelectorAll('[data-home-section]')].map((node) =>
    node.getAttribute('data-home-section'),
  );
const pendingLinks = () =>
  [...main().querySelectorAll('[data-home-pending] a')].map((node) => [
    node.textContent,
    node.getAttribute('href'),
  ]);

function stub(
  as: SessionUserBody,
  routes: Record<string, Responder | Responder[]> = {},
): ApiStub {
  return stubApi({
    'GET /api/v1/auth/me': meAs(as),
    ...defaults(),
    ...routes,
  });
}

/** Home's own GET paths (no shell, no dashboard reports), sorted. */
function homeRequests(api: ApiStub): string[] {
  return api.requests
    .filter(
      (r) =>
        r.method === 'GET' &&
        !SHELL.has(r.path) &&
        !r.path.startsWith('/api/v1/reports'),
    )
    .map((r) => r.path)
    .sort();
}

/** The query of the first request to `path`. */
function queryOf(api: ApiStub, path: string): URLSearchParams {
  const request = api.fetch.mock.calls
    .map(([r]) => r)
    .find((r) => new URL(r.url).pathname === path)!;
  return new URL(request.url).searchParams;
}

async function open() {
  app = await renderApp('/');
  await settleUntil(
    () =>
      main().querySelector('h1') &&
      !main().querySelector('[data-home-sections] [aria-busy="true"]'),
  );
}

beforeEach(() => {
  applyLanguage('en');
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(NOW);
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('the requests and sections per role (F1, C2, E6)', () => {
  it.each([
    [
      'the Owner',
      OWNER,
      ['staff', 'pending'],
      [P.accounts, P.approvals, P.sessions, P.closeouts],
    ],
    [
      'an Administrator',
      ADMIN,
      ['staff', 'pending'],
      [P.approvals, P.sessions],
    ],
    [
      'a Coordinator',
      COORDINATOR,
      ['staff', 'pending'],
      [P.approvals, P.sessions, P.closeouts],
    ],
    ['Finance Staff', FINANCE, ['staff', 'pending'], [P.accounts, P.sessions]],
    [
      'a tutor',
      TUTOR,
      ['tutor', 'pending'],
      [P.offers, P.assignments, P.tutorSchedule],
    ],
    [
      'a student',
      STUDENT,
      ['family', 'pending'],
      [
        P.familyStudents,
        P.homework(SELF_STUDENT.student_id),
        P.schedule(SELF_STUDENT.student_id),
      ],
    ],
    [
      'a guardian with a consent_required child',
      GUARDIAN,
      ['family', 'pending'],
      [
        P.ownFamily,
        P.billing,
        P.familyStudents,
        P.homework(KID_A.student_id),
        P.schedule(KID_A.student_id),
      ],
    ],
    ['an account with no role', NO_ROLE, ['fallback'], []],
    [
      'a Coordinator who is also a tutor',
      COORDINATOR_TUTOR,
      ['staff', 'tutor', 'pending'],
      [
        P.approvals,
        P.offers,
        P.assignments,
        P.sessions,
        P.closeouts,
        P.tutorSchedule,
      ],
    ],
  ] as const)(
    '%s: its sections, and exactly its requests, once each',
    async (_who, as, sections, paths) => {
      const api = stub(as, {
        [`GET ${P.familyStudents}`]: ok({
          items: as === STUDENT ? [SELF_STUDENT] : [KID_A, KID_B],
        }),
      });
      await open();

      expect(sectionCodes()).toEqual(sections);
      expect(homeRequests(api)).toEqual([...paths].sort());
      // A consent_required student's schedule and homework are never read.
      expect(api.requests.some((r) => r.path.includes(KID_B.student_id))).toBe(
        false,
      );
      expect(api.calls('GET /health/live')).toHaveLength(0);
      expect(alerts(main())).toEqual([]);
    },
  );

  it('staff Home is the dashboard frame with the Today panel; the others greet', async () => {
    stub(OWNER);
    await open();
    expect(main().querySelector('h1')?.textContent).toBe('Dashboard');
    expect(main().querySelector('[data-home-today] h2')?.textContent).toBe(
      'Today',
    );
    expect(section('staff')?.querySelector('h3')?.textContent).toBe(
      "Today's classes",
    );
    app!.unmount();

    stub(TUTOR);
    await open();
    expect(main().querySelector('h1')?.textContent).toBe('Hello, Demo Tutor');
    expect(document.title).toBe('Classora');
    expect(section('tutor')?.querySelector('h2')?.textContent).toBe(
      'My classes',
    );
  });
});

describe('the staff agenda (B1, B4) and the staff counts (C1)', () => {
  it('reads [start of today, +7 days) in the center zone, without cancelled', async () => {
    const api = stub(OWNER);
    await open();
    const query = queryOf(api, P.sessions);
    expect(query.get('from')).toBe('2031-04-02T06:00:00.000Z');
    expect(query.get('to')).toBe('2031-04-09T06:00:00.000Z');
    expect(query.has('include_cancelled')).toBe(false);
    expect(queryOf(api, P.approvals).get('status')).toBe('open');
    expect(queryOf(api, P.approvals).get('limit')).toBe('1');
    expect(queryOf(api, P.closeouts).get('limit')).toBe('1');
    expect(queryOf(api, P.accounts).get('limit')).toBe('1');
  });

  it("lists today's first 8 classes, then 'and n more', with links and counts", async () => {
    stub(OWNER);
    await open();
    const staff = section('staff')!;
    const rows = staff.querySelectorAll('[data-home-session]');
    expect(rows).toHaveLength(8);
    expect(rows[0]!.getAttribute('href')).toBe(
      `/schedule/sessions/${BUSY_TODAY[0]!.id}`,
    );
    expect(rows[0]!.textContent).toContain('Matemáticas');
    expect(rows[0]!.textContent).toContain('Carmen Ruiz');
    expect(rows[0]!.textContent).toContain('Confirmed');
    expect(staff.querySelector('[data-home-more]')?.textContent).toBe(
      'and 2 more',
    );
    expect([...staff.querySelectorAll('a')].at(-1)?.getAttribute('href')).toBe(
      '/schedule?view=day&date=2031-04-02',
    );

    expect(pendingLinks()).toEqual([
      ['3 open approvals', '/approvals'],
      ['2 classes overdue for closeout', '/schedule/overdue-closeouts'],
      ['1 overdue family account', '/billing/overdue'],
      [
        '3 classes without a tutor or in conflict in the next 7 days',
        '/schedule?view=list&date=2031-04-02',
      ],
    ]);
  });

  it('shows "n+" when the API truncated the window', async () => {
    stub(FINANCE, {
      [`GET ${P.sessions}`]: ok(calendarPage(BUSY_TODAY, true)),
    });
    await open();
    expect(
      section('staff')!.querySelector('[data-home-more]')?.textContent,
    ).toBe('and 2+ more');
    expect(pendingLinks()).toContainEqual([
      '3+ classes without a tutor or in conflict in the next 7 days',
      '/schedule?view=list&date=2031-04-02',
    ]);
  });

  it('today is the center date: 23:30 local is today, 00:30 the next day is not', async () => {
    const late = staffItem({ starts_at: at(0, 23, 30), ends_at: at(1, 0, 15) });
    const early = staffItem({ starts_at: at(1, 0, 30), ends_at: at(1, 1, 30) });
    stub(COORDINATOR, {
      [`GET ${P.sessions}`]: ok(calendarPage([early, late])),
    });
    await open();
    const rows = [
      ...section('staff')!.querySelectorAll('[data-home-session]'),
    ].map((node) => node.getAttribute('data-home-session'));
    expect(rows).toEqual([late.id]);
  });

  it('no classes today: the next one, or "none in 7 days"; nothing pending', async () => {
    const tomorrow = staffItem({ starts_at: at(1, 10), ends_at: at(1, 11) });
    stub(COORDINATOR, {
      [`GET ${P.sessions}`]: ok(calendarPage([tomorrow])),
      [`GET ${P.approvals}`]: ok(offsetPage(0)),
      [`GET ${P.closeouts}`]: ok(offsetPage(0)),
    });
    await open();
    const staff = section('staff')!;
    expect(staff.querySelector('[data-home-empty]')?.textContent).toContain(
      'No classes today.',
    );
    expect(staff.textContent).toContain('Next class:');
    expect(staff.textContent).toContain('Matemáticas');
    expect(
      section('pending')!.querySelector('[data-home-nothing]')?.textContent,
    ).toBe('Nothing waiting for you.');
    app!.unmount();

    stub(COORDINATOR, { [`GET ${P.sessions}`]: ok(calendarPage([])) });
    await open();
    expect(section('staff')!.textContent).toContain(
      'No classes in the next 7 days.',
    );
  });
});

describe('the tutor (B2, C1)', () => {
  it("lists today's classes and the upcoming ones; offered is labelled", async () => {
    const api = stub(TUTOR);
    await open();
    const query = queryOf(api, P.tutorSchedule);
    expect(query.get('from')).toBe('2031-03-19T06:00:00.000Z');
    expect(query.get('to')).toBe('2031-04-16T06:00:00.000Z');
    expect(queryOf(api, P.offers).get('status')).toBe('pending');
    expect(queryOf(api, P.assignments).get('due')).toBe('past');
    expect(queryOf(api, P.assignments).get('limit')).toBe('50');

    const tutor = section('tutor')!;
    const today = tutor.querySelectorAll(
      '[data-home-today-list] [data-home-session]',
    );
    expect(today).toHaveLength(2);
    expect(today[1]!.textContent).toContain('Awaiting your answer');
    expect(today[0]!.getAttribute('href')).toBe(
      '/my-schedule?for=teaching&view=day&date=2031-04-02',
    );
    // The cancelled one is not listed.
    expect(
      tutor.querySelectorAll('[data-home-upcoming-list] [data-home-session]'),
    ).toHaveLength(1);

    expect(pendingLinks()).toEqual([
      ['2 offers waiting for your answer', '/offers'],
      [
        '1 class to close out',
        `/my-schedule/sessions/${TUTOR_TODAY[0]!.id}/closeout`,
      ],
      ['3 homework submissions to grade', '/assignments'],
    ]);
  });

  it('"n+" to grade when there is a next page; several to close out link My schedule', async () => {
    stub(TUTOR, {
      [`GET ${P.assignments}`]: ok(assignmentsPage([4], true)),
      [`GET ${P.tutorSchedule}`]: ok(
        scheduleOf([
          TUTOR_TODAY[0]!,
          {
            ...TUTOR_TODAY[0]!,
            id: 'second',
            starts_at: at(-1, 9),
            ends_at: at(-1, 10),
          },
        ]),
      ),
    });
    await open();
    expect(pendingLinks()).toContainEqual([
      '4+ homework submissions to grade',
      '/assignments',
    ]);
    expect(pendingLinks()).toContainEqual([
      '2 classes to close out',
      '/my-schedule',
    ]);
  });

  it('B5: a failed schedule shows its own error; the pending counts stay', async () => {
    stub(TUTOR, {
      [`GET ${P.tutorSchedule}`]: () => problem(500, 'internal_error'),
    });
    await open();
    const tutor = section('tutor')!;
    expect(tutor.querySelector('[role="alert"]')).toBeTruthy();
    expect(buttonNamed(tutor, 'Try again')).toBeTruthy();
    expect(pendingLinks()).toEqual([
      ['2 offers waiting for your answer', '/offers'],
      ['3 homework submissions to grade', '/assignments'],
    ]);
  });
});

describe('the family (B3, C1)', () => {
  it('a guardian: one consent line, the readable child, homework and balance', async () => {
    stub(GUARDIAN);
    await open();
    const family = section('family')!;
    expect(family.querySelector('[data-home-consent]')?.textContent).toBe(
      "Mateo Pérez hasn't shared their schedule with you.",
    );
    const rows = family.querySelectorAll('[data-home-session]');
    expect(rows).toHaveLength(2);
    expect(rows[0]!.getAttribute('href')).toBe(
      `/my-schedule?for=${KID_A.student_id}&view=day&date=2031-04-02`,
    );
    expect(pendingLinks()).toEqual([
      ['2 homework assignments to do', '/my-homework'],
      ['1 missing homework assignment', '/my-homework'],
      ['Overdue balance: $1,500.00', '/my-billing?status=overdue'],
    ]);
  });

  it('two readable children: each row names its student', async () => {
    const other = {
      ...KID_A,
      student_id: '00000000-0000-4000-8000-000000549c09',
      display_name: 'Tomás Pérez',
    };
    stub(GUARDIAN, {
      [`GET ${P.familyStudents}`]: ok({ items: [KID_A, other] }),
      [`GET ${P.schedule(other.student_id)}`]: ok(scheduleOf([])),
      [`GET ${P.homework(other.student_id)}`]: ok(homeworkSummary(1, 0)),
    });
    await open();
    const rows = section('family')!.querySelectorAll('[data-home-session]');
    expect(rows[0]!.textContent).toContain('For Lucía Pérez');
    expect(pendingLinks()[0]).toEqual([
      '3 homework assignments to do',
      '/my-homework',
    ]);
  });

  it('a guardian with no linked student and no family: empty states, no error', async () => {
    const api = stub(GUARDIAN, {
      [`GET ${P.familyStudents}`]: ok({ items: [] }),
      [`GET ${P.ownFamily}`]: ok({ family: null }),
    });
    await open();
    expect(section('family')!.textContent).toContain('No classes today.');
    expect(section('family')!.textContent).toContain(
      'No classes in the next 14 days.',
    );
    expect(section('pending')!.textContent).toContain(
      'Nothing waiting for you.',
    );
    expect(api.calls(`GET ${P.billing}`)).toHaveLength(0);
    expect(alerts(main())).toEqual([]);
  });

  it('a student with nothing: the empty states', async () => {
    stub(STUDENT, {
      [`GET ${P.familyStudents}`]: ok({ items: [SELF_STUDENT] }),
    });
    await open();
    expect(main().querySelector('h1')?.textContent).toBe('Hello, Demo Student');
    expect(section('family')!.querySelector('[data-home-empty]')).toBeTruthy();
    expect(
      section('pending')!.querySelector('[data-home-nothing]'),
    ).toBeTruthy();
  });
});

// HF-07 (E2 b): every agenda row starts with the class type (swatch, icon
// and text) and names the tutor; the row link's name carries both.
describe('the class type and the tutor on each row (HF-07)', () => {
  const LABELS = { individual: 'Individual', group: 'Group', course: 'Course' };

  function rows(code: string): HTMLAnchorElement[] {
    return [
      ...section(code)!.querySelectorAll<HTMLAnchorElement>(
        '[data-home-session]',
      ),
    ];
  }

  /** The type cue at the start of the row: swatch, icon and text. */
  function expectKind(row: HTMLAnchorElement, kind: keyof typeof LABELS) {
    const badge = row.querySelector(`[data-kind="${kind}"]`)!;
    expect(badge).toBeTruthy();
    // The first thing in the row (its first line).
    expect(row.querySelector('[data-kind]')).toBe(badge);
    expect(badge.textContent).toBe(LABELS[kind]);
    const swatch = badge.querySelector(`[data-kind-swatch="${kind}"]`);
    expect(swatch?.getAttribute('aria-hidden')).toBe('true');
    expect(swatch?.getAttribute('style')).toContain(`var(--kind-${kind})`);
    expect(badge.querySelector('svg')?.getAttribute('aria-hidden')).toBe(
      'true',
    );
    // The link's name is its text: the type is in it.
    expect(row.textContent).toContain(LABELS[kind]);
  }

  it('staff rows: the type cue and "Tutor: …" or "No tutor"', async () => {
    const none = staffItem({
      kind: 'group',
      state: 'unassigned',
      tutor: null,
      starts_at: at(0, 13),
      ends_at: at(0, 14),
    });
    stub(OWNER, {
      [`GET ${P.sessions}`]: ok(
        calendarPage([...BUSY_TODAY.slice(0, 3), none]),
      ),
    });
    await open();
    const shown = rows('staff');
    expect(shown.map((row) => row.getAttribute('data-home-session'))).toEqual([
      BUSY_TODAY[0]!.id,
      BUSY_TODAY[1]!.id,
      BUSY_TODAY[2]!.id,
      none.id,
    ]);
    expectKind(shown[0]!, 'individual');
    expectKind(shown[1]!, 'group');
    expectKind(shown[2]!, 'course');
    expectKind(shown[3]!, 'group');
    expect(shown[0]!.textContent).toContain('Tutor: Carmen Ruiz · Aula 2');
    expect(shown[3]!.textContent).toContain('No tutor · Aula 2 · Unassigned');
  });

  it("tutor rows: the type cue and the tutor's own name, then the students", async () => {
    stub(TUTOR);
    await open();
    const shown = rows('tutor');
    // Today (individual, group), then upcoming (course); cancelled stays out.
    expect(shown).toHaveLength(3);
    expectKind(shown[0]!, 'individual');
    expectKind(shown[1]!, 'group');
    expectKind(shown[2]!, 'course');
    for (const row of shown) {
      expect(row.textContent).toContain(
        `Tutor: ${TUTOR.display_name} · Lucía Pérez`,
      );
    }
  });

  it('family rows: the type cue and the existing "Tutor: …"', async () => {
    stub(GUARDIAN, {
      [`GET ${P.schedule(KID_A.student_id)}`]: ok(
        scheduleOf([...FAMILY_TODAY, FAMILY_COURSE]),
      ),
    });
    await open();
    const shown = rows('family');
    expect(shown).toHaveLength(3);
    expectKind(shown[0]!, 'individual');
    expectKind(shown[1]!, 'group');
    expectKind(shown[2]!, 'course');
    for (const row of shown) {
      expect(row.textContent).toContain('Tutor: Carmen Ruiz');
    }
  });

  it('in Spanish: "Tutor(a): …", "Sin tutor(a)" and the type labels', async () => {
    applyLanguage('es');
    const none = staffItem({
      kind: 'course',
      state: 'unassigned',
      tutor: null,
      starts_at: at(0, 13),
      ends_at: at(0, 14),
    });
    stub(COORDINATOR, {
      [`GET ${P.sessions}`]: ok(calendarPage([BUSY_TODAY[1]!, none])),
    });
    await open();
    const [group, course] = rows('staff');
    expect(group!.querySelector('[data-kind]')?.textContent).toBe('Grupal');
    expect(group!.textContent).toContain('Tutor(a): Carmen Ruiz');
    expect(course!.querySelector('[data-kind]')?.textContent).toBe('Curso');
    expect(course!.textContent).toContain('Sin tutor(a)');
  });
});

describe('refusals and errors on counts (C2)', () => {
  it('a 403 or 404 hides its item silently', async () => {
    stub(COORDINATOR, {
      [`GET ${P.approvals}`]: () => problem(403, 'permission_denied'),
      [`GET ${P.closeouts}`]: () => problem(404, 'not_found'),
    });
    await open();
    expect(pendingLinks()).toEqual([
      [
        '3 classes without a tutor or in conflict in the next 7 days',
        '/schedule?view=list&date=2031-04-02',
      ],
    ]);
    expect(alerts(main())).toEqual([]);
    expect(main().textContent).not.toContain("Couldn't load");
  });

  it('another error shows a compact line on that item, with Try again', async () => {
    const api = stub(COORDINATOR, {
      // A 5xx is retried twice (spec §10) before the item shows it.
      [`GET ${P.approvals}`]: [
        () => problem(500, 'internal_error'),
        () => problem(500, 'internal_error'),
        () => problem(500, 'internal_error'),
        ok(offsetPage(4)),
      ],
    });
    await open();
    const item = main().querySelector('[data-home-pending="approvals"]')!;
    expect(item.textContent).toContain("Couldn't load: open approvals.");
    expect(pendingLinks()).toContainEqual([
      '2 classes overdue for closeout',
      '/schedule/overdue-closeouts',
    ]);
    await click(buttonNamed(item, 'Try again'));
    await settleUntil(() =>
      pendingLinks().some(([text]) => text === '4 open approvals'),
    );
    expect(api.calls(`GET ${P.approvals}`)).toHaveLength(4);
  });
});

describe('the fallback (D3)', () => {
  it('quick links: the visible nav items except Home', async () => {
    stub({ ...TEST_USER, roles: [], permissions: ['user.view', 'audit.view'] });
    await open();
    const links = [...section('fallback')!.querySelectorAll('a')].map((node) =>
      node.getAttribute('href'),
    );
    expect(links).toEqual(['/users', '/audit']);
    expect(main().querySelector('h1')?.textContent).toBe('Hello, Demo Owner');
  });

  it('no visible page: the "ask the center" text', async () => {
    stub(NO_ROLE);
    await open();
    expect(
      section('fallback')!.querySelector('[data-home-no-access]')?.textContent,
    ).toBe(
      "Your account doesn't have access to any section yet. Ask the center for access.",
    );
  });
});

describe('language (E1)', () => {
  it('renders in Spanish with plural forms', async () => {
    applyLanguage('es');
    stub(COORDINATOR, { [`GET ${P.closeouts}`]: ok(offsetPage(1)) });
    await open();
    expect(main().querySelector('[data-home-today] h2')?.textContent).toBe(
      'Hoy',
    );
    expect(pendingLinks()).toEqual([
      ['3 aprobaciones abiertas', '/approvals'],
      ['1 clase con cierre atrasado', '/schedule/overdue-closeouts'],
      [
        '3 clases sin tutor o en conflicto en los próximos 7 días',
        '/schedule?view=list&date=2031-04-02',
      ],
    ]);
  });
});
