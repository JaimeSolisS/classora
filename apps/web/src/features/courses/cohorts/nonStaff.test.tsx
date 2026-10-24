import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SessionUserBody } from '@/features/auth/session';
import { applyLanguage } from '@/i18n';
import {
  meAs,
  stubApi,
  TEST_USER,
  type ApiStub,
  type Responder,
} from '@/test/api-stub';
import { jsonResponse, renderApp, type RenderedApp } from '@/test/render-app';
import {
  COHORT_ID,
  FAMILY_COHORT,
  FAMILY_LIST_ITEMS,
  FAMILY_SESSIONS,
  TUTOR_COHORT,
} from './fixtures';
import { assignmentOf } from './rules';

// #246 B2-B4, B6 (cohorts): the anyOf guard per role and "My courses", the
// tab bar without "Templates", the read-only list (no template or subject
// filter, no "New cohort", no template or subject request), the detail for
// a tutor ("Students" from `tutor_roster`, no Students panel, no Edit), a
// student (the syllabus) and a guardian of two (the "Student" column), and
// the links to "My schedule".

const LIST = 'GET /api/v1/cohorts';
const DETAIL = `GET /api/v1/cohorts/${COHORT_ID}`;
const SESSIONS_ROUTE = `GET /api/v1/cohorts/${COHORT_ID}/sessions`;
const TEMPLATES = 'GET /api/v1/course-templates';
const SUBJECTS = 'GET /api/v1/subjects';

const user = (
  roles: SessionUserBody['roles'],
  permissions: SessionUserBody['permissions'],
): SessionUserBody => ({ ...TEST_USER, roles, permissions });

const TUTOR = user(['tutor'], ['student.view', 'tutor.view']);
const GUARDIAN = user(['guardian'], ['student.view', 'guardian.view']);
const STUDENT = user(['student'], ['student.view']);
const OWNER = user(['owner'], ['course.view', 'schedule.view']);

let app: RenderedApp | undefined;
const main = () => app!.container.querySelector('main')!;

function stub(
  as: SessionUserBody,
  routes: Record<string, Responder | Responder[]> = {},
): ApiStub {
  return stubApi({
    'GET /api/v1/auth/me': meAs(as),
    [LIST]: () =>
      jsonResponse(200, { items: FAMILY_LIST_ITEMS, next_cursor: null }),
    [DETAIL]: () => jsonResponse(200, FAMILY_COHORT),
    [SESSIONS_ROUTE]: () => jsonResponse(200, { items: FAMILY_SESSIONS }),
    [TEMPLATES]: () =>
      jsonResponse(200, { items: [], total: 0, limit: 100, offset: 0 }),
    [SUBJECTS]: () =>
      jsonResponse(200, { items: [], total: 0, limit: 100, offset: 0 }),
    ...routes,
  });
}

/** Waits on the page's heading (and `text`), never a fixed delay. */
async function open(path: string, text?: string): Promise<RenderedApp> {
  const rendered = await renderApp(path);
  await vi.waitFor(
    () => {
      expect(rendered.container.querySelector('main h1')).not.toBeNull();
      if (text) {
        expect(rendered.container.querySelector('main')?.textContent).toContain(
          text,
        );
      }
    },
    { timeout: 3000, interval: 20 },
  );
  return rendered;
}

const navLabels = () =>
  [...app!.container.querySelectorAll('header nav a')].map(
    (a) => a.textContent,
  );

beforeEach(() => {
  applyLanguage('en');
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.unstubAllGlobals();
  document.body.innerHTML = '';
});

describe('the assignment of a non-staff read (A4)', () => {
  it("shows the series' tutor when the assignment is left out", () => {
    expect(assignmentOf(FAMILY_COHORT.series)).toEqual({
      kind: 'accepted',
      tutor: 'Carmen Ruiz',
    });
    expect(assignmentOf({ ...FAMILY_COHORT.series!, tutor: null })).toEqual({
      kind: 'none',
    });
  });
});

describe('access, "My courses" and the tab bar (B2)', () => {
  it.each([
    ['tutor', TUTOR, true],
    ['guardian', GUARDIAN, true],
    ['student', STUDENT, true],
    ['owner', OWNER, false],
  ] as const)('shows "My courses" to a %s: %s', async (_role, as, shown) => {
    stub(as);
    app = await open('/');
    expect(navLabels().includes('My courses')).toBe(shown);
  });

  it.each([
    ['tutor', TUTOR],
    ['guardian', GUARDIAN],
    ['student', STUDENT],
  ] as const)(
    'gives a %s the read-only list, without templates or subjects',
    async (_role, as) => {
      const api = stub(as);
      app = await open('/courses/cohorts', 'Álgebra en curso');

      expect(main().querySelector('h1')?.textContent).toBe('My courses');
      const tabs = main().querySelector('nav[aria-label="Courses sections"]')!;
      expect([...tabs.querySelectorAll('a')].map((a) => a.textContent)).toEqual(
        ['Cohorts'],
      );
      expect(main().querySelector('a[href="/courses/cohorts/new"]')).toBeNull();
      // The status filter only.
      expect(main().querySelectorAll('select')).toHaveLength(1);
      expect(api.calls(TEMPLATES)).toHaveLength(0);
      expect(api.calls(SUBJECTS)).toHaveLength(0);
      expect(main().textContent).toContain('Carmen Ruiz');
    },
  );

  it.each([
    ['tutor', TUTOR],
    ['guardian', GUARDIAN],
  ] as const)(
    'keeps /courses/cohorts/new and /courses Forbidden for a %s',
    async (_role, as) => {
      stub(as);
      for (const path of ['/courses/cohorts/new', '/courses']) {
        app = await open(path);
        expect(main().querySelector('h1')?.textContent).toBe(
          "You don't have access to this page",
        );
        app.unmount();
        app = undefined;
      }
    },
  );
});

describe('the cohort page (B2-B4)', () => {
  it('gives the tutor "Students" by name and no staff control', async () => {
    stub(TUTOR, { [DETAIL]: () => jsonResponse(200, TUTOR_COHORT) });
    app = await open(`/courses/cohorts/${COHORT_ID}`, 'Ana López');

    const heading = [...main().querySelectorAll('h2')].find(
      (h) => h.textContent === 'Students',
    )!;
    const list = main().querySelector(`ul[aria-labelledby="${heading.id}"]`)!;
    expect(
      [...list.querySelectorAll('li')].map((li) => li.textContent),
    ).toEqual(['Ana López', 'Bruno Díaz']);
    const buttons = [...main().querySelectorAll('button')].map(
      (b) => b.textContent,
    );
    expect(buttons).not.toContain('Edit');
    expect(buttons).not.toContain('Enroll student');
    expect(buttons).not.toContain('Offer to a tutor');
    expect(main().textContent).not.toContain('Show withdrawn');
    expect(main().querySelector('a[href^="/schedule/sessions/"]')).toBeNull();
    expect(main().querySelector('a[href^="/courses/templates/"]')).toBeNull();
  });

  it('shows a student the syllabus and the sessions linked to My schedule', async () => {
    stub(STUDENT, {
      [SESSIONS_ROUTE]: () =>
        jsonResponse(200, {
          items: FAMILY_SESSIONS.map((item) => ({
            ...item,
            students: item.students!.slice(0, 1),
          })),
        }),
    });
    app = await open(
      `/courses/cohorts/${COHORT_ID}`,
      'Not held: center closed',
    );

    const text = main().textContent ?? '';
    expect(text).toContain('Ecuaciones (copia congelada)');
    expect(text).toContain('Carmen Ruiz');
    expect(
      [...main().querySelectorAll('h2')].map((h) => h.textContent),
    ).not.toContain('Students');
    const headers = [...main().querySelectorAll('th')].map(
      (th) => th.textContent,
    );
    expect(headers).not.toContain('Student');
    const links = [...main().querySelectorAll('a[href^="/my-schedule"]')].map(
      (a) => a.getAttribute('href'),
    );
    expect(links).toContain('/my-schedule?date=2031-01-06&view=list');
    expect(links).toContain('/my-schedule?date=2031-01-08&view=list');
  });

  it('names whose session it is for a guardian of two', async () => {
    stub(GUARDIAN);
    app = await open(`/courses/cohorts/${COHORT_ID}`, 'Bruno Díaz');

    const headers = [...main().querySelectorAll('th')].map(
      (th) => th.textContent,
    );
    expect(headers).toContain('Student');
    const rows = [...main().querySelectorAll('tbody tr')].map(
      (row) => row.textContent ?? '',
    );
    expect(rows[0]).toContain('Ana López, Bruno Díaz');
    expect(rows[0]).toContain('Confirmed');
    expect(rows[1]).toContain('Not held: center closed');
    const headings = [...main().querySelectorAll('h2')].map(
      (h) => h.textContent,
    );
    expect(headings).not.toContain('Students');
  });
});
